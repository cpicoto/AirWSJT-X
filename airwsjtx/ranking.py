"""Relative ranking indices; legacy geometry still controls lock eligibility."""
from __future__ import annotations

import math

from .airscout import Aircraft
from .geo import bearing_deg, elevation_deg, haversine_km, heading_delta_deg, slant_range_km

RANKING_METHODS = ("legacy", "range-product", "window-product")
StationPosition = tuple[float, float, float]


def _destination(lat: float, lon: float, heading: float, distance_km: float) -> tuple[float, float]:
    distance, bearing = distance_km / 6371, math.radians(heading)
    latitude, longitude = math.radians(lat), math.radians(lon)
    target_lat = math.asin(max(-1, min(1,
        math.sin(latitude) * math.cos(distance)
        + math.cos(latitude) * math.sin(distance) * math.cos(bearing))))
    target_lon = longitude + math.atan2(
        math.sin(bearing) * math.sin(distance) * math.cos(latitude),
        math.cos(distance) - math.sin(latitude) * math.sin(target_lat))
    return math.degrees(target_lat), (math.degrees(target_lon) + 180) % 360 - 180


def usable_seconds(ac: Aircraft, a: StationPosition, b: StationPosition) -> tuple[float, bool]:
    """First exit with the same five-second search as src/lib/usableTime.ts."""
    assert ac.lat is not None and ac.lon is not None and ac.altitude_m is not None
    assert ac.heading_deg is not None and ac.velocity_ms is not None
    ab, ba = bearing_deg(*a[:2], *b[:2]), bearing_deg(*b[:2], *a[:2])
    distinct = haversine_km(*a[:2], *b[:2]) > 0

    def usable_at(seconds: float) -> bool:
        lat, lon = _destination(ac.lat, ac.lon, ac.heading_deg, ac.velocity_ms * seconds / 1000)
        return (
            elevation_deg(haversine_km(*a[:2], lat, lon), ac.altitude_m, a[2]) > 0
            and elevation_deg(haversine_km(*b[:2], lat, lon), ac.altitude_m, b[2]) > 0
            and (not distinct or (
                heading_delta_deg(bearing_deg(*a[:2], lat, lon), ab) < 90
                and heading_delta_deg(bearing_deg(*b[:2], lat, lon), ba) < 90)))

    if not usable_at(0):
        return 0.0, False
    for seconds in range(5, 1801, 5):
        if not usable_at(seconds):
            low, high = seconds - 5.0, float(seconds)
            while high - low > 0.1:
                middle = (low + high) / 2
                if usable_at(middle):
                    low = middle
                else:
                    high = middle
            return (low + high) / 2, False
    return 1800.0, True


def rank_geometry(
    ac: Aircraft, a: StationPosition, b: StationPosition, method: str,
    legacy_score: float, now_epoch_s: float,
) -> dict:
    if method not in RANKING_METHODS:
        raise ValueError("Unknown aircraft ranking method")
    result = dict(ranking_value=None, ranking_reason="Outside geometry gates",
                  range_gain_db=None, ranking_window_s=None, ranking_window_capped=False)
    if method == "legacy":
        return {**result, "ranking_value": legacy_score, "ranking_reason": "Legacy geometry score"}
    if (not ac.has_position or not all(math.isfinite(v) for v in (
            ac.lat, ac.lon, ac.altitude_m, *a, *b) if v is not None)
            or abs(ac.lat) > 90 or abs(ac.lon) > 180
            or any(abs(station[0]) > 90 or abs(station[1]) > 180 for station in (a, b))):
        return {**result, "ranking_reason": "Invalid geometry"}
    if legacy_score <= 0:
        return result
    ra = slant_range_km(haversine_km(*a[:2], ac.lat, ac.lon), ac.altitude_m, a[2])
    rb = slant_range_km(haversine_km(*b[:2], ac.lat, ac.lon), ac.altitude_m, b[2])
    if ra <= 0 or rb <= 0:
        return {**result, "ranking_reason": "Nonpositive slant range"}
    gain = 20 * (2 * math.log10(250) - (math.log10(ra) + math.log10(rb)))
    result.update(range_gain_db=gain, ranking_value=gain,
                  ranking_reason="Range-only power relative to 250/250 km; not SNR")
    if method == "range-product":
        return result
    result["ranking_value"] = None
    timestamp = ac.position_updated_epoch_s
    if timestamp is None or not math.isfinite(timestamp):
        return {**result, "ranking_reason": "Range fallback: position timestamp unavailable"}
    age = now_epoch_s - timestamp
    if not math.isfinite(age) or age < 0 or age > 45:
        return {**result, "ranking_reason": "Range fallback: stale or future-dated position"}
    if (ac.heading_deg is None or not math.isfinite(ac.heading_deg)
            or ac.velocity_ms is None or not math.isfinite(ac.velocity_ms) or ac.velocity_ms <= 0):
        return {**result, "ranking_reason": "Range fallback: track or positive speed unavailable"}
    seconds, capped = usable_seconds(ac, a, b)
    seconds = max(0.0, seconds - age)
    result.update(ranking_window_s=seconds, ranking_window_capped=capped)
    if seconds <= 0:
        return {**result, "ranking_reason": "Range fallback: predicted window ended"}
    return {**result, "ranking_value": gain + 10 * math.log10(seconds / 60),
            "ranking_reason": "Window index: current range potential times remaining geometry minutes; "
                              "constant motion, 30-minute cap; not QSO probability"}


def ranking_key(score: float, ranking: dict, method: str) -> tuple:
    if method == "legacy":
        return (score,)
    gain, value = ranking["range_gain_db"], ranking["ranking_value"]
    return (score > 0, value is not None,
            value if value is not None else (gain if gain is not None else -1e300),
            gain if gain is not None else -1e300)
