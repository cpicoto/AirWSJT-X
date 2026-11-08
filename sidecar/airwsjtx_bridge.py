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
import queue
import re
import secrets
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from airwsjtx.airscout import ADSBExchangeFeed, Aircraft, Dump1090Feed, OpenSkyFeed, SourceRateLimit
from airwsjtx.rtlsdr import (LocalRTLSDRFeed, discover_devices, platform_supported as rtlsdr_platform_supported,
                             validate_gain, support_error as rtlsdr_support_error)
from airwsjtx.speech import PiperSpeech
from airwsjtx.callsign import (
    DEFAULT_DB_MANIFEST_URL,
    check_db_update as callsign_check_db_update,
    download_db as callsign_download_db,
    get_db_status as callsign_get_db_status,
    list_db_entries as callsign_list_db_entries,
    lookup as lookup_callsign,
    submit_grid_contribution as callsign_submit_grid_contribution,
)
from airwsjtx.geo import bearing_deg, haversine_km, heading_delta_deg, heading_within_tolerance, locator_to_latlon, scatter_geometry
from airwsjtx.hamlib_rotator import HamlibRotator, RotatorPosition
from airwsjtx.satpc32_dde import SatPC32DDE
from airwsjtx.adsb import AIRCRAFT_RETENTION_S, POSITION_RETENTION_S
from airwsjtx.models import AircraftSample, AircraftTrack, Station
from airwsjtx.ranking import (RANKING_METHODS, rank_geometry, ranking_key, within_qso_window,
                             apply_scatter_preferences, scatter_eligible)
from airwsjtx.supervisor import AirWSJTXSupervisor
from airwsjtx.tracking import build_track, select_best_aircraft
from airwsjtx.wsjtx import MIN_QSO_FREQUENCY_HZ
from airwsjtx.wsjtx_udp import (
    WSJTXDecode, WSJTXListener, WSJTXQsoLogged, WSJTXStartDxTxResult, WSJTXStatus,
)
from airwsjtx.wsjtx_qso import (
    CONFIGURATION_CONFIRM_S,
    START_DX_TX_CONFIRM_S,
    QsoProgress,
    WSJTXQsoAssistant,
)

