"""
Geodesy helpers: Maidenhead locator ↔ lat/lon, great-circle bearing,
distance, elevation and scatter-path geometry.
"""
from __future__ import annotations

import math


# ─── Maidenhead ───────────────────────────────────────────────────────────────

def locator_to_latlon(locator: str) -> tuple[float, float]:
    """Convert Maidenhead grid locator (4, 6 or 8 chars) to (lat, lon) centre."""
    loc = locator.strip().upper()
    if len(loc) < 4:
        raise ValueError(f"Locator too short: {locator!r}")
    lon = (ord(loc[0]) - ord("A")) * 20 - 180
    lat = (ord(loc[1]) - ord("A")) * 10 - 90
    lon += int(loc[2]) * 2
    lat += int(loc[3]) * 1
    if len(loc) >= 6:
        lon += (ord(loc[4]) - ord("A")) * (2.0 / 24)
        lat += (ord(loc[5]) - ord("A")) * (1.0 / 24)
        if len(loc) == 8:
            lon += (int(loc[6]) + 0.5) * (2.0 / 240)
            lat += (int(loc[7]) + 0.5) * (1.0 / 240)
        else:
            lon += 1.0 / 24        # centre of sub-square
            lat += 0.5 / 24
    else:
        lon += 1.0             # centre of 2° square
        lat += 0.5
    return lat, lon


# ─── Great-circle math ────────────────────────────────────────────────────────

_R_KM = 6371.0
K_FACTOR = 4.0 / 3.0  # standard-atmosphere effective Earth radius factor


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Surface distance in km between two (lat, lon) points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dlon / 2) ** 2
    return 2 * _R_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (degrees, 0=N) from point 1 to point 2."""
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(
        lat2r
    ) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def heading_delta_deg(source_deg: float, target_deg: float) -> float:
    """Angular difference between headings in degrees."""
    return abs((source_deg - target_deg + 180.0) % 360.0 - 180.0)


def heading_within_tolerance(path_bearing_deg: float, aircraft_heading_deg: float, tolerance_deg: float = 30.0) -> bool:
    """Return True when an aircraft heading follows either direction of the path."""
    delta = heading_delta_deg(aircraft_heading_deg, path_bearing_deg)
    return min(delta, 180.0 - delta) <= abs(tolerance_deg)


def elevation_deg(surface_km: float, altitude_m: float, station_alt_m: float = 0.0) -> float:
    """Geometric elevation angle (degrees) from a ground station to an aircraft
    on an effective-radius Earth (K = 4/3 refraction model). Negative when the
    aircraft is below the radio horizon.
    """
    if surface_km <= 0:
        return 90.0
    r_eff = _R_KM * K_FACTOR
    theta = surface_km / r_eff
    r_s = r_eff + station_alt_m / 1000.0
    r_p = r_eff + altitude_m / 1000.0
    return math.degrees(math.atan2(r_p * math.cos(theta) - r_s, r_p * math.sin(theta)))


def slant_range_km(surface_km: float, altitude_m: float, station_alt_m: float = 0.0) -> float:
    """Straight-line distance (km) from a ground station to an aircraft on a spherical Earth."""
    theta = surface_km / _R_KM
    r_s = _R_KM + station_alt_m / 1000.0
    r_p = _R_KM + altitude_m / 1000.0
    return math.sqrt(max(0.0, r_s**2 + r_p**2 - 2 * r_s * r_p * math.cos(theta)))


# ─── Scatter geometry ─────────────────────────────────────────────────────────

def scatter_geometry(
    lat_a: float, lon_a: float,
    lat_b: float, lon_b: float,
    lat_p: float, lon_p: float,
    altitude_m: float,
    station_a_alt_m: float = 0.0,
    station_b_alt_m: float = 0.0,
) -> dict:
    """
    Compute full scatter geometry for an aircraft P between stations A and B.

    Returns dict with:
      az_a, el_a, range_a_km  — bearing/elevation/slant from A to P
      az_b, el_b, range_b_km  — bearing/elevation/slant from B to P
      dist_ab_km              — A↔B great-circle distance
      off_path_deg            — how far P is off the A-B midpoint bearing
      scatter_score           — 0-1 suitability score
    """
    # Distances
    d_a = haversine_km(lat_a, lon_a, lat_p, lon_p)
    d_b = haversine_km(lat_b, lon_b, lat_p, lon_p)
    d_ab = haversine_km(lat_a, lon_a, lat_b, lon_b)

    # Azimuths
    az_a = bearing_deg(lat_a, lon_a, lat_p, lon_p)
    az_b = bearing_deg(lat_b, lon_b, lat_p, lon_p)
    az_ab = bearing_deg(lat_a, lon_a, lat_b, lon_b)

    # Elevations
    el_a = elevation_deg(d_a, altitude_m, station_a_alt_m)
    el_b = elevation_deg(d_b, altitude_m, station_b_alt_m)

    # Slant ranges
    sr_a = slant_range_km(d_a, altitude_m, station_a_alt_m)
    sr_b = slant_range_km(d_b, altitude_m, station_b_alt_m)

    # Off-path: angle between A→P bearing and A→B bearing
    off_path = abs((az_a - az_ab + 180) % 360 - 180)
    off_path_b = heading_delta_deg(az_b, bearing_deg(lat_b, lon_b, lat_a, lon_a))

    # Geometry score (0-1):
    # Require mutual visibility and an inward bearing from both path endpoints.
    if el_a <= 0 or el_b <= 0 or (d_ab > 0 and (off_path >= 90.0 or off_path_b >= 90.0)):
        scatter_score = 0.0
    else:
        # Elevation sweet-spot: 1-20° from each station
        def el_score(el: float) -> float:
            if el <= 0:
                return 0.0
            if el <= 20:
                return el / 20.0
            return max(0.0, 1.0 - (el - 20) / 20.0)

        # Path balance: aircraft should be roughly on the great-circle path
        path_score = max(0.0, 1.0 - off_path / 45.0)

        # Range penalty: prefer <500 km from each station
        range_score = max(0.0, 1.0 - max(sr_a, sr_b) / 800.0)

        scatter_score = (
            el_score(el_a) * 0.30
            + el_score(el_b) * 0.30
            + path_score * 0.25
            + range_score * 0.15
        )

    return {
        "az_a": round(az_a, 1),
        "el_a": round(el_a, 1),
        "range_a_km": round(sr_a, 1),
        "az_b": round(az_b, 1),
        "el_b": round(el_b, 1),
        "range_b_km": round(sr_b, 1),
        "dist_ab_km": round(d_ab, 1),
        "off_path_deg": round(off_path, 1),
        "scatter_score": round(scatter_score, 3),
    }
