"""
Aircraft data feeds for AirScatter.

Supported sources:
  - OpenSky Network REST API (anonymous or OAuth2 client credentials)
  - ADS-B Exchange v2 REST API (API key required)
  - Local dump1090 / readsb SBS TCP feed (port 30003)
"""
from __future__ import annotations

import gzip
import json
import math
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from collections.abc import Mapping
from typing import Callable, cast

try:
    import certifi
except Exception:  # pragma: no cover
    certifi = None


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
    position_updated_epoch_s: float | None = None

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

class SourceRateLimit:
    """Quota samples share aircraft requests; cooldown also gates manual refresh."""

    def __init__(self, opensky: bool = True) -> None:
        self.opensky = opensky
        self.remaining: int | None = None
        self.checked_epoch_s: float | None = None
        self.retry_epoch_s: float | None = None
        self.error: str | None = None
        self._next_sample = 0.0
        self._retry_until = 0.0
        self._limited = False
        self._paused = False
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "remaining": self.remaining, "checked_epoch_s": self.checked_epoch_s,
                "retry_epoch_s": self.retry_epoch_s, "quota_supported": self.opensky,
                "check_interval_s": 1800 if self._limited else 600, "error": self.error,
            }

    def retry_wait(self) -> float:
        with self._lock:
            return max(0.0, self._retry_until - time.monotonic())

    def observe(self, headers: Mapping[str, str], status: int) -> bool:
        headers = {name.lower(): value for name, value in headers.items()}
        now = time.monotonic()
        epoch = time.time()
        with self._lock:
            resumed = status == 200 and self._paused and now >= self._retry_until
            sample = self.opensky and (now >= self._next_sample or (status == 429 and not self._limited) or resumed)
            if sample:
                raw = headers.get("x-rate-limit-remaining")
                self.remaining = None
                self.error = None
                if raw is not None:
                    if raw.isascii() and raw.isdigit():
                        self.remaining = int(raw)
                    else:
                        self.error = "Invalid remaining-quota response header"
                self.checked_epoch_s = epoch
            if status == 429:
                self._limited = True
                self._paused = True
                server_delays: list[float] = []
                for name in ("x-rate-limit-retry-after-seconds", "retry-after"):
                    raw = headers.get(name)
                    if raw is None:
                        continue
                    try:
                        seconds = float(raw)
                        if not math.isfinite(seconds) or seconds < 0:
                            raise ValueError("Invalid retry time")
                    except ValueError:
                        try:
                            seconds = max(0.0, parsedate_to_datetime(raw).timestamp() - epoch)
                        except (ValueError, TypeError, OverflowError):
                            self.error = "Invalid retry-time response header"
                            continue
                    server_delays.append(seconds)
                delay = max(server_delays, default=1800.0 if self.opensky else 0.0)
                self._retry_until = max(self._retry_until, now + delay)
                self.retry_epoch_s = epoch + max(0.0, self._retry_until - now)
            elif resumed or now >= self._retry_until:
                self._paused = False
                self._retry_until = 0.0
                self.retry_epoch_s = None
            if sample:
                self._next_sample = now + (1800 if self._limited else 600)
            return sample or status == 429 or resumed