supervisor = AirWSJTXSupervisor()
_qso_assistant = WSJTXQsoAssistant()
_qso_lock = threading.RLock()
_qso_request_id = secrets.randbits(32)
_latest_wsjtx_status: WSJTXStatus | None = None
_stdout_lock = threading.Lock()
_speech: PiperSpeech | None = None

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
_rtlsdr_feed: LocalRTLSDRFeed | None = None
_rtlsdr_last_snapshot: dict[str, object] = {}
_rtlsdr_device_index: int | None = None
_rtlsdr_device_info: dict[str, object] | None = None
_rtlsdr_gain_db: float | None = None
_rtlsdr_startup_attempted = False
_rtlsdr_startup_network_priority: list[str] | None = None
_supported_sources = ("opensky", "dump1090", "adsbexchange") + (
    ("rtlsdr",) if rtlsdr_platform_supported() else ()
)
# dump1090 is OFF by default; the user must explicitly enable it via the UI.
# The priority is persisted in source_priority.json so opt-in choices survive restarts.
_DEFAULT_SOURCE_PRIORITY: list[str] = ["opensky"]
_source_aliases = {
    "rtl-sdr": "rtlsdr",
    "rtl_sdr": "rtlsdr",
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
if "rtlsdr" in _supported_sources:
    _source_status["rtlsdr"] = {"state": "stopped", "message": None}
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
_STATION_GRID_RE = re.compile(r"[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?")
_manual_pointing: bool = False
_rotator_control_enabled: bool = False
_output_mode: str = "off"
_output_lock = threading.RLock()
_satpc32_dde: SatPC32DDE | None = None

# Aircraft display filters
_DEFAULT_FILTERS = {
    "ranking_method": "legacy",
    "scatter_metadata": True,
    "lat_min": None,
    "lat_max": None,
    "lon_min": None,
    "lon_max": None,
    "altitude_min_ft": None,
    "heading_tolerance_deg": 30.0,
    "position_tolerance_deg": 70.0,
    "priority_flight": "",
    "priority_flight_exclusive": False,
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


def _read_filters() -> dict[str, float | str | bool | None]:
    try:
        with open(_aircraft_filter_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            result = dict(_DEFAULT_FILTERS)
            if data.get("ranking_method") in RANKING_METHODS:
                result["ranking_method"] = data["ranking_method"]
            if isinstance(data.get("scatter_metadata"), bool):
                result["scatter_metadata"] = data["scatter_metadata"]
            for key in ("lat_min", "lat_max", "lon_min", "lon_max", "altitude_min_ft",
                        "heading_tolerance_deg", "position_tolerance_deg"):
                if key in data:
                    val = data[key]
                    result[key] = None if val is None or val == "" else float(val)
            return result
    except Exception:
        pass
    return dict(_DEFAULT_FILTERS)


def _write_filters(filters: dict[str, float | str | bool | None]) -> None:
    payload = {
        key: value if key in ("priority_flight", "priority_flight_exclusive", "ranking_method", "scatter_metadata")
        else (None if value is None else float(value))
        for key, value in filters.items()
    }
    with open(_aircraft_filter_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


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
_manual_aircraft_lock = False
_manual_lock_source: str | None = None
_manual_lock_last_seen = 0.0
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
            "poll_intervals_s": _poll_intervals(),
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


def _replace_aircraft_cache(aircraft: list[Aircraft], *, source: str | None = None) -> None:
    global _aircraft_last_update_monotonic, _aircraft_last_update_epoch_s
    now_monotonic = time.monotonic()
    now_epoch = int(time.time())
    if source is not None:
        for ac in aircraft:
            ac.position_source = source
    with _qso_lock:
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


def _current_aircraft_snapshot() -> list[Aircraft]:
    global _aircraft_last_update_monotonic, _aircraft_last_update_epoch_s
    now = time.monotonic()
    with _aircraft_lock:
        age_s = (max(0.0, now - _aircraft_last_update_monotonic)
                 if _aircraft_last_update_monotonic is not None else None)
        if _aircraft_cache_freshness(age_s) == "expired":
            _aircraft_cache.clear()
            _aircraft_last_update_monotonic = None
            _aircraft_last_update_epoch_s = None
        return list(_aircraft_cache)


def _emit(msg: dict) -> None:
    """Thread-safe write to stdout."""
    with _stdout_lock:
        print(json.dumps(msg), flush=True)


def _emit_qso_progress(progress: QsoProgress) -> None:
    with _qso_lock:
        if progress != _qso_assistant.snapshot():
            return
        _emit({
            "event": "qso_progress",
            "data": {
                "stage": progress.stage,
                "message": progress.message,
                "dx_call": progress.dx_call,
            },
        })


def _check_qso_configuration_timeout() -> None:
    with _qso_lock:
        previous_progress = _qso_assistant.snapshot()
        progress = _qso_assistant.configuration_timeout()
    if progress != previous_progress:
        _emit_qso_progress(progress)


def _qso_tracking_viable_locked(*, require_active: bool = True) -> bool:
    """Check the current aircraft and path before issuing a QSO control command."""
    from airwsjtx.state_machine import SessionState

    session = supervisor.session
    if require_active and session.state != SessionState.ACTIVE_QSO:
        return False
    if not _locked_aircraft_id or session.selected_aircraft_id != _locked_aircraft_id:
        return False
    if not _station_a_locator or not _station_b_locator:
        return False
    if require_active and not _qso_assistant.matches_station(
        _station_b_call, _station_b_locator, _station_a_call,
    ):
        return False
    aircraft = next(
        (ac for ac in _current_aircraft_snapshot() if ac.icao24 == _locked_aircraft_id),
        None,
    )
    if aircraft is None or not aircraft.has_position or not scatter_eligible(aircraft) or _position_valid_for_s(aircraft) <= 0:
        return False
    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator)
        geo = scatter_geometry(
            lat_a, lon_a, lat_b, lon_b, aircraft.lat, aircraft.lon, aircraft.altitude_m,
            _station_a_alt_m, _station_b_alt_m,
        )
    except (TypeError, ValueError, OverflowError):
        return False
    return within_qso_window(geo, distinct_stations=(lat_a, lon_a) != (lat_b, lon_b))


def _suspend_qso_for_aircraft() -> QsoProgress:
    return _qso_assistant.suspend(
        "Aircraft is unlocked or has no current viable position; re-arm the QSO after checking tracking"
    )


def _check_qso_start_tx(request_id: int) -> None:
    with _qso_lock:
        previous_progress = _qso_assistant.snapshot()
        progress = _qso_assistant.start_timeout(request_id)
    if progress != previous_progress:
        _emit_qso_progress(progress)


def _send_qso_start_locked() -> QsoProgress:
    """Request an atomic direct call once; caller must hold _qso_lock."""
    global _qso_request_id
    status = _qso_assistant.direct_start_status()
    if status is None:
        return _qso_assistant.snapshot()
    if not _qso_tracking_viable_locked():
        return _suspend_qso_for_aircraft()
    if (
        status.source_addr is None or not status.de_call or not status.dx_call
        or not status.mode or status.tr_period != 15
    ):
        return _qso_assistant.suspend("Cannot identify the verified WSJT-X settings; re-arm")
    _qso_request_id = (_qso_request_id + 1) & 0xFFFFFFFF or 1
    request_id = _qso_request_id
    _qso_assistant.mark_start_requested(request_id)
    if not (
        _wsjtx_listener
        and _wsjtx_listener.send_start_dx_tx(
            request_id=request_id,
            app_id=status.app_id,
            destination=status.source_addr,
            de_call=status.de_call,
            freq_hz=status.freq_hz,
            dx_call=status.dx_call,
            dx_grid=status.dx_grid or "",
            mode=status.mode,
            sub_mode=status.sub_mode or "",
            tr_period=int(status.tr_period),
        )
    ):
        return _qso_assistant.suspend(
            "Could not send the direct-call request to the verified WSJT-X instance; check its connection and re-arm"
        )
    progress = _qso_assistant.snapshot()
    timer = threading.Timer(START_DX_TX_CONFIRM_S, _check_qso_start_tx, args=(request_id,))
    timer.daemon = True
    timer.start()
    return progress


def _ok(id_: str | None, result: object) -> None:
    _emit({"id": id_, "ok": True, "result": result})


def _err(id_: str | None, message: str) -> None:
    _emit({"id": id_, "ok": False, "error": message})


def _speech_manager() -> PiperSpeech:
    global _speech
    if _speech is None:
        _speech = PiperSpeech(on_error=lambda message: _emit({
            "event": "speech_error", "data": {"message": message},
        }))
    return _speech


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
    global _rtlsdr_device_index, _rtlsdr_device_info, _rtlsdr_gain_db, _rtlsdr_startup_attempted
    global _station_a_locator, _station_b_locator, _dx_grid_manual
    global _station_a_call, _station_b_call, _station_a_alt_m
    global _my_call_override, _my_grid_override
    global _station_b_alt_m, _opensky_client_id, _opensky_client_secret, _adsbexchange_api_key
    global _dump1090_host, _dump1090_port, _aircraft_filters, _manual_pointing, _auto_lock_enabled
    method = msg.get("method", "")
    params = msg.get("params", {})
    id_ = msg.get("id")

    try:
        if method == "speech_status":
            _ok(id_, _speech_manager().status())

        elif method == "speech_probe":
            _ok(id_, _speech_manager().probe())

        elif method == "speech_speak":
            _ok(id_, _speech_manager().speak(params.get("text", "")))

        elif method == "speech_stop":
            if _speech is not None:
                _speech.stop()
            _ok(id_, {})

        elif method == "source_credentials_set":
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
            with _qso_lock:
                hint = supervisor.begin_session(
                    station_a=station_a,
                    station_b=station_b,
                    band_mhz=params["band_mhz"],
                    frequency_hz=params["frequency_hz"],
                    tracks=tracks,
                )
                _ok(id_, {"hint": hint, "state": supervisor.session.state.value})

        elif method == "update_tracking":
            with _qso_lock:
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
            with _qso_lock:
                dx_call = str(params.get("dx_call") or _station_b_call or "").strip().upper()
                dx_grid = str(params.get("dx_grid") or _station_b_locator or "").strip().upper() or None
                # After a terminal session, re-arm the still-locked aircraft.
                if supervisor.session.state in (SessionState.ABORTED, SessionState.COMPLETED) and _locked_aircraft_id:
                    _do_lock(_locked_aircraft_id)
                if supervisor.session.state not in (
                    SessionState.ACTIVE_QSO, SessionState.PLANE_LOCKED,
                    SessionState.TRACKING, SessionState.READY,
                ) or not _qso_tracking_viable_locked(require_active=False):
                    raise ValueError("Lock a viable aircraft with a current position before preparing a QSO.")
                if (
                    supervisor.session.state == SessionState.ACTIVE_QSO
                    and _qso_assistant.snapshot().stage not in {
                        "idle", "suspended", "aborted", "finished", "logged",
                    }
                ):
                    raise ValueError("A QSO is already armed; abort or finish it before starting another.")
                if (
                    (_station_b_call and _station_b_call.strip().upper() != dx_call)
                    or not WSJTXQsoAssistant.grids_match(dx_grid, _station_b_locator)
                ):
                    raise ValueError("The selected DX no longer matches the tracked station; check the DX and re-arm.")
                status = _latest_wsjtx_status
                listener = _wsjtx_listener
                if status is None or listener is None or not listener.is_connected():
                    raise ValueError("WSJT-X is not connected; connect the selected rig before preparing a QSO.")
                if status.source_addr is None:
                    raise ValueError("Cannot identify the selected WSJT-X source address; wait for its status.")
                if status.tx_enabled or status.transmitting:
                    raise ValueError("Disable WSJT-X Tx before preparing the DX; you can re-enable it after settings are verified.")
                if status.freq_hz < MIN_QSO_FREQUENCY_HZ:
                    raise ValueError("QSO control requires WSJT-X to be on 2 m (144 MHz) or higher.")
                supervisor.wsjtx.configure_q65_15s(
                    band_mhz=status.freq_mhz,
                    frequency_hz=status.freq_hz,
                )
                progress = _qso_assistant.arm(
                    dx_call=dx_call,
                    dx_grid=dx_grid,
                    my_call=_station_a_call or status.de_call,
                    app_id=status.app_id,
                    source_addr=status.source_addr,
                )
                requested_at = time.monotonic()
                if not listener.send_configure(
                    dx_call=dx_call, dx_grid=dx_grid,
                    app_id=status.app_id, destination=status.source_addr,
                ):
                    progress = _qso_assistant.suspend(
                        "Could not send QSO settings; verify Accept UDP requests and re-arm"
                    )
                    _emit_qso_progress(progress)
                    raise ValueError("Could not send QSO settings to WSJT-X. Enable Accept UDP requests and check the selected rig.")
                _qso_assistant.configuration_requested(requested_at)
                if not listener.send_replay(app_id=status.app_id, destination=status.source_addr):
                    progress = _qso_assistant.suspend(
                        "Could not request a fresh WSJT-X status; check the connection and re-arm"
                    )
                    _emit_qso_progress(progress)
                    raise ValueError("Could not request a fresh status from the selected WSJT-X instance.")
                try:
                    if supervisor.session.state != SessionState.ACTIVE_QSO:
                        supervisor.operator_start_qso()
                except ValueError:
                    progress = _qso_assistant.suspend("Aircraft is no longer locked; re-arm the QSO")
                    _emit_qso_progress(progress)
                    raise
                _emit_qso_progress(progress)
                _ok(id_, {"state": supervisor.session.state.value, "qso": progress.__dict__})
            timeout = threading.Timer(CONFIGURATION_CONFIRM_S, _check_qso_configuration_timeout)
            timeout.daemon = True
            timeout.start()

        elif method == "finish_qso":
            with _qso_lock:
                target = _qso_assistant.halt_target()
                if (
                    target is None and _qso_assistant.snapshot().stage == "idle"
                    and _latest_wsjtx_status and _latest_wsjtx_status.source_addr
                ):
                    target = (_latest_wsjtx_status.app_id, _latest_wsjtx_status.source_addr)
                progress = _qso_assistant.finish(aborted=bool(params.get("abort", False)))
                _emit_qso_progress(progress)
                wsjtx_tx = None
                if params.get("control_wsjtx"):
                    wsjtx_tx = bool(
                        _wsjtx_listener and target
                        and _wsjtx_listener.send_halt_tx(app_id=target[0], destination=target[1])
                    )
                try:
                    supervisor.finish_qso(abort=params.get("abort", False))
                except ValueError:
                    # Halt Tx may be needed after the app session has already closed.
                    if not wsjtx_tx:
                        raise
                _ok(id_, {"state": supervisor.session.state.value, "wsjtx_tx_sent": wsjtx_tx})

        elif method == "get_status":
            _recover_startup_receiver_failure()
            s = supervisor.session
            _ok(id_, {
                "state": s.state.value,
                "wsjtx_known_rigs": _wsjtx_listener.get_known_rigs() if _wsjtx_listener else [],
                "wsjtx_error": _wsjtx_listener.get_error() if _wsjtx_listener else None,
                "station_a": {"callsign": s.station_a.callsign, "locator": s.station_a.locator} if s.station_a else None,
                "station_b": {"callsign": s.station_b.callsign, "locator": s.station_b.locator} if s.station_b else None,
                "selected_aircraft_id": s.selected_aircraft_id,
                "candidate_count": s.candidate_count,
                "wsjtx": {
                    "configured": supervisor.wsjtx.status.configured,
                    "mode": supervisor.wsjtx.status.mode,
                    "band_mhz": supervisor.wsjtx.status.band_mhz,
                },
                "qso": _qso_assistant.snapshot().__dict__,
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
            with _qso_lock:
                _station_a_locator = params.get("locator_a")
                _station_b_locator = params.get("locator_b")
                # A DX chosen in the app must not be clobbered by WSJT-X's DX grid.
                _dx_grid_manual = bool(params.get("dx_local", bool(_station_b_locator)))
            _start_configured_airscout_feed(force_restart=True)
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {"locator_a": _station_a_locator, "locator_b": _station_b_locator})

        elif method == "rtlsdr_discover":
            _ok(id_, {"devices": discover_devices()})

        elif method == "rtlsdr_status":
            _ok(id_, _rtlsdr_feed.snapshot() if _rtlsdr_feed else {
                **_rtlsdr_last_snapshot,
                "state": _source_status.get("rtlsdr", {}).get("state", "stopped"),
                "message": rtlsdr_support_error() or _source_status.get("rtlsdr", {}).get("message"),
                "device_index": _rtlsdr_device_index,
                "gain_db": _rtlsdr_last_snapshot.get("gain_db"),
                "last_message_age_s": None,
            })

        elif method == "rtlsdr_get_gains":
            index = params.get("device_index", 0)
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("device_index must be a non-negative integer")
            if _rtlsdr_feed is not None:
                if index != _rtlsdr_feed.device_index:
                    raise ValueError("Stop the active receiver before selecting another USB device")
                gains = _rtlsdr_feed.supported_gains_db
            else:
                devices = discover_devices()
                device = next((device for device in devices if device["index"] == index), None)
                if device is None or device["available"] is not True:
                    raise ValueError("Selected RTL-SDR receiver is no longer attached")
                gains = device["supported_gains_db"]
            _ok(id_, {"supported_gains_db": gains})

        elif method == "rtlsdr_auto_start":
            _auto_start_rtlsdr()
            _ok(id_, {"attempted": _rtlsdr_startup_attempted})

        elif method == "rtlsdr_start":
            index = params.get("device_index", 0)
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ValueError("device_index must be a non-negative integer")
            devices = discover_devices()
            device = next((device for device in devices if device["index"] == index), None)
            if device is None or device["available"] is not True:
                raise ValueError("No accessible RTL-SDR receiver found. Check USB connection and the WinUSB driver for Bulk-In Interface 0; close other SDR apps.")
            requested_gain = params.get("gain_db")
            gain = validate_gain(requested_gain, device["supported_gains_db"])
            _rtlsdr_device_index = index
            _rtlsdr_device_info = device
            _rtlsdr_gain_db = gain
            with _source_priority_lock:
                next_priority = ["rtlsdr"] + [source for source in _source_priority if source != "rtlsdr"]
            _set_aircraft_sources(next_priority)
            _ok(id_, _rtlsdr_feed.snapshot() if _rtlsdr_feed else {
                "state": "error", "message": _source_status.get("rtlsdr", {}).get("message"),
            })

        elif method == "rtlsdr_set_gain":
            if _rtlsdr_feed is None:
                raise ValueError("Start a local receiver before adjusting its gain")
            gain = validate_gain(params.get("gain_db"), _rtlsdr_feed.supported_gains_db)
            _stop_source("rtlsdr")
            _rtlsdr_gain_db = gain
            if not _start_rtlsdr_feed():
                raise RuntimeError(_source_status["rtlsdr"]["message"])
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, _rtlsdr_feed.snapshot())

        elif method == "rtlsdr_stop":
            _rtlsdr_startup_attempted = True
            with _source_priority_lock:
                next_priority = [source for source in _source_priority if source != "rtlsdr"]
            _set_aircraft_sources(next_priority or list(_DEFAULT_SOURCE_PRIORITY))
            _ok(id_, {"state": "stopped", "message": "Receiver stopped; returned to configured network sources."})

        elif method == "airscout_get_dump1090_host":
            _ok(id_, {"host": _dump1090_host, "port": _dump1090_port})

        elif method == "airscout_set_dump1090_host":
            host = _normalize_dump1090_host(params.get("host"))
            _dump1090_host = host
            _write_dump1090_host(host)
            # Reconnect immediately only if the dump1090 feed is running.
            if _dump1090_feed is not None:
                _start_dump1090_feed(host, _dump1090_port)
                _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {"host": host, "port": _dump1090_port})

        elif method == "airscout_start_dump1090":
            host = params.get("host", _dump1090_host)
            port = int(params.get("port", 30003))
            _dump1090_host = host
            _dump1090_port = port
            _start_dump1090_feed(host, port)
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {"host": host, "port": port})

        elif method == "airscout_set_sources":
            next_priority = _normalize_sources(params.get("sources"))
            _set_aircraft_sources(next_priority)
            with _source_priority_lock:
                priority = list(_source_priority)
            with _source_status_lock:
                active_source = _active_source
                sources = {k: dict(v) for k, v in _source_status.items()}
            _ok(id_, {
                "active_source": active_source,
                "priority": priority,
                "sources": sources,
                "poll_intervals_s": _poll_intervals(),
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
                "poll_intervals_s": _poll_intervals(),
            })

        elif method == "airscout_apply_filters":
            raw_filters = params.get("filters")
            if not isinstance(raw_filters, dict):
                _err(id_, "filters object required")
                return
            next_filters = dict(_DEFAULT_FILTERS)
            ranking_method = raw_filters.get("ranking_method", "legacy")
            if ranking_method not in RANKING_METHODS:
                _err(id_, "Unknown aircraft ranking method")
                return
            next_filters["ranking_method"] = ranking_method
            scatter_metadata = raw_filters.get("scatter_metadata", True)
            if not isinstance(scatter_metadata, bool):
                _err(id_, "scatter_metadata must be a boolean")
                return
            next_filters["scatter_metadata"] = scatter_metadata
            for key in ("lat_min", "lat_max", "lon_min", "lon_max", "altitude_min_ft",
                        "heading_tolerance_deg", "position_tolerance_deg"):
                if key in raw_filters:
                    value = raw_filters[key]
                    if value is None or value == "":
                        next_filters[key] = None
                    else:
                        next_filters[key] = float(value)
            priority_flight = raw_filters.get("priority_flight", "")
            exclusive = raw_filters.get("priority_flight_exclusive", False)
            if not isinstance(priority_flight, str) or not isinstance(exclusive, bool):
                _err(id_, "priority_flight must be a string and priority_flight_exclusive must be a boolean")
                return
            next_filters["priority_flight"] = priority_flight.strip().upper()
            next_filters["priority_flight_exclusive"] = exclusive
            _write_filters(next_filters)
            _aircraft_filters = next_filters
            _emit({"event": "airscout_filters_updated", "data": {"filters": dict(next_filters)}})
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {"filters": dict(next_filters)})

        elif method == "airscout_get_aircraft":
            result, total = _get_scatter_aircraft_with_total()
            _ok(id_, {"aircraft": result, "total": total})

        elif method == "airscout_fetch_now":
            try:
                feed = _adsbexchange_feed if _active_source == "adsbexchange" else _opensky_feed
                if _active_source in ("opensky", "adsbexchange") and feed:
                    aircraft = feed.fetch_once()
                    _replace_aircraft_cache(aircraft, source=_active_source)
                    _emit_aircraft_update()
                    _ok(id_, {"count": len(aircraft)})
                elif _active_source in ("dump1090", "rtlsdr"):
                    with _aircraft_lock:
                        count = len(_aircraft_cache)
                    _emit_aircraft_update()
                    _ok(id_, {"count": count})
                else:
                    _err(id_, "No active aircraft source — call airscout_set_stations first")
            except Exception as e:
                _err(id_, str(e))

        # ── Rotator methods ───────────────────────────────────────────────────

        elif method == "satpc32_dde_status":
            _ok(id_, _dde_status())

        elif method == "satpc32_dde_set":
            enabled = params.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            _set_output_mode("dde" if enabled else "off")
            _ok(id_, _dde_status())

        elif method == "rotator_output_status":
            _ok(id_, _output_status())

        elif method == "rotator_output_set":
            _set_output_mode(params.get("mode"))
            _ok(id_, _output_status())

        elif method == "rotator_connect":
            host = params.get("host", "127.0.0.1")
            port = int(params.get("port", 4533))
            _start_rotator(host, port)
            _ok(id_, {"host": host, "port": port})

        elif method == "set_pointing_mode":
            with _output_lock:
                _manual_pointing = params.get("mode") == "manual"
                if _manual_pointing and _satpc32_dde:
                    _satpc32_dde.clear()
                elif _output_mode == "dde":
                    _prime_dde_target()
            _ok(id_, {"mode": "manual" if _manual_pointing else "rotator"})

        elif method == "set_rotator_control":
            _set_output_mode("hamlib" if params.get("enabled", True) else "off")
            _ok(id_, {"enabled": _rotator_control_enabled})

        elif method == "rotator_set":
            with _output_lock:
                if _output_mode != "hamlib":
                    raise ValueError("Select Hamlib output before sending coordinates")
                if not _manual_pointing:
                    raise ValueError("Direct coordinates require Manual mode; Auto uses fresh backend aircraft positions")
                if not _rotator or not _rotator.connected:
                    raise ValueError("Rotator not connected")
                az, el = float(params["azimuth"]), float(params.get("elevation", 0.0))
                if not math.isfinite(az) or not math.isfinite(el) or not 0 <= az <= 450 or not 0 <= el <= 90:
                    raise ValueError("Invalid rotator coordinates")
                _rotator.set_position(az, el)
                _ok(id_, {"azimuth": az, "elevation": el})

        elif method == "rotator_stop":
            with _output_lock:
                was_hamlib = _output_mode == "hamlib"
                if was_hamlib and _rotator:
                    _rotator.stop_rotation()
                _set_output_mode("off")
            _ok(id_, {})

        elif method == "rotator_park":
            with _output_lock:
                if _output_mode != "hamlib":
                    raise ValueError("Select Hamlib output before parking")
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

        elif method == "callsign_db_entries":
            _ok(id_, callsign_list_db_entries())

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
            call = (params.get("callsign") or "").strip().upper() or None
            grid = (params.get("grid") or "").strip().upper() or None
            if grid:
                if not _STATION_GRID_RE.fullmatch(grid):
                    raise ValueError("MY grid must be a valid 4, 6, or 8 character Maidenhead locator")
            if call and (not any(c.isdigit() for c in call) or not any(c.isalpha() for c in call)
                         or not all(c.isascii() and (c.isalnum() or c == "/") for c in call)):
                raise ValueError("MY callsign must contain letters and digits, with optional /")
            alt_raw = params.get("altitude_m")
            alt_m = 0.0 if alt_raw in (None, "") else float(alt_raw)
            if not -500.0 <= alt_m <= 9000.0:
                raise ValueError("MY altitude must be between -500 and 9000 metres")
            with _qso_lock:
                _station_a_alt_m = alt_m
                _my_call_override, _my_grid_override = call, grid
                _station_a_call = call or _wsjtx_de_call
                _station_a_locator = grid or _wsjtx_de_grid
            _start_configured_airscout_feed(force_restart=True)
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {"callsign": call, "grid": grid, "altitude_m": alt_m})

        elif method == "set_dx_altitude":
            alt_raw = params.get("altitude_m")
            alt_m = 0.0 if alt_raw in (None, "") else float(alt_raw)
            if not -500.0 <= alt_m <= 9000.0:
                raise ValueError("DX altitude must be between -500 and 9000 metres")
            with _qso_lock:
                _station_b_alt_m = alt_m
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {"altitude_m": alt_m})

        elif method == "set_station_calls":
            with _qso_lock:
                _station_a_call = params.get("call_a")
                _station_b_call = params.get("call_b")
            _emit_aircraft_update(try_auto_lock=False)
            _ok(id_, {})

        elif method == "wsjtx_set_rig_name_filter":
            if _wsjtx_listener is None:
                _err(id_, "WSJT-X listener not ready")
            else:
                rig_name = params.get("rig_name")
                if rig_name is not None:
                    rig_name = str(rig_name)
                with _qso_lock:
                    configured_rig_name = _wsjtx_listener.set_rig_name_filter(rig_name)
                _ok(id_, {"rig_name": configured_rig_name})

        elif method == "wsjtx_get_rig_name_filter":
            if _wsjtx_listener is None:
                _err(id_, "WSJT-X listener not ready")
            else:
                _ok(id_, {"rig_name": _wsjtx_listener.get_rig_name_filter()})

        elif method == "wsjtx_request_status":
            listener = _wsjtx_listener
            if listener is None or not listener.is_connected():
                _ok(id_, {"requested": False})
            elif listener.send_replay():
                _ok(id_, {"requested": True})
            else:
                _err(id_, "Could not request status from the selected WSJT-X instance.")

        elif method == "auto_lock_set":
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

