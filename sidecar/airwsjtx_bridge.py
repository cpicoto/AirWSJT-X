#!/usr/bin/env python3
"""
JSON-RPC bridge between Tauri and the airwsjtx supervisor.
Reads newline-delimited JSON from stdin, writes responses to stdout.
Also runs background WSJT-X UDP listener and AirScout aircraft feeds.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from airwsjtx.airscout import ADSBExchangeFeed, Aircraft, Dump1090Feed, OpenSkyFeed, SourceRateLimit
from airwsjtx.callsign import (
    DEFAULT_DB_MANIFEST_URL,
    check_db_update as callsign_check_db_update,
    download_db as callsign_download_db,
    get_db_status as callsign_get_db_status,
    lookup as lookup_callsign,
    submit_grid_contribution as callsign_submit_grid_contribution,
)
from airwsjtx.geo import bearing_deg, haversine_km, heading_delta_deg, heading_within_tolerance, locator_to_latlon, scatter_geometry
from airwsjtx.hamlib_rotator import HamlibRotator, RotatorPosition
from airwsjtx.models import AircraftSample, AircraftTrack, Station
from airwsjtx.supervisor import AirWSJTXSupervisor
from airwsjtx.tracking import build_track, select_best_aircraft
from airwsjtx.wsjtx_udp import WSJTXDecode, WSJTXListener, WSJTXQsoLogged, WSJTXStatus

supervisor = AirWSJTXSupervisor()
_stdout_lock = threading.Lock()

# AirScout state
_aircraft_lock = threading.Lock()
_aircraft_cache: list[Aircraft] = []
_opensky_feed: OpenSkyFeed | None = None
_adsbexchange_feed: ADSBExchangeFeed | None = None
_opensky_client_id = ""
_opensky_client_secret = ""
_adsbexchange_api_key = ""
_source_poll_intervals = {"opensky": 30.0, "adsbexchange": 30.0}
_source_rate_limits = {"opensky": SourceRateLimit(), "adsbexchange": SourceRateLimit(opensky=False)}
_dump1090_feed: Dump1090Feed | None = None
_dump1090_port: int = 30003
_supported_sources = ("opensky", "dump1090", "adsbexchange")
# dump1090 is OFF by default; the user must explicitly enable it via the UI.
# The priority is persisted in source_priority.json so opt-in choices survive restarts.
_DEFAULT_SOURCE_PRIORITY: list[str] = ["opensky"]
_source_aliases = {
    "rtl-sdr": "dump1090",
    "rtl_sdr": "dump1090",
    "dump1090": "dump1090",
    "opensky": "opensky",
    "adsbexchange": "adsbexchange",
    "adsb-exchange": "adsbexchange",
}
_source_priority_lock = threading.Lock()
_source_status_lock = threading.Lock()
_source_status: dict[str, dict[str, str | None]] = {
    "opensky": {"state": "stopped", "message": None},
    "dump1090": {"state": "stopped", "message": None},
    "adsbexchange": {"state": "stopped", "message": None},
}
_active_source: str | None = None
_opensky_rate_limit_count = 0
_aircraft_cache_stale_after_s = 45.0
_aircraft_cache_expire_after_s = 120.0
_aircraft_last_update_monotonic: float | None = None
_aircraft_last_update_epoch_s: int | None = None

# Station locators (updated from WSJT-X or RPC)
_station_a_locator: str | None = None
_station_b_locator: str | None = None
_station_a_call: str | None = None
_my_call_override: str | None = None
_my_grid_override: str | None = None
_station_a_alt_m: float = 0.0  # user-entered altitude of MY station above sea level
_station_b_alt_m: float = 0.0  # user-entered altitude of DX station above sea level
_wsjtx_de_call: str | None = None
_wsjtx_de_grid: str | None = None
_station_b_call: str | None = None
_dx_grid_manual: bool = False
_manual_pointing: bool = False
_rotator_control_enabled: bool = True

# Aircraft display filters
_DEFAULT_FILTERS = {
    "lat_min": None,
    "lat_max": None,
    "lon_min": None,
    "lon_max": None,
    "altitude_min_ft": None,
    "heading_tolerance_deg": 30.0,
    "position_tolerance_deg": 70.0,
}


def _aircraft_filter_path() -> str:
    user_dir = os.path.join(tempfile.gettempdir(), "airwsjtx")
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "aircraft_filters.json")


def _source_priority_path() -> str:
    user_dir = os.path.join(tempfile.gettempdir(), "airwsjtx")
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "source_priority.json")


def _read_source_priority() -> list[str]:
    """Load the persisted source priority list. Returns the default (opensky only)
    if no file exists or the file is invalid. dump1090 is off by default."""
    try:
        with open(_source_priority_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            normalized = []
            for src in data:
                if isinstance(src, str) and src in _supported_sources and src not in normalized:
                    normalized.append(src)
            if normalized:
                return normalized
    except Exception:
        pass
    return list(_DEFAULT_SOURCE_PRIORITY)


def _write_source_priority(priority: list[str]) -> None:
    try:
        with open(_source_priority_path(), "w", encoding="utf-8") as fh:
            json.dump(priority, fh)
    except Exception:
        pass


def _read_filters() -> dict[str, float | None]:
    try:
        with open(_aircraft_filter_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            result = dict(_DEFAULT_FILTERS)
            for key, _default in _DEFAULT_FILTERS.items():
                if key in data:
                    val = data[key]
                    if val is None or val == "":
                        result[key] = None
                    else:
                        result[key] = float(val)
            return result
    except Exception:
        pass
    return dict(_DEFAULT_FILTERS)


def _write_filters(filters: dict[str, float | None]) -> None:
    try:
        payload = {k: (None if v is None else float(v)) for k, v in filters.items()}
        with open(_aircraft_filter_path(), "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
    except Exception:
        pass


_DEFAULT_DUMP1090_HOST = "127.0.0.1"
_DUMP1090_HOST_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,253}$")


def _dump1090_host_path() -> str:
    user_dir = os.path.join(tempfile.gettempdir(), "airwsjtx")
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "dump1090_host.json")


def _normalize_dump1090_host(value: object) -> str:
    """Return a validated dump1090 host; an empty value resets to localhost."""
    host = str(value or "").strip()
    if not host:
        return _DEFAULT_DUMP1090_HOST
    if not _DUMP1090_HOST_RE.fullmatch(host):
        raise ValueError("Enter a valid IP address or hostname for dump1090.")
    return host


def _read_dump1090_host() -> str:
    try:
        with open(_dump1090_host_path(), "r", encoding="utf-8") as fh:
            return _normalize_dump1090_host(json.load(fh).get("host"))
    except Exception:
        return _DEFAULT_DUMP1090_HOST


def _write_dump1090_host(host: str) -> None:
    try:
        with open(_dump1090_host_path(), "w", encoding="utf-8") as fh:
            json.dump({"host": host}, fh)
    except Exception:
        pass


_aircraft_filters = _read_filters()
_source_priority: list[str] = _read_source_priority()
_dump1090_host = _read_dump1090_host()

# Hamlib rotator
_rotator: HamlibRotator | None = None

# Auto-lock state
_auto_lock_enabled: bool = True
_locked_aircraft_id: str | None = None
_lock_score: float = 0.0
_wsjtx_freq_hz: int | None = None
_wsjtx_band_mhz: float | None = None
_wsjtx_listener: WSJTXListener | None = None


def _set_source_status(source: str, state: str, message: str | None = None) -> None:
    if source not in _source_status:
        return
    with _source_status_lock:
        _source_status[source] = {"state": state, "message": message}
    _emit_source_status()


def _emit_source_status() -> None:
    with _source_priority_lock:
        priority = list(_source_priority)
    with _source_status_lock:
        active_source = _active_source
        sources = {k: dict(v) for k, v in _source_status.items()}
    _emit({
        "event": "airscout_source_status",
        "data": {
            "active_source": active_source,
            "priority": priority,
            "sources": sources,
            "poll_intervals_s": {**_source_poll_intervals, "dump1090": 5.0},
        },
    })


def _normalize_sources(raw_sources: object) -> list[str]:
    if not isinstance(raw_sources, list):
        with _source_priority_lock:
            return list(_source_priority)
    normalized = []
    for src in raw_sources:
        if not isinstance(src, str):
            continue
        src_norm = src.strip().lower()
        canonical = _source_aliases.get(src_norm, src_norm)
        if canonical in _supported_sources and canonical not in normalized:
            normalized.append(canonical)
    if normalized:
        return normalized
    with _source_priority_lock:
        return list(_source_priority)


def _replace_aircraft_cache(aircraft: list[Aircraft]) -> None:
    global _aircraft_last_update_monotonic, _aircraft_last_update_epoch_s
    now_monotonic = time.monotonic()
    now_epoch = int(time.time())
    with _aircraft_lock:
        _aircraft_cache.clear()
        _aircraft_cache.extend(aircraft)
        _aircraft_last_update_monotonic = now_monotonic
        _aircraft_last_update_epoch_s = now_epoch


def _aircraft_cache_age_s(now: float | None = None) -> float | None:
    with _aircraft_lock:
        last_update_monotonic = _aircraft_last_update_monotonic
    if last_update_monotonic is None:
        return None
    now_monotonic = time.monotonic() if now is None else now
    return max(0.0, now_monotonic - last_update_monotonic)


def _aircraft_cache_freshness(age_s: float | None) -> str:
    if age_s is None:
        return "empty"
    if age_s <= _aircraft_cache_stale_after_s:
        return "fresh"
    if age_s <= _aircraft_cache_expire_after_s:
        return "stale"
    return "expired"


def _emit(msg: dict) -> None:
    """Thread-safe write to stdout."""
    with _stdout_lock:
        print(json.dumps(msg), flush=True)


def _ok(id_: str | None, result: object) -> None:
    _emit({"id": id_, "ok": True, "result": result})


def _err(id_: str | None, message: str) -> None:
    _emit({"id": id_, "ok": False, "error": message})


def _parse_tracks(raw: list[dict]) -> list[AircraftTrack]:
    tracks = []
    for t in raw:
        samples = tuple(
            AircraftSample(
                timestamp=datetime.now(timezone.utc),
                azimuth_deg=s["azimuth_deg"],
                elevation_deg=s["elevation_deg"],
                slant_range_km=s["slant_range_km"],
            )
            for s in t["samples"]
        )
        tracks.append(AircraftTrack(aircraft_id=t["aircraft_id"], samples=samples))
    return tracks


def handle(msg: dict) -> None:
    method = msg.get("method", "")
    params = msg.get("params", {})
    id_ = msg.get("id")

    try:
        if method == "source_credentials_set":
            global _opensky_client_id, _opensky_client_secret, _adsbexchange_api_key
            provider = params.get("provider")
            client_id = params.get("client_id", "")
            secret = params.get("secret", "")
            if not isinstance(client_id, str) or not isinstance(secret, str):
                raise ValueError("Aircraft credentials must be text values")
            client_id, secret = client_id.strip(), secret.strip()
            if provider == "opensky" and bool(client_id) != bool(secret):
                raise ValueError("OpenSky requires both Client ID and Client Secret")
            seconds = params.get("poll_interval_s", _source_poll_intervals.get(provider, 30))
            _validate_poll_interval(provider, seconds)
            if provider == "opensky":
                _opensky_client_id = client_id
                _opensky_client_secret = secret
            elif provider == "adsbexchange":
                _adsbexchange_api_key = secret
            else:
                raise ValueError("Unsupported aircraft credential provider")
            _source_poll_intervals[provider] = float(seconds)
            _source_rate_limits[provider] = SourceRateLimit(opensky=provider == "opensky")
            _emit_source_status()
            _emit({"event": "source_rate_limit_update", "data": {
                "provider": provider, **_source_rate_limits[provider].snapshot(),
            }})
            if _active_source == provider:
                _start_configured_airscout_feed(force_restart=True)
            _ok(id_, {})

        elif method == "source_poll_interval_set":
            provider = params.get("provider")
            seconds = params.get("seconds")
            _validate_poll_interval(provider, seconds)
            _source_poll_intervals[provider] = float(seconds)
            if _active_source == provider:
                feed = _opensky_feed if provider == "opensky" else _adsbexchange_feed
                if feed:
                    feed.set_poll_interval(float(seconds))
            _emit_source_status()
            _ok(id_, {})

        elif method == "begin_session":
            station_a = Station(**params["station_a"])
            station_b = Station(**params["station_b"])
            tracks = _parse_tracks(params["tracks"])
            hint = supervisor.begin_session(
                station_a=station_a,
                station_b=station_b,
                band_mhz=params["band_mhz"],
                frequency_hz=params["frequency_hz"],
                tracks=tracks,
            )
            _ok(id_, {"hint": hint, "state": supervisor.session.state.value})

        elif method == "update_tracking":
            cmd = supervisor.update_tracking(
                current_azimuth_deg=params["current_azimuth_deg"],
                current_elevation_deg=params["current_elevation_deg"],
            )
            _ok(id_, {
                "state": supervisor.session.state.value,
                "target_azimuth_deg": cmd.target_azimuth_deg,
                "target_elevation_deg": cmd.target_elevation_deg,
            })

        elif method == "start_qso":
            from airwsjtx.state_machine import SessionState
            # After an abort or completed QSO the session is terminal; re-arm it for the still-locked plane
            if supervisor.session.state in (SessionState.ABORTED, SessionState.COMPLETED) and _locked_aircraft_id:
                _do_lock(_locked_aircraft_id)
            supervisor.operator_start_qso()
            _ok(id_, {"state": supervisor.session.state.value})

        elif method == "finish_qso":
            wsjtx_tx = None
            if params.get("control_wsjtx") and _wsjtx_listener:
                wsjtx_tx = _wsjtx_listener.send_halt_tx()
            try:
                supervisor.finish_qso(abort=params.get("abort", False))
            except ValueError:
                # Halt Tx must still go out when the app session is already closed but WSJT-X is transmitting
                if not wsjtx_tx:
                    raise
            _ok(id_, {"state": supervisor.session.state.value, "wsjtx_tx_sent": wsjtx_tx})

        elif method == "get_status":
            s = supervisor.session
            _ok(id_, {
                "state": s.state.value,
                "wsjtx_known_rigs": _wsjtx_listener.get_known_rigs() if _wsjtx_listener else [],
                "station_a": {"callsign": s.station_a.callsign, "locator": s.station_a.locator} if s.station_a else None,
                "station_b": {"callsign": s.station_b.callsign, "locator": s.station_b.locator} if s.station_b else None,
                "selected_aircraft_id": s.selected_aircraft_id,
                "candidate_count": s.candidate_count,
                "wsjtx": {
                    "configured": supervisor.wsjtx.status.configured,
                    "mode": supervisor.wsjtx.status.mode,
                    "band_mhz": supervisor.wsjtx.status.band_mhz,
                    "ptt_enabled": supervisor.wsjtx.status.ptt_enabled,
                },
            })

        elif method == "rank_candidates":
            tracks = _parse_tracks(params["tracks"])
            ranked = supervisor.tracker.rank_candidates(tracks)
            _ok(id_, [
                {
                    "aircraft_id": c.aircraft_id,
                    "geometry_score": round(c.geometry_score, 3),
                    "stability_score": round(c.stability_score, 3),
                    "total_score": round(c.total_score, 3),
                }
                for c in ranked
            ])

        # ── AirScout methods ──────────────────────────────────────────────────

        elif method == "airscout_set_stations":
            global _station_a_locator, _station_b_locator, _dx_grid_manual
            _station_a_locator = params.get("locator_a")
            _station_b_locator = params.get("locator_b")
            # A DX chosen in the app must not be clobbered by WSJT-X's DX grid.
            _dx_grid_manual = bool(_station_b_locator)
            _start_configured_airscout_feed(force_restart=True)
            _ok(id_, {"locator_a": _station_a_locator, "locator_b": _station_b_locator})

        elif method == "airscout_get_dump1090_host":
            global _dump1090_host, _dump1090_port
            _ok(id_, {"host": _dump1090_host, "port": _dump1090_port})

        elif method == "airscout_set_dump1090_host":
            host = _normalize_dump1090_host(params.get("host"))
            _dump1090_host = host
            _write_dump1090_host(host)
            # Reconnect immediately only if the dump1090 feed is running.
            if _dump1090_feed is not None:
                _start_dump1090_feed(host, _dump1090_port)
            _ok(id_, {"host": host, "port": _dump1090_port})

        elif method == "airscout_start_dump1090":
            host = params.get("host", _dump1090_host)
            port = int(params.get("port", 30003))
            _dump1090_host = host
            _dump1090_port = port
            _start_dump1090_feed(host, port)
            _ok(id_, {"host": host, "port": port})

        elif method == "airscout_set_sources":
            global _source_priority
            next_priority = _normalize_sources(params.get("sources"))
            with _source_priority_lock:
                _source_priority = next_priority
            _write_source_priority(next_priority)
            _start_configured_airscout_feed(force_restart=True)
            with _source_priority_lock:
                priority = list(_source_priority)
            with _source_status_lock:
                active_source = _active_source
                sources = {k: dict(v) for k, v in _source_status.items()}
            _ok(id_, {
                "active_source": active_source,
                "priority": priority,
                "sources": sources,
                "poll_intervals_s": {**_source_poll_intervals, "dump1090": 5.0},
            })

        elif method == "airscout_get_sources":
            with _source_priority_lock:
                priority = list(_source_priority)
            with _source_status_lock:
                active_source = _active_source
                sources = {k: dict(v) for k, v in _source_status.items()}
            _ok(id_, {
                "active_source": active_source,
                "priority": priority,
                "sources": sources,
                "poll_intervals_s": {**_source_poll_intervals, "dump1090": 5.0},
            })

        elif method == "airscout_apply_filters":
            raw_filters = params.get("filters")
            if not isinstance(raw_filters, dict):
                _err(id_, "filters object required")
                return
            next_filters = dict(_DEFAULT_FILTERS)
            for key, _default in _DEFAULT_FILTERS.items():
                if key in raw_filters:
                    value = raw_filters[key]
                    if value is None or value == "":
                        next_filters[key] = None
                    else:
                        next_filters[key] = float(value)
            global _aircraft_filters
            _aircraft_filters = next_filters
            _write_filters(_aircraft_filters)
            _emit({"event": "airscout_filters_updated", "data": {"filters": dict(next_filters)}})
            _ok(id_, {"filters": dict(next_filters)})

        elif method == "airscout_get_aircraft":
            result, total = _get_scatter_aircraft_with_total()
            _ok(id_, {"aircraft": result, "total": total})

        elif method == "airscout_fetch_now":
            try:
                feed = _adsbexchange_feed if _active_source == "adsbexchange" else _opensky_feed
                if _active_source in ("opensky", "adsbexchange") and feed:
                    aircraft = feed.fetch_once()
                    _replace_aircraft_cache(aircraft)
                    _emit_aircraft_update()
                    _ok(id_, {"count": len(aircraft)})
                elif _active_source == "dump1090":
                    with _aircraft_lock:
                        count = len(_aircraft_cache)
                    _emit_aircraft_update()
                    _ok(id_, {"count": count})
                else:
                    _err(id_, "No active aircraft source — call airscout_set_stations first")
            except Exception as e:
                _err(id_, str(e))

        # ── Rotator methods ───────────────────────────────────────────────────

        elif method == "rotator_connect":
            host = params.get("host", "127.0.0.1")
            port = int(params.get("port", 4533))
            _start_rotator(host, port)
            _ok(id_, {"host": host, "port": port})

        elif method == "set_pointing_mode":
            global _manual_pointing
            _manual_pointing = params.get("mode") == "manual"
            _ok(id_, {"mode": "manual" if _manual_pointing else "rotator"})

        elif method == "set_rotator_control":
            global _rotator_control_enabled
            _rotator_control_enabled = bool(params.get("enabled", True))
            _ok(id_, {"enabled": _rotator_control_enabled})

        elif method == "rotator_set":
            if not _rotator_control_enabled:
                _err(id_, "Rotator control is disabled")
            elif not _rotator or not _rotator.connected:
                _err(id_, "Rotator not connected")
            else:
                az = float(params["azimuth"])
                el = float(params.get("elevation", 0.0))
                _rotator.set_position(az, el)
                _ok(id_, {"azimuth": az, "elevation": el})

        elif method == "rotator_stop":
            if _rotator:
                _rotator.stop_rotation()
            _ok(id_, {})

        elif method == "rotator_park":
            if not _rotator_control_enabled:
                _err(id_, "Rotator control is disabled")
            else:
                if _rotator:
                    _rotator.park()
                _ok(id_, {})

        elif method == "rotator_status":
            pos = _rotator.current_position if _rotator else None
            _ok(id_, {
                "connected": bool(_rotator and _rotator.connected),
                "azimuth": pos.azimuth if pos else None,
                "elevation": pos.elevation if pos else None,
            })

        elif method == "callsign_lookup":
            call = params.get("callsign", "").strip()
            if not call:
                _err(id_, "callsign required")
            else:
                info = lookup_callsign(call)
                _ok(id_, {
                    "callsign": info.callsign,
                    "name": info.name,
                    "grid": info.grid,
                    "lat": info.lat,
                    "lon": info.lon,
                    "country": info.country,
                    "source": info.source,
                })

        elif method == "callsign_db_status":
            _ok(id_, callsign_get_db_status())

        elif method == "callsign_db_check_update":
            db_url = str(params.get("url") or "")
            manifest_url = str(params.get("manifest_url") or "")
            timeout_s = int(params.get("timeout_s", 8))
            allow_full_db_fallback = bool(params.get("allow_full_db_fallback", False))
            if db_url:
                _ok(
                    id_,
                    callsign_check_db_update(
                        db_url,
                        timeout_s=timeout_s,
                        manifest_url=manifest_url or DEFAULT_DB_MANIFEST_URL,
                        allow_full_db_fallback=allow_full_db_fallback,
                    ),
                )
            else:
                _ok(
                    id_,
                    callsign_check_db_update(
                        timeout_s=timeout_s,
                        manifest_url=manifest_url or DEFAULT_DB_MANIFEST_URL,
                        allow_full_db_fallback=allow_full_db_fallback,
                    ),
                )

        elif method == "callsign_db_download":
            db_url = str(params.get("url") or "")
            timeout_s = int(params.get("timeout_s", 10))
            if db_url:
                _ok(id_, callsign_download_db(db_url, timeout_s=timeout_s))
            else:
                _ok(id_, callsign_download_db(timeout_s=timeout_s))

        elif method == "callsign_db_submit":
            call = str(params.get("callsign", "")).strip()
            grid = str(params.get("grid", "")).strip()
            if not call or not grid:
                _err(id_, "callsign and grid required")
            else:
                lat = params.get("lat")
                lon = params.get("lon")
                lat_val = float(lat) if lat not in (None, "") else None
                lon_val = float(lon) if lon not in (None, "") else None
                result = callsign_submit_grid_contribution(
                    call,
                    grid,
                    name=params.get("name"),
                    country=params.get("country"),
                    lat=lat_val,
                    lon=lon_val,
                )
                _ok(id_, result)

        elif method == "set_my_station_override":
            global _my_call_override, _my_grid_override
            global _station_a_call, _station_b_call, _station_a_alt_m
            call = (params.get("callsign") or "").strip().upper() or None
            grid = (params.get("grid") or "").strip().upper() or None
            if grid:
                import re
                if not re.fullmatch(r"[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?", grid):
                    raise ValueError("MY grid must be a valid 4, 6, or 8 character Maidenhead locator")
            if call and (not any(c.isdigit() for c in call) or not any(c.isalpha() for c in call)
                         or not all(c.isascii() and (c.isalnum() or c == "/") for c in call)):
                raise ValueError("MY callsign must contain letters and digits, with optional /")
            alt_raw = params.get("altitude_m")
            alt_m = 0.0 if alt_raw in (None, "") else float(alt_raw)
            if not -500.0 <= alt_m <= 9000.0:
                raise ValueError("MY altitude must be between -500 and 9000 metres")
            _station_a_alt_m = alt_m
            _my_call_override, _my_grid_override = call, grid
            _station_a_call = call or _wsjtx_de_call
            _station_a_locator = grid or _wsjtx_de_grid
            _start_configured_airscout_feed(force_restart=True)
            _ok(id_, {"callsign": call, "grid": grid, "altitude_m": alt_m})

        elif method == "set_dx_altitude":
            global _station_b_alt_m
            alt_raw = params.get("altitude_m")
            alt_m = 0.0 if alt_raw in (None, "") else float(alt_raw)
            if not -500.0 <= alt_m <= 9000.0:
                raise ValueError("DX altitude must be between -500 and 9000 metres")
            _station_b_alt_m = alt_m
            _ok(id_, {"altitude_m": alt_m})

        elif method == "set_station_calls":
            _station_a_call = params.get("call_a")
            _station_b_call = params.get("call_b")
            _ok(id_, {})

        elif method == "wsjtx_set_rig_name_filter":
            if _wsjtx_listener is None:
                _err(id_, "WSJT-X listener not ready")
            else:
                rig_name = params.get("rig_name")
                if rig_name is not None:
                    rig_name = str(rig_name)
                configured_rig_name = _wsjtx_listener.set_rig_name_filter(rig_name)
                _ok(id_, {"rig_name": configured_rig_name})

        elif method == "wsjtx_get_rig_name_filter":
            if _wsjtx_listener is None:
                _err(id_, "WSJT-X listener not ready")
            else:
                _ok(id_, {"rig_name": _wsjtx_listener.get_rig_name_filter()})

        elif method == "auto_lock_set":
            global _auto_lock_enabled
            _auto_lock_enabled = bool(params.get("enabled", True))
            _ok(id_, {"enabled": _auto_lock_enabled})

        elif method == "lock_aircraft":
            icao = params.get("icao24", "").strip()
            result = _do_lock(icao)
            if result:
                _ok(id_, result)
            else:
                _err(id_, f"Cannot lock {icao}: aircraft not found or no station data")

        elif method == "unlock_aircraft":
            _do_unlock()
            _ok(id_, {"state": supervisor.session.state.value})

        else:
            _err(id_, f"Unknown method: {method}")

    except Exception as exc:
        if method == "source_credentials_set":
            _emit({"event": "source_credentials_error", "data": {
                "message": "Unable to apply saved aircraft credentials to the active feed",
            }})
            _err(id_, "Unable to apply aircraft credentials")
        else:
            _err(id_, str(exc))


# ─── AirScout feed helpers ────────────────────────────────────────────────────

def _validate_poll_interval(provider: str, seconds: object) -> None:
    if provider not in _source_poll_intervals:
        raise ValueError("Unsupported aircraft poll provider")
    minimum = 10 if provider == "opensky" else 5
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < minimum:
        raise ValueError(f"{provider} poll interval must be at least {minimum} seconds")

def _stop_source(source: str) -> None:
    global _opensky_feed, _dump1090_feed, _adsbexchange_feed
    if source == "opensky":
        if _opensky_feed:
            _opensky_feed.stop()
            _opensky_feed = None
        _set_source_status("opensky", "stopped")
    elif source == "dump1090":
        if _dump1090_feed:
            _dump1090_feed.stop()
            _dump1090_feed = None
        _set_source_status("dump1090", "stopped")
    elif source == "adsbexchange":
        if _adsbexchange_feed:
            _adsbexchange_feed.stop()
            _adsbexchange_feed = None
        _set_source_status("adsbexchange", "stopped")


def _start_source(source: str) -> bool:
    global _active_source
    if source == "opensky":
        _start_opensky_feed()
        return _opensky_feed is not None
    if source == "dump1090":
        _start_dump1090_feed(_dump1090_host, _dump1090_port)
        with _source_status_lock:
            _active_source = "dump1090"
        return True
    if source == "adsbexchange":
        _start_adsbexchange_feed()
        return _adsbexchange_feed is not None
    return False


def _start_configured_airscout_feed(force_restart: bool = False) -> None:
    global _active_source
    with _source_status_lock:
        active_source = _active_source
    if active_source and not force_restart:
        return
    if force_restart and active_source:
        _stop_source(active_source)
        with _source_status_lock:
            _active_source = None
    with _source_priority_lock:
        priority = list(_source_priority)
    for source in priority:
        if _start_source(source):
            with _source_status_lock:
                _active_source = source
            _emit_source_status()
            return
    with _source_status_lock:
        _active_source = None
    _emit_source_status()


def _next_fallback_source(failed_source: str) -> str | None:
    with _source_priority_lock:
        priority = list(_source_priority)
    if failed_source not in priority:
        return None
    if len(priority) <= 1:
        return None
    idx = priority.index(failed_source)
    for offset in range(1, len(priority)):
        source = priority[(idx + offset) % len(priority)]
        if source != "adsbexchange" or _adsbexchange_api_key or os.environ.get("ADSBEXCHANGE_API_KEY", "").strip():
            return source
    return None


def _try_fallback(failed_source: str, reason: str) -> None:
    global _active_source
    with _source_status_lock:
        active_source = _active_source
    if failed_source != active_source:
        return
    fallback = _next_fallback_source(failed_source)
    if not fallback:
        _set_source_status(failed_source, "error", reason)
        return
    _stop_source(failed_source)
    _emit({"event": "airscout_source_switched", "data": {
        "from": failed_source,
        "to": fallback,
        "reason": reason,
    }})
    with _source_status_lock:
        _active_source = None
    if not _start_source(fallback):
        _set_source_status(fallback, "error", "Failed to start fallback source")
        return
    with _source_status_lock:
        _active_source = fallback
    _emit_source_status()


def _bbox_for_locators() -> tuple[float, float, float, float] | None:
    """Compute a bounding box covering both stations + 400 km margin."""
    if not _station_a_locator:
        return None
    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lats = [lat_a]
        lons = [lon_a]
        if _station_b_locator:
            lat_b, lon_b = locator_to_latlon(_station_b_locator)
            lats.append(lat_b)
            lons.append(lon_b)
        # ~4° ≈ 400 km margin
        margin = 4.0
        return (min(lats) - margin, min(lons) - margin,
                max(lats) + margin, max(lons) + margin)
    except Exception:
        return None


def _start_adsbexchange_feed() -> None:
    global _adsbexchange_feed, _active_source
    if _adsbexchange_feed:
        _adsbexchange_feed.stop()
        _adsbexchange_feed = None
    key = _adsbexchange_api_key or os.environ.get("ADSBEXCHANGE_API_KEY", "").strip()
    if not key:
        _set_source_status("adsbexchange", "error", "Save an API key in Aircraft accounts or set ADSBEXCHANGE_API_KEY")
        return
    if not _station_a_locator:
        _set_source_status("adsbexchange", "error", "Set MY grid before enabling ADS-B Exchange")
        return
    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator or _station_a_locator)
    except ValueError as e:
        _set_source_status("adsbexchange", "error", f"Invalid station locator: {e}")
        return
    # A circle centered on MY covers the full station path plus the existing 400 km margin.
    radius_nm = (haversine_km(lat_a, lon_a, lat_b, lon_b) + 400) / 1.852
    def on_update(aircraft: list[Aircraft]) -> None:
        if _active_source != "adsbexchange" or _adsbexchange_feed is not feed:
            return
        _set_source_status("adsbexchange", "connected")
        _replace_aircraft_cache(aircraft)
        _emit_aircraft_update()

    def on_error(message: str) -> None:
        if _active_source != "adsbexchange" or _adsbexchange_feed is not feed:
            return
        _set_source_status("adsbexchange", "error", message)
        _emit({"event": "airscout_error", "data": {"source": "adsbexchange", "message": message}})
        _try_fallback("adsbexchange", message)

    def on_rate_limited(wait_s: float) -> None:
        if _active_source != "adsbexchange" or _adsbexchange_feed is not feed:
            return
        message = f"ADS-B Exchange rate limited; retrying in {int(wait_s)}s"
        _set_source_status("adsbexchange", "backoff", message)
        _emit({"event": "airscout_error", "data": {"source": "adsbexchange", "message": message}})

    feed = ADSBExchangeFeed(
        key, lat_a, lon_a, radius_nm, on_update, on_error, on_rate_limited,
        interval_s=_source_poll_intervals["adsbexchange"],
        rate_limit=_source_rate_limits["adsbexchange"],
        on_quota=lambda value: _emit({"event": "source_rate_limit_update", "data": {
            "provider": "adsbexchange", **value,
        }}) if _adsbexchange_feed is feed else None,
    )
    _adsbexchange_feed = feed
    with _source_status_lock:
        _active_source = "adsbexchange"
    _set_source_status("adsbexchange", "connecting")
    _adsbexchange_feed.start()
    _emit({"event": "airscout_feed_started", "data": {"source": "adsbexchange"}})


def _start_opensky_feed() -> None:
    global _opensky_feed, _active_source, _opensky_rate_limit_count
    bbox = _bbox_for_locators()
    if not bbox:
        _set_source_status("opensky", "error", "Missing/invalid station locator")
        return
    if _opensky_feed:
        _opensky_feed.stop()
        _opensky_feed = None

    def _on_update(aircraft: list[Aircraft]) -> None:
        global _opensky_rate_limit_count
        if _active_source != "opensky" or _opensky_feed is not feed:
            return
        with _source_status_lock:
            _opensky_rate_limit_count = 0
        _set_source_status("opensky", "connected")
        _replace_aircraft_cache(aircraft)
        _emit_aircraft_update()

    def _on_rate_limited(wait_s: float) -> None:
        global _opensky_rate_limit_count
        if _active_source != "opensky" or _opensky_feed is not feed:
            return
        with _source_status_lock:
            _opensky_rate_limit_count += 1
            rate_limit_count = _opensky_rate_limit_count
        _set_source_status("opensky", "backoff", f"Rate limited; retrying in {int(wait_s)}s")
        _emit({"event": "airscout_error", "data": {"source": "opensky", "message": f"HTTP Error 429: OpenSky rate limit; retrying in {int(wait_s)}s"}})
        if rate_limit_count >= 2:
            _try_fallback("opensky", "OpenSky rate limited repeatedly (HTTP 429)")

    def _on_error(msg: str) -> None:
        if _active_source != "opensky" or _opensky_feed is not feed:
            return
        _set_source_status("opensky", "error", msg)
        _emit({"event": "airscout_error", "data": {"source": "opensky", "message": msg}})
        _try_fallback("opensky", msg)

    lat_min, lon_min, lat_max, lon_max = bbox
    with _source_status_lock:
        _opensky_rate_limit_count = 0
        _active_source = "opensky"
    _set_source_status("opensky", "connecting")
    feed = OpenSkyFeed(
        lat_min=lat_min, lon_min=lon_min,
        lat_max=lat_max, lon_max=lon_max,
        interval_s=_source_poll_intervals["opensky"],
        on_update=_on_update,
        on_error=_on_error,
        on_rate_limited=_on_rate_limited,
        client_id=_opensky_client_id,
        client_secret=_opensky_client_secret,
        rate_limit=_source_rate_limits["opensky"],
        on_quota=lambda value: _emit({"event": "source_rate_limit_update", "data": {
            "provider": "opensky", **value,
        }}) if _opensky_feed is feed else None,
    )
    _opensky_feed = feed
    feed.start()
    _emit({"event": "airscout_feed_started", "data": {"source": "opensky", "bbox": list(bbox)}})


# ─── Auto-lock / tracking helpers ────────────────────────────────────────────

def _locked_aircraft_info(ac: Aircraft, geo: dict) -> dict:
    return {
        "icao24": ac.icao24,
        "callsign": ac.callsign or ac.icao24,
        "altitude_ft": round((ac.altitude_m or 0) * 3.28084),
        "range_a_km": geo["range_a_km"],
        "range_b_km": geo["range_b_km"],
        "az_a": geo["az_a"],
        "el_a": geo["el_a"],
        "az_b": geo["az_b"],
        "el_b": geo["el_b"],
        "scatter_score": geo["scatter_score"],
        "off_path_deg": geo["off_path_deg"],
        "position_updated_epoch_s": ac.position_updated_epoch_s,
        "state": supervisor.session.state.value,
    }


def _do_lock(icao24: str) -> dict | None:
    """Lock onto a specific aircraft (by icao24). Returns lock info or None."""
    global _locked_aircraft_id, _lock_score, supervisor
    if not _station_a_locator or not _station_b_locator:
        return None

    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator)
    except Exception:
        return None

    with _aircraft_lock:
        ac = next((a for a in _aircraft_cache if a.icao24 == icao24), None)
    if ac is None or not ac.has_position:
        return None

    # Compute scatter score
    geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore

    # Build track (current pos + projections)
    track = build_track(ac, lat_a, lon_a, n_samples=6, sample_interval_s=15.0, station_alt_m=_station_a_alt_m)

    # Reset supervisor and begin session
    from airwsjtx.state_machine import SessionState
    if supervisor.session.state not in (SessionState.IDLE, SessionState.ABORTED, SessionState.COMPLETED):
        try:
            supervisor.finish_qso(abort=True)
        except Exception:
            pass

    # Re-init supervisor for new session
    from airwsjtx.supervisor import AirWSJTXSupervisor, SupervisorConfig
    supervisor = AirWSJTXSupervisor(config=SupervisorConfig(minimum_score_to_lock=0.05))

    station_a = Station(
        callsign=_station_a_call or "DE",
        locator=_station_a_locator,
    )
    station_b = Station(
        callsign=_station_b_call or "DX",
        locator=_station_b_locator,
    )

    band_mhz = _wsjtx_band_mhz or 1296.0
    freq_hz = _wsjtx_freq_hz or 1296150000

    try:
        hint = supervisor.begin_session(
            station_a=station_a,
            station_b=station_b,
            band_mhz=band_mhz,
            frequency_hz=freq_hz,
            tracks=[track],
        )
    except ValueError as e:
        # Band check — force-set band for non-23cm if needed
        if "23cm" in str(e) or "threshold" in str(e):
            from unittest.mock import patch
            supervisor.wsjtx.MIN_AUTOMATION_BAND_MHZ = 0
            hint = supervisor.begin_session(
                station_a=station_a,
                station_b=station_b,
                band_mhz=max(band_mhz, 1240.0),
                frequency_hz=freq_hz,
                tracks=[track],
            )
        else:
            raise

    _locked_aircraft_id = icao24
    _lock_score = geo["scatter_score"]

    # Point rotator immediately (manual pointing mode ignores plane positions)
    if _rotator and _rotator.connected and _rotator_control_enabled and not _manual_pointing:
        _rotator.set_position(geo["az_a"], geo["el_a"])

    info = {**_locked_aircraft_info(ac, geo), "hint": hint}
    _emit({"event": "plane_locked", "data": info})
    return info


def _do_unlock() -> None:
    global _locked_aircraft_id, _lock_score
    _locked_aircraft_id = None
    _lock_score = 0.0
    try:
        supervisor.finish_qso(abort=True)
    except Exception:
        pass
    _emit({"event": "plane_unlocked", "data": {}})


def _select_auto_lock_candidate(aircraft: list[Aircraft]) -> Aircraft | None:
    """Return the best candidate using the same filtered geometry path as the UI."""
    if not _station_a_locator or not _station_b_locator:
        return None

    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator)
    except Exception:
        return None

    best_ac: Aircraft | None = None
    best_score = 0.0
    for ac in aircraft:
        if not ac.has_position or not _aircraft_matches_filters(ac, lat_a, lon_a, lat_b, lon_b):
            continue
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore[arg-type]
        score = float(geo.get("scatter_score", 0.0))
        if score > best_score and score >= 0.05:
            best_score = score
            best_ac = ac

    return best_ac


def _try_auto_lock(aircraft: list[Aircraft]) -> None:
    """Called on every aircraft update. Auto-locks the best plane if enabled."""
    if not _auto_lock_enabled:
        return
    from airwsjtx.state_machine import SessionState
    # Only auto-lock from idle/completed/aborted states
    if supervisor.session.state not in (SessionState.IDLE, SessionState.COMPLETED,
                                         SessionState.ABORTED):
        return

    best_ac = _select_auto_lock_candidate(aircraft)
    if best_ac:
        _do_lock(best_ac.icao24)


def _update_rotator_for_locked(aircraft: list[Aircraft]) -> None:
    """After a lock, track the locked aircraft's updated position with the rotator."""
    if not _locked_aircraft_id or not _station_a_locator:
        return
    ac = next((a for a in aircraft if a.icao24 == _locked_aircraft_id), None)
    if ac is None or not ac.has_position:
        return

    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator or _station_a_locator)
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore
    except Exception:
        return

    # Point rotator at new position (unless manual pointing mode)
    if _rotator and _rotator.connected and _rotator_control_enabled and not _manual_pointing:
        _rotator.set_position(geo["az_a"], geo["el_a"])

    # Check if aircraft has left the scatter window
    if geo["scatter_score"] < 0.05:
        _emit({"event": "plane_out_of_window", "data": {"icao24": _locked_aircraft_id}})
        _do_unlock()
        return

    _emit({"event": "plane_tracking", "data": {
        **_locked_aircraft_info(ac, geo),
        "rotator_az": _rotator.current_position.azimuth if _rotator and _rotator.current_position else None,
        "rotator_el": _rotator.current_position.elevation if _rotator and _rotator.current_position else None,
    }})