class OpenSkyFeed:
    """
    Polls OpenSky Network every `interval_s` seconds for aircraft in a
    bounding box and calls `on_update` with the fresh list.
    Anonymous rate limit: 1 req/10s, 100 req/day.
    """

    URL = "https://opensky-network.org/api/states/all"
    TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

    @staticmethod
    def _ssl_context() -> ssl.SSLContext:
        """Use the installed CA bundle so packaged macOS builds validate OpenSky TLS."""
        if certifi is not None:
            try:
                return ssl.create_default_context(cafile=certifi.where())
            except Exception:
                pass
        return ssl.create_default_context()

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
        client_id: str = "",
        client_secret: str = "",
        rate_limit: SourceRateLimit | None = None,
        on_quota: Callable[[dict[str, object]], None] | None = None,
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
        if bool(client_id) != bool(client_secret):
            raise ValueError("OpenSky requires both Client ID and Client Secret")
        self._client_id, self._client_secret = client_id, client_secret
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = threading.Lock()
        self.rate_limit = rate_limit if rate_limit is not None else SourceRateLimit()
        self.on_quota = on_quota

    def _check_cooldown(self) -> None:
        delay = self.rate_limit.retry_wait()
        if delay > 0:
            headers = HTTPMessage()
            headers["Retry-After"] = str(delay)
            raise urllib.error.HTTPError(self.URL, 429, "Aircraft API cooldown is active",
                                         headers, None)

    def _observe_quota(self, headers: Mapping[str, str], status: int) -> None:
        if self.rate_limit.observe(headers, status) and self.on_quota:
            self.on_quota(self.rate_limit.snapshot())

    def set_poll_interval(self, seconds: float) -> None:
        if not math.isfinite(seconds) or seconds < (5 if isinstance(self, ADSBExchangeFeed) else 10):
            raise ValueError("Invalid aircraft poll interval")
        self.interval_s = seconds

    def _get_token(self) -> str:
        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires:
                return self._token
            body = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            }).encode("utf-8")
            req = urllib.request.Request(self.TOKEN_URL, data=body, headers={
                "Content-Type": "application/x-www-form-urlencoded",
            })
            try:
                with urllib.request.urlopen(req, timeout=15, context=self._ssl_context()) as response:
                    raw: object = json.loads(response.read())
            except urllib.error.HTTPError as e:
                raise ValueError(f"OpenSky authentication failed (HTTP {e.code})") from None
            except urllib.error.URLError:
                raise ValueError("Cannot reach the OpenSky authentication service") from None
            except ValueError:
                raise ValueError("OpenSky authentication returned invalid JSON") from None
            if not isinstance(raw, dict):
                raise ValueError("OpenSky authentication returned an invalid response")
            data = cast(dict[str, object], raw)
            token = data.get("access_token")
            if not isinstance(token, str) or not token:
                raise ValueError("OpenSky authentication returned no access token")
            expires = data.get("expires_in")
            if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= 0:
                raise ValueError("OpenSky authentication returned an invalid token lifetime")
            self._token = token
            self._token_expires = time.monotonic() + max(0, expires - min(30, expires / 2))
            return self._token

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def fetch_once(self) -> list[Aircraft]:
        self._check_cooldown()
        url = (
            f"{self.URL}?lamin={self.lat_min}&lomin={self.lon_min}"
            f"&lamax={self.lat_max}&lomax={self.lon_max}"
        )
        ctx = self._ssl_context()
        for attempt in range(2):
            headers = {"User-Agent": "AirWSJT-X/0.1 (ham radio scatter tool)"}
            if self._client_id:
                headers["Authorization"] = f"Bearer {self._get_token()}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    self._observe_quota(dict(resp.headers.items()), 200)
                    data = json.loads(resp.read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self._observe_quota(dict(e.headers.items()) if e.headers else {}, e.code)
                if e.code != 401 or not self._client_id or attempt:
                    raise
                with self._token_lock:
                    self._token = None
        else:
            raise ValueError("OpenSky authentication retry failed")

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
                position_updated_epoch_s=s[3],
            ))
        return aircraft

    def _run(self) -> None:
        consecutive_429 = 0
        while not self._stop.is_set():
            started = time.monotonic()
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
                    cooldown = self.rate_limit.retry_wait()
                    if cooldown > 0:
                        wait_s = max(10.0 if self.rate_limit.opensky else 5.0, cooldown)
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
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.0, wait_s - elapsed) if consecutive_429 == 0 and wait_s == self.interval_s else wait_s)