def _poll_intervals() -> dict[str, float]:
    return {**_source_poll_intervals, "dump1090": 5.0,
            **({"rtlsdr": 1.0} if "rtlsdr" in _supported_sources else {})}


def _set_aircraft_sources(priority: list[str]) -> None:
    global _source_priority
    with _source_priority_lock:
        _source_priority = priority
    _write_source_priority(priority)
    _start_configured_airscout_feed(force_restart=True)
    _emit_aircraft_update(try_auto_lock=False)


def _start_rtlsdr_feed() -> bool:
    global _rtlsdr_feed, _active_source
    if _rtlsdr_device_index is None:
        _set_source_status("rtlsdr", "error", "No receiver selected. Automatic startup may find an attached receiver; use Discover and Start to retry.")
        return False

    def on_status(state: str, message: str | None) -> None:
        _set_source_status("rtlsdr", state, message)
        if state == "error":
            _emit({"event": "airscout_error", "data": {"source": "rtlsdr", "message": message}})

    def on_update(aircraft: list[Aircraft]) -> None:
        _replace_aircraft_cache(aircraft, source="rtlsdr")
        _emit_aircraft_update()
        _emit({"event": "rtlsdr_update", "data": feed.snapshot()})

    feed = LocalRTLSDRFeed(
        _rtlsdr_device_index, on_update, on_status,
        gain_db=_rtlsdr_gain_db, device_info=_rtlsdr_device_info,
    )
    _rtlsdr_feed = feed
    with _source_status_lock:
        _active_source = "rtlsdr"
    # Never carry positioned network aircraft over as if received locally.
    _replace_aircraft_cache([])
    try:
        feed.start()
    except (OSError, RuntimeError) as error:
        feed.stop()
        _rtlsdr_feed = None
        _set_source_status("rtlsdr", "error", str(error))
        _emit({"event": "airscout_error", "data": {"source": "rtlsdr", "message": str(error)}})
        return False
    return True