def _start_rotator(host: str, port: int) -> None:
    global _rotator
    if _rotator:
        _rotator.stop()

    def _on_pos(pos: RotatorPosition) -> None:
        _emit({"event": "rotator_position", "data": {
            "azimuth": round(pos.azimuth, 1),
            "elevation": round(pos.elevation, 1),
        }})

    _rotator = HamlibRotator(
        host=host, port=port,
        poll_interval_s=0.5,
        on_position=_on_pos,
        on_connected=lambda: _emit({"event": "rotator_connected", "data": {"host": host, "port": port}}),
        on_disconnected=lambda: _emit({"event": "rotator_disconnected", "data": {}}),
        on_error=lambda msg: _emit({"event": "rotator_error", "data": {"message": msg}}),
    )
    _rotator.start()


def _start_dump1090_feed(host: str, port: int) -> None:
    global _dump1090_feed, _active_source
    if _dump1090_feed:
        _dump1090_feed.stop()
        _dump1090_feed = None

    def _on_update(aircraft: list[Aircraft]) -> None:
        _set_source_status("dump1090", "connected")
        _replace_aircraft_cache(aircraft)
        feed = _dump1090_feed
        last_message = feed.last_message_epoch_s if feed else None
        _emit({"event": "dump1090_activity", "data": {
            "host": host, "port": port,
            "messages": feed.messages_received if feed else 0,
            "aircraft": len(aircraft),
            "with_position": sum(1 for ac in aircraft if ac.has_position),
            "last_message_age_s": round(time.time() - last_message, 1) if last_message else None,
        }})
        _emit_aircraft_update()

    def _on_connected() -> None:
        global _active_source
        with _source_status_lock:
            _active_source = "dump1090"
        _set_source_status("dump1090", "connected")
        _emit({"event": "airscout_feed_started", "data": {"source": "dump1090"}})

    def _on_disconnected() -> None:
        _set_source_status("dump1090", "disconnected")
        _emit({"event": "airscout_feed_stopped", "data": {"source": "dump1090"}})
        _try_fallback("dump1090", "dump1090 disconnected")

    def _on_error(msg: str) -> None:
        _set_source_status("dump1090", "error", msg)
        _emit({"event": "airscout_error", "data": {"source": "dump1090", "message": msg}})
        _try_fallback("dump1090", msg)

    def _on_busy(msg: str) -> None:
        _set_source_status("dump1090", "busy", msg)
        _emit({"event": "dump1090_busy", "data": {"host": host, "port": port, "message": msg}})

    with _source_status_lock:
        _active_source = "dump1090"
    _set_source_status("dump1090", "connecting")
    _dump1090_feed = Dump1090Feed(
        host=host, port=port,
        report_interval_s=5.0,
        on_update=_on_update,
        on_connected=_on_connected,
        on_disconnected=_on_disconnected,
        on_error=_on_error,
        on_busy=_on_busy,
    )
    _dump1090_feed.start()


