"""
Callsign lookup — resolves callsign → (name, grid, lat, lon).

Priority:
  1. Built-in known stations (instant, no network)
  2. callook.info (US callsigns, free JSON API)
  3. qrzcq.com scrape (international, no auth)
  4. Infer grid from call area prefix as last resort
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class StationInfo:
    callsign: str
    name: str | None
    grid: str | None          # Maidenhead (4 or 6 char)
    lat: float | None
    lon: float | None
    country: str | None
    source: str               # where the data came from


# ─── Offline callsign/grid database ────────────────────────────────────────────

DEFAULT_DB_URL = "https://ad7np.us/airwsjtx/callsign-grid-db.json"
DEFAULT_DB_MANIFEST_URL = "https://ad7np.us/airwsjtx/callsign-grid-db-version.json"
DEFAULT_CONTRIBUTION_URL = "https://ad7np.us/airwsjtx/callsign-grid-submit"
DB_FRESHNESS_DAYS = 30

_LOCAL_DB_CACHE: dict[str, StationInfo] = {}
_LOCAL_DB_META: dict[str, Any] = {}
_LOCAL_DB_LOADED = False
_LOCAL_DB_LOCK = threading.Lock()


def _data_dir() -> Path:
    override = os.environ.get("AIRWSJTX_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".airwsjtx"


def _db_file() -> Path:
    return _data_dir() / "callsign_grid_db.json"


def _meta_file() -> Path:
    return _data_dir() / "callsign_grid_db_meta.json"


def _contrib_file() -> Path:
    return _data_dir() / "callsign_grid_contrib.json"


def _ensure_data_dir() -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    _ensure_data_dir()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _coerce_station(callsign: str, raw: dict[str, Any], source: str) -> StationInfo:
    lat_raw = raw.get("lat")
    lon_raw = raw.get("lon")
    lat = float(lat_raw) if lat_raw not in (None, "") else None
    lon = float(lon_raw) if lon_raw not in (None, "") else None
    return StationInfo(
        callsign=callsign.upper(),
        name=str(raw["name"]) if raw.get("name") else None,
        grid=str(raw["grid"]) if raw.get("grid") else None,
        lat=lat,
        lon=lon,
        country=str(raw["country"]) if raw.get("country") else None,
        source=source,
    )


def _parse_db_entries(raw: object) -> dict[str, StationInfo]:
    parsed: dict[str, StationInfo] = {}
    if not isinstance(raw, dict):
        return parsed
    for callsign, payload in raw.items():
        if not isinstance(callsign, str) or not isinstance(payload, dict):
            continue
        station = _coerce_station(callsign, payload, "local-db")
        parsed[station.callsign] = station
    return parsed


def _load_local_db() -> None:
    global _LOCAL_DB_LOADED, _LOCAL_DB_CACHE, _LOCAL_DB_META
    with _LOCAL_DB_LOCK:
        if _LOCAL_DB_LOADED:
            return
        _LOCAL_DB_LOADED = True
        _LOCAL_DB_CACHE = {}
        _LOCAL_DB_META = {}
        db_path = _db_file()
        meta_path = _meta_file()
        try:
            if db_path.exists():
                payload = json.loads(db_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    _LOCAL_DB_CACHE = _parse_db_entries(payload.get("entries"))
                    version = payload.get("version")
                    if version:
                        _LOCAL_DB_META["version"] = str(version)
        except Exception:
            _LOCAL_DB_CACHE = {}
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta, dict):
                    _LOCAL_DB_META.update(meta)
        except Exception:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _current_db_status() -> dict[str, Any]:
    _load_local_db()
    with _LOCAL_DB_LOCK:
        meta = dict(_LOCAL_DB_META)
        entry_count = len(_LOCAL_DB_CACHE)
    checked_at = _parse_iso(meta.get("downloaded_at"))
    stale_after = datetime.now(timezone.utc) - timedelta(days=DB_FRESHNESS_DAYS)
    is_stale = checked_at is None or checked_at < stale_after
    return {
        "db_url": str(meta.get("db_url") or DEFAULT_DB_URL),
        "version": meta.get("version"),
        "downloaded_at": meta.get("downloaded_at"),
        "entries": entry_count,
        "fresh": not is_stale,
        "freshness_days": DB_FRESHNESS_DAYS,
    }


def get_db_status() -> dict[str, Any]:
    return _current_db_status()


def _download_json(url: str, timeout_s: int = 10) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "AirWSJT-X/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read())
    if not isinstance(payload, dict):
        raise ValueError("Database payload must be a JSON object")
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("Database payload missing entries")
    return payload


def _download_manifest(url: str, timeout_s: int = 8) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "AirWSJT-X/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read())
    if not isinstance(payload, dict):
        raise ValueError("Manifest payload must be a JSON object")
    return payload


def download_db(url: str = DEFAULT_DB_URL, timeout_s: int = 10) -> dict[str, Any]:
    payload = _download_json(url, timeout_s=timeout_s)
    parsed_entries = _parse_db_entries(payload.get("entries"))
    version = payload.get("version")
    if version is None:
        version = _now_iso()
        payload["version"] = version
    meta = {
        "version": str(version),
        "downloaded_at": _now_iso(),
        "db_url": url,
    }
    _write_json(_db_file(), payload)
    _write_json(_meta_file(), meta)
    global _LOCAL_DB_LOADED, _LOCAL_DB_CACHE, _LOCAL_DB_META
    with _LOCAL_DB_LOCK:
        _LOCAL_DB_LOADED = True
        _LOCAL_DB_CACHE = parsed_entries
        _LOCAL_DB_META = dict(meta)
    return _current_db_status()


def check_db_update(
    url: str = DEFAULT_DB_URL,
    timeout_s: int = 8,
    manifest_url: str = DEFAULT_DB_MANIFEST_URL,
) -> dict[str, Any]:
    status = _current_db_status()
    result = dict(status)
    result["online"] = False
    result["remote_version"] = None
    result["update_available"] = False
    result["checked_at"] = _now_iso()
    try:
        manifest = _download_manifest(manifest_url, timeout_s=timeout_s)
        remote_version = manifest.get("version")
        remote_entries = manifest.get("entries")
        if remote_version is None:
            remote_version = result["checked_at"]
        if remote_entries is None:
            remote_entries = 0
        remote_version_str = str(remote_version)
        result["online"] = True
        result["remote_version"] = remote_version_str
        result["remote_entries"] = int(remote_entries)
        result["update_available"] = remote_version_str != (status.get("version") or "")
    except Exception as manifest_exc:
        try:
            payload = _download_json(url, timeout_s=timeout_s)
            remote_version = payload.get("version")
            if remote_version is None:
                remote_version = result["checked_at"]
            remote_version_str = str(remote_version)
            result["online"] = True
            result["remote_version"] = remote_version_str
            result["remote_entries"] = len(_parse_db_entries(payload.get("entries")))
            result["update_available"] = remote_version_str != (status.get("version") or "")
        except Exception:
            result["error"] = str(manifest_exc)
    return result


def submit_grid_contribution(
    callsign: str,
    grid: str,
    *,
    name: str | None = None,
    country: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    submit_url: str = DEFAULT_CONTRIBUTION_URL,
    timeout_s: int = 8,
) -> dict[str, Any]:
    call = callsign.strip().upper()
    grid_norm = grid.strip().upper()
    if not call:
        raise ValueError("callsign required")
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}(?:[A-X]{2})?", grid_norm):
        raise ValueError("grid must be Maidenhead 4 or 6 characters")
    payload: dict[str, Any] = {
        "callsign": call,
        "grid": grid_norm,
        "name": name.strip() if isinstance(name, str) and name.strip() else None,
        "country": country.strip() if isinstance(country, str) and country.strip() else None,
        "lat": lat,
        "lon": lon,
        "submitted_at": _now_iso(),
    }
    def _post(data: dict[str, Any]) -> tuple[bool, str | None]:
        try:
            req = urllib.request.Request(
                submit_url,
                method="POST",
                data=json.dumps(data).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "AirWSJT-X/0.1",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout_s):
                return True, None
        except Exception as exc:
            return False, str(exc)

    accepted, error = _post(payload)
    queued: list[dict[str, Any]] = []
    q_path = _contrib_file()
    try:
        if q_path.exists():
            existing = json.loads(q_path.read_text(encoding="utf-8"))
            if isinstance(existing, list):
                queued = [e for e in existing if isinstance(e, dict)]
    except Exception:
        queued = []

    if accepted:
        if queued:
            still_pending: list[dict[str, Any]] = []
            for item in queued:
                ok, _ = _post(item)
                if not ok:
                    still_pending.append(item)
            queued = still_pending
        if queued:
            _write_json(q_path, queued)
        elif q_path.exists():
            q_path.unlink()
    else:
        queued.append(payload)
        _write_json(q_path, queued)
    return {
        "accepted": accepted,
        "queued": len(queued),
        "error": error,
    }


# ─── Known stations (pre-seeded) ─────────────────────────────────────────────

_KNOWN: dict[str, StationInfo] = {
    "VE7NX": StationInfo("VE7NX", "Neil Olson", "CN89oh", 49.300, -122.810, "Canada", "known"),
    "W7PUA": StationInfo("W7PUA", "Bob Larkin", "CN84jq", 44.677, -123.227, "USA", "known"),
}


# ─── callook.info (US callsigns) ─────────────────────────────────────────────

def _callook(callsign: str) -> StationInfo | None:
    url = f"https://callook.info/{callsign.upper()}/json"
    try:
        import json
        req = urllib.request.Request(url, headers={"User-Agent": "AirWSJT-X/0.1"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d: dict[str, Any] = json.loads(resp.read())
        if d.get("status") != "VALID":
            return None
        loc = d.get("location", {})
        lat = float(loc["latitude"]) if loc.get("latitude") else None
        lon = float(loc["longitude"]) if loc.get("longitude") else None
        grid = loc.get("gridsquare") or (_latlon_to_grid(lat, lon) if lat and lon else None)
        name_parts = d.get("name", {})
        name = (
            f"{name_parts.get('first','')} {name_parts.get('last','')}".strip()
            if isinstance(name_parts, dict)
            else str(name_parts)
        ) or None
        return StationInfo(
            callsign=callsign.upper(),
            name=name,
            grid=grid,
            lat=lat,
            lon=lon,
            country="USA",
            source="callook.info",
        )
    except Exception:
        return None


# ─── qrzcq.com scrape (international) ────────────────────────────────────────

def _qrzcq(callsign: str) -> StationInfo | None:
    url = f"https://www.qrzcq.com/call/{callsign.upper()}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AirWSJT-X/0.1"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", "ignore")
        lat_m = re.search(r'"latitude":\s*"([0-9.-]+)"', html)
        lon_m = re.search(r'"longitude":\s*"([0-9.-]+)"', html)
        grid_m = re.search(r"Grid.*?([A-R]{2}[0-9]{2}[a-x]{2})", html, re.I | re.S)
        name_m = re.search(r'<title>[^<]*?([A-Z0-9/]+)\s*-\s*([^<]+)</title>', html)
        lat = float(lat_m.group(1)) if lat_m else None
        lon = float(lon_m.group(1)) if lon_m else None
        grid = grid_m.group(1) if grid_m else (_latlon_to_grid(lat, lon) if lat and lon else None)
        name = name_m.group(2).strip() if name_m else None
        if not lat and not grid:
            return None
        return StationInfo(
            callsign=callsign.upper(),
            name=name,
            grid=grid,
            lat=lat,
            lon=lon,
            country=None,
            source="qrzcq.com",
        )
    except Exception:
        return None


# ─── Lat/lon → Maidenhead ─────────────────────────────────────────────────────

def _latlon_to_grid(lat: float, lon: float) -> str:
    lon2 = lon + 180
    lat2 = lat + 90
    field_lon = chr(ord("A") + int(lon2 / 20))
    field_lat = chr(ord("A") + int(lat2 / 10))
    sq_lon = int((lon2 % 20) / 2)
    sq_lat = int(lat2 % 10)
    sub_lon = chr(ord("a") + int((lon2 % 2) / (2 / 24)))
    sub_lat = chr(ord("a") + int((lat2 % 1) / (1 / 24)))
    return f"{field_lon}{field_lat}{sq_lon}{sq_lat}{sub_lon}{sub_lat}"


# ─── Call-prefix fallback ─────────────────────────────────────────────────────

_PREFIX_GRID: dict[str, str] = {
    # US call areas (approximate grid centres)
    "W1": "FN42", "K1": "FN42", "N1": "FN42",
    "W2": "FN20", "K2": "FN20", "N2": "FN20",
    "W3": "FM19", "K3": "FM19",
    "W4": "EM73", "K4": "EM73",
    "W5": "EM30", "K5": "EM30",
    "W6": "DM04", "K6": "DM04",
    "W7": "CN87", "K7": "CN87",
    "W8": "EN82", "K8": "EN82",
    "W9": "EN61", "K9": "EN61",
    "W0": "DN70", "K0": "DN70",
    # Canada
    "VE1": "FN74", "VA1": "FN74",
    "VE2": "FN35", "VA2": "FN35",
    "VE3": "EN93", "VA3": "EN93",
    "VE4": "EN19", "VA4": "EN19",
    "VE5": "DO33", "VA5": "DO33",
    "VE6": "DO33",
    "VE7": "CN89", "VA7": "CN89",
    "VE8": "CP18",
    # Europe
    "G": "IO91", "GM": "IO88",
    "DL": "JO51", "OK": "JO70",
    "F": "JN03", "I": "JN53",
    "PA": "JO22", "SM": "JP80",
    "OH": "KP30", "OZ": "JO55",
    "SP": "KO02", "HA": "JN97",
    "CT": "IM57",
    "EA": "IM98",
}


def _prefix_fallback(callsign: str) -> StationInfo | None:
    call = callsign.upper()
    for prefix, grid in _PREFIX_GRID.items():
        if call.startswith(prefix):
            return StationInfo(
                callsign=call,
                name=None,
                grid=grid,
                lat=None,
                lon=None,
                country=None,
                source="prefix-fallback",
            )
    return None


# ─── Public entry point ───────────────────────────────────────────────────────

def lookup(callsign: str) -> StationInfo:
    """Look up a callsign. Never raises — returns best-effort result."""
    call = callsign.strip().upper()

    # 1. Local downloaded DB
    _load_local_db()
    cached = _LOCAL_DB_CACHE.get(call)
    if cached:
        return cached

    # 2. Known stations
    if call in _KNOWN:
        return _KNOWN[call]

    # 3. callook.info (works well for US)
    result = _callook(call)
    if result:
        return result

    # 4. qrzcq scrape (international)
    result = _qrzcq(call)
    if result:
        return result

    # 5. Prefix fallback
    result = _prefix_fallback(call)
    if result:
        return result

    # 6. Unknown
    return StationInfo(callsign=call, name=None, grid=None, lat=None, lon=None,
                       country=None, source="not-found")