def _auto_start_rtlsdr() -> None:
    global _rtlsdr_startup_attempted, _rtlsdr_device_index, _rtlsdr_device_info
    global _rtlsdr_startup_network_priority, _source_priority, _active_source
    if _rtlsdr_startup_attempted:
        return
    _rtlsdr_startup_attempted = True
    if rtlsdr_support_error() or _rtlsdr_feed is not None:
        return
    try:
        devices = discover_devices()
    except (OSError, RuntimeError) as error:
        _set_source_status("rtlsdr", "error", str(error))
        _start_configured_airscout_feed()
        return
    available = next((device for device in devices if device["available"]), None)
    if available is None:
        _set_source_status("rtlsdr", "stopped",
                           "No accessible RTL-SDR attached; configured network sources remain available.")
        _start_configured_airscout_feed()
        return
    with _source_priority_lock:
        _rtlsdr_startup_network_priority = [s for s in _source_priority if s != "rtlsdr"]
    # Start before stopping a working network feed, so USB failure cannot disrupt it.
    previous_source = _active_source
    previous_aircraft = _current_aircraft_snapshot()
    _rtlsdr_device_index = int(available["index"])
    _rtlsdr_device_info = available
    if _start_rtlsdr_feed():
        if previous_source and previous_source != "rtlsdr":
            _stop_source(previous_source)
        with _source_priority_lock:
            _source_priority = ["rtlsdr"] + _rtlsdr_startup_network_priority
        _emit_source_status()
    else:
        _rtlsdr_device_index = None
        with _source_status_lock:
            _active_source = previous_source
        _replace_aircraft_cache(previous_aircraft)
        _start_configured_airscout_feed()