def _aircraft_matches_filters(ac: Aircraft, lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> bool:
    if ac.lat is None or ac.lon is None or ac.altitude_m is None:
        return False

    filters = dict(_aircraft_filters)
    if filters.get("lat_min") is not None and ac.lat < float(filters["lat_min"]):
        return False
    if filters.get("lat_max") is not None and ac.lat > float(filters["lat_max"]):
        return False
    if filters.get("lon_min") is not None and ac.lon < float(filters["lon_min"]):
        return False
    if filters.get("lon_max") is not None and ac.lon > float(filters["lon_max"]):
        return False

    if filters.get("altitude_min_ft") is not None:
        altitude_ft = ac.altitude_m * 3.28084
        if altitude_ft < float(filters["altitude_min_ft"]):
            return False

    path_unset = abs(lat_a - lat_b) < 1e-9 and abs(lon_a - lon_b) < 1e-9
    if not path_unset:
        position_tol = filters.get("position_tolerance_deg")
        position_tol = 70.0 if position_tol is None else float(position_tol)
        az_to_plane = bearing_deg(lat_a, lon_a, ac.lat, ac.lon)
        if heading_delta_deg(az_to_plane, bearing_deg(lat_a, lon_a, lat_b, lon_b)) > abs(position_tol):
            return False

    if ac.heading_deg is not None and ac.velocity_ms not in (None, 0):
        bearing = bearing_deg(lat_a, lon_a, lat_b, lon_b)
        tolerance = float(filters.get("heading_tolerance_deg") or 30.0)
        if not heading_within_tolerance(bearing, ac.heading_deg, tolerance):
            return False

    return True


def _get_scatter_aircraft() -> list[dict]:
    """Compute scatter geometry for all cached aircraft and return sorted list."""
    global _aircraft_last_update_monotonic, _aircraft_last_update_epoch_s
    now_monotonic = time.monotonic()
    with _aircraft_lock:
        age_s = (
            max(0.0, now_monotonic - _aircraft_last_update_monotonic)
            if _aircraft_last_update_monotonic is not None
            else None
        )
        if _aircraft_cache_freshness(age_s) == "expired":
            _aircraft_cache.clear()
            _aircraft_last_update_monotonic = None
            _aircraft_last_update_epoch_s = None
            return []
        snapshot = list(_aircraft_cache)
    if not _station_a_locator:
        return []
    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
    except Exception:
        return []
    lat_b, lon_b = None, None
    if _station_b_locator:
        try:
            lat_b, lon_b = locator_to_latlon(_station_b_locator)
        except Exception:
            pass

    if lat_b is None or lon_b is None:
        lat_b = lat_a
        lon_b = lon_a

    results = []
    for ac in snapshot:
        if not ac.has_position:
            continue
        geo = scatter_geometry(
            lat_a, lon_a,
            lat_b, lon_b,
            ac.lat, ac.lon, ac.altitude_m,  # type: ignore[arg-type]
            _station_a_alt_m,
            _station_b_alt_m,
        )
        if not _aircraft_matches_filters(ac, lat_a, lon_a, lat_b, lon_b):
            continue
        results.append({
            "icao24": ac.icao24,
            "callsign": ac.callsign or ac.icao24,
            "lat": round(ac.lat, 4),       # type: ignore[arg-type]
            "lon": round(ac.lon, 4),       # type: ignore[arg-type]
            "altitude_m": round(ac.altitude_m),  # type: ignore[arg-type]
            "altitude_ft": round(ac.altitude_m * 3.28084),  # type: ignore[arg-type]
            "velocity_kts": round(ac.velocity_ms * 1.94384) if ac.velocity_ms else None,
            "heading_deg": round(ac.heading_deg) if ac.heading_deg is not None else None,
            **geo,
        })

    results.sort(key=lambda r: r["scatter_score"], reverse=True)
    return results


def _get_scatter_aircraft_with_total() -> tuple[list[dict], int]:
    if not _station_a_locator:
        return [], 0
    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
    except Exception:
        return [], 0

    lat_b, lon_b = None, None
    if _station_b_locator:
        try:
            lat_b, lon_b = locator_to_latlon(_station_b_locator)
        except Exception:
            pass
    if lat_b is None or lon_b is None:
        lat_b = lat_a
        lon_b = lon_a

    results = []
    total = 0
    with _aircraft_lock:
        snapshot = list(_aircraft_cache)

    for ac in snapshot:
        if not ac.has_position:
            continue
        total += 1
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore[arg-type]
        if not _aircraft_matches_filters(ac, lat_a, lon_a, lat_b, lon_b):
            continue
        results.append({
            "icao24": ac.icao24,
            "callsign": ac.callsign or ac.icao24,
            "lat": round(ac.lat, 4),
            "lon": round(ac.lon, 4),
            "altitude_m": round(ac.altitude_m),
            "altitude_ft": round(ac.altitude_m * 3.28084),
            "velocity_kts": round(ac.velocity_ms * 1.94384) if ac.velocity_ms else None,
            "heading_deg": round(ac.heading_deg) if ac.heading_deg is not None else None,
            **geo,
        })

    results.sort(key=lambda r: r["scatter_score"], reverse=True)
    return results, total


def _emit_aircraft_update() -> None:
    age_s = _aircraft_cache_age_s()
    freshness = _aircraft_cache_freshness(age_s)
    aircraft, total = _get_scatter_aircraft_with_total()
    visible_aircraft = aircraft[:20]
    if _locked_aircraft_id:
        locked = next((ac for ac in aircraft[20:] if ac["icao24"] == _locked_aircraft_id), None)
        if locked is not None:
            visible_aircraft.append(locked)
    with _aircraft_lock:
        last_update_epoch_s = _aircraft_last_update_epoch_s
    with _source_status_lock:
        active_source = _active_source
    _emit({
        "event": "airscout_update",
        "data": {
            "aircraft": visible_aircraft,
            "total": total,
            "station_a": _station_a_locator,
            "station_b": _station_b_locator,
            "active_source": active_source,
            "last_update_epoch_s": last_update_epoch_s,
            "cache_age_s": round(age_s, 1) if age_s is not None else None,
            "data_freshness": freshness,
        },
    })
    # Update tracking or try auto-lock
    with _aircraft_lock:
        snapshot = list(_aircraft_cache)
    if _locked_aircraft_id:
        _update_rotator_for_locked(snapshot)
    else:
        _try_auto_lock(snapshot)


def _on_wsjtx_status(st: WSJTXStatus) -> None:
    global _station_a_locator, _station_b_locator, _station_a_call, _wsjtx_freq_hz, _wsjtx_band_mhz
    global _wsjtx_de_call, _wsjtx_de_grid
    _wsjtx_de_call, _wsjtx_de_grid = st.de_call, st.de_grid
    # Capture frequency for supervisor begin_session
    if st.freq_hz:
        _wsjtx_freq_hz = st.freq_hz
        _wsjtx_band_mhz = st.freq_mhz
    # Auto-populate station A call + locator from WSJT-X de_call/de_grid
    if st.de_call and not _my_call_override:
        _station_a_call = st.de_call
    if st.de_grid and not _my_grid_override and st.de_grid != _station_a_locator:
        _station_a_locator = st.de_grid
        with _source_status_lock:
            active_source = _active_source
        if active_source == "opensky":
            _start_opensky_feed()
        elif active_source == "adsbexchange":
            _start_adsbexchange_feed()
        else:
            _start_configured_airscout_feed()
    if st.dx_grid and not _dx_grid_manual and st.dx_grid != _station_b_locator:
        _station_b_locator = st.dx_grid
        with _source_status_lock:
            active_source = _active_source
        if active_source == "opensky":
            _start_opensky_feed()
        elif active_source == "adsbexchange":
            _start_adsbexchange_feed()
        else:
            _start_configured_airscout_feed()
    _emit({
        "event": "wsjtx_status",
        "data": {
            "app_id": st.app_id,
            "freq_hz": st.freq_hz,
            "freq_mhz": round(st.freq_mhz, 6),
            "mode": st.mode,
            "sub_mode": st.sub_mode,
            "de_call": st.de_call,
            "de_grid": st.de_grid,
            "dx_call": st.dx_call,
            "dx_grid": st.dx_grid,
            "transmitting": st.transmitting,
            "decoding": st.decoding,
            "tx_enabled": st.tx_enabled,
            "rx_df": st.rx_df,
            "tx_df": st.tx_df,
            "tr_period": st.tr_period,
            "config_name": st.config_name,
        },
    })


def _on_wsjtx_decode(dec: WSJTXDecode) -> None:
    _emit({
        "event": "wsjtx_decode",
        "data": {
            "message": dec.message,
            "snr": dec.snr,
            "delta_time_s": round(dec.delta_time_s, 1),
            "delta_freq_hz": dec.delta_freq_hz,
            "mode": dec.mode,
            "off_air": dec.off_air,
        },
    })


def _on_wsjtx_qso_logged(qso: WSJTXQsoLogged) -> None:
    from airwsjtx.state_machine import SessionState
    # WSJT-X is the authority on a finished QSO; close the app session to match
    completed = False
    if supervisor.session.state == SessionState.ACTIVE_QSO:
        try:
            supervisor.finish_qso(abort=False)
            completed = True
        except Exception:
            pass
    _emit({
        "event": "qso_logged",
        "data": {
            "dx_call": qso.dx_call,
            "dx_grid": qso.dx_grid,
            "freq_hz": qso.freq_hz,
            "mode": qso.mode,
            "report_sent": qso.report_sent,
            "report_rcvd": qso.report_rcvd,
            "state": supervisor.session.state.value,
            "session_completed": completed,
        },
    })


def _on_wsjtx_connected(app_id: str) -> None:
    _emit({"event": "wsjtx_connected", "data": {"app_id": app_id}})


def _on_wsjtx_disconnected() -> None:
    _emit({"event": "wsjtx_disconnected", "data": {}})


def _on_wsjtx_error(message: str) -> None:
    _emit({"event": "wsjtx_error", "data": {"message": message}})


def main() -> None:
    global _wsjtx_listener
    _emit_source_status()
    listener = WSJTXListener(
        on_status=_on_wsjtx_status,
        on_decode=_on_wsjtx_decode,
        on_qso_logged=_on_wsjtx_qso_logged,
        on_connected=_on_wsjtx_connected,
        on_disconnected=_on_wsjtx_disconnected,
        on_error=_on_wsjtx_error,
    )
    _wsjtx_listener = listener
    listener.start()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            _err(None, f"Invalid JSON: {e}")
            continue
        handle(msg)

    listener.stop()
    if _rotator:
        _rotator.stop()
    if _opensky_feed:
        _opensky_feed.stop()
    if _dump1090_feed:
        _dump1090_feed.stop()
    if _adsbexchange_feed:
        _adsbexchange_feed.stop()


if __name__ == "__main__":
    main()
