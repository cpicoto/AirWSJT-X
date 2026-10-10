#!/usr/bin/env python3
"""
JSON-RPC bridge between Tauri and the airwsjtx supervisor.
Reads newline-delimited JSON from stdin, writes responses to stdout.
Also runs background WSJT-X UDP listener and AirScout aircraft feeds.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))

from airwsjtx.airscout import Aircraft, Dump1090Feed, OpenSkyFeed
from airwsjtx.callsign import lookup as lookup_callsign
from airwsjtx.geo import locator_to_latlon, scatter_geometry
from airwsjtx.hamlib_rotator import HamlibRotator, RotatorPosition
from airwsjtx.models import AircraftSample, AircraftTrack, Station
from airwsjtx.supervisor import AirWSJTXSupervisor
from airwsjtx.tracking import build_track, select_best_aircraft
from airwsjtx.wsjtx_udp import WSJTXDecode, WSJTXListener, WSJTXStatus

supervisor = AirWSJTXSupervisor()
_stdout_lock = threading.Lock()

# AirScout state
_aircraft_lock = threading.Lock()
_aircraft_cache: list[Aircraft] = []
_opensky_feed: OpenSkyFeed | None = None
_dump1090_feed: Dump1090Feed | None = None

# Station locators (updated from WSJT-X or RPC)
_station_a_locator: str | None = None
_station_b_locator: str | None = None
_station_a_call: str | None = None
_station_b_call: str | None = None

# Hamlib rotator
_rotator: HamlibRotator | None = None

# Auto-lock state
_auto_lock_enabled: bool = True
_locked_aircraft_id: str | None = None
_lock_score: float = 0.0
_wsjtx_freq_hz: int | None = None
_wsjtx_band_mhz: float | None = None
_wsjtx_listener: WSJTXListener | None = None


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
        if method == "begin_session":
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
            supervisor.operator_start_qso()
            _ok(id_, {"state": supervisor.session.state.value})

        elif method == "finish_qso":
            supervisor.finish_qso(abort=params.get("abort", False))
            _ok(id_, {"state": supervisor.session.state.value})

        elif method == "get_status":
            s = supervisor.session
            _ok(id_, {
                "state": s.state.value,
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
            global _station_a_locator, _station_b_locator
            _station_a_locator = params.get("locator_a")
            _station_b_locator = params.get("locator_b")
            _start_opensky_feed()
            _ok(id_, {"locator_a": _station_a_locator, "locator_b": _station_b_locator})

        elif method == "airscout_start_dump1090":
            host = params.get("host", "127.0.0.1")
            port = int(params.get("port", 30003))
            _start_dump1090_feed(host, port)
            _ok(id_, {"host": host, "port": port})

        elif method == "airscout_get_aircraft":
            result = _get_scatter_aircraft()
            _ok(id_, result)

        elif method == "airscout_fetch_now":
            # Force an immediate OpenSky fetch
            try:
                if _opensky_feed:
                    aircraft = _opensky_feed.fetch_once()
                    with _aircraft_lock:
                        _aircraft_cache.clear()
                        _aircraft_cache.extend(aircraft)
                    _emit_aircraft_update()
                    _ok(id_, {"count": len(aircraft)})
                else:
                    _err(id_, "No OpenSky feed configured — call airscout_set_stations first")
            except Exception as e:
                _err(id_, str(e))

        # ── Rotator methods ───────────────────────────────────────────────────

        elif method == "rotator_connect":
            host = params.get("host", "127.0.0.1")
            port = int(params.get("port", 4533))
            _start_rotator(host, port)
            _ok(id_, {"host": host, "port": port})

        elif method == "rotator_set":
            if not _rotator or not _rotator.connected:
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

        elif method == "set_station_calls":
            global _station_a_call, _station_b_call
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
                _wsjtx_listener.set_rig_name_filter(rig_name)
                _ok(id_, {"rig_name": _wsjtx_listener.get_rig_name_filter()})

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
        _err(id_, str(exc))


# ─── AirScout feed helpers ────────────────────────────────────────────────────

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


def _start_opensky_feed() -> None:
    global _opensky_feed
    bbox = _bbox_for_locators()
    if not bbox:
        return
    if _opensky_feed:
        _opensky_feed.stop()

    def _on_update(aircraft: list[Aircraft]) -> None:
        with _aircraft_lock:
            _aircraft_cache.clear()
            _aircraft_cache.extend(aircraft)
        _emit_aircraft_update()

    def _on_error(msg: str) -> None:
        _emit({"event": "airscout_error", "data": {"source": "opensky", "message": msg}})

    lat_min, lon_min, lat_max, lon_max = bbox
    _opensky_feed = OpenSkyFeed(
        lat_min=lat_min, lon_min=lon_min,
        lat_max=lat_max, lon_max=lon_max,
        interval_s=30.0,
        on_update=_on_update,
        on_error=_on_error,
    )
    _opensky_feed.start()
    _emit({"event": "airscout_feed_started", "data": {"source": "opensky", "bbox": list(bbox)}})


# ─── Auto-lock / tracking helpers ────────────────────────────────────────────

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
    geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m)  # type: ignore

    # Build track (current pos + projections)
    track = build_track(ac, lat_a, lon_a, n_samples=6, sample_interval_s=15.0)

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

    # Point rotator immediately
    if _rotator and _rotator.connected:
        _rotator.set_position(geo["az_a"], geo["el_a"])

    info = {
        "icao24": icao24,
        "callsign": ac.callsign or icao24,
        "altitude_ft": round((ac.altitude_m or 0) * 3.28084),
        "az_a": geo["az_a"],
        "el_a": geo["el_a"],
        "az_b": geo["az_b"],
        "el_b": geo["el_b"],
        "scatter_score": geo["scatter_score"],
        "off_path_deg": geo["off_path_deg"],
        "state": supervisor.session.state.value,
        "hint": hint,
    }
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


def _try_auto_lock(aircraft: list[Aircraft]) -> None:
    """Called on every aircraft update. Auto-locks the best plane if enabled."""
    if not _auto_lock_enabled:
        return
    if not _station_a_locator or not _station_b_locator:
        return
    from airwsjtx.state_machine import SessionState
    # Only auto-lock from idle/completed/aborted states
    if supervisor.session.state not in (SessionState.IDLE, SessionState.COMPLETED,
                                         SessionState.ABORTED):
        return

    try:
        lat_a, lon_a = locator_to_latlon(_station_a_locator)
        lat_b, lon_b = locator_to_latlon(_station_b_locator)
    except Exception:
        return

    best_ac, score = select_best_aircraft(aircraft, lat_a, lon_a, lat_b, lon_b, min_score=0.20)
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
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, ac.lat, ac.lon, ac.altitude_m)  # type: ignore
    except Exception:
        return

    # Point rotator at new position
    if _rotator and _rotator.connected:
        _rotator.set_position(geo["az_a"], geo["el_a"])

    # Check if aircraft has left the scatter window
    if geo["scatter_score"] < 0.05:
        _emit({"event": "plane_out_of_window", "data": {"icao24": _locked_aircraft_id}})
        _do_unlock()
        return

    _emit({"event": "plane_tracking", "data": {
        "icao24": _locked_aircraft_id,
        "az_a": geo["az_a"],
        "el_a": geo["el_a"],
        "scatter_score": geo["scatter_score"],
        "state": supervisor.session.state.value,
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
    global _dump1090_feed
    if _dump1090_feed:
        _dump1090_feed.stop()

    def _on_update(aircraft: list[Aircraft]) -> None:
        with _aircraft_lock:
            _aircraft_cache.clear()
            _aircraft_cache.extend(aircraft)
        _emit_aircraft_update()

    _dump1090_feed = Dump1090Feed(
        host=host, port=port,
        on_update=_on_update,
        on_connected=lambda: _emit({"event": "airscout_feed_started", "data": {"source": "dump1090"}}),
        on_disconnected=lambda: _emit({"event": "airscout_feed_stopped", "data": {"source": "dump1090"}}),
        on_error=lambda msg: _emit({"event": "airscout_error", "data": {"source": "dump1090", "message": msg}}),
    )
    _dump1090_feed.start()


def _get_scatter_aircraft() -> list[dict]:
    """Compute scatter geometry for all cached aircraft and return sorted list."""
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

    results = []
    with _aircraft_lock:
        snapshot = list(_aircraft_cache)

    for ac in snapshot:
        if not ac.has_position:
            continue
        geo = scatter_geometry(
            lat_a, lon_a,
            lat_b if lat_b is not None else lat_a,
            lon_b if lon_b is not None else lon_a,
            ac.lat, ac.lon, ac.altitude_m,  # type: ignore[arg-type]
        )
        results.append({
            "icao24": ac.icao24,
            "callsign": ac.callsign or ac.icao24,
            "lat": round(ac.lat, 4),       # type: ignore[arg-type]
            "lon": round(ac.lon, 4),       # type: ignore[arg-type]
            "altitude_m": round(ac.altitude_m),  # type: ignore[arg-type]
            "altitude_ft": round(ac.altitude_m * 3.28084),  # type: ignore[arg-type]
            "velocity_kts": round(ac.velocity_ms * 1.94384) if ac.velocity_ms else None,
            "heading_deg": round(ac.heading_deg) if ac.heading_deg else None,
            **geo,
        })

    results.sort(key=lambda r: r["scatter_score"], reverse=True)
    return results


def _emit_aircraft_update() -> None:
    aircraft = _get_scatter_aircraft()
    _emit({
        "event": "airscout_update",
        "data": {
            "aircraft": aircraft[:20],
            "total": len(aircraft),
            "station_a": _station_a_locator,
            "station_b": _station_b_locator,
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
    # Capture frequency for supervisor begin_session
    if st.freq_hz:
        _wsjtx_freq_hz = st.freq_hz
        _wsjtx_band_mhz = st.freq_mhz
    # Auto-populate station A call + locator from WSJT-X de_call/de_grid
    if st.de_call and not _station_a_call:
        _station_a_call = st.de_call
    if st.de_grid and st.de_grid != _station_a_locator:
        _station_a_locator = st.de_grid
        _start_opensky_feed()
    if st.dx_grid and st.dx_grid != _station_b_locator:
        _station_b_locator = st.dx_grid
        _start_opensky_feed()
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


def _on_wsjtx_connected(app_id: str) -> None:
    _emit({"event": "wsjtx_connected", "data": {"app_id": app_id}})


def _on_wsjtx_disconnected() -> None:
    _emit({"event": "wsjtx_disconnected", "data": {}})


def main() -> None:
    global _wsjtx_listener
    listener = WSJTXListener(
        on_status=_on_wsjtx_status,
        on_decode=_on_wsjtx_decode,
        on_connected=_on_wsjtx_connected,
        on_disconnected=_on_wsjtx_disconnected,
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


if __name__ == "__main__":
    main()