def _recover_startup_receiver_failure() -> None:
    global _source_priority, _active_source, _rtlsdr_device_index
    if (_rtlsdr_startup_network_priority is None or _active_source != "rtlsdr"
            or _rtlsdr_feed is None or _rtlsdr_feed.snapshot()["state"] != "error"):
        return
    message = _rtlsdr_feed.snapshot().get("message")
    try:
        _stop_source("rtlsdr")
    except RuntimeError as error:
        _set_source_status("rtlsdr", "error", str(error))
        return
    _rtlsdr_device_index = None
    with _source_priority_lock:
        _source_priority = [s for s in _source_priority if s != "rtlsdr"]
    with _source_status_lock:
        _active_source = None
    _set_source_status("rtlsdr", "error", message)
    _start_configured_airscout_feed()


def _validate_poll_interval(provider: str, seconds: object) -> None:
    if provider not in _source_poll_intervals:
        raise ValueError("Unsupported aircraft poll provider")
    minimum = 10 if provider == "opensky" else 5
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < minimum:
        raise ValueError(f"{provider} poll interval must be at least {minimum} seconds")

def _stop_source(source: str) -> None:
    global _opensky_feed, _dump1090_feed, _adsbexchange_feed, _rtlsdr_feed, _rtlsdr_last_snapshot
    if source == _active_source and _satpc32_dde:
        _satpc32_dde.clear()
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
    elif source == "rtlsdr":
        if _rtlsdr_feed:
            _rtlsdr_feed.stop()
            _rtlsdr_last_snapshot = _rtlsdr_feed.snapshot()
            _rtlsdr_feed = None
        _replace_aircraft_cache([])
        _set_source_status("rtlsdr", "stopped")


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
    if source == "rtlsdr":
        if _rtlsdr_startup_attempted and _rtlsdr_device_index is None:
            return False
        return _start_rtlsdr_feed()
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
    _emit_aircraft_update(try_auto_lock=False)


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
    if _rtlsdr_feed:
        _stop_source("rtlsdr")
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
        _replace_aircraft_cache(aircraft, source="adsbexchange")
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
        _replace_aircraft_cache(aircraft, source="opensky")
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

def _aircraft_metadata(ac: Aircraft) -> dict:
    return {key: getattr(ac, key) for key in (
        "category", "type_code", "registration", "description", "squawk", "emergency",
        "db_flags", "vertical_rate_ms", "on_ground", "position_updated_epoch_s",
        "last_seen_epoch_s",
    )} | {"position_source": ac.position_source or _active_source, "heading_deg": ac.heading_deg}


def _locked_aircraft_info(ac: Aircraft, geo: dict) -> dict:
    return {
        **_aircraft_metadata(ac),
        "icao24": ac.icao24,
        "callsign": ac.callsign or ac.icao24,
        "altitude_ft": round((ac.altitude_m or 0) * 3.28084),
        "range_a_km": geo["range_a_km"],
        "range_b_km": geo.get("range_b_km"),
        "az_a": geo["az_a"],
        "el_a": geo["el_a"],
        "az_b": geo["az_b"],
        "el_b": geo["el_b"],
        "scatter_score": geo["scatter_score"],
        "off_path_deg": geo["off_path_deg"],
        "position_updated_epoch_s": ac.position_updated_epoch_s,
        "position_source": ac.position_source or _active_source,
        "position_max_age_s": _position_max_age_s(ac),
        "state": supervisor.session.state.value,
    }


def _do_lock(icao24: str, *, manual: bool = True) -> dict | None:
    """Lock onto a specific aircraft (by icao24). Returns lock info or None."""
    global _locked_aircraft_id, _lock_score, supervisor
    global _manual_aircraft_lock, _manual_lock_source, _manual_lock_last_seen
    with _qso_lock:
        station_geometry = (
            _station_a_locator, _station_b_locator, _station_a_alt_m, _station_b_alt_m,
        )
    locator_a, locator_b, altitude_a, altitude_b = station_geometry
    if not locator_a or not locator_b:
        return None

    try:
        lat_a, lon_a = locator_to_latlon(locator_a)
        lat_b, lon_b = locator_to_latlon(locator_b)
    except Exception:
        return None

    with _aircraft_lock:
        ac = next((a for a in _aircraft_cache if a.icao24 == icao24), None)
    if ac is None or not ac.has_position:
        return None
    if not scatter_eligible(ac):
        raise ValueError("Ground or surface targets cannot be selected for air scatter")

    # Compute scatter score
    geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, altitude_a, altitude_b)  # type: ignore
    if not within_qso_window(geo, distinct_stations=(lat_a, lon_a) != (lat_b, lon_b)):
        raise ValueError("Selected aircraft is outside the current mutual QSO window")

    # Build track (current pos + projections)
    track = build_track(ac, lat_a, lon_a, n_samples=6, sample_interval_s=15.0, station_alt_m=altitude_a)

    # Replace the aircraft session atomically with respect to QSO commands.
    from airwsjtx.state_machine import SessionState
    from airwsjtx.supervisor import AirWSJTXSupervisor, SupervisorConfig
    with _qso_lock:
        if station_geometry != (
            _station_a_locator, _station_b_locator, _station_a_alt_m, _station_b_alt_m,
        ):
            return None
        with _aircraft_lock:
            if not any(candidate is ac for candidate in _aircraft_cache):
                return None
        new_supervisor = AirWSJTXSupervisor(config=SupervisorConfig(minimum_score_to_lock=0.05))
        station_a = Station(callsign=_station_a_call or "DE", locator=locator_a)
        station_b = Station(callsign=_station_b_call or "DX", locator=locator_b)
        hint = new_supervisor.begin_session(
            station_a=station_a,
            station_b=station_b,
            band_mhz=_wsjtx_band_mhz or 0.0,
            frequency_hz=_wsjtx_freq_hz or 0,
            tracks=[track],
            allow_unconfigured_tracking=True,
        )
        previous_progress = _qso_assistant.snapshot()
        if previous_progress.stage not in {"idle", "logged", "aborted", "finished"}:
            progress = _qso_assistant.finish(
                aborted=True,
                message="Aircraft selection changed; QSO control disarmed. Stop WSJT-X Tx if needed.",
            )
        else:
            progress = previous_progress
        if supervisor.session.state not in (SessionState.IDLE, SessionState.ABORTED, SessionState.COMPLETED):
            try:
                supervisor.finish_qso(abort=True)
            except ValueError:
                pass
        supervisor = new_supervisor
        _locked_aircraft_id = icao24
        _lock_score = geo["scatter_score"]
        _manual_aircraft_lock = manual
        _manual_lock_source = _active_source
        _manual_lock_last_seen = time.monotonic()
    if progress != previous_progress:
        _emit_qso_progress(progress)
    _publish_dde_target(ac, geo)

    # Point rotator immediately (manual pointing mode ignores plane positions)
    _publish_hamlib_target(ac, geo)

    info = {**_locked_aircraft_info(ac, geo), "hint": hint}
    _emit({"event": "plane_locked", "data": info})
    return info