class ADSBExchangeFeed(OpenSkyFeed):
    """Authenticated ADS-B Exchange v2 polling using the existing REST feed lifecycle."""

    URL = "https://gateway.adsbexchange.com/api/aircraft/v2"

    def __init__(
        self, api_key: str, lat: float, lon: float, radius_nm: float,
        on_update: Callable[[list[Aircraft]], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_rate_limited: Callable[[float], None] | None = None,
        interval_s: float = 30.0,
        rate_limit: SourceRateLimit | None = None,
        on_quota: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Set ADSBEXCHANGE_API_KEY before enabling ADS-B Exchange")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and math.isfinite(radius_nm) and radius_nm > 0):
            raise ValueError("Invalid ADS-B Exchange query coordinates or radius")
        super().__init__(
            lat, lon, lat, lon, interval_s=interval_s,
            on_update=on_update, on_error=on_error, on_rate_limited=on_rate_limited,
            rate_limit=rate_limit if rate_limit is not None else SourceRateLimit(opensky=False),
            on_quota=on_quota,
        )
        self._api_key = api_key.strip()
        self.lat, self.lon, self.radius_nm = lat, lon, radius_nm
        self._request_lock = threading.Lock()
        self._last_request: float | None = None

    @staticmethod
    def _number(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if not isinstance(value, (int, float, str)):
            raise ValueError("Invalid number in ADS-B Exchange aircraft response")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite number in ADS-B Exchange aircraft response")
        return number

    def fetch_once(self) -> list[Aircraft]:
        self._check_cooldown()
        # Manual refresh and background polling share the API key's request budget.
        with self._request_lock:
            if self._last_request is not None and time.monotonic() - self._last_request < 5:
                raise ValueError("ADS-B Exchange requests must be at least five seconds apart")
            self._last_request = time.monotonic()
            req = urllib.request.Request(
                f"{self.URL}/lat/{self.lat}/lon/{self.lon}/dist/{self.radius_nm}",
                headers={
                    "x-api-key": self._api_key,
                    "Accept-Encoding": "gzip",
                    "User-Agent": "AirWSJT-X",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=15, context=self._ssl_context()) as resp:
                    self._observe_quota(dict(resp.headers.items()), 200)
                    body = resp.read()
                    if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    data = json.loads(body)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self._observe_quota(dict(e.headers.items()) if e.headers else {}, e.code)
                raise
        if not isinstance(data, dict) or not isinstance(data.get("ac"), list):
            raise ValueError("ADS-B Exchange response must contain an ac array")
        now = self._number(data.get("now"))
        aircraft = []
        for item in data["ac"]:
            if not isinstance(item, dict) or not isinstance(item.get("hex"), str):
                raise ValueError("ADS-B Exchange aircraft is missing its hex address")
            ground = item.get("alt_baro") == "ground"
            altitude = self._number(item.get("alt_geom"))
            if altitude is None and not ground:
                altitude = self._number(item.get("alt_baro"))
            speed = self._number(item.get("gs"))
            rate = self._number(item.get("geom_rate"))
            if rate is None:
                rate = self._number(item.get("baro_rate"))
            seen = self._number(item.get("seen_pos"))
            flight = item.get("flight")
            if flight is not None and not isinstance(flight, str):
                raise ValueError("Invalid ADS-B Exchange aircraft callsign")
            aircraft.append(Aircraft(
                icao24=item["hex"].lower(), callsign=(flight or "").strip() or None,
                lat=self._number(item.get("lat")), lon=self._number(item.get("lon")),
                altitude_m=altitude * 0.3048 if altitude is not None else None,
                velocity_ms=speed * 0.514444 if speed is not None else None,
                heading_deg=self._number(item.get("track")),
                vertical_rate_ms=rate * 0.00508 if rate is not None else None,
                on_ground=ground,
                position_updated_epoch_s=now / 1000 - max(0, seen)
                if now is not None and seen is not None else None,
            ))
        return aircraft


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
        upper = text.upper()
        if re.fullmatch(r"[A-Z0-9_]{2,8}", upper):
            return upper.replace("_", " ").strip()
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
                if len(parts) > 21:
                    ac.on_ground = parts[21].strip() == "1"
                if alt_ft is not None:
                    ac.altitude_m = alt_ft * 0.3048
                if lat is not None:
                    ac.lat = lat
                if lon is not None:
                    ac.lon = lon
                if lat is not None and lon is not None:
                    ac.position_updated_epoch_s = time.time()
            elif msg_type == "4":  # Airborne velocity
                speed_kts = self._coerce_float(parts[12]) if len(parts) > 12 else None
                heading = self._coerce_float(parts[13]) if len(parts) > 13 else None
                vert = self._coerce_float(parts[16]) if len(parts) > 16 else None
                if speed_kts is not None:
                    ac.velocity_ms = speed_kts * 0.514444
                if heading is not None:
                    ac.heading_deg = heading
                if vert is not None:
                    ac.vertical_rate_ms = vert * 0.00508  # ft/min → m/s

    def _parse_frame(self, frame: str | bytes | bytearray) -> None:
        if isinstance(frame, (bytes, bytearray)):
            if frame and frame[0] == 0x1A:
                self._parse_beast(frame)
                return
            text = frame.decode("ascii", "replace")
            if text:
                self._parse_sbs(text)
                return
        else:
            self._parse_sbs(frame)

    def _parse_beast(self, frame: bytes | bytearray) -> None:
        raise NotImplementedError(
            "dump1090 Beast binary frames are not supported by this client; "
            "use SBS/CSV output (the standard dump1090 --net-sbs mode)."
        )

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
