"""
Aircraft data feeds for AirScatter.

Supported sources:
  - OpenSky Network REST API (anonymous, no key required)
  - Local dump1090 / readsb SBS TCP feed (port 30003)
"""
from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable


# ─── Aircraft state ───────────────────────────────────────────────────────────

@dataclass
class Aircraft:
    icao24: str
    callsign: str | None
    lat: float | None
    lon: float | None
    altitude_m: float | None          # geometric altitude in metres
    velocity_ms: float | None
    heading_deg: float | None
    vertical_rate_ms: float | None
    on_ground: bool
    last_seen: float = field(default_factory=time.monotonic)

    @property
    def has_position(self) -> bool:
        return (
            self.lat is not None
            and self.lon is not None
            and self.altitude_m is not None
            and self.altitude_m > 0
            and not self.on_ground
        )


# ─── OpenSky feed ─────────────────────────────────────────────────────────────

class OpenSkyFeed:
    """
    Polls OpenSky Network every `interval_s` seconds for aircraft in a
    bounding box and calls `on_update` with the fresh list.
    Anonymous rate limit: 1 req/10s, 100 req/day.
    """

    URL = "https://opensky-network.org/api/states/all"

    def __init__(
        self,
        lat_min: float,
        lon_min: float,
        lat_max: float,
        lon_max: float,
        interval_s: float = 30.0,
        max_backoff_s: float = 300.0,
        on_update: Callable[[list[Aircraft]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_rate_limited: Callable[[float], None] | None = None,
    ) -> None:
        self.lat_min = lat_min
        self.lon_min = lon_min
        self.lat_max = lat_max
        self.lon_max = lon_max
        self.interval_s = interval_s
        self.max_backoff_s = max_backoff_s
        self.on_update = on_update
        self.on_error = on_error
        self.on_rate_limited = on_rate_limited
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def fetch_once(self) -> list[Aircraft]:
        url = (
            f"{self.URL}?lamin={self.lat_min}&lomin={self.lon_min}"
            f"&lamax={self.lat_max}&lomax={self.lon_max}"
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AirWSJT-X/0.1 (ham radio scatter tool)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        aircraft = []
        for s in data.get("states") or []:
            # OpenSky state vector fields:
            # 0:icao24 1:callsign 2:origin_country 3:time_position
            # 4:last_contact 5:longitude 6:latitude 7:baro_altitude
            # 8:on_ground 9:velocity 10:true_track 11:vertical_rate
            # 12:sensors 13:geo_altitude 14:squawk 15:spi 16:position_source
            icao24 = s[0] or ""
            callsign = (s[1] or "").strip() or None
            lon = s[5]
            lat = s[6]
            baro_alt = s[7]   # metres, can be None
            geo_alt = s[13]   # geometric altitude, preferred
            altitude_m = geo_alt if geo_alt is not None else baro_alt
            on_ground = bool(s[8])
            velocity = s[9]
            heading = s[10]
            vert_rate = s[11]

            aircraft.append(Aircraft(
                icao24=icao24,
                callsign=callsign,
                lat=lat,
                lon=lon,
                altitude_m=altitude_m,
                velocity_ms=velocity,
                heading_deg=heading,
                vertical_rate_ms=vert_rate,
                on_ground=on_ground,
            ))
        return aircraft

    def _run(self) -> None:
        consecutive_429 = 0
        while not self._stop.is_set():
            wait_s = self.interval_s
            try:
                aircraft = self.fetch_once()
                consecutive_429 = 0
                if self.on_update:
                    self.on_update(aircraft)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    consecutive_429 += 1
                    base_delay = max(self.interval_s, 10.0)
                    wait_s = min(base_delay * (2 ** (consecutive_429 - 1)), self.max_backoff_s)
                    if self.on_rate_limited:
                        self.on_rate_limited(wait_s)
                else:
                    consecutive_429 = 0
                    if self.on_error:
                        self.on_error(f"HTTP Error {e.code}: {e.reason}")
            except Exception as e:
                consecutive_429 = 0
                if self.on_error:
                    self.on_error(str(e))
            self._stop.wait(wait_s)


# ─── dump1090 SBS (port 30003) feed ──────────────────────────────────────────

class Dump1090Feed:
    """
    Connects to a dump1090/readsb SBS TCP feed (default localhost:30003).
    Parses MSG,3 (airborne position) messages.
    Calls `on_update` with the current aircraft dict every `report_interval_s`.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 30003,
        report_interval_s: float = 5.0,
        stale_after_s: float = 60.0,
        on_update: Callable[[list[Aircraft]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.report_interval_s = report_interval_s
        self.stale_after_s = stale_after_s
        self.on_update = on_update
        self.on_error = on_error
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._aircraft: dict[str, Aircraft] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text in {"nan", "inf", "-inf"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _decode_callsign(raw: str) -> str | None:
        text = (raw or "").strip()
        if not text:
            return None
        if re.fullmatch(r"[A-Z0-9_]{2,8}", text):
            return text.replace("_", " ").strip()
        return None

    def _parse_sbs(self, line: str) -> None:
        clean = line.strip().strip("\x00")
        if not clean:
            return
        if clean.startswith("*"):
            clean = clean[1:]
        parts = [p.strip() for p in clean.split(",")]
        if len(parts) < 5 or parts[0] not in {"MSG", "ES"}:
            return
        msg_type = parts[1]
        icao24 = (parts[4] or "").lower()
        if not icao24:
            return

        callsign_raw = parts[10] if len(parts) > 10 else ""

        with self._lock:
            ac = self._aircraft.setdefault(
                icao24,
                Aircraft(
                    icao24=icao24, callsign=None, lat=None, lon=None,
                    altitude_m=None, velocity_ms=None, heading_deg=None,
                    vertical_rate_ms=None, on_ground=False,
                ),
            )
            ac.last_seen = time.monotonic()

            callsign = self._decode_callsign(callsign_raw)
            if callsign:
                ac.callsign = callsign

            if msg_type == "1":  # Identification
                pass
            elif msg_type == "3":  # Airborne position
                alt_ft = self._coerce_float(parts[11]) if len(parts) > 11 else None
                lat = self._coerce_float(parts[14]) if len(parts) > 14 else None
                lon = self._coerce_float(parts[15]) if len(parts) > 15 else None
                on_ground = False
                if len(parts) > 21:
                    on_ground = self._coerce_float(parts[21]) == 1.0
                if alt_ft is not None:
                    ac.altitude_m = alt_ft * 0.3048
                if lat is not None:
                    ac.lat = lat
                if lon is not None:
                    ac.lon = lon
                ac.on_ground = on_ground
            elif msg_type == "4":  # Airborne velocity
                speed_kts = self._coerce_float(parts[11]) if len(parts) > 11 else None
                heading = self._coerce_float(parts[12]) if len(parts) > 12 else None
                vert = self._coerce_float(parts[16]) if len(parts) > 16 else None
                if vert is None and len(parts) > 15:
                    vert = self._coerce_float(parts[15])
                if speed_kts is not None:
                    ac.velocity_ms = speed_kts * 0.514444
                if heading is not None:
                    ac.heading_deg = heading
                if vert is not None:
                    ac.vertical_rate_ms = vert * 0.00508  # ft/min → m/s

    def _parse_frame(self, frame: str | bytes | bytearray) -> None:
        if isinstance(frame, (bytes, bytearray)):
            try:
                text = frame.decode("ascii", "ignore")
            except Exception:
                text = ""
            if text:
                self._parse_sbs(text)
                return
        else:
            self._parse_sbs(frame)

    def _parse_beast(self, frame: bytes | bytearray) -> None:
        self._parse_frame(frame)

    def _expire_stale(self) -> None:
        now = time.monotonic()
        with self._lock:
            stale = [k for k, v in self._aircraft.items()
                     if now - v.last_seen > self.stale_after_s]
            for k in stale:
                del self._aircraft[k]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10.0)
                sock.connect((self.host, self.port))
                sock.settimeout(5.0)
                if self.on_connected:
                    self.on_connected()
                buf = ""
                last_report = time.monotonic()

                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096).decode("ascii", "ignore")
                        if not chunk:
                            break
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            self._parse_sbs(line.strip())
                    except socket.timeout:
                        pass

                    now = time.monotonic()
                    if now - last_report >= self.report_interval_s:
                        self._expire_stale()
                        if self.on_update:
                            with self._lock:
                                self.on_update(list(self._aircraft.values()))
                        last_report = now

                sock.close()
            except Exception as e:
                if self.on_error:
                    self.on_error(str(e))
                if self.on_disconnected:
                    self.on_disconnected()

            if not self._stop.is_set():
                self._stop.wait(5.0)