def _do_unlock() -> None:
    global _locked_aircraft_id, _lock_score
    global _manual_aircraft_lock, _manual_lock_source
    with _qso_lock:
        _locked_aircraft_id = None
        _lock_score = 0.0
        _manual_aircraft_lock = False
        _manual_lock_source = None
        progress = _qso_assistant.finish(
            aborted=True,
            message="Aircraft unlocked; QSO control disarmed. Stop WSJT-X Tx if needed.",
        )
        try:
            supervisor.finish_qso(abort=True)
        except ValueError:
            pass
    if _satpc32_dde:
        _satpc32_dde.clear()
    _emit_qso_progress(progress)
    _emit({"event": "plane_unlocked", "data": {}})


def _select_auto_lock_candidate(aircraft: list[Aircraft], *, filters: dict | None = None) -> Aircraft | None:
    """Return the best candidate using the same filtered geometry path as the UI."""
    if not _station_a_locator or not _station_b_locator:
        return None

    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator)
    except Exception:
        return None

    best_ac: Aircraft | None = None
    best_key: tuple | None = None
    filters = dict(_aircraft_filters) if filters is None else filters
    method = filters.get("ranking_method", "legacy")
    now = time.time()
    for ac in aircraft:
        if not ac.has_position or not scatter_eligible(ac) or not _aircraft_matches_filters(ac, lat_a, lon_a, lat_b, lon_b, filters=filters):
            continue
        if filters.get("priority_flight") and not _matches_priority_flight(ac, filters=filters):
            continue
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore[arg-type]
        if not within_qso_window(geo, distinct_stations=(lat_a, lon_a) != (lat_b, lon_b)):
            continue
        score = float(geo.get("scatter_score", 0.0))
        rank = rank_geometry(ac, (lat_a, lon_a, _station_a_alt_m),
                             (lat_b, lon_b, _station_b_alt_m), method, score, now)
        rank = apply_scatter_preferences(rank, ac, now, method, enabled=filters.get("scatter_metadata", True))
        key = ranking_key(score, rank, method)
        if score >= 0.05 and (best_key is None or key > best_key
                             or (key == best_key and best_ac is not None and ac.icao24 < best_ac.icao24)):
            best_key = key
            best_ac = ac

    return best_ac


def _try_auto_lock(aircraft: list[Aircraft], *, filters: dict | None = None) -> None:
    """Called on every aircraft update. Auto-locks the best plane if enabled."""
    if not _auto_lock_enabled:
        return
    from airwsjtx.state_machine import SessionState
    # Only auto-lock from idle/completed/aborted states
    if supervisor.session.state not in (SessionState.IDLE, SessionState.COMPLETED,
                                         SessionState.ABORTED):
        return

    best_ac = _select_auto_lock_candidate(aircraft, filters=filters)
    if best_ac:
        _do_lock(best_ac.icao24, manual=False)


def _update_rotator_for_locked(aircraft: list[Aircraft]) -> None:
    """After a lock, track the locked aircraft's updated position with the rotator."""
    if not _locked_aircraft_id or not _station_a_locator:
        return
    ac = next((a for a in aircraft if a.icao24 == _locked_aircraft_id), None)
    if ac is None:
        return
    if not scatter_eligible(ac):
        _emit({"event": "plane_out_of_window", "data": {"icao24": _locked_aircraft_id}})
        _do_unlock()
        return
    if not ac.has_position:
        return

    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator or _station_a_locator)
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore
    except Exception:
        return

    if not within_qso_window(geo, distinct_stations=(lat_a, lon_a) != (lat_b, lon_b)):
        _emit({"event": "plane_out_of_window", "data": {"icao24": _locked_aircraft_id}})
        _do_unlock()
        return

    _publish_hamlib_target(ac, geo)
    _publish_dde_target(ac, geo)
    _emit({"event": "plane_tracking", "data": {
        **_locked_aircraft_info(ac, geo),
        "rotator_az": _rotator.current_position.azimuth if _rotator and _rotator.current_position else None,
        "rotator_el": _rotator.current_position.elevation if _rotator and _rotator.current_position else None,
    }})


def _dde_status() -> dict:
    reference = {
        "reference": "MY station (A)",
        "station_grid": _station_a_locator,
        "station_altitude_m": _station_a_alt_m,
        "azimuth_units": "degrees clockwise from true north",
        "elevation_units": "degrees above the radio horizon (4/3 Earth)",
        "name_field": "flight identifier/callsign, or ICAO address if unavailable",
    }
    if _satpc32_dde:
        return {**_satpc32_dde.status(), **reference}
    return {"supported": sys.platform == "win32", "enabled": False,
            "service": "SatPC32", "topic": "SatPcDdeConv", "item": "SatPcDdeItem",
            "target": "** NO SATELLITE **", "error": None, **reference}

def _output_status() -> dict:
    with _output_lock:
        return {"mode": _output_mode, "manual": _manual_pointing,
                "hamlib_connected": bool(_rotator and _rotator.connected),
                "dde": _dde_status()}


def _set_output_mode(mode: str) -> None:
    global _output_mode, _rotator_control_enabled, _satpc32_dde
    if mode not in ("off", "hamlib", "dde"):
        raise ValueError("Output mode must be off, hamlib, or dde")
    with _output_lock:
        if mode == _output_mode and ((mode != "dde" and _satpc32_dde is None) or
                (mode == "dde" and _satpc32_dde and _satpc32_dde.status()["enabled"])):
            return
        previous_mode = _output_mode
        _output_mode, _rotator_control_enabled = "off", False
        if previous_mode == "hamlib" and mode != "hamlib" and _rotator:
            # Selecting Off or DDE must release rotctld immediately.  Merely
            # suppressing coordinate output leaves the TCP client connected and
            # prevents another station/application from taking control.
            _rotator.stop()
        if _satpc32_dde:
            _satpc32_dde.clear()
            _satpc32_dde.stop()
            _satpc32_dde = None
        if mode == "dde":
            server = SatPC32DDE(on_error=lambda message: _emit({
                "event": "rotator_error", "data": {"message": f"SatPC32 DDE: {message}"},
            }))
            server.start()
            _satpc32_dde = server
        _output_mode, _rotator_control_enabled = mode, mode != "off"
        if mode == "dde":
            _prime_dde_target()


def _publish_hamlib_target(ac: Aircraft, geo: dict) -> None:
    with _output_lock:
        if (_output_mode != "hamlib" or _manual_pointing or
                not _rotator or not _rotator.connected or not scatter_eligible(ac) or _position_valid_for_s(ac) <= 0):
            return
        az, el = float(geo["az_a"]), float(geo["el_a"])
        if math.isfinite(az) and math.isfinite(el) and 0 <= el <= 90:
            _rotator.set_position(az, el)


