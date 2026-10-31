"""CRC-checked DF17 ADS-B state from AVR frames emitted by the native receiver.

Airborne barometric Q=1 altitude and global even/odd CPR are supported.
Surface, Gillham Q=0, MLAT, and error-corrected frames are deliberately excluded.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
import re
import time

from airwsjtx.airscout import Aircraft

_AVR = re.compile(r"\*([0-9A-Fa-f]{28});")
_CHARACTERS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######"
POSITION_RETENTION_S = 120.0
AIRCRAFT_RETENTION_S = 180.0


def crc_ok(frame: bytes) -> bool:
    if len(frame) != 14:
        return False
    value = int.from_bytes(frame, "big")
    for shift in range(111, 23, -1):
        if value & (1 << shift):
            value ^= 0x1FFF409 << (shift - 24)
    return value == 0


def _nl(latitude: float) -> int:
    latitude = abs(latitude)
    if latitude >= 87:
        return 2 if latitude == 87 else 1
    return int(2 * math.pi / math.acos(
        1 - (1 - math.cos(math.pi / 30)) / math.cos(math.radians(latitude)) ** 2
    ))


def global_cpr(even: tuple[float, int, int], odd: tuple[float, int, int]) -> tuple[float, float] | None:
    """Return a globally unambiguous airborne position from a <=10s pair."""
    if abs(even[0] - odd[0]) > 10:
        return None
    ye, yo = even[1] / 131072, odd[1] / 131072
    j = math.floor(59 * ye - 60 * yo + 0.5)
    lat_e, lat_o = 6 * (j % 60 + ye), (360 / 59) * (j % 59 + yo)
    if lat_e >= 270:
        lat_e -= 360
    if lat_o >= 270:
        lat_o -= 360
    if not (-90 <= lat_e <= 90 and -90 <= lat_o <= 90) or _nl(lat_e) != _nl(lat_o):
        return None
    is_odd = odd[0] > even[0]
    lat = lat_o if is_odd else lat_e
    nl = _nl(lat)
    m = math.floor(even[2] * (nl - 1) / 131072 - odd[2] * nl / 131072 + 0.5)
    ni = max(nl - int(is_odd), 1)
    lon = (360 / ni) * (m % ni + (odd[2] if is_odd else even[2]) / 131072)
    if lon >= 180:
        lon -= 360
    return lat, lon


@dataclass
class _Track:
    aircraft: Aircraft
    cpr: dict[int, tuple[float, int, int]] = field(default_factory=dict)
    position_at: float | None = None
    velocity_at: float | None = None


class ADSBDecoder:
    def __init__(self) -> None:
        self.tracks: dict[str, _Track] = {}
        self.frames = 0
        self.crc_errors = 0
        self.unsupported_frames = 0
        self.valid_messages = 0
        self.positions = 0
        self.position_rejections = 0
        self.last_message_at: float | None = None

    def consume(self, line: str, now: float | None = None, epoch: float | None = None) -> bool:
        match = _AVR.fullmatch(line.strip())
        if match is None:
            return False
        self.frames += 1
        frame = bytes.fromhex(match[1])
        # Other Mode S formats can use address-overlaid parity, not ADS-B's
        # zero CRC remainder. They are unsupported, not proven CRC failures.
        if frame[0] >> 3 != 17:
            self.unsupported_frames += 1
            return False
        if not crc_ok(frame):
            self.crc_errors += 1
            return False
        now = time.monotonic() if now is None else now
        epoch = time.time() if epoch is None else epoch
        self.valid_messages += 1
        self.last_message_at = now
        address = frame[1:4].hex()
        track = self.tracks.get(address)
        if track is None:
            track = _Track(Aircraft(address, None, None, None, None, None, None, None, False))
            self.tracks[address] = track
        aircraft = track.aircraft
        aircraft.last_seen = now
        value = int.from_bytes(frame, "big")

        def bits(start: int, length: int) -> int:
            return (value >> (112 - start - length + 1)) & ((1 << length) - 1)

        tc = bits(33, 5)
        if 1 <= tc <= 4:
            aircraft.callsign = "".join(
                _CHARACTERS[bits(start, 6)] for start in range(41, 89, 6)
            ).replace("_", " ").replace("#", "").strip() or None
        elif 5 <= tc <= 8:
            aircraft.on_ground = True
            aircraft.lat = aircraft.lon = aircraft.altitude_m = None
            track.cpr.clear()
            track.position_at = None
        elif 9 <= tc <= 18:
            aircraft.on_ground = False
            altitude = bits(41, 12)
            if altitude & 0x10:
                n = ((altitude & 0xFE0) >> 1) | (altitude & 0xF)
                aircraft.altitude_m = (n * 25 - 1000) * 0.3048
            else:
                aircraft.altitude_m = None
            parity = bits(54, 1)
            track.cpr[parity] = (now, bits(55, 17), bits(72, 17))
            if 0 in track.cpr and 1 in track.cpr:
                position = global_cpr(track.cpr[0], track.cpr[1])
                if position is not None:
                    if not plausible_position(track, position, now):
                        self.position_rejections += 1
                        track.cpr.clear()
                        return True
                    aircraft.lat, aircraft.lon = position
                    aircraft.position_updated_epoch_s = epoch
                    aircraft.position_source = "rtlsdr"
                    track.position_at = now
                    self.positions += 1
        elif tc == 19 and bits(38, 3) in (1, 2):
            scale = 4 if bits(38, 3) == 2 else 1
            ew, ns = bits(47, 10), bits(58, 10)
            if ew and ns:
                vx = (ew - 1) * scale * (-1 if bits(46, 1) else 1)
                vy = (ns - 1) * scale * (-1 if bits(57, 1) else 1)
                aircraft.velocity_ms = math.hypot(vx, vy) * 0.514444
                aircraft.heading_deg = math.degrees(math.atan2(vx, vy)) % 360
            else:
                aircraft.velocity_ms = aircraft.heading_deg = None
            vertical = bits(70, 9)
            aircraft.vertical_rate_ms = (
                (vertical - 1) * 64 * 0.00508 * (-1 if bits(69, 1) else 1)
                if vertical else None
            )
            track.velocity_at = now
        return True

    def snapshot(self, now: float | None = None) -> list[Aircraft]:
        now = time.monotonic() if now is None else now
        self.tracks = {key: track for key, track in self.tracks.items()
                       if now - track.aircraft.last_seen <= AIRCRAFT_RETENTION_S}
        result = []
        for track in self.tracks.values():
            aircraft = replace(track.aircraft)
            if track.position_at is None or now - track.position_at >= POSITION_RETENTION_S:
                aircraft.lat = aircraft.lon = aircraft.altitude_m = None
                aircraft.position_updated_epoch_s = None
            if track.velocity_at is None or now - track.velocity_at > 15:
                aircraft.velocity_ms = aircraft.heading_deg = aircraft.vertical_rate_ms = None
            result.append(aircraft)
        return result


def plausible_position(track: _Track, position: tuple[float, float], now: float) -> bool:
    """Reject implausible jumps only against a recent established position."""
    previous = track.aircraft
    if (track.position_at is None or now - track.position_at > 15
            or previous.lat is None or previous.lon is None):
        return True
    elapsed = now - track.position_at
    if elapsed < 0:
        return False
    # Great-circle distance; allow a generous 500 m base error and at least
    # 1200 knots. A fresh reported higher speed increases, never tightens, it.
    lat1, lat2 = math.radians(previous.lat), math.radians(position[0])
    dlat = lat2 - lat1
    dlon = math.radians(position[1] - previous.lon)
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    distance = 6371000 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, hav))))
    speed = 1200 * 0.514444
    if (track.velocity_at is not None and 0 <= now - track.velocity_at <= 15
            and previous.velocity_ms is not None):
        speed = max(speed, previous.velocity_ms * 1.5)
    return distance <= 500 + (elapsed + 1) * speed
