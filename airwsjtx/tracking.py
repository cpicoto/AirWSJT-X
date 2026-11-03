"""
Converts live ADS-B aircraft positions into AircraftTrack objects
that the supervisor can consume, including forward projections.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from .airscout import Aircraft
from .geo import scatter_geometry, locator_to_latlon
from .models import AircraftSample, AircraftTrack
from .ranking import apply_scatter_preferences, rank_geometry, ranking_key, scatter_eligible


# ─── Position projection ──────────────────────────────────────────────────────

def _project_latlon(
    lat: float, lon: float, heading_deg: float, speed_ms: float, dt_s: float
) -> tuple[float, float]:
    """Project a position forward by dt_s seconds (flat-earth approx, <500 km)."""
    d_km = speed_ms * dt_s / 1000.0
    heading_rad = math.radians(heading_deg)
    lat_rad = math.radians(lat)
    dlat = d_km / 111.32 * math.cos(heading_rad)
    cos_lat = math.cos(lat_rad) or 1e-9
    dlon = d_km / (111.32 * cos_lat) * math.sin(heading_rad)
    return lat + dlat, lon + dlon


def build_track(
    ac: Aircraft,
    lat_a: float,
    lon_a: float,
    n_samples: int = 6,
    sample_interval_s: float = 15.0,
    station_alt_m: float = 0.0,
) -> AircraftTrack:
    """
    Build an AircraftTrack by projecting the aircraft position forward.
    Each sample is `sample_interval_s` seconds apart.
    The az/el/range in each sample is computed from Station A.
    """
    now = datetime.now(timezone.utc)
    samples = []

    lat = ac.lat or 0.0
    lon = ac.lon or 0.0
    alt = ac.altitude_m or 0.0
    speed = ac.velocity_ms or 200.0          # default 200 m/s if unknown
    heading = ac.heading_deg or 0.0

    for i in range(n_samples):
        dt = i * sample_interval_s
        if i > 0 and ac.velocity_ms and ac.heading_deg:
            lat, lon = _project_latlon(lat, lon, heading, speed, sample_interval_s)

        # Compute geometry from Station A
        from .geo import haversine_km, elevation_deg, slant_range_km, bearing_deg
        d_surf = haversine_km(lat_a, lon_a, lat, lon)
        az = bearing_deg(lat_a, lon_a, lat, lon)
        el = elevation_deg(d_surf, alt, station_alt_m)
        sr = slant_range_km(d_surf, alt, station_alt_m)

        samples.append(AircraftSample(
            timestamp=now + timedelta(seconds=dt),
            azimuth_deg=round(az, 2),
            elevation_deg=round(el, 2),
            slant_range_km=round(sr, 2),
        ))

    return AircraftTrack(
        aircraft_id=ac.icao24,
        samples=tuple(samples),
    )


# ─── Auto-selector ────────────────────────────────────────────────────────────

def select_best_aircraft(
    aircraft: list[Aircraft],
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
    min_score: float = 0.15,
    station_a_alt_m: float = 0.0,
) -> tuple[Aircraft | None, float]:
    """
    Return (best_aircraft, score) for the aircraft with the highest scatter
    score above `min_score`, or (None, 0) if none qualifies.
    """
    best: Aircraft | None = None
    best_score = 0.0
    best_key: tuple | None = None
    now = time.time()

    for ac in aircraft:
        if not ac.has_position or not scatter_eligible(ac):
            continue
        geo = scatter_geometry(
            lat_a, lon_a, lat_b, lon_b,
            ac.lat, ac.lon, ac.altitude_m,  # type: ignore[arg-type]
            station_a_alt_m,
        )
        score = geo["scatter_score"]
        rank = apply_scatter_preferences(
            rank_geometry(ac, (lat_a, lon_a, station_a_alt_m), (lat_b, lon_b, 0),
                          "legacy", score, now), ac, now, "legacy")
        key = ranking_key(score, rank, "legacy")
        if score >= min_score and (best_key is None or key > best_key
                                  or (key == best_key and best is not None and ac.icao24 < best.icao24)):
            best_key = key
            best_score = score
            best = ac

    return best, best_score