def _prime_dde_target() -> None:
    """Publish the existing selection on enable, without Hamlib or lock side effects."""
    if not _locked_aircraft_id or not _station_a_locator:
        return
    ac = next((ac for ac in _current_aircraft_snapshot()
               if ac.icao24 == _locked_aircraft_id), None)
    if ac is None or not ac.has_position or not scatter_eligible(ac):
        return
    lat_a, lon_a = locator_to_latlon(_station_a_locator)
    lat_b, lon_b = locator_to_latlon(_station_b_locator or _station_a_locator)
    geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon,
                           ac.altitude_m, _station_a_alt_m, _station_b_alt_m)
    if not within_qso_window(geo, distinct_stations=(lat_a, lon_a) != (lat_b, lon_b)):
        if _satpc32_dde:
            _satpc32_dde.clear()
        return
    _publish_dde_target(ac, geo)


def _publish_dde_target(ac: Aircraft, geo: dict) -> None:
    with _output_lock:
        _publish_dde_target_locked(ac, geo)


def _publish_dde_target_locked(ac: Aircraft, geo: dict) -> None:
    if not _satpc32_dde:
        return
    if _output_mode != "dde" or not _rotator_control_enabled or _manual_pointing or not ac.has_position or not scatter_eligible(ac):
        _satpc32_dde.clear()
        return
    remaining = _position_valid_for_s(ac)
    az, el = float(geo["az_a"]), float(geo["el_a"])
    if remaining <= 0 or not math.isfinite(az) or not math.isfinite(el) or not 0 <= el <= 90:
        _satpc32_dde.clear()
        return
    name = "".join((ac.callsign or "").upper().split())
    if not name or len(name) > 32 or not all(c.isascii() and (c.isalnum() or c == "-") for c in name):
        name = ac.icao24
    _satpc32_dde.update(name, az, el, valid_for_s=remaining)


def _position_valid_for_s(ac: Aircraft) -> float:
    stamp = ac.position_updated_epoch_s
    if not ac.has_position or stamp is None or not math.isfinite(stamp):
        return 0
    max_age = _position_max_age_s(ac)
    return max(0, max_age - max(0, time.time() - stamp))


def _position_max_age_s(ac: Aircraft) -> float:
    source = ac.position_source or _active_source
    return POSITION_RETENTION_S if source == "rtlsdr" else _poll_intervals().get(source, 30) + 5


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
    if _rtlsdr_feed is not None:
        _stop_source("rtlsdr")
    if _dump1090_feed:
        _dump1090_feed.stop()
        _dump1090_feed = None

    def _on_update(aircraft: list[Aircraft]) -> None:
        _set_source_status("dump1090", "connected")
        _replace_aircraft_cache(aircraft, source="dump1090")
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


def _matches_priority_flight(ac: Aircraft, *, filters: dict | None = None) -> bool:
    filters = _aircraft_filters if filters is None else filters
    priority_flight = str(filters.get("priority_flight") or "").strip().upper()
    return bool(priority_flight and ac.callsign and ac.callsign.strip().upper() == priority_flight)


def _aircraft_matches_filters(ac: Aircraft, lat_a: float, lon_a: float, lat_b: float, lon_b: float,
                              *, filters: dict | None = None) -> bool:
    if ac.lat is None or ac.lon is None or ac.altitude_m is None:
        return False

    filters = dict(_aircraft_filters) if filters is None else filters
    priority_flight = str(filters.get("priority_flight") or "").strip().upper()
    matches_priority_flight = _matches_priority_flight(ac, filters=filters)
    if filters.get("priority_flight_exclusive") and priority_flight:
        return matches_priority_flight
    if matches_priority_flight:
        return True

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
    path_bearings = (
        bearing_deg(lat_a, lon_a, lat_b, lon_b),
        bearing_deg(lat_b, lon_b, lat_a, lon_a),
    )
    if not path_unset:
        position_tol = filters.get("position_tolerance_deg")
        position_tol = 70.0 if position_tol is None else float(position_tol)
        deviations = (
            heading_delta_deg(bearing_deg(lat_a, lon_a, ac.lat, ac.lon), path_bearings[0]),
            heading_delta_deg(bearing_deg(lat_b, lon_b, ac.lat, ac.lon), path_bearings[1]),
        )
        if max(deviations) > abs(position_tol):
            return False

    if not path_unset and ac.heading_deg is not None and ac.velocity_ms not in (None, 0):
        tolerance = filters.get("heading_tolerance_deg")
        tolerance = 30.0 if tolerance is None else float(tolerance)
        if not all(heading_within_tolerance(bearing, ac.heading_deg, tolerance) for bearing in path_bearings):
            return False

    return True


def _get_scatter_aircraft() -> list[dict]:
    """Compute scatter geometry for all cached aircraft and return sorted list."""
    return _get_scatter_aircraft_with_total()[0]


def _get_scatter_aircraft_with_total(*, apply_filters: bool = True, filters: dict | None = None,
                                    now: float | None = None) -> tuple[list[dict], int]:
    filters = dict(_aircraft_filters) if filters is None else filters
    method = filters.get("ranking_method", "legacy")
    now = time.time() if now is None else now
    snapshot = _current_aircraft_snapshot()
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
    for ac in snapshot:
        if not ac.has_position or not scatter_eligible(ac):
            continue
        total += 1
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m, _station_a_alt_m, _station_b_alt_m)  # type: ignore[arg-type]
        if apply_filters and not _aircraft_matches_filters(ac, lat_a, lon_a, lat_b, lon_b, filters=filters):
            continue
        results.append({
            **_aircraft_metadata(ac),
            "icao24": ac.icao24,
            "callsign": ac.callsign or ac.icao24,
            "lat": round(ac.lat, 4),
            "lon": round(ac.lon, 4),
            "altitude_m": round(ac.altitude_m),
            "altitude_ft": round(ac.altitude_m * 3.28084),
            "velocity_kts": round(ac.velocity_ms * 1.94384) if ac.velocity_ms else None,
            "heading_deg": round(ac.heading_deg) if ac.heading_deg is not None else None,
            **geo,
            **apply_scatter_preferences(
                rank_geometry(ac, (lat_a, lon_a, _station_a_alt_m),
                              (lat_b, lon_b, _station_b_alt_m), method, geo["scatter_score"], now),
                ac, now, method, enabled=filters.get("scatter_metadata", True)),
        })

    results.sort(key=lambda row: row["icao24"])
    results.sort(key=lambda row: ranking_key(row["scatter_score"], row, method), reverse=True)
    return results, total


def _emit_aircraft_update(*, try_auto_lock: bool = True) -> None:
    global _manual_lock_last_seen
    filters = dict(_aircraft_filters)
    now = time.time()
    age_s = _aircraft_cache_age_s()
    freshness = _aircraft_cache_freshness(age_s)
    aircraft, total = _get_scatter_aircraft_with_total(filters=filters, now=now)
    flight_data, _ = _get_scatter_aircraft_with_total(apply_filters=False, filters=filters, now=now)
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
            "flight_data": flight_data,
            "total": total,
            "station_a": _station_a_locator,
            "station_b": _station_b_locator,
            "station_a_alt_m": _station_a_alt_m,
            "station_b_alt_m": _station_b_alt_m,
            "active_source": active_source,
            "last_update_epoch_s": last_update_epoch_s,
            "cache_age_s": round(age_s, 1) if age_s is not None else None,
            "data_freshness": freshness,
            "ranking_method": filters.get("ranking_method", "legacy"),
        },
    })
    # Update tracking or try auto-lock
    snapshot = _current_aircraft_snapshot()
    if _locked_aircraft_id:
        locked_record = next((ac for ac in snapshot if ac.icao24 == _locked_aircraft_id), None)
        if _manual_aircraft_lock:
            if active_source != _manual_lock_source or (locked_record and locked_record.on_ground):
                _do_unlock()
                if try_auto_lock:
                    _try_auto_lock(snapshot, filters=filters)
                return
            if locked_record is not None:
                _manual_lock_last_seen = max(_manual_lock_last_seen, locked_record.last_seen)
            retention = AIRCRAFT_RETENTION_S if active_source == "rtlsdr" else _aircraft_cache_expire_after_s
            if time.monotonic() - _manual_lock_last_seen > retention:
                _do_unlock()
                if try_auto_lock:
                    _try_auto_lock(snapshot, filters=filters)
                return
            if locked_record is None or not locked_record.has_position:
                if _satpc32_dde:
                    _satpc32_dde.clear()
            else:
                _update_rotator_for_locked(snapshot)
            if not _locked_aircraft_id and try_auto_lock:
                _try_auto_lock(snapshot, filters=filters)
            return
        local_position_gap = (active_source == "rtlsdr" and locked_record is not None
                              and not locked_record.on_ground and not locked_record.has_position)
        if local_position_gap:
            # Keep selection, but never refresh pointing with an expired CPR fix.
            if _satpc32_dde:
                _satpc32_dde.clear()
        elif any(ac["icao24"] == _locked_aircraft_id for ac in aircraft):
            _update_rotator_for_locked(snapshot)
        else:
            _do_unlock()
    if not _locked_aircraft_id and try_auto_lock:
        _try_auto_lock(snapshot, filters=filters)


def _on_wsjtx_status(st: WSJTXStatus) -> None:
    global _latest_wsjtx_status
    global _station_a_locator, _station_b_locator, _station_a_call, _wsjtx_freq_hz, _wsjtx_band_mhz
    global _wsjtx_de_call, _wsjtx_de_grid
    observed_at = time.monotonic()
    de_grid = (st.de_grid or "").strip().upper()
    dx_grid = (st.dx_grid or "").strip().upper()
    with _qso_lock:
        source = _qso_assistant.active_source()
        if source is not None and (st.app_id, st.source_addr) != source:
            previous_progress = _qso_assistant.snapshot()
            progress = _qso_assistant.observe_status(st, observed_at=observed_at)
            if progress != previous_progress:
                _emit_qso_progress(progress)
            return
        _latest_wsjtx_status = st
        previous_locators = (_station_a_locator, _station_b_locator)
        _wsjtx_de_call = st.de_call
        _wsjtx_de_grid = de_grid if _STATION_GRID_RE.fullmatch(de_grid) else None
        if st.freq_hz:
            _wsjtx_freq_hz = st.freq_hz
            _wsjtx_band_mhz = st.freq_mhz
        if st.de_call and not _my_call_override:
            _station_a_call = st.de_call
        my_grid_changed = bool(
            _wsjtx_de_grid and not _my_grid_override and _wsjtx_de_grid != _station_a_locator
        )
        if my_grid_changed:
            _station_a_locator = _wsjtx_de_grid
        dx_grid_changed = bool(
            _STATION_GRID_RE.fullmatch(dx_grid) and not _dx_grid_manual and dx_grid != _station_b_locator
        )
        if dx_grid_changed:
            _station_b_locator = dx_grid
        locators_changed = (_station_a_locator, _station_b_locator) != previous_locators
        previous_progress = _qso_assistant.snapshot()
        _qso_assistant.observe_status(st, observed_at=observed_at)
        progress = _send_qso_start_locked()
    if progress != previous_progress:
        _emit_qso_progress(progress)
    for changed in (my_grid_changed, dx_grid_changed):
        if not changed:
            continue
        with _source_status_lock:
            active_source = _active_source
        if active_source == "opensky":
            _start_opensky_feed()
        elif active_source == "adsbexchange":
            _start_adsbexchange_feed()
        else:
            _start_configured_airscout_feed()
    if locators_changed:
        _emit_aircraft_update(try_auto_lock=False)
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
            "tx_message": st.tx_message,
        },
    })


def _on_wsjtx_decode(dec: WSJTXDecode) -> None:
    with _qso_lock:
        previous_progress = _qso_assistant.snapshot()
        progress = _qso_assistant.observe_decode(dec)
    if progress != previous_progress:
        _emit_qso_progress(progress)
    _emit({
        "event": "wsjtx_decode",
        "data": {
            "message": dec.message,
            "time_ms": dec.time_ms,
            "snr": dec.snr,
            "delta_time_s": round(dec.delta_time_s, 1),
            "delta_freq_hz": dec.delta_freq_hz,
            "mode": dec.mode,
            "off_air": dec.off_air,
        },
    })


def _on_wsjtx_start_dx_tx_result(result: WSJTXStartDxTxResult) -> None:
    with _qso_lock:
        previous_progress = _qso_assistant.snapshot()
        progress = _qso_assistant.observe_start_result(result)
        if progress.stage in {"tx_enabled", "transmitting"} and not _qso_tracking_viable_locked():
            progress = _qso_assistant.suspend(
                "Aircraft is no longer viable; WSJT-X may have enabled Tx. Stop Tx if needed."
            )
    if progress != previous_progress:
        _emit_qso_progress(progress)


def _on_wsjtx_qso_logged(qso: WSJTXQsoLogged) -> None:
    from airwsjtx.state_machine import SessionState
    # WSJT-X is the authority on a finished QSO; close the app session to match
    completed = False
    with _qso_lock:
        previous_progress = _qso_assistant.snapshot()
        matched = _qso_assistant.complete_logged_qso(qso)
        progress = _qso_assistant.snapshot()
        if matched and supervisor.session.state == SessionState.ACTIVE_QSO:
            supervisor.finish_qso(abort=False)
            completed = True
        state = supervisor.session.state.value
    if progress != previous_progress:
        _emit_qso_progress(progress)
    _emit({
        "event": "qso_logged",
        "data": {
            "dx_call": qso.dx_call,
            "dx_grid": qso.dx_grid,
            "freq_hz": qso.freq_hz,
            "mode": qso.mode,
            "report_sent": qso.report_sent,
            "report_rcvd": qso.report_rcvd,
            "state": state,
            "session_completed": completed,
        },
    })


def _on_wsjtx_connected(app_id: str) -> None:
    _emit({"event": "wsjtx_connected", "data": {"app_id": app_id}})


def _on_wsjtx_disconnected() -> None:
    global _latest_wsjtx_status
    with _qso_lock:
        _latest_wsjtx_status = None
        previous_progress = _qso_assistant.snapshot()
        progress = _qso_assistant.suspend("WSJT-X disconnected; check Tx before re-arming")
    if progress != previous_progress:
        _emit_qso_progress(progress)
    _emit({"event": "wsjtx_disconnected", "data": {}})


def _on_wsjtx_error(message: str) -> None:
    with _qso_lock:
        previous_progress = _qso_assistant.snapshot()
        progress = _qso_assistant.suspend("WSJT-X connection error; check Tx before re-arming")
    if progress != previous_progress:
        _emit_qso_progress(progress)
    _emit({"event": "wsjtx_error", "data": {"message": message}})


def _shutdown_backend() -> None:
    global _output_mode, _rotator_control_enabled
    with _output_lock:
        _output_mode, _rotator_control_enabled = "off", False
        if _satpc32_dde:
            _satpc32_dde.clear()
    for name, resource in (
        ("DDE", _satpc32_dde), ("WSJT-X", _wsjtx_listener),
        ("Hamlib", _rotator), ("OpenSky", _opensky_feed),
        ("dump1090", _dump1090_feed), ("ADS-B Exchange", _adsbexchange_feed),
        ("RTL-SDR", _rtlsdr_feed),
    ):
        if resource:
            try:
                resource.stop()
            except Exception as exc:
                print(f"Cannot stop {name} during shutdown: {exc}", file=sys.stderr, flush=True)
    if _speech is not None:
        try:
            _speech.shutdown()
        except Exception as exc:
            print(f"Cannot stop Piper speech during shutdown: {exc}", file=sys.stderr, flush=True)


def main() -> None:
    global _wsjtx_listener
    _emit_source_status()
    listener = WSJTXListener(
        on_status=_on_wsjtx_status,
        on_decode=_on_wsjtx_decode,
        on_qso_logged=_on_wsjtx_qso_logged,
        on_start_dx_tx_result=_on_wsjtx_start_dx_tx_result,
        on_connected=_on_wsjtx_connected,
        on_disconnected=_on_wsjtx_disconnected,
        on_error=_on_wsjtx_error,
    )
    _wsjtx_listener = listener
    listener.start()

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                _err(None, f"Invalid JSON: {e}")
                continue
            if msg.get("method") == "shutdown":
                break
            handle(msg)
    finally:
        _shutdown_backend()


if __name__ == "__main__":
    main()
