import { invoke } from "@tauri-apps/api/core";
import { emit, listen } from "@tauri-apps/api/event";
import { useCallback, useEffect, useRef, useState } from "react";
import DarkSelect from "./DarkSelect";
import RotatorPanel from "./RotatorPanel";
import PlaneLockPanel from "./PlaneLockPanel";
import AircraftAccounts from "./AircraftAccounts";
import AzElReadout from "./AzElReadout";
import Dump1090Source, { Dump1090Host } from "./Dump1090Source";
import {
  FLOAT_ACTION_EVENT, FLOAT_CLOSED_EVENT, FLOAT_READY_EVENT, FLOAT_STATE_EVENT,
  nativeFloatSupported, syncFloatWindow,
  type FloatAction, type FloatKind, type FloatState,
} from "./lib/floatWindow";
import { bearingDeg, haversineKm, isValidLocator, locatorToLatlon } from "./lib/geo";
import SkyDome from "./SkyDome";
import { isSpeechSupported, speak, cancelSpeech } from "./lib/speech";

// ─── Types ────────────────────────────────────────────────────────────────────

interface WsjtxLive {
  connected: boolean;
  app_id: string;
  freq_mhz: number | null;
  mode: string | null;
  sub_mode: string | null;
  de_call: string | null;
  de_grid: string | null;
  dx_call: string | null;
  dx_grid: string | null;
  transmitting: boolean;
  decoding: boolean;
  tr_period: number | null;
  rx_df: number | null;
  tx_df: number | null;
  config_name: string | null;
}
interface WsjtxDecodeEntry {
  message: string | null;
  snr: number;
  delta_freq_hz: number;
  mode: string | null;
  ts: string;
}
const WSJTX_DAY_MS = 24 * 60 * 60 * 1000;
function formatWsjtxDecodeTime(value: unknown): string {
  if (typeof value !== "number"
      || !Number.isInteger(value)
      || value < 0
      || value >= WSJTX_DAY_MS) {
    return "—";
  }
  const totalSeconds = Math.floor(value / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map(part => String(part).padStart(2, "0")).join("");
}
interface WsjtxRig {
  app_id: string;
  config_name: string | null;
}
function isWsjtxRig(value: unknown): value is WsjtxRig {
  if (typeof value !== "object" || value === null) return false;
  return "app_id" in value && typeof value.app_id === "string"
    && "config_name" in value
    && (value.config_name === null || typeof value.config_name === "string");
}
interface AircraftEntry {
  icao24: string;
  callsign: string;
  lat: number;
  lon: number;
  altitude_ft: number;
  velocity_kts: number | null;
  heading_deg: number | null;
  az_a: number;
  el_a: number;
  range_a_km: number;
  az_b: number;
  el_b: number;
  range_b_km: number;
  off_path_deg: number;
  scatter_score: number;
}
interface AirscoutUpdate {
  aircraft: AircraftEntry[];
  flight_data?: AircraftEntry[];
  total: number;
  station_a: string | null;
  station_b: string | null;
  active_source?: string | null;
  last_update_epoch_s?: number | null;
  cache_age_s?: number | null;
  data_freshness?: string | null;
}
interface AirscoutSourceState {
  active_source: string | null;
  priority: string[];
  sources: Record<string, { state: string; message: string | null }>;
  poll_intervals_s?: Record<string, number>;
}
interface Dump1090Activity {
  host: string;
  port: number;
  messages: number;
  aircraft: number;
  with_position: number;
  last_message_age_s: number | null;
}
interface CallsignDbStatus {
  db_url: string;
  version: string | null;
  downloaded_at: string | null;
  entries: number;
  fresh: boolean;
  freshness_days: number;
  online?: boolean;
  remote_version?: string | null;
  update_available?: boolean;
}

interface AircraftFilterSettings {
  lat_min: number | null;
  lat_max: number | null;
  lon_min: number | null;
  lon_max: number | null;
  altitude_min_ft: number | null;
  heading_tolerance_deg: number;
  position_tolerance_deg: number;
  priority_flight: string;
  priority_flight_exclusive: boolean;
}

type NumericAircraftFilterKey =
  | "lat_min"
  | "lat_max"
  | "lon_min"
  | "lon_max"
  | "altitude_min_ft"
  | "heading_tolerance_deg"
  | "position_tolerance_deg";

const DEFAULT_AIRCRAFT_FILTERS: AircraftFilterSettings = {
  lat_min: null,
  lat_max: null,
  lon_min: null,
  lon_max: null,
  altitude_min_ft: null,
  heading_tolerance_deg: 30,
  position_tolerance_deg: 70,
  priority_flight: "",
  priority_flight_exclusive: false,
};

function getAircraftFilterStorageKey(stationA: string | null | undefined, stationB: string | null | undefined): string {
  const keyA = (stationA ?? "default").trim().toUpperCase();
  const keyB = (stationB ?? "default").trim().toUpperCase();
  return `airwsjtx.aircraftFilters.${keyA}__${keyB}`;
}

function parseAircraftFilterNumber(value: string): number | null {
  if (value.trim() === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function loadAircraftFilters(stationA: string | null | undefined, stationB: string | null | undefined): AircraftFilterSettings {
  try {
    const raw = localStorage.getItem(getAircraftFilterStorageKey(stationA, stationB));
    if (!raw) return DEFAULT_AIRCRAFT_FILTERS;
    const parsed = JSON.parse(raw) as Partial<AircraftFilterSettings>;
    return {
      lat_min: parsed.lat_min ?? null,
      lat_max: parsed.lat_max ?? null,
      lon_min: parsed.lon_min ?? null,
      lon_max: parsed.lon_max ?? null,
      altitude_min_ft: parsed.altitude_min_ft ?? null,
      heading_tolerance_deg: Number(parsed.heading_tolerance_deg ?? 30),
      position_tolerance_deg: Number(parsed.position_tolerance_deg ?? 70),
      priority_flight: "",
      priority_flight_exclusive: false,
    };
  } catch {
    return DEFAULT_AIRCRAFT_FILTERS;
  }
}

// ─── Sidecar RPC ──────────────────────────────────────────────────────────────

let _rpcId = 0;
const _pending = new Map<string, (r: { ok: boolean; result?: unknown; error?: string }) => void>();

async function rpc(method: string, params: Record<string, unknown> = {}): Promise<unknown> {
  const id = String(++_rpcId);
  const msg = JSON.stringify({ id, method, params });
  return new Promise((resolve, reject) => {
    _pending.set(id, (r) => r.ok ? resolve(r.result) : reject(new Error(r.error)));
    invoke("send_to_sidecar", { message: msg }).catch(reject);
  });
}

const loadDump1090Host = () => rpc("airscout_get_dump1090_host") as Promise<Dump1090Host>;
const saveDump1090Host = (host: string) =>
  rpc("airscout_set_dump1090_host", { host }) as Promise<Dump1090Host>;

// ─── Colours ──────────────────────────────────────────────────────────────────

const STATE_COLOR: Record<string, string> = {
  idle: "#8b949e",
  stations_ready: "#d29922",
  candidates_available: "#d29922",
  plane_locked: "#58a6ff",
  tracking: "#58a6ff",
  ready: "#3fb950",
  active_qso: "#3fb950",
  completed: "#3fb950",
  aborted: "#f85149",
};

const PORTABLE_GRID_OVERRIDES_KEY = "airwsjtx.portableGridOverrides";
const REMOTE_DB_URL = "https://ad7np.us/RoverAIM/DB";
const PORTABLE_VIEW_KEY = "airwsjtx.portableViewMode";
const MY_CALL_OVERRIDE_KEY = "airwsjtx.myCallOverride";
const MY_GRID_OVERRIDE_KEY = "airwsjtx.myGridOverride";
const MY_ALT_KEY = "airwsjtx.myAltitudeM";
const DX_ALT_KEY = "airwsjtx.dxAltitudeM";
const DX_LOCAL_KEY = "airwsjtx.dxLocal";
const LAST_DX_KEY = "airwsjtx.lastDx";
function loadLastDx(): { call: string | null; grid: string | null } {
  try {
    const raw = localStorage.getItem(LAST_DX_KEY);
    if (!raw) return { call: null, grid: null };
    const saved: unknown = JSON.parse(raw);
    if (typeof saved !== "object" || saved === null
      || !("call" in saved) || typeof saved.call !== "string" || !isCompleteCall(saved.call)
      || !("grid" in saved) || (saved.grid !== null
        && (typeof saved.grid !== "string" || !isValidLocator(saved.grid)))) {
      throw new Error("Invalid saved DX station");
    }
    return { call: cleanCall(saved.call), grid: typeof saved.grid === "string" ? saved.grid : null };
  } catch (e) {
    console.error("DX station restore error:", e);
    return { call: null, grid: null };
  }
}
const FAVORITE_DX_KEY = "airwsjtx.favoriteDx";
const FAVORITE_DX_MAX = 20;
type FavoriteDx = { call: string; grid: string | null; alt?: string };
const favAlt = (f: FavoriteDx) => (f.alt?.trim() ? f.alt.trim() : "0");
const favGrid = (f: FavoriteDx) => (f.grid ?? "").trim().toUpperCase();
const DEFAULT_FAVORITE_DX: FavoriteDx[] = [{ call: "VE7NX", grid: "CN89oh" }, { call: "W7PUA", grid: "CN84jq" }];
function loadFavoriteDx(): FavoriteDx[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(FAVORITE_DX_KEY) ?? localStorage.getItem("airwsjtx.recentDx") ?? "null") as FavoriteDx[] | null;
    if (Array.isArray(parsed) && parsed.every(r => r && typeof r.call === "string")) {
      const out = parsed.map(r => ({ ...r, call: cleanCall(r.call) })).filter(r => isCompleteCall(r.call));
      for (const d of DEFAULT_FAVORITE_DX) if (out.length < 2 && !out.some(r => r.call === d.call)) out.push(d);
      return out.slice(0, FAVORITE_DX_MAX);
    }
  } catch { /* fall back to defaults */ }
  return DEFAULT_FAVORITE_DX;
}
const FAVORITE_FLIGHTS_KEY = "airwsjtx.favoriteFlights";
const FAVORITE_FLIGHTS_MAX = 20;
type FavoriteFlight = { flight: string; only: boolean };
function loadFavoriteFlights(): FavoriteFlight[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(FAVORITE_FLIGHTS_KEY) ?? "[]");
    if (!Array.isArray(parsed) || !parsed.every((entry: unknown) =>
      typeof entry === "object" && entry !== null
      && "flight" in entry && typeof entry.flight === "string" && entry.flight.trim().length > 0
      && "only" in entry && typeof entry.only === "boolean"
    )) {
      throw new Error("Invalid saved flight favorites");
    }
    return (parsed as FavoriteFlight[]).map(entry => ({
      flight: entry.flight.trim().toUpperCase(), only: entry.only,
    })).slice(0, FAVORITE_FLIGHTS_MAX);
  } catch (e) {
    console.error("Flight favorites load error:", e);
    return [];
  }
}
const STATION_SOURCE_BTN_STYLE: React.CSSProperties = {
  fontSize: 10, padding: "2px 0", width: 52, boxSizing: "border-box", textAlign: "center", whiteSpace: "nowrap", flex: "0 0 auto",
};
const STATION_INPUT_STYLE: React.CSSProperties = {
  background: "var(--surface)", color: "var(--text)", border: "1px solid var(--border)",
  borderRadius: 4, padding: "2px 4px", fontSize: 11, fontFamily: "monospace", boxSizing: "content-box",
};
const PORTABLE_BASE_FONT_PX = 72;

type PortableViewMode = "hidden" | "panel" | "overlay";
// The docked Az/El panel is switched off (code kept): set to true to bring the Panel mode back.
const AZEL_PANEL_ENABLED = false;
const ROTATOR_SPEECH_ENABLED_KEY = "airwsjtx.rotatorSpeech.enabled";
const ROTATOR_SPEECH_THRESHOLD_KEY = "airwsjtx.rotatorSpeech.thresholdDeg";
const ROTATOR_SPEECH_INTERVAL_KEY = "airwsjtx.rotatorSpeech.intervalSec";
const ROTATOR_CONTROL_KEY = "airwsjtx.rotatorControlEnabled";
const ROTATOR_SPEECH_MIGRATED_KEY = "airwsjtx.rotatorSpeech.threshold1degMigrated";
const SKYDOME_RECT_KEY = "airwsjtx.skyDomeRect";
const SKYDOME_MIN_W = 280;
const SKYDOME_MIN_H = 320;

interface SkyDomeRect { x: number; y: number; w: number; h: number }

function clampSkyDomeRect(r: SkyDomeRect, minW = SKYDOME_MIN_W, minH = SKYDOME_MIN_H): SkyDomeRect {
  const w = Math.min(Math.max(r.w, minW), window.innerWidth);
  const h = Math.min(Math.max(r.h, minH), window.innerHeight);
  return {
    w, h,
    x: Math.min(Math.max(r.x, 0), Math.max(0, window.innerWidth - w)),
    y: Math.min(Math.max(r.y, 0), Math.max(0, window.innerHeight - h)),
  };
}

function loadSkyDomeRect(): SkyDomeRect {
  try {
    const p = JSON.parse(localStorage.getItem(SKYDOME_RECT_KEY) ?? "null");
    if (p && [p.x, p.y, p.w, p.h].every(Number.isFinite)) return clampSkyDomeRect(p);
  } catch { /* fall through to default */ }
  return clampSkyDomeRect({ w: 460, h: 560, x: window.innerWidth - 480, y: 110 });
}

const OVERLAY_RECT_KEY = "airwsjtx.overlayRect";
const OVERLAY_MIN_W = 220;
const OVERLAY_MIN_H = 120;

function clampOverlayRect(r: SkyDomeRect): SkyDomeRect {
  return clampSkyDomeRect(r, OVERLAY_MIN_W, OVERLAY_MIN_H);
}

function loadOverlayRect(): SkyDomeRect {
  try {
    const p = JSON.parse(localStorage.getItem(OVERLAY_RECT_KEY) ?? "null");
    if (p && [p.x, p.y, p.w, p.h].every(Number.isFinite)) return clampOverlayRect(p);
  } catch { /* fall through to default */ }
  return clampOverlayRect({ w: 600, h: 200, x: window.innerWidth - 620, y: 90 });
}

// Callbook lookups ignore portable/mobile markers: W7GLF/P, VE7/W7GLF, W7GLF/QRP
const CALL_SUFFIXES = new Set(["P", "M", "MM", "AM", "QRP", "R", "A", "B"]);
function baseCall(callsign: string): string {
  const parts = callsign.trim().toUpperCase().split("/").filter(Boolean);
  if (parts.length < 2) return parts[0] ?? "";
  const rest = parts.filter(p => !CALL_SUFFIXES.has(p) && !/^\d$/.test(p));
  if (rest.length === 0) return parts[0];
  return rest.reduce((a, b) => (b.length > a.length ? b : a));
}

// WSJT-X can send a half-typed call such as "W7GLF/"; drop dangling slashes
function cleanCall(call: string | null | undefined): string {
  return (call ?? "").trim().toUpperCase().replace(/^\/+|\/+$/g, "");
}

function isCompleteCall(call: string): boolean {
  return /^[A-Z0-9]+(\/[A-Z0-9]+)*$/.test(call) && /[A-Z]/.test(call) && /[0-9]/.test(call) && call.length >= 3;
}

function normalizeGrid(grid: string | null | undefined): string | null {
  const normalized = (grid ?? "").trim().toUpperCase();
  return normalized.length > 0 ? normalized : null;
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [status, setStatus] = useState<{ state: string } | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const flightDataRef = useRef<HTMLDivElement>(null);
  const logCardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const observers: ResizeObserver[] = [];
    for (const [ref, key] of [
      [flightDataRef, "airwsjtx.flightDataHeight"],
      [logCardRef, "airwsjtx.logHeight"],
    ] as const) {
      const element = ref.current;
      if (!element) continue;
      try {
        const saved = localStorage.getItem(key);
        if (saved !== null) {
          const height = Number(saved);
          if (!Number.isFinite(height) || height < 80) {
            throw new Error(`Invalid saved panel height: ${saved}`);
          }
          element.style.height = `${height}px`;
        }
      } catch (error) {
        console.error(`Panel height restore error (${key}):`, error);
      }
      let previousHeight = element.offsetHeight;
      const observer = new ResizeObserver(() => {
        const height = element.offsetHeight;
        if (height < 80 || height === previousHeight) return;
        previousHeight = height;
        try {
          localStorage.setItem(key, String(height));
        } catch (error) {
          console.error(`Panel height save error (${key}):`, error);
        }
      });
      observer.observe(element);
      observers.push(observer);
    }
    return () => observers.forEach(observer => observer.disconnect());
  }, []);
  const [wsjtxError, setWsjtxError] = useState<string | null>(null);
  const [wsjtxLive, setWsjtxLive] = useState<WsjtxLive>({
    connected: false, app_id: "", freq_mhz: null, mode: null,
    sub_mode: null, de_call: null, de_grid: null, dx_call: null, dx_grid: null,
    transmitting: false, decoding: false, tr_period: null,
    rx_df: null, tx_df: null, config_name: null,
  });
  const [decodes, setDecodes] = useState<WsjtxDecodeEntry[]>([]);
  const [airscout, setAirscout] = useState<AirscoutUpdate | null>(null);
  const [sourceStatus, setSourceStatus] = useState<AirscoutSourceState | null>(null);
  const [dump1090Activity, setDump1090Activity] = useState<Dump1090Activity | null>(null);
  const dump1090LogRef = useRef<{ signature: string; at: number } | null>(null);
  const dump1090BusyLogRef = useRef<{ signature: string; at: number } | null>(null);
  const [rotatorConnected, setRotatorConnected] = useState(false);
  const [rotatorControlEnabled, setRotatorControlEnabled] = useState(
    () => localStorage.getItem(ROTATOR_CONTROL_KEY) !== "false",
  );
  const [rotatorAz, setRotatorAz] = useState(0);
  const [rotatorEl, setRotatorEl] = useState(0);
  const [rotatorTargetAz, setRotatorTargetAz] = useState<number | null>(null);
  const [rotatorTargetEl, setRotatorTargetEl] = useState<number | null>(null);
  const [rotatorSpeechEnabled, setRotatorSpeechEnabled] = useState(() => localStorage.getItem(ROTATOR_SPEECH_ENABLED_KEY) === "1");
  const [rotatorSpeechThresholdDeg, setRotatorSpeechThresholdDeg] = useState(() => {
    let raw = localStorage.getItem(ROTATOR_SPEECH_THRESHOLD_KEY);
    // The old 3° default was auto-persisted; move it to the new 1° default once.
    if (localStorage.getItem(ROTATOR_SPEECH_MIGRATED_KEY) !== "1") {
      localStorage.setItem(ROTATOR_SPEECH_MIGRATED_KEY, "1");
      if (raw === "3") raw = null;
    }
    if (raw === null) return 1;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : 1;
  });
  const [rotatorSpeechIntervalSec, setRotatorSpeechIntervalSec] = useState(() => {
    const raw = localStorage.getItem(ROTATOR_SPEECH_INTERVAL_KEY);
    if (raw === null) return 2;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 0 ? value : 2;
  });
  const [showSkyDome, setShowSkyDome] = useState(() => localStorage.getItem("airwsjtx.showSkyDome") === "true");
  const [skyDomeRect, setSkyDomeRect] = useState(loadSkyDomeRect);
  const skyDomeRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = skyDomeRef.current;
    if (!showSkyDome || !el) return;
    // Native CSS resize doesn't raise React events; persist the size it produces.
    const ro = new ResizeObserver(() => {
      const w = Math.round(el.offsetWidth);
      const h = Math.round(el.offsetHeight);
      setSkyDomeRect((r) => (r.w === w && r.h === h ? r : { ...r, w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [showSkyDome]);
  useEffect(() => {
    localStorage.setItem(SKYDOME_RECT_KEY, JSON.stringify(skyDomeRect));
  }, [skyDomeRect]);
  const skyDomeDragRef = useRef<{ dx: number; dy: number } | null>(null);
  useEffect(() => {
    const onResize = () => setSkyDomeRect((r) => {
      const c = clampSkyDomeRect(r);
      return c.x === r.x && c.y === r.y && c.w === r.w && c.h === r.h ? r : c;
    });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const [lastDx] = useState(loadLastDx);
  const [dxCallsign, setDxCallsign] = useState<string | null>(lastDx.call);
  const [dxGrid, setDxGrid] = useState<string | null>(lastDx.grid);
  const [stationGridOverrides, setStationGridOverrides] = useState<Record<string, string>>(() => {
    try {
      const raw = localStorage.getItem(PORTABLE_GRID_OVERRIDES_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw) as Record<string, string>;
      return Object.fromEntries(
        Object.entries(parsed)
          .map(([call, grid]) => [call.trim().toUpperCase(), normalizeGrid(grid)])
          .filter((entry): entry is [string, string] => Boolean(entry[1])),
      );
    } catch {
      return {};
    }
  });
  const [remoteStationGrids, setRemoteStationGrids] = useState<Record<string, string>>({});
  const [myGridInput, setMyGridInput] = useState("");
  const [myCallOverride, setMyCallOverride] = useState(() => localStorage.getItem(MY_CALL_OVERRIDE_KEY) ?? "");
  const [myGridOverride, setMyGridOverride] = useState(() => localStorage.getItem(MY_GRID_OVERRIDE_KEY) ?? "");
  const [myCallInput, setMyCallInput] = useState(() => localStorage.getItem(MY_CALL_OVERRIDE_KEY) ?? "");
  const myCallEditing = useRef(false);
  const [myStationBusy, setMyStationBusy] = useState(false);
  const myStationRestored = useRef(false);
  const [dxGridInput, setDxGridInput] = useState("");
  const myGridEditing = useRef(false);
  const [myAltInput, setMyAltInput] = useState(() => localStorage.getItem(MY_ALT_KEY) ?? "");
  const [dxAltInput, setDxAltInput] = useState(() => localStorage.getItem(DX_ALT_KEY) ?? "");
  const [myAltitudeM, setMyAltitudeM] = useState(() => Number(localStorage.getItem(MY_ALT_KEY) ?? "0"));
  const dxGridEditing = useRef(false);
  const dxCallEditing = useRef(false);
  const [favoriteDx, setFavoriteDx] = useState<FavoriteDx[]>(loadFavoriteDx);
  const [favoriteFlights, setFavoriteFlights] = useState<FavoriteFlight[]>(loadFavoriteFlights);
  const [dxLocal, setDxLocal] = useState(() => localStorage.getItem(DX_LOCAL_KEY) === "1");
  const [dxCallInput, setDxCallInput] = useState("");
  const [nowEpochS, setNowEpochS] = useState(() => Math.floor(Date.now() / 1000));
  const [autoLock, setAutoLock] = useState(true);
  const [uiMode, setUiMode] = useState<"rotator" | "manual">("rotator");
  const [wsjtxRigFilter, setWsjtxRigFilter] = useState("");
  const [wsjtxKnownRigs, setWsjtxKnownRigs] = useState<WsjtxRig[]>([]);
  const [wsjtxRigDiscoveryError, setWsjtxRigDiscoveryError] = useState<string | null>(null);
  const [wsjtxRigFilterBusy, setWsjtxRigFilterBusy] = useState(false);
  const [portableView, setPortableView] = useState<PortableViewMode>(() => {
    const saved = localStorage.getItem(PORTABLE_VIEW_KEY) as PortableViewMode | null;
    if (saved === "panel") return AZEL_PANEL_ENABLED ? "panel" : "overlay";
    return saved === "overlay" || saved === "hidden" ? saved : (AZEL_PANEL_ENABLED ? "panel" : "hidden");
  });
  const [overlayRect, setOverlayRect] = useState(loadOverlayRect);
  const overlayRef = useRef<HTMLDivElement>(null);
  const overlayDragRef = useRef<{ dx: number; dy: number } | null>(null);
  const [aircraftFilters, setAircraftFilters] = useState<AircraftFilterSettings>(() => loadAircraftFilters(null, null));
  const activeFlightRef = useRef({ priority_flight: "", priority_flight_exclusive: false });
  const [callsignDbStatus, setCallsignDbStatus] = useState<CallsignDbStatus | null>(null);
  const [callsignDbBusy, setCallsignDbBusy] = useState(false);
  const [lockedPlane, setLockedPlane] = useState<{
    icao24: string; callsign: string; altitude_ft: number;
    az_a: number; el_a: number; az_b: number; el_b: number;
    scatter_score: number; off_path_deg: number; state: string;
    rotator_az?: number | null; rotator_el?: number | null;
    position_updated_epoch_s?: number | null;
    range_a_km?: number | null;
    range_b_km?: number | null;
  } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const rotatorSpeechLastAnnouncedRef = useRef<{ az: number; el: number; at: number; source: "rotator" | "manual" } | null>(null);
  const [speechRecheck, setSpeechRecheck] = useState(0);
  const rotatorSpeechLastProcessedRef = useRef<{ az: number; el: number; source: "rotator" | "manual" } | null>(null);

  const addLog = useCallback((msg: string) => {
    setLog((l) => [...l.slice(-29), `${new Date().toLocaleTimeString()} ${msg}`]);
  }, []);

  useEffect(() => {
    function parseEntries(entries: Array<{ name: string; grid6: string }>) {
      const grids: Record<string, string> = {};
      for (const e of entries) {
        const at = e.name.indexOf("@");
        if (at < 0 || !e.grid6) continue;
        const call = e.name.slice(0, at).trim().toUpperCase();
        if (call) grids[call] = e.grid6.toUpperCase();
      }
      return grids;
    }

    invoke<string>("fetch_remote_db")
      .then(text => {
        const grids = parseEntries(JSON.parse(text));
        setRemoteStationGrids(grids);
        addLog(`Remote station DB loaded — ${Object.keys(grids).length} stations`);
      })
      .catch(() => {
        fetch(REMOTE_DB_URL)
          .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
          .then((entries: Array<{ name: string; grid6: string }>) => {
            const grids = parseEntries(entries);
            setRemoteStationGrids(grids);
            addLog(`Remote station DB loaded — ${Object.keys(grids).length} stations`);
          })
          .catch(() => addLog("Remote station DB unavailable"));
      });
  }, [addLog]);

  function lookupGrid(callsign: string | null): string | null {
    if (!callsign) return null;
    const call = callsign.trim().toUpperCase();
    const base = baseCall(call);
    return stationGridOverrides[call] ?? remoteStationGrids[call]
      ?? stationGridOverrides[base] ?? remoteStationGrids[base] ?? null;
  }

  // Wire sidecar messages — handles both RPC responses and push events
  useEffect(() => {
    const unlisten = listen<string>("sidecar-message", (ev) => {
      try {
        const data = JSON.parse(ev.payload) as Record<string, unknown>;

        // Push event (no id)
        if (data.event) {
          const evt = data.event as string;
          const evtData = data.data as Record<string, unknown>;

          if (evt === "wsjtx_connected") {
            addLog(`WSJT-X connected: ${evtData.app_id}`);
            setWsjtxError(null);
            setWsjtxLive(w => ({ ...w, connected: true, app_id: evtData.app_id as string }));
          } else if (evt === "wsjtx_disconnected") {
            addLog("WSJT-X disconnected");
            setWsjtxLive(w => ({ ...w, connected: false }));
          } else if (evt === "wsjtx_error") {
            addLog(`WSJT-X error: ${evtData.message}`);
            setWsjtxError(evtData.message as string);
            setWsjtxLive(w => ({ ...w, connected: false }));
          } else if (evt === "wsjtx_status") {
            setWsjtxError(null);
            setWsjtxLive(w => ({
              ...w,
              connected: true,
              freq_mhz: evtData.freq_mhz as number,
              mode: evtData.mode as string | null,
              sub_mode: evtData.sub_mode as string | null,
              de_call: evtData.de_call as string | null,
              de_grid: evtData.de_grid as string | null,
              dx_call: evtData.dx_call as string | null,
              dx_grid: evtData.dx_grid as string | null,
              transmitting: evtData.transmitting as boolean,
              decoding: evtData.decoding as boolean,
              tr_period: evtData.tr_period as number | null,
              rx_df: evtData.rx_df as number | null,
              tx_df: evtData.tx_df as number | null,
              config_name: evtData.config_name as string | null,
            }));
          } else if (evt === "qso_logged") {
            const q = evtData as { dx_call?: string | null; mode?: string | null; report_sent?: string | null; report_rcvd?: string | null; state?: string; session_completed?: boolean };
            addLog(`WSJT-X logged QSO with ${q.dx_call ?? "?"}${q.mode ? ` (${q.mode})` : ""} ${q.report_sent ?? ""}/${q.report_rcvd ?? ""}${q.session_completed ? " — session completed" : ""}`.replace(/\s+/g, " ").trim());
            if (q.state) setStatus((s) => s ? { ...s, state: q.state as string } : s);
          } else if (evt === "wsjtx_decode") {
            const entry: WsjtxDecodeEntry = {
              message: evtData.message as string | null,
              snr: evtData.snr as number,
              delta_freq_hz: evtData.delta_freq_hz as number,
              mode: evtData.mode as string | null,
              ts: formatWsjtxDecodeTime(evtData.time_ms),
            };
            setDecodes(d => [entry, ...d.slice(0, 49)]);
            addLog(`DX: ${entry.message ?? "?"} SNR ${entry.snr}dB`);
          } else if (evt === "airscout_update") {
            setAirscout(evtData as unknown as AirscoutUpdate);
          } else if (evt === "dump1090_busy") {
            const signature = `${evtData.host}:${evtData.message}`;
            const last = dump1090BusyLogRef.current;
            // The host is retried every few seconds; log a change, then a reminder each minute.
            if (!last || last.signature !== signature || Date.now() - last.at >= 60000) {
              dump1090BusyLogRef.current = { signature, at: Date.now() };
              addLog(`dump1090 ${evtData.host}:${evtData.port} busy: ${evtData.message} (retrying)`);
            }
          } else if (evt === "dump1090_activity") {
            dump1090BusyLogRef.current = null;
            const activity = evtData as unknown as Dump1090Activity;
            setDump1090Activity(activity);
            const signature = `${activity.host}:${activity.aircraft}:${activity.with_position}`;
            const last = dump1090LogRef.current;
            const idle = activity.aircraft === 0;
            // Log on change, plus a periodic reminder while idle so silence isn't mistaken for a hang.
            if (!last || last.signature !== signature || (idle && Date.now() - last.at >= 60000)) {
              dump1090LogRef.current = { signature, at: Date.now() };
              addLog(idle
                ? `dump1090 ${activity.host}:${activity.port}: connected, no aircraft received (${activity.messages} messages total)`
                : `dump1090 ${activity.host}:${activity.port}: ${activity.aircraft} aircraft, ${activity.with_position} with position (${activity.messages} messages)`);
            }
          } else if (evt === "airscout_source_status") {
            setSourceStatus(evtData as unknown as AirscoutSourceState);
          } else if (evt === "airscout_source_switched") {
            addLog(`AirScout source switched: ${String(evtData.from)} → ${String(evtData.to)} (${String(evtData.reason)})`);
          } else if (evt === "airscout_feed_started") {
            addLog(`AirScout feed started: ${evtData.source}`);
          } else if (evt === "airscout_error") {
            addLog(`AirScout error (${evtData.source}): ${evtData.message}`);
          } else if (evt === "rotator_position") {
            setRotatorAz(evtData.azimuth as number);
            setRotatorEl(evtData.elevation as number);
          } else if (evt === "rotator_connected") {
            setRotatorConnected(true);
            addLog(`Rotator connected (${evtData.host}:${evtData.port})`);
          } else if (evt === "rotator_disconnected") {
            setRotatorConnected(false);
            addLog("Rotator disconnected");
          } else if (evt === "source_credentials_error") {
            addLog(`Aircraft credentials: ${evtData.message}`);
          } else if (evt === "rotator_error") {
            addLog(`Rotator: ${evtData.message}`);
          } else if (evt === "plane_locked") {
            const d = evtData as typeof lockedPlane;
            setLockedPlane(d);
            setStatus(s => s ? { ...s, state: (evtData.state as string) } : s);
            addLog(`🔒 Locked: ${evtData.callsign} FL${Math.round((evtData.altitude_ft as number)/100)} score=${((evtData.scatter_score as number)*100).toFixed(0)}%`);
          } else if (evt === "plane_tracking") {
            setLockedPlane(prev => prev && prev.icao24 === evtData.icao24 ? {
              ...prev,
              callsign: typeof evtData.callsign === "string" ? evtData.callsign : prev.callsign,
              altitude_ft: typeof evtData.altitude_ft === "number" ? evtData.altitude_ft : prev.altitude_ft,
              range_a_km: typeof evtData.range_a_km === "number" ? evtData.range_a_km : null,
              range_b_km: typeof evtData.range_b_km === "number" ? evtData.range_b_km : null,
              az_a: evtData.az_a as number,
              el_a: evtData.el_a as number,
              az_b: typeof evtData.az_b === "number" ? evtData.az_b : prev.az_b,
              el_b: typeof evtData.el_b === "number" ? evtData.el_b : prev.el_b,
              off_path_deg: typeof evtData.off_path_deg === "number" ? evtData.off_path_deg : prev.off_path_deg,
              position_updated_epoch_s: typeof evtData.position_updated_epoch_s === "number"
                ? evtData.position_updated_epoch_s : null,
              scatter_score: evtData.scatter_score as number,
              state: evtData.state as string,
              rotator_az: evtData.rotator_az as number | null,
              rotator_el: evtData.rotator_el as number | null,
            } : prev);
            setStatus(s => s ? { ...s, state: evtData.state as string } : s);
          } else if (evt === "plane_unlocked" || evt === "plane_out_of_window") {
            setLockedPlane(null);
            if (evt === "plane_out_of_window") addLog(`✈ Aircraft left scatter window`);
          }
          return;
        }

        // RPC response
        if (data.id && _pending.has(data.id as string)) {
          const resolver = _pending.get(data.id as string)!;
          _pending.delete(data.id as string);
          resolver(data as { ok: boolean; result?: unknown; error?: string });
        }
      } catch { /* ignore */ }
    });
    return () => { unlisten.then((f) => f()); };
  }, [addLog]);

  // Poll status every 2 s once sidecar is ready
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const s = await rpc("get_status") as { state: string; wsjtx_known_rigs?: unknown; wsjtx_error?: string | null };
        setStatus(s);
        if (s.wsjtx_error !== undefined) {
          setWsjtxError(s.wsjtx_error);
          if (s.wsjtx_error) setWsjtxLive(w => ({ ...w, connected: false }));
        }
        const rigs = s.wsjtx_known_rigs;
        if (Array.isArray(rigs) && rigs.every(isWsjtxRig)) {
          setWsjtxKnownRigs(rigs);
          setWsjtxRigDiscoveryError(null);
        } else {
          setWsjtxRigDiscoveryError(rigs === undefined
            ? "Rig discovery unavailable: rebuild the Python sidecar and restart the app."
            : "Invalid WSJT-X rig list received from the Python sidecar.");
        }
      } catch { /* sidecar may not be up yet */ }
    }, 2000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (wsjtxRigDiscoveryError) addLog(wsjtxRigDiscoveryError);
  }, [wsjtxRigDiscoveryError, addLog]);

  useEffect(() => {
    const saved = localStorage.getItem("wsjtxRigNameFilter") ?? "";
    setWsjtxRigFilter(saved);
    rpc("wsjtx_set_rig_name_filter", { rig_name: saved || null }).catch(() => {});
  }, []);

  useEffect(() => {
    rpc("airscout_get_sources")
      .then((s) => setSourceStatus(s as AirscoutSourceState))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNowEpochS(Math.floor(Date.now() / 1000)), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    rpc("callsign_db_status")
      .then((s) => setCallsignDbStatus(s as CallsignDbStatus))
      .catch(() => {});
  }, []);

  // Newest log entries are rendered first.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = 0;
  }, [log]);

  useEffect(() => {
    localStorage.setItem(ROTATOR_SPEECH_ENABLED_KEY, rotatorSpeechEnabled ? "1" : "0");
  }, [rotatorSpeechEnabled]);
  useEffect(() => {
    localStorage.setItem(ROTATOR_SPEECH_THRESHOLD_KEY, String(rotatorSpeechThresholdDeg));
  }, [rotatorSpeechThresholdDeg]);
  useEffect(() => {
    localStorage.setItem(ROTATOR_SPEECH_INTERVAL_KEY, String(rotatorSpeechIntervalSec));
  }, [rotatorSpeechIntervalSec]);

  // In Auto mode, keep the last locked plane's Az/El visible (greyed) after lock is lost.
  const lastPlaneTargetRef = useRef<{ az: number; el: number } | null>(null);
  const planeTargetValid = !!lockedPlane && Number.isFinite(lockedPlane.az_a) && Number.isFinite(lockedPlane.el_a);
  if (lockedPlane && planeTargetValid) lastPlaneTargetRef.current = { az: lockedPlane.az_a, el: lockedPlane.el_a };
  const autoTargetInvalid = uiMode !== "manual" && !planeTargetValid;
  const lastPlaneTarget = lastPlaneTargetRef.current;

  const manualSpeechTarget = uiMode !== "manual" && lockedPlane
    && Number.isFinite(lockedPlane.az_a)
    && Number.isFinite(lockedPlane.el_a)
    ? { az: lockedPlane.az_a, el: lockedPlane.el_a }
    : (
      rotatorTargetAz !== null && rotatorTargetEl !== null
        ? { az: rotatorTargetAz, el: rotatorTargetEl }
        : null
    );

  // Auto mode with the rotor off/disconnected still announces the target.
  const rotatorSpeechSource = autoTargetInvalid
    ? null
    : !(rotatorControlEnabled && rotatorConnected)
    ? (
      uiMode !== "manual" && manualSpeechTarget
        ? { ...manualSpeechTarget, source: "manual" as const, announceFirstUpdate: true }
        : null
    )
    : uiMode === "manual"
    ? (
      manualSpeechTarget
        ? { ...manualSpeechTarget, source: "manual" as const, announceFirstUpdate: true }
        : null
    )
    : (
      rotatorConnected
        ? { az: rotatorAz, el: rotatorEl, source: "rotator" as const, announceFirstUpdate: false }
        : null
    );

  useEffect(() => {
    if (!isSpeechSupported()) return;
    if (!rotatorSpeechEnabled || !rotatorSpeechSource) {
      if (rotatorSpeechLastAnnouncedRef.current) void cancelSpeech().catch(e => addLog(`Speech: ${e}`));
      rotatorSpeechLastAnnouncedRef.current = null;
      rotatorSpeechLastProcessedRef.current = null;
      return;
    }
    const { az, el, source, announceFirstUpdate } = rotatorSpeechSource;
    const processed = rotatorSpeechLastProcessedRef.current;
    if (processed && processed.source === source && processed.az === az && processed.el === el) return;
    const now = Date.now();
    const last = rotatorSpeechLastAnnouncedRef.current;
    const speechText = `Azimuth ${Math.round(az)}, Elevation ${Math.round(el)}`;
    if (!last || last.source !== source) {
      rotatorSpeechLastProcessedRef.current = { az, el, source };
      if (announceFirstUpdate) void speak(speechText).catch(e => addLog(`Speech: ${e}`));
      rotatorSpeechLastAnnouncedRef.current = { az, el, at: now, source };
      return;
    }
    const azDelta = Math.abs(((az - last.az + 540) % 360) - 180);
    const elDelta = Math.abs(el - last.el);
    if (Math.max(azDelta, elDelta) < rotatorSpeechThresholdDeg) {
      rotatorSpeechLastProcessedRef.current = { az, el, source };
      return;
    }
    const waitMs = rotatorSpeechIntervalSec * 1000 - (now - last.at);
    if (waitMs > 0) {
      // Change is big enough but too soon: re-evaluate once the interval elapses
      // instead of dropping it when the position then stops changing.
      const timer = window.setTimeout(() => setSpeechRecheck((n) => n + 1), waitMs + 20);
      return () => window.clearTimeout(timer);
    }
    rotatorSpeechLastProcessedRef.current = { az, el, source };
    void speak(speechText).catch(e => addLog(`Speech: ${e}`));
    rotatorSpeechLastAnnouncedRef.current = { az, el, at: now, source };
  }, [
    rotatorAz,
    rotatorEl,
    rotatorConnected,
    rotatorSpeechEnabled,
    rotatorSpeechSource,
    rotatorSpeechThresholdDeg,
    rotatorSpeechIntervalSec,
    speechRecheck,
    addLog,
  ]);

  const prevAutoLockedRef = useRef(false);
  useEffect(() => {
    const autoLocked = uiMode !== "manual" && planeTargetValid;
    if (prevAutoLockedRef.current && uiMode !== "manual" && !planeTargetValid
      && rotatorSpeechEnabled && isSpeechSupported()) {
      void speak("Lost plane lock").catch(e => addLog(`Speech: ${e}`));
    }
    prevAutoLockedRef.current = autoLocked;
  }, [uiMode, planeTargetValid, rotatorSpeechEnabled, addLog]);

  const [controlWsjtx, setControlWsjtx] = useState<boolean>(() => localStorage.getItem("airwsjtx.controlWsjtx") === "1");
  useEffect(() => { localStorage.setItem("airwsjtx.controlWsjtx", controlWsjtx ? "1" : "0"); }, [controlWsjtx]);

  async function startQso() {
    try {
      const r = await rpc("start_qso") as { state: string };
      setStatus((s) => s ? { ...s, state: r.state } : s);
      addLog("QSO marked started — press Enable Tx in WSJT-X (it cannot be enabled remotely)");
    } catch (e) { addLog(`Error: ${e}`); }
  }

  async function finishQso(abort = false) {
    try {
      const r = await rpc("finish_qso", { abort, control_wsjtx: controlWsjtx }) as { state: string; wsjtx_tx_sent?: boolean | null };
      setStatus((s) => s ? { ...s, state: r.state } : s);
      if (!controlWsjtx) addLog(abort ? "QSO marked aborted (use Halt Tx in WSJT-X to stop transmitting)" : "QSO marked complete (WSJT-X logs the contact itself)");
      else addLog(`${abort ? "QSO aborted" : "QSO completed"} — ${r.wsjtx_tx_sent ? "Halt Tx sent to WSJT-X" : "could not reach WSJT-X, use Halt Tx there"}`);
    } catch (e) { addLog(`Error: ${e}`); }
  }

  async function applyWsjtxRigFilter(rawValue: string) {
    const value = rawValue.trim();
    setWsjtxRigFilterBusy(true);
    try {
      await rpc("wsjtx_set_rig_name_filter", { rig_name: value || null });
      localStorage.setItem("wsjtxRigNameFilter", value);
      setWsjtxRigFilter(value);
      addLog(value ? `WSJT-X rig filter: ${value}` : "WSJT-X rig filter cleared");
    } catch (e) {
      addLog(`WSJT-X filter error: ${e}`);
    } finally {
      setWsjtxRigFilterBusy(false);
    }
  }

  const state = status?.state ?? "idle";
  // Timeline state derived from live data: the backend only advances to ready/tracking via a flight-track path the UI never drives
  const READY_TOLERANCE_DEG = 3;
  const displayState = (() => {
    if (state === "active_qso") return "active_qso";
    if (!lockedPlane) return "idle";
    if (wsjtxLive.transmitting) return "active_qso";
    if (!(rotatorControlEnabled && rotatorConnected && uiMode !== "manual")) return "plane_locked";
    const azErr = Math.abs(((rotatorAz - lockedPlane.az_a + 540) % 360) - 180);
    const elErr = Math.abs(rotatorEl - lockedPlane.el_a);
    return Number.isFinite(azErr) && Number.isFinite(elErr) && Math.max(azErr, elErr) <= READY_TOLERANCE_DEG
      ? "ready" : "tracking";
  })();
  const stateColor = STATE_COLOR[displayState] ?? "#8b949e";
  const aircraft = airscout?.aircraft ?? [];
  const flightData = [...(airscout?.flight_data ?? [])].sort((a, b) => b.scatter_score - a.scatter_score);
  const savedFlights = new Set(favoriteFlights.map(favorite => favorite.flight.trim().toUpperCase()));
  const skyDomeAircraft = aircraft.filter((ac, index) => index < 20 || ac.icao24 === lockedPlane?.icao24);
  const txOffsetTitle = wsjtxLive.tx_df != null ? `TX frequency offset: ${wsjtxLive.tx_df}Hz.` : null;
  const txOffset = txOffsetTitle ? ` ${txOffsetTitle}` : "";
  const txIndicatorTitle = wsjtxLive.transmitting
    ? `Red TX means transmitting (PTT ON).${txOffset}`
    : `TX○ means idle (not transmitting).${txOffset}`;
  const wsjtxReady = wsjtxLive.connected &&
    wsjtxLive.mode === "Q65" &&
    wsjtxLive.tr_period === 15 &&
    (wsjtxLive.freq_mhz == null || wsjtxLive.freq_mhz >= 1240);
  const canStartQso = wsjtxReady && (
    displayState === "ready" ||
    state === "ready" ||
    (!autoLock && !!lockedPlane && (state === "plane_locked" || state === "tracking"))
  );
  const activeSource = sourceStatus?.active_source ?? airscout?.active_source ?? null;
  const configuredPollInterval = activeSource ? sourceStatus?.poll_intervals_s?.[activeSource] : undefined;
  const activePollIntervalS = typeof configuredPollInterval === "number"
    && Number.isFinite(configuredPollInterval) && configuredPollInterval > 0
    ? configuredPollInterval : activeSource === "dump1090" ? 5 : 30;
  const activeSourceState = activeSource ? sourceStatus?.sources?.[activeSource]?.state ?? "unknown" : "stopped";
  const activeSourceMessage = activeSource ? sourceStatus?.sources?.[activeSource]?.message ?? null : null;
  const internetSourceState = activeSource === "adsbexchange"
    ? activeSourceState : sourceStatus?.sources?.opensky?.state ?? "stopped";
  const dump1090Busy = activeSource === "dump1090" && activeSourceState === "busy";
  const connectivityState = activeSource === "dump1090" && activeSourceState === "connected"
    ? "local"
    : dump1090Busy
      ? "busy"
      : internetSourceState === "connected"
        ? "online"
        : ["backoff", "connecting", "disconnected"].includes(internetSourceState)
          ? "degraded"
          : "offline";
  const connectivityColor = connectivityState === "online" || connectivityState === "local"
    ? "var(--green)"
    : connectivityState === "degraded" || connectivityState === "busy"
      ? "var(--yellow)"
      : "var(--red)";
  const connectivityLabel = connectivityState === "local"
    ? "LOCAL ADS-B"
    : connectivityState === "busy"
      ? "DUMP1090 BUSY"
      : connectivityState === "online"
        ? "NET ONLINE"
        : connectivityState === "degraded"
          ? "NET DEGRADED"
          : "NET OFFLINE";
  const cacheAgeSeconds = airscout?.last_update_epoch_s
    ? Math.max(0, nowEpochS - airscout.last_update_epoch_s)
    : airscout?.cache_age_s ?? null;
  const dataFreshness = airscout?.data_freshness ?? "empty";
  const freshnessLabel = ["fresh", "stale", "expired"].includes(dataFreshness) ? dataFreshness : "waiting";
  const sourceDotColor = activeSourceState === "connected"
    ? "var(--green)"
    : activeSourceState === "backoff" || activeSourceState === "connecting" || activeSourceState === "busy"
      ? "var(--yellow)"
      : "var(--red)";
  const availableSources = sourceStatus ? Object.keys(sourceStatus.sources) : ["opensky", "adsbexchange"];

  const filteredAircraftCount = aircraft.length;
  const totalAircraftCount = airscout?.total ?? filteredAircraftCount;

  const updateSourcePriority = (nextPriority: string[]) => {
    if (nextPriority.length === 0) return;
    rpc("airscout_set_sources", { sources: nextPriority })
      .then((s) => setSourceStatus(s as AirscoutSourceState))
      .catch((e) => addLog(`AirScout source config: ${e}`));
  };

  const persistAircraftFilters = useCallback((nextFilters: AircraftFilterSettings) => {
    const normalized: AircraftFilterSettings = {
      ...nextFilters,
      priority_flight: nextFilters.priority_flight.trim().toUpperCase(),
      heading_tolerance_deg: Number.isFinite(nextFilters.heading_tolerance_deg) ? nextFilters.heading_tolerance_deg : DEFAULT_AIRCRAFT_FILTERS.heading_tolerance_deg,
      position_tolerance_deg: Number.isFinite(nextFilters.position_tolerance_deg) ? nextFilters.position_tolerance_deg : DEFAULT_AIRCRAFT_FILTERS.position_tolerance_deg,
    };
    activeFlightRef.current = {
      priority_flight: normalized.priority_flight,
      priority_flight_exclusive: normalized.priority_flight_exclusive,
    };
    const storageKey = getAircraftFilterStorageKey(airscout?.station_a, airscout?.station_b);
    localStorage.setItem(storageKey, JSON.stringify(normalized));
    setAircraftFilters(normalized);
    rpc("airscout_apply_filters", { filters: normalized })
      .catch((e) => addLog(`Aircraft filter update: ${e}`));
  }, [airscout?.station_a, airscout?.station_b, addLog]);

  const storeFlight = () => {
    const flight = aircraftFilters.priority_flight.trim().toUpperCase();
    if (!flight) return;
    const entry: FavoriteFlight = { flight, only: aircraftFilters.priority_flight_exclusive };
    const next = [
      entry,
      ...favoriteFlights.filter(f => f.flight !== entry.flight || f.only !== entry.only),
    ].slice(0, FAVORITE_FLIGHTS_MAX);
    try {
      localStorage.setItem(FAVORITE_FLIGHTS_KEY, JSON.stringify(next));
      setFavoriteFlights(next);
    } catch (e) {
      addLog(`Flight favorites store error: ${e}`);
    }
  };

  const myCallsign = myCallOverride || wsjtxLive.de_call?.trim().toUpperCase() || null;
  const dxCall = dxCallsign ? cleanCall(dxCallsign) || null : null;
  const wsjtxMyGrid = normalizeGrid(wsjtxLive.de_grid);
  const myGrid = myGridOverride || (isValidLocator(wsjtxMyGrid) ? wsjtxMyGrid : lookupGrid(myCallsign));
  const wsjtxDxGrid = normalizeGrid(wsjtxLive.dx_grid);
  const selectedDxGrid = dxLocal ? (dxGrid ?? lookupGrid(dxCall))
    : isValidLocator(wsjtxDxGrid) ? wsjtxDxGrid
    : !wsjtxLive.dx_call ? (dxGrid ?? lookupGrid(dxCall)) : lookupGrid(dxCall);
  useEffect(() => {
    if (!dxCall) return;
    try {
      localStorage.setItem(LAST_DX_KEY, JSON.stringify({ call: dxCall, grid: selectedDxGrid ?? null }));
    } catch (e) {
      addLog(`DX station save error: ${e}`);
    }
  }, [dxCall, selectedDxGrid, addLog]);
  const stationDistanceKm = isValidLocator(myGrid) && isValidLocator(selectedDxGrid)
    ? haversineKm(...locatorToLatlon(myGrid), ...locatorToLatlon(selectedDxGrid))
    : null;
  const dxBearing = isValidLocator(myGrid) && isValidLocator(selectedDxGrid)
    ? bearingDeg(...locatorToLatlon(myGrid), ...locatorToLatlon(selectedDxGrid))
    : null;

  useEffect(() => {
    if (!status) return;
    rpc("set_station_calls", { call_a: myCallsign, call_b: dxCall })
      .catch(e => addLog(`Station calls error: ${e}`));
    rpc("airscout_set_stations", {
      locator_a: isValidLocator(myGrid) ? myGrid : null,
      locator_b: isValidLocator(selectedDxGrid) ? selectedDxGrid : null,
      dx_local: dxLocal,
    }).catch(e => addLog(`Station geometry error: ${e}`));
  }, [status === null, myCallsign, myGrid, myCallOverride, myGridOverride, dxCall, selectedDxGrid, dxLocal, addLog]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    localStorage.setItem(ROTATOR_CONTROL_KEY, String(rotatorControlEnabled));
    if (!status) return;
    rpc("set_rotator_control", { enabled: rotatorControlEnabled }).catch(() => {});
  }, [status === null, rotatorControlEnabled]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!status) return;
    rpc("set_pointing_mode", { mode: uiMode }).catch(() => {});
  }, [status === null, uiMode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!status || myStationRestored.current) return;
    myStationRestored.current = true;
    const savedAlt = localStorage.getItem(MY_ALT_KEY) ?? "";
    if (myCallOverride || myGridOverride || savedAlt) {
      rpc("set_my_station_override", { callsign: myCallOverride || null, grid: myGridOverride || null, altitude_m: savedAlt === "" ? null : Number(savedAlt) })
        .catch(e => addLog(`MY station restore error: ${e}`));
    }
  }, [status, myCallOverride, myGridOverride, addLog]);

  useEffect(() => {
    if (!status) return;
    const saved = Number(localStorage.getItem(DX_ALT_KEY) ?? "0");
    if (Number.isFinite(saved) && saved !== 0) {
      rpc("set_dx_altitude", { altitude_m: saved }).catch(e => addLog(`DX altitude restore error: ${e}`));
    }
  }, [status === null]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!myCallEditing.current) setMyCallInput(myCallOverride || wsjtxLive.de_call || "");
  }, [myCallOverride, wsjtxLive.de_call]);

  async function applyMyStation(reset = false) {
    const call = reset ? "" : myCallInput.trim().toUpperCase();
    const grid = reset ? "" : myGridInput.trim().toUpperCase();
    if (call && (!/^[A-Z0-9/]+$/.test(call) || !/[A-Z]/.test(call) || !/[0-9]/.test(call))) {
      addLog("MY callsign must contain letters and digits, with optional /");
      return;
    }
    if (grid && !isValidLocator(grid)) {
      addLog("MY grid must be a valid 4, 6, or 8 character Maidenhead locator");
      return;
    }
    const altText = myAltInput.trim();
    const altM = altText === "" ? null : Number(altText);
    if (altM !== null && (!Number.isFinite(altM) || altM < -500 || altM > 9000)) {
      addLog("MY altitude must be a number of metres between -500 and 9000");
      return;
    }
    setMyStationBusy(true);
    try {
      await rpc("set_my_station_override", { callsign: call || null, grid: grid || null, altitude_m: altM });
      localStorage.setItem(MY_ALT_KEY, altText);
      setMyAltitudeM(altM ?? 0);
      localStorage.setItem(MY_CALL_OVERRIDE_KEY, call);
      localStorage.setItem(MY_GRID_OVERRIDE_KEY, grid);
      if (reset) savePortableGrid(myCallsign, null);
      setMyCallOverride(call);
      setMyGridOverride(grid);
      setMyCallInput(call || wsjtxLive.de_call || "");
      setMyGridInput(grid || wsjtxLive.de_grid || "");
      addLog(reset ? "MY station reset to WSJT-X" : `MY station override: ${call || "WSJT-X"} ${grid || "WSJT-X"}`);
    } catch (e) {
      addLog(`MY station error: ${e}`);
    } finally {
      setMyStationBusy(false);
    }
  }

  useEffect(() => {
    localStorage.setItem(PORTABLE_VIEW_KEY, portableView);
  }, [portableView]);

  useEffect(() => {
    localStorage.setItem(OVERLAY_RECT_KEY, JSON.stringify(overlayRect));
  }, [overlayRect]);

  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    // Native CSS resize doesn't raise React events; persist the size it produces.
    const ro = new ResizeObserver(() => {
      const w = Math.round(el.offsetWidth);
      const h = Math.round(el.offsetHeight);
      setOverlayRect((r) => (r.w === w && r.h === h ? r : { ...r, w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  });

  useEffect(() => {
    const onResize = () => setOverlayRect((r) => {
      const c = clampOverlayRect(r);
      return c.x === r.x && c.y === r.y && c.w === r.w && c.h === r.h ? r : c;
    });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    if (!myGridEditing.current) setMyGridInput(myGrid ?? "");
  }, [myGrid]);
  useEffect(() => {
    if (!dxGridEditing.current) setDxGridInput(selectedDxGrid ?? "");
  }, [selectedDxGrid]);
  useEffect(() => {
    if (!dxCallEditing.current) setDxCallInput(dxCall ?? "");
  }, [dxCall]);
  useEffect(() => {
    const stored = {
      ...loadAircraftFilters(airscout?.station_a, airscout?.station_b),
      ...activeFlightRef.current,
    };
    setAircraftFilters(stored);
    rpc("airscout_apply_filters", { filters: stored }).catch(e => addLog(`Aircraft filter restore: ${e}`));
  }, [airscout?.station_a, airscout?.station_b]);

  function savePortableGrid(callsign: string | null, grid: string | null) {
    if (!callsign) return;
    const call = callsign.trim().toUpperCase();
    if (!call) return;
    setStationGridOverrides(prev => {
      const next = { ...prev };
      if (grid) next[call] = grid;
      else delete next[call];
      localStorage.setItem(PORTABLE_GRID_OVERRIDES_KEY, JSON.stringify(next));
      return next;
    });
  }

  const myUsesLocal = !!(myCallOverride || myGridOverride);
  const dxUsesLocal = dxLocal;

  // Newest first, FIFO capped at 20. A station that matches an entry exactly (call, grid and
  // altitude) moves to the top instead of being listed twice.
  function storeDx(rawCall: string, grid: string | null, alt: string) {
    const call = cleanCall(rawCall);
    if (!isCompleteCall(call)) return;
    const entry: FavoriteDx = { call, grid: normalizeGrid(grid), alt: alt.trim() || "0" };
    setFavoriteDx(prev => {
      const next = [entry, ...prev.filter(r => !(r.call === call && favGrid(r) === favGrid(entry) && favAlt(r) === favAlt(entry)))]
        .slice(0, FAVORITE_DX_MAX);
      localStorage.setItem(FAVORITE_DX_KEY, JSON.stringify(next));
      return next;
    });
  }
  function chooseDxMode(local: boolean) {
    setDxLocal(local);
    localStorage.setItem(DX_LOCAL_KEY, local ? "1" : "0");
  }

  // Selecting a DX station by hand is a local choice; WSJT-X-driven changes are not.
  function selectDx(call: string, grid: string | null, local: boolean, name?: string | null, alt?: string) {
    const dxLocator = grid ?? lookupGrid(call);
    if (alt !== undefined || call !== dxCallsign) {
      // a different station starts at 0 m unless a favorite supplies its altitude
      const nextAlt = alt ?? "";
      setDxAltInput(nextAlt);
      localStorage.setItem(DX_ALT_KEY, nextAlt);
      rpc("set_dx_altitude", { altitude_m: nextAlt === "" ? 0 : Number(nextAlt) })
        .catch(e => addLog(`DX altitude error: ${e}`));
    }
    setDxCallsign(call);
    setDxGrid(grid);
    if (local) chooseDxMode(true);
    addLog(dxLocator
      ? `DX → ${call} ${dxLocator}${name ? ` (${name})` : ""}`
      : `DX → ${call} (enter portable grid manually)`);
  }

  // WSJT-X mode: follow both the DX call and grid WSJT-X reports (the callbook is only the fallback)
  useEffect(() => {
    if (dxLocal) return;
    const wsjtxDx = cleanCall(wsjtxLive.dx_call);
    if (!wsjtxDx || !isCompleteCall(wsjtxDx)) return;
    const grid = isValidLocator(wsjtxDxGrid) ? wsjtxDxGrid : null;
    if (wsjtxDx !== dxCallsign || grid !== dxGrid) {
      selectDx(wsjtxDx, grid, false);
    }
  }, [wsjtxLive.dx_call, wsjtxLive.dx_grid, dxLocal]); // eslint-disable-line react-hooks/exhaustive-deps

  // Local mode: typed call/grid take effect as soon as they are complete and valid
  useEffect(() => {
    if (!dxLocal) return;
    const call = cleanCall(dxCallInput);
    const grid = normalizeGrid(dxGridInput);
    if (!isCompleteCall(call)) return;
    if (grid && !isValidLocator(grid)) return;
    if (call === dxCallsign && (grid ?? lookupGrid(call)) === selectedDxGrid) return;
    const t = setTimeout(() => {
      savePortableGrid(call, grid);
      selectDx(call, grid, true);
    }, 500);
    return () => clearTimeout(t);
  }, [dxCallInput, dxGridInput, dxLocal]); // eslint-disable-line react-hooks/exhaustive-deps
  function applyDxStation(reset: boolean, toggle = false) {
    const altText = dxAltInput.trim();
    const altM = altText === "" ? 0 : Number(altText);
    if (!Number.isFinite(altM) || altM < -500 || altM > 9000) {
      addLog("DX altitude must be a number of metres between -500 and 9000");
      return;
    }
    if (reset || (!dxLocal && !toggle)) {
      localStorage.setItem(DX_ALT_KEY, altText);
      rpc("set_dx_altitude", { altitude_m: altM }).catch(e => addLog(`DX altitude error: ${e}`));
    }
    if (reset) {
      chooseDxMode(false);
      if (dxCall) savePortableGrid(dxCall, null);
      addLog("DX station follows WSJT-X");
      return;
    }
    if (!dxLocal && !toggle) {
      // WSJT-X mode: Store DX keeps the current station and its altitude without changing mode
      if (!dxCall) {
        addLog("No DX station to store");
        return;
      }
      storeDx(dxCall, selectedDxGrid, altText);
      addLog(`Stored DX ${dxCall}`);
      return;
    }
    chooseDxMode(true);
    const call = cleanCall(dxCallInput) || dxCall;
    if (!call) {
      addLog(toggle ? "DX station is Local; enter a callsign and press Apply" : "Set DX callsign before applying DX grid");
      return;
    }
    const grid = normalizeGrid(dxGridInput);
    if (grid && !isValidLocator(grid)) {
      addLog("DX grid must be a valid 4, 6, or 8 character Maidenhead locator");
      return;
    }
    savePortableGrid(call, grid);
    selectDx(call, grid, true, null, altText);
    if (!toggle) {
      storeDx(call, grid ?? lookupGrid(call), altText);
      addLog(`Stored DX ${call}`);
    }
  }
  const currentAz = autoTargetInvalid && lastPlaneTarget ? lastPlaneTarget.az
    : lockedPlane && uiMode !== "manual" ? (Number.isFinite(lockedPlane.az_a) ? lockedPlane.az_a : rotatorAz) : rotatorAz;
  const currentEl = autoTargetInvalid && lastPlaneTarget ? lastPlaneTarget.el
    : lockedPlane && uiMode !== "manual" ? (Number.isFinite(lockedPlane.el_a) ? lockedPlane.el_a : rotatorEl) : rotatorEl;
  const rotorOn = rotatorControlEnabled && rotatorConnected;
  const lastSpeechPreviewAt = useRef(0);
  function previewSpeech() {
    if (!isSpeechSupported() || !rotatorSpeechEnabled || autoTargetInvalid || !(rotorOn || uiMode !== "manual")) return;
    const now = Date.now();
    if (now - lastSpeechPreviewAt.current < 1200) return;
    lastSpeechPreviewAt.current = now;
    const az = lockedPlane && uiMode !== "manual" ? lockedPlane.az_a : rotatorTargetAz ?? rotatorAz;
    const el = lockedPlane && uiMode !== "manual" ? lockedPlane.el_a : rotatorTargetEl ?? rotatorEl;
    void speak(`Azimuth ${Math.round(az)}, Elevation ${Math.round(el)}`).catch(e => addLog(`Speech: ${e}`));
  }
  function setSpeechEnabled(enabled: boolean) {
    setRotatorSpeechEnabled(enabled);
    if (!enabled) {
      void cancelSpeech().catch(e => addLog(`Speech: ${e}`));
      rotatorSpeechLastAnnouncedRef.current = null;
    }
  }
  const portableReadout = (
    <AzElReadout
      className="portable-panel"
      az={currentAz} el={currentEl} invalid={autoTargetInvalid} fontPx={PORTABLE_BASE_FONT_PX}
    />
  );

  const portableDisplay = portableView === "hidden" ? null : portableReadout;

  // Sky Dome and the Az/El overlay float in their own native windows on desktop so they can leave
  // the app window. Browsers (and a failed window creation) fall back to in-page floating panels.
  const [floatFallback, setFloatFallback] = useState(false);
  const nativeFloat = nativeFloatSupported() && !floatFallback;
  const wantSkyDomeWindow = showSkyDome;
  const wantOverlayWindow = portableView === "overlay";
  const floatStateRef = useRef<FloatState | null>(null);
  floatStateRef.current = {
    aircraft: skyDomeAircraft,
    selectedAircraftId: lockedPlane?.icao24 ?? null,
    stationLocator: airscout?.station_a ?? myGrid,
    stationAltitudeM: myAltitudeM,
    rotatorAz,
    rotatorEl,
    targetAz: lockedPlane ? lockedPlane.az_a : rotatorTargetAz,
    targetEl: lockedPlane ? lockedPlane.el_a : rotatorTargetEl,
    pathBearing: dxBearing,
    dxLabel: dxCall,
    myLabel: myCallsign,
    readoutAz: currentAz,
    readoutEl: currentEl,
    readoutInvalid: autoTargetInvalid,
  };
  const lastFloatJsonRef = useRef("");
  const publishFloatState = useCallback((force = false) => {
    if (!floatStateRef.current) return;
    const json = JSON.stringify(floatStateRef.current);
    if (!force && json === lastFloatJsonRef.current) return;
    lastFloatJsonRef.current = json;
    void emit(FLOAT_STATE_EVENT, floatStateRef.current);
  }, []);

  useEffect(() => {
    if (!nativeFloat) return;
    for (const [kind, wanted] of [["skydome", wantSkyDomeWindow], ["overlay", wantOverlayWindow]] as const) {
      const action = syncFloatWindow(kind, wanted);
      action.catch((e) => {
        if (!wanted) return;
        addLog(`Floating ${kind} window unavailable, showing it in the app instead: ${e}`);
        setFloatFallback(true);
      });
    }
  }, [nativeFloat, wantSkyDomeWindow, wantOverlayWindow, addLog]);

  useEffect(() => {
    if (!nativeFloat) return;
    const offs = [
      listen(FLOAT_READY_EVENT, () => publishFloatState(true)),
      listen<FloatAction>(FLOAT_ACTION_EVENT, (e) => {
        const a = e.payload;
        if (a.type === "lock") {
          rpc("lock_aircraft", { icao24: a.icao24 }).catch(err => addLog(`Lock: ${err}`));
        } else {
          setRotatorTargetAz(a.az);
          setRotatorTargetEl(a.el);
          rpc("rotator_set", { azimuth: a.az, elevation: a.el }).catch(err => addLog(`Rotator: ${err}`));
        }
      }),
      listen<{ kind: FloatKind }>(FLOAT_CLOSED_EVENT, (e) => {
        if (e.payload.kind === "skydome") {
          setShowSkyDome(false);
          localStorage.setItem("airwsjtx.showSkyDome", "false");
        } else {
          setPortableView("hidden");
        }
      }),
    ];
    return () => { offs.forEach((p) => void p.then((f) => f())); };
  }, [nativeFloat, publishFloatState, addLog]);

  useEffect(() => {
    if (nativeFloat && (wantSkyDomeWindow || wantOverlayWindow)) publishFloatState();
  });

  return (
    <div className={`app${AZEL_PANEL_ENABLED && portableView === "panel" ? " has-display-panels" : ""}`}>
      {!nativeFloat && portableView === "overlay" && (
        <div
          ref={overlayRef}
          className="overlay-frame"
          aria-label="Az / El overlay"
          style={{ left: overlayRect.x, top: overlayRect.y, width: overlayRect.w, height: overlayRect.h }}
        >
          <AzElReadout
            className="portable-overlay"
            az={currentAz} el={currentEl} invalid={autoTargetInvalid}
            headerProps={{
              className: "portable-header overlay-drag",
              onPointerDown: (e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                overlayDragRef.current = { dx: e.clientX - overlayRect.x, dy: e.clientY - overlayRect.y };
              },
              onPointerMove: (e) => {
                const d = overlayDragRef.current;
                if (d) setOverlayRect((r) => clampOverlayRect({ ...r, x: e.clientX - d.dx, y: e.clientY - d.dy }));
              },
              onPointerUp: () => { overlayDragRef.current = null; },
              onPointerCancel: () => { overlayDragRef.current = null; },
            }}
          />
        </div>
      )}

      {/* ── Top bar ── */}
      <header className="topbar">
        <h1>AirWSJT-X</h1>
        <span className="badge" style={{ background: stateColor + "22", color: stateColor }}>
          {displayState.replace(/_/g, " ")}
        </span>
        {wsjtxLive.transmitting && (
          <span className="badge" style={{ background: "#f8514922", color: "#f85149" }}>PTT</span>
        )}
        <div style={{ width: 1, height: 20, background: "var(--border)", margin: "0 4px" }} />
        {/* WSJT-X inline status */}
        <div className="wsjtx-strip">
          <span style={{ color: wsjtxLive.connected ? "var(--green)" : "var(--red)", fontSize: 8 }}>●</span>
          {wsjtxLive.connected ? (
            <>
              <span style={{ fontFamily: "monospace", fontWeight: 700, color: "var(--accent)" }}>
                {wsjtxLive.de_call ?? "—"}
              </span>
              {wsjtxLive.de_grid && <span style={{ color: "var(--text-muted)" }}>{wsjtxLive.de_grid}</span>}
              {wsjtxLive.freq_mhz != null && (
                <span style={{ color: "var(--text)" }}>{wsjtxLive.freq_mhz.toFixed(3)} MHz</span>
              )}
              {wsjtxLive.mode && (
                <span style={{ color: "var(--text-muted)" }}>
                  {wsjtxLive.mode}{wsjtxLive.tr_period ? `-${wsjtxLive.tr_period}s` : ""}
                </span>
              )}
              <span style={{ color: wsjtxLive.decoding ? "var(--green)" : "var(--text-muted)", fontSize: 10 }}>
                {wsjtxLive.decoding ? "DEC●" : "DEC○"}
              </span>
              <span
                style={{ color: wsjtxLive.transmitting ? "var(--red)" : "var(--text-muted)", fontSize: 10 }}
                title={txIndicatorTitle}
              >
                {wsjtxLive.transmitting ? "TX●" : "TX○"}
              </span>
              {wsjtxLive.rx_df != null && (
                <span style={{ color: "var(--text-muted)", fontSize: 11 }}>RX {wsjtxLive.rx_df}Hz</span>
              )}
              {wsjtxLive.tx_df != null && (
                <span
                  style={{ color: "var(--text-muted)", fontSize: 11 }}
                  title={txOffsetTitle ?? undefined}
                >
                  TX {wsjtxLive.tx_df}Hz
                </span>
              )}
            </>
          ) : (
            <span style={{ color: "var(--text-muted)" }}>WSJT-X not connected</span>
          )}
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: "var(--text-muted)" }}>
            Rig
            <DarkSelect
              ariaLabel="Rig"
              value={wsjtxRigFilter}
              disabled={wsjtxRigFilterBusy}
              options={[
                { value: "", label: "Any rig" },
                ...(wsjtxRigFilter && !wsjtxKnownRigs.some(rig => rig.app_id === wsjtxRigFilter)
                  ? [{ value: wsjtxRigFilter, label: `${wsjtxRigFilter} (saved)` }]
                  : []),
                ...wsjtxKnownRigs.map(rig => ({
                  value: rig.app_id,
                  label: rig.config_name ? `${rig.app_id} (${rig.config_name})` : rig.app_id,
                })),
              ]}
              onChange={(v) => { void applyWsjtxRigFilter(v); }}
            />
          </label>
          {wsjtxRigDiscoveryError && (
            <span title={wsjtxRigDiscoveryError} style={{ fontSize: 10, color: "var(--yellow)" }}>
              Rig discovery unavailable
            </span>
          )}
          <span style={{ fontSize: 10, color: connectivityColor, fontWeight: 600 }}>
            {connectivityLabel}
          </span>
          <span style={{ fontSize: 10, color: sourceDotColor }}>
            {activeSource ? "●" : "○"} {activeSource ?? "no source"} · {freshnessLabel}
          </span>
          {rotatorConnected
            ? <span style={{ fontSize: 10, color: "var(--green)" }}>● rotctld</span>
            : <span style={{ fontSize: 10, color: "var(--red)" }}>○ rotctld</span>}
          {airscout && (
            <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
              {airscout.total} aircraft
            </span>
          )}
        </div>
      </header>

      <div className="display-toolbar">
        <div className="portable-toolbar" role="group" aria-label="Az / El display controls">
          <span>Az / El</span>
          <div className="portable-controls">
            {AZEL_PANEL_ENABLED && (
              <button className="portable-mode" aria-pressed={portableView === "panel"} onClick={() => setPortableView("panel")}>Panel</button>
            )}
            <button
              className="portable-mode"
              aria-pressed={portableView === "overlay"}
              title={portableView === "overlay" ? "Close the Az / El window" : "Open the Az / El window"}
              onClick={() => setPortableView(portableView === "overlay" ? "hidden" : "overlay")}
            >{portableView === "overlay" ? "Close" : "Open"}</button>
          </div>
        </div>

        <div className="portable-toolbar" role="group" aria-label="Sky Dome display controls">
          <span>Sky Dome</span>
          <div className="portable-controls">
            <button
              className="portable-mode"
              aria-pressed={showSkyDome}
              aria-controls="skydome-panel"
              title={showSkyDome ? "Close the Sky Dome window" : "Open the Sky Dome window"}
              onClick={() => {
                setShowSkyDome(!showSkyDome);
                localStorage.setItem("airwsjtx.showSkyDome", String(!showSkyDome));
              }}
            >{showSkyDome ? "Close" : "Open"}</button>
          </div>
        </div>
        {wsjtxError && (
          <div className="wsjtx-error" role="alert" aria-label="WSJT-X UDP error">
            {wsjtxError}
          </div>
        )}
      </div>

      {AZEL_PANEL_ENABLED && portableView === "panel" && (
        <div className="display-panels">
          {portableDisplay}
        </div>
      )}
      {showSkyDome && !nativeFloat && (
        <section
          ref={skyDomeRef}
          className="skydome-panel skydome-float"
          id="skydome-panel"
          aria-label="Sky Dome"
          style={{ left: skyDomeRect.x, top: skyDomeRect.y, width: skyDomeRect.w, height: skyDomeRect.h }}
        >
          <h3
            className="portable-header skydome-drag"
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId);
              skyDomeDragRef.current = { dx: e.clientX - skyDomeRect.x, dy: e.clientY - skyDomeRect.y };
            }}
            onPointerMove={(e) => {
              const d = skyDomeDragRef.current;
              if (d) setSkyDomeRect((r) => clampSkyDomeRect({ ...r, x: e.clientX - d.dx, y: e.clientY - d.dy }));
            }}
            onPointerUp={() => { skyDomeDragRef.current = null; }}
            onPointerCancel={() => { skyDomeDragRef.current = null; }}
          >Sky Dome</h3>
          <SkyDome
            aircraft={skyDomeAircraft}
            selectedAircraftId={lockedPlane?.icao24 ?? null}
            stationLocator={airscout?.station_a ?? myGrid}
            stationAltitudeM={myAltitudeM}
            rotatorAz={rotatorAz}
            rotatorEl={rotatorEl}
            targetAz={lockedPlane ? lockedPlane.az_a : rotatorTargetAz}
            targetEl={lockedPlane ? lockedPlane.el_a : rotatorTargetEl}
            pathBearing={dxBearing}
            dxLabel={dxCall}
            myLabel={myCallsign}
            onClickAircraft={(icao24) => rpc("lock_aircraft", { icao24 }).catch(e => addLog(`Lock: ${e}`))}
            onClickSky={(az, el) => {
              setRotatorTargetAz(az);
              setRotatorTargetEl(el);
              rpc("rotator_set", { azimuth: az, elevation: el }).catch(e => addLog(`Rotator: ${e}`));
            }}
          />
        </section>
      )}

      {/* ── 3-column content ── */}
      <div className="content">

        {/* LEFT: Stations + Plane Lock */}
        <div className="col" style={{ overflowY: "auto" }}>

          {/* Compact station cards */}
          <div className="card" style={{ flexShrink: 0 }}>
            <h3>Stations</h3>
            <div className="station-compact">
              <div className="station-pill" style={{ borderColor: "#58a6ff44" }}>
                <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 1 }}>MY</div>
                <div className="call" style={{ color: "var(--accent)" }}>{myCallsign ?? "—"}</div>
                <div className="grid">{myGrid ?? "—"}</div>
              </div>
              <div className="station-distance" style={{ flex: "0 0 auto", fontSize: 11, color: "var(--text-muted)", whiteSpace: "nowrap", textAlign: "center" }}>
                ← {stationDistanceKm === null ? "--" : stationDistanceKm.toFixed(1)} km →
              </div>
              <div className="station-pill" style={{ borderColor: "#3fb95044" }}>
                <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 1 }}>DX</div>
                <div className="call" style={{ color: "var(--green)" }}>{dxCall ?? "—"}</div>
                <div className="grid">{selectedDxGrid ?? "—"}</div>
              </div>
            </div>
            <div style={{ display: "grid", gap: 3, marginTop: 6 }}>
              <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
                <span style={{ fontSize: 10, color: "var(--text-muted)", width: 17, flex: "0 0 auto" }}>MY</span>
                <input
                  aria-label="MY callsign"
                  value={myCallInput}
                  maxLength={10}
                  onChange={e => setMyCallInput(e.target.value.toUpperCase())}
                  onFocus={() => { myCallEditing.current = true; }}
                  onBlur={() => { myCallEditing.current = false; }}
                  placeholder="Callsign"
                  style={{ ...STATION_INPUT_STYLE, width: "10.4ch" }}
                />
                <input
                  aria-label="MY grid"
                  value={myGridInput}
                  maxLength={10}
                  onChange={(e) => setMyGridInput(e.target.value.toUpperCase())}
                  onFocus={() => { myGridEditing.current = true; }}
                  onBlur={() => { myGridEditing.current = false; }}
                  placeholder={wsjtxLive.de_grid ?? "Grid"}
                  style={{ ...STATION_INPUT_STYLE, width: "10.4ch" }}
                />
                <button className="btn-secondary" aria-label="MY station source" aria-pressed={myUsesLocal}
                  disabled={myStationBusy}
                  title={myUsesLocal ? "Using local MY values; click to use WSJT-X" : "Using WSJT-X values; click to use the local values typed here"}
                  style={STATION_SOURCE_BTN_STYLE}
                  onClick={() => { void applyMyStation(myUsesLocal); }}>
                  {myUsesLocal ? "Local" : "WSJT-X"}
                </button>
              </div>
              <div style={{ display: "flex", gap: 3, alignItems: "center", paddingLeft: 20 }}>
                <input
                  aria-label="MY altitude (m)"
                  title="Station altitude above sea level, metres"
                  value={myAltInput}
                  onChange={(e) => setMyAltInput(e.target.value)}
                  inputMode="decimal"
                  placeholder="Alt m"
                  style={{ ...STATION_INPUT_STYLE, width: "7ch" }}
                />
                <button className="btn-secondary" aria-label="Apply MY station" disabled={myStationBusy}
                  style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => { void applyMyStation(); }}>
                  Apply
                </button>
              </div>
              <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
                <span style={{ fontSize: 10, color: "var(--text-muted)", width: 17, flex: "0 0 auto" }}>DX</span>
                <input
                  aria-label="DX callsign"
                  value={dxCallInput}
                  maxLength={10}
                  onChange={(e) => { setDxCallInput(e.target.value.toUpperCase()); chooseDxMode(true); }}
                  onFocus={() => { dxCallEditing.current = true; }}
                  onBlur={() => { dxCallEditing.current = false; }}
                  placeholder="Callsign"
                  style={{ ...STATION_INPUT_STYLE, width: "10.4ch" }}
                />
                <input
                  aria-label="DX grid"
                  value={dxGridInput}
                  maxLength={10}
                  onChange={(e) => { setDxGridInput(e.target.value.toUpperCase()); chooseDxMode(true); }}
                  onFocus={() => { dxGridEditing.current = true; }}
                  onBlur={() => { dxGridEditing.current = false; }}
                  placeholder={dxGrid ?? "Grid"}
                  style={{ ...STATION_INPUT_STYLE, width: "10.4ch" }}
                />
                <button className="btn-secondary" aria-label="DX station source" aria-pressed={dxUsesLocal}
                  title={dxUsesLocal ? "Using local DX values; click to use WSJT-X / callbook" : "Using WSJT-X / callbook values; click to use the local values typed here"}
                  style={STATION_SOURCE_BTN_STYLE}
                  onClick={() => applyDxStation(dxUsesLocal, true)}>
                  {dxUsesLocal ? "Local" : "WSJT-X"}
                </button>
              </div>
              <div style={{ display: "flex", gap: 3, alignItems: "center", paddingLeft: 20 }}>
                <input
                  aria-label="DX altitude (m)"
                  title="DX station altitude above sea level, metres (default 0)"
                  value={dxAltInput}
                  onChange={(e) => setDxAltInput(e.target.value)}
                  inputMode="decimal"
                  placeholder="Alt m"
                  style={{ ...STATION_INPUT_STYLE, width: "7ch" }}
                />
                <button className="btn-secondary" aria-label="Store DX station" style={{ fontSize: 10, padding: "2px 8px" }}
                  title="Save this DX station and altitude in the DX favorites list"
                  onClick={() => applyDxStation(false)}>
                  Store DX
                </button>
              </div>
            </div>
            {/* DX favorites */}
            <div id="dx-favorites-label" style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 6 }}>DX favorites</div>
            <div style={{ display: "flex", gap: 3, marginTop: 2, flexWrap: "wrap", paddingLeft: 20 }}>
              <DarkSelect
                ariaLabel="DX favorites"
                value=""
                placeholder="DX favorites"
                width={140}
                popupWidth={240}
                textColor="var(--text)"
                columnTemplate="10ch 8ch minmax(0, 1fr)"
                options={favoriteDx.map((f, i) => ({
                  value: String(i),
                  label: `${f.call} ${f.grid ?? "—"} ${favAlt(f)} m`,
                  columns: [f.call, f.grid ?? "—", `${favAlt(f)} m`],
                }))}
                onChange={(value) => {
                  const f = favoriteDx[Number(value)];
                  if (!f) return;
                  setDxCallInput(f.call);
                  setDxGridInput(f.grid ?? "");
                  selectDx(f.call, f.grid, true, null, f.alt ?? "");
                }}
              />
              <button className="btn-secondary" style={{ fontSize: 10, padding: "3px 8px" }}
                onClick={async () => {
                  const call = prompt("DX callsign:");
                  if (!call) return;
                  const info = await rpc("callsign_lookup", { callsign: call }) as { callsign: string; grid: string | null; name: string | null };
                  const nextCall = info.callsign.trim().toUpperCase();
                  const lookedUpGrid = normalizeGrid(info.grid);
                  selectDx(nextCall, lookedUpGrid, true, info.name);
                }}>
                + Other
              </button>
            </div>
            <div style={{ display: "flex", gap: 3, marginTop: 6, flexWrap: "nowrap" }}>              <button className="btn-secondary" style={{ fontSize: 10, padding: "3px 8px", whiteSpace: "nowrap" }}
                onClick={async () => {
                  setCallsignDbBusy(true);
                  try {
                    const s = await rpc("callsign_db_check_update") as CallsignDbStatus;
                    setCallsignDbStatus(s);
                    addLog(
                      s.online
                        ? (s.update_available
                          ? `Callsign DB update available (${s.remote_version ?? "new version"})`
                          : "Callsign DB is up to date")
                        : "Callsign DB update check failed (offline)",
                    );
                  } catch (e) {
                    addLog(`Callsign DB check failed: ${e}`);
                  } finally {
                    setCallsignDbBusy(false);
                  }
                }}
                disabled={callsignDbBusy}>
                DB Check
              </button>
              <button className="btn-secondary" style={{ fontSize: 10, padding: "3px 8px", whiteSpace: "nowrap" }}
                onClick={async () => {
                  setCallsignDbBusy(true);
                  try {
                    const s = await rpc("callsign_db_download") as CallsignDbStatus;
                    setCallsignDbStatus(s);
                    addLog(`Callsign DB downloaded (${s.entries} entries, ${s.version ?? "no version"})`);
                  } catch (e) {
                    addLog(`Callsign DB download failed: ${e}`);
                  } finally {
                    setCallsignDbBusy(false);
                  }
                }}
                disabled={callsignDbBusy}>
                DB Update
              </button>
              <button className="btn-secondary" style={{ fontSize: 10, padding: "3px 8px", whiteSpace: "nowrap" }}
                onClick={async () => {
                  const call = prompt("Contribute callsign:");
                  if (!call) return;
                  const grid = prompt("Contribute grid (Maidenhead):");
                  if (!grid) return;
                  try {
                    const result = await rpc("callsign_db_submit", { callsign: call, grid }) as { accepted: boolean; queued: number };
                    addLog(
                      result.accepted
                        ? `Contribution submitted: ${call.toUpperCase()} ${grid.toUpperCase()}`
                        : `Contribution queued offline (${result.queued} pending)`,
                    );
                  } catch (e) {
                    addLog(`Contribution failed: ${e}`);
                  }
                }}>
                Contribute
              </button>
            </div>
            {callsignDbStatus && (
              <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-muted)" }}>
                Callsign DB: {callsignDbStatus.version ?? "not downloaded"} · {callsignDbStatus.entries} entries ·
                {callsignDbStatus.fresh ? " fresh" : " stale"}
                {callsignDbStatus.update_available ? " · update available" : ""}
              </div>
            )}
          </div>

          {/* Plane Lock */}
          <div className="card plane-lock-card" style={{ flex: "0 0 auto", overflowY: "visible" }}>
            <PlaneLockPanel
              locked={lockedPlane}
              nowEpochS={nowEpochS}
              pollIntervalS={activePollIntervalS}
              autoLock={autoLock}
              sessionState={displayState}
              onAutoLockToggle={(v) => {
                setAutoLock(v);
                rpc("auto_lock_set", { enabled: v }).catch(() => {});
              }}
              onUnlock={() => { setLockedPlane(null); rpc("unlock_aircraft", {}).catch(() => {}); }}
              onLockAircraft={(icao24) => rpc("lock_aircraft", { icao24 }).catch(e => addLog(`Lock: ${e}`))}
              sourceStatusText={activeSource ? `${activeSource} ${activeSourceState}` : undefined}
              priorityFlight={aircraftFilters.priority_flight}
              aircraft={aircraft.slice(0, 5).map(ac => ({
                icao24: ac.icao24, callsign: ac.callsign, altitude_ft: ac.altitude_ft,
                scatter_score: ac.scatter_score, az_a: ac.az_a, el_a: ac.el_a,
                az_b: ac.az_b, el_b: ac.el_b, off_path_deg: ac.off_path_deg,
              }))}
            />
          </div>
        </div>

        {/* CENTRE: Rotator */}
        <div className="col" style={{ alignItems: "center", justifyContent: "flex-start", overflowY: "auto" }}>
          <div className="card" style={{ width: "100%", alignSelf: "stretch", flexShrink: 0 }}>
            <div style={{ display: "flex", justifyContent: "flex-start", alignItems: "center", gap: 6, flexWrap: "nowrap", marginBottom: 8 }}>
              <button
                type="button"
                className="btn-secondary"
                title={rotatorControlEnabled
                  ? (rotatorConnected
                    ? "Rotor control is on. Click to block all rotator movement commands."
                    : "Rotor control is on but rotctld is not connected. Click to block all rotator movement commands.")
                  : "Rotor control is off: no movement commands are sent. Click to re-enable."}
                aria-pressed={!rotatorControlEnabled}
                style={{
                  fontSize: 10,
                  padding: "4px 10px",
                  whiteSpace: "nowrap",
                  border: rotorOn ? "1px solid var(--green)" : "1px solid var(--red)",
                  background: rotorOn ? "rgba(63,185,80,0.25)" : "rgba(248,81,73,0.25)",
                  color: rotorOn ? "var(--green)" : "var(--red)",
                }}
                onClick={() => {
                  const next = !rotatorControlEnabled;
                  setRotatorControlEnabled(next);
                  if (!next) return;
                  // Re-sending the displayed pointing once control resumes
                  const useLocked = uiMode !== "manual" && lockedPlane
                    && Number.isFinite(lockedPlane.az_a) && Number.isFinite(lockedPlane.el_a);
                  const az = useLocked ? lockedPlane!.az_a : rotatorTargetAz;
                  const el = useLocked ? lockedPlane!.el_a : rotatorTargetEl;
                  if (az === null || el === null || !rotatorConnected) return;
                  rpc("set_rotator_control", { enabled: true })
                    .then(() => rpc("rotator_set", { azimuth: az, elevation: el }))
                    .catch(e => addLog(`Rotator: ${e}`));
                }}
              >
                {rotorOn ? "Rotor: ON" : "Rotor: OFF"}
              </button>
              <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ display: "inline-flex", padding: 3, borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface)", gap: 4 }}>
                {(["rotator", "manual"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    className="btn-secondary"
                    style={{
                      fontSize: 10,
                      padding: "4px 10px",
                      border: uiMode === mode ? "1px solid var(--accent)" : "1px solid var(--border)",
                      background: uiMode === mode ? "rgba(88,166,255,0.12)" : "var(--surface)",
                      color: uiMode === mode ? "var(--accent)" : "var(--text-muted)",
                    }}
                    onClick={() => setUiMode(mode)}
                  >
                    {mode === "rotator" ? "Auto" : "Manual"}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="btn-secondary"
                aria-label={rotatorSpeechEnabled ? "Speech on" : "Speech off"}
                aria-pressed={rotatorSpeechEnabled}
                disabled={!isSpeechSupported()}
                title={isSpeechSupported()
                  ? `Speech callouts ${rotatorSpeechEnabled ? "on" : "off"}. Hover to preview the current azimuth and elevation.`
                  : "Web Speech API not available on this platform"}
                style={{
                  padding: "4px 7px",
                  lineHeight: 0,
                  border: rotatorSpeechEnabled ? "1px solid var(--green)" : "1px solid var(--border)",
                  background: rotatorSpeechEnabled ? "rgba(63,185,80,0.25)" : "#000",
                  color: rotatorSpeechEnabled ? "var(--green)" : "var(--text-muted)",
                }}
                onClick={() => setSpeechEnabled(!rotatorSpeechEnabled)}
                onMouseEnter={previewSpeech}
                onFocus={previewSpeech}
              >
                <svg width="18" height="14" viewBox="0 0 18 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M1 5h2.5L7 2v10L3.5 9H1z" fill="currentColor" />
                  <path d="M10 4.5a3.5 3.5 0 0 1 0 5" />
                  <path d="M12.5 2.5a6.5 6.5 0 0 1 0 9" />
                </svg>
              </button>
              </div>
            </div>
            <RotatorPanel
              currentAz={rotatorAz} currentEl={rotatorEl}
              targetAz={lockedPlane && uiMode !== "manual" ? lockedPlane.az_a : autoTargetInvalid && lastPlaneTarget ? lastPlaneTarget.az : rotatorTargetAz}
              targetEl={lockedPlane && uiMode !== "manual" ? lockedPlane.el_a : autoTargetInvalid && lastPlaneTarget ? lastPlaneTarget.el : rotatorTargetEl}
              targetInvalid={autoTargetInvalid}
              connected={rotatorConnected}
              dxAz={dxBearing}
              dxLabel={dxCall}
              myLabel={myCallsign}
              speechEnabled={rotatorSpeechEnabled}
              speechThresholdDeg={rotatorSpeechThresholdDeg}
              speechIntervalSec={rotatorSpeechIntervalSec}
              onSpeechEnabledChange={setSpeechEnabled}
              onSpeechThresholdDegChange={(value) => setRotatorSpeechThresholdDeg(Math.max(0.1, value))}
              onSpeechIntervalSecChange={(value) => setRotatorSpeechIntervalSec(Math.max(0, value))}
              mode={uiMode}
              aircraftLabel={uiMode !== "manual" && lockedPlane && lockedPlane.callsign ? `${lockedPlane.callsign} target` : "Manual target"}
              onSetPosition={(az, el) => {
                setRotatorTargetAz(az); setRotatorTargetEl(el);
                if (!rotatorControlEnabled) { addLog("Rotator control is off: command not sent"); return; }
                rpc("rotator_set", { azimuth: az, elevation: el }).catch(e => addLog(`Rotator: ${e}`));
              }}
              onStop={() => rpc("rotator_stop", {}).catch(e => addLog(`Rotator: ${e}`))}
              onPark={() => {
                if (!rotatorControlEnabled) { addLog("Rotator control is off: command not sent"); return; }
                setRotatorTargetAz(0); setRotatorTargetEl(0); rpc("rotator_park", {}).catch(e => addLog(`Rotator: ${e}`));
              }}
              onConnect={(host, port) => rpc("rotator_connect", { host, port }).catch(e => addLog(`Rotator: ${e}`))}
              onSync={(az, el) => { setRotatorTargetAz(az); setRotatorTargetEl(el); }}
              syncDisabled={uiMode !== "manual" && rotatorControlEnabled}
              clicksBlocked={!rotorOn}
              onBlockedClick={() => {
                addLog("Rotor off: compass click ignored");
                if (rotatorSpeechEnabled && isSpeechSupported()) void speak("Rotor off").catch(e => addLog(`Speech: ${e}`));
              }}
            />
          </div>
        </div>

        {/* RIGHT: Decodes + Aircraft + Log */}
        <div className="col" style={{ overflowY: "auto" }}>

          {/* WSJT-X Decodes */}
          <div className="card" style={{ flexShrink: 0 }}>
            <h3>WSJT-X Decodes (UTC) {wsjtxLive.connected && <span style={{ color: "var(--green)" }}>● live</span>}</h3>
            <div className="decode-list">
              {decodes.length === 0
                ? <div style={{ color: "var(--text-muted)", fontSize: 11 }}>No decodes yet</div>
                : decodes.slice(0, 8).map((d, i) => (
                  <div key={i} className="decode-row">
                    <span className="decode-time" style={{ color: "var(--text-muted)" }}>{d.ts}</span>
                    <span style={{ color: d.snr >= -10 ? "var(--green)" : d.snr >= -20 ? "var(--yellow)" : "var(--text-muted)" }}>
                      {d.snr >= 0 ? `+${d.snr}` : d.snr}dB
                    </span>
                    <span style={{ color: "var(--text-muted)" }}>{d.delta_freq_hz}Hz</span>
                    <span>{d.message ?? ""}</span>
                  </div>
                ))
              }
            </div>
          </div>

          {/* Aircraft scatter table */}
          <div className="card" style={{ flexShrink: 0 }}>
            <h3>
              Scatter Aircraft
              {airscout && <span style={{ color: "var(--text-muted)", fontWeight: 400, marginLeft: 6, fontSize: 10 }}>
                {airscout.station_a ?? "?"} → {airscout.station_b ?? "set DX"}
                <button
                  style={{ marginLeft: 6, padding: "1px 6px", fontSize: 9, borderRadius: 3,
                    background: "var(--surface)", color: "var(--text-muted)", border: "1px solid var(--border)" }}
                  onClick={() => rpc("airscout_fetch_now", {}).catch(e => addLog(`Aircraft refresh error: ${e}`))}>
                  ↻
                </button>
              </span>}
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              <details className="aircraft-filters">
                <summary>Filters</summary>
                <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "4px 0" }}>
                {[
                  [
                  ["lat_min", "Min lat", "number"],
                  ["lat_max", "Max lat", "number"],
                  ["lon_min", "Min lon", "number"],
                  ["lon_max", "Max lon", "number"],
                  ["altitude_min_ft", "Min alt ft", "number"],
                  ],
                  [
                  ["heading_tolerance_deg", "Heading ±°", "number"],
                  ["position_tolerance_deg", "Path bearing ±°", "number"],
                  ],
                ].map((fields, groupIndex) => (
              <div key={groupIndex} style={{ display: "flex", flexWrap: groupIndex === 0 ? "wrap" : "nowrap", gap: "6px 8px" }}>
                {fields.map(([key, label, type]) => {
                  const numericKey = key as NumericAircraftFilterKey;
                  const value = aircraftFilters[numericKey];
                  return (
                    <label key={key} style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 9, color: "var(--text-muted)" }}>
                      <span>{label}</span>
                      <input
                        type={type}
                        aria-label={label}
                        value={value == null ? "" : value}
                        onChange={(e) => {
                          const next = { ...aircraftFilters };
                          const parsed = parseAircraftFilterNumber(e.target.value);
                          (next as Record<NumericAircraftFilterKey, number | null>)[numericKey] = parsed;
                          if (key === "heading_tolerance_deg") {
                            next.heading_tolerance_deg = parsed ?? DEFAULT_AIRCRAFT_FILTERS.heading_tolerance_deg;
                          }
                          if (key === "position_tolerance_deg") {
                            next.position_tolerance_deg = parsed ?? DEFAULT_AIRCRAFT_FILTERS.position_tolerance_deg;
                          }
                          persistAircraftFilters(next);
                        }}
                        style={{
                          background: "var(--surface)", color: "var(--text)", border: "1px solid var(--border)",
                          borderRadius: 4, padding: "2px 5px", fontSize: 10,
                          width: key.startsWith("lat_") || key.startsWith("lon_") ? 100 : 86,
                          fontFamily: "monospace",
                        }}
                      />
                    </label>
                  );
                })}
              </div>
                ))}
                </div>
              </details>
                <div data-flight-controls style={{ display: "flex", alignItems: "flex-end", flexWrap: "wrap", gap: "10px 8px", fontSize: 9, color: "var(--text-muted)", flexShrink: 0 }}>
                  <div style={{ display: "flex", alignItems: "flex-end", gap: 8, flexWrap: "wrap" }}>
                    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                    <label htmlFor="priority-flight" style={{ whiteSpace: "nowrap" }}>Flight number</label>
                    <input
                      id="priority-flight"
                      aria-label="Priority flight number"
                      type="text"
                      maxLength={8}
                      value={aircraftFilters.priority_flight}
                      placeholder="e.g. UAL123"
                      onChange={(e) => persistAircraftFilters({
                        ...aircraftFilters,
                        priority_flight: e.target.value.toUpperCase(),
                      })}
                      style={{
                        width: 82, flexShrink: 0, fontFamily: "monospace",
                        background: "var(--surface)", color: "var(--text)",
                        border: "1px solid var(--border)", borderRadius: 4, padding: "2px 3px", fontSize: 10,
                      }}
                    />
                    </div>
                    <label title="Show only this flight in the candidate list" style={{ display: "flex", alignItems: "center", gap: 5, flexShrink: 0, whiteSpace: "nowrap", alignSelf: "flex-end", padding: "2px 5px" }}>
                      <input
                        aria-label="Show only priority flight"
                        type="checkbox"
                        style={{ width: 13, height: 13, margin: 0 }}
                        checked={aircraftFilters.priority_flight_exclusive}
                        disabled={!aircraftFilters.priority_flight}
                        onChange={(e) => persistAircraftFilters({
                          ...aircraftFilters,
                          priority_flight_exclusive: e.target.checked,
                        })}
                      />
                      Only
                    </label>
                    <button
                      className="btn-secondary"
                      aria-label="Store Flight#"
                      title="Save this flight number and Only setting in flight favorites"
                      disabled={!aircraftFilters.priority_flight.trim()}
                      onClick={storeFlight}
                      style={{
                        flexShrink: 0, fontSize: 10, padding: "2px 3px", whiteSpace: "nowrap", borderRadius: 3,
                        background: "var(--surface)",
                        color: "var(--text)",
                        border: "1px solid var(--input-border)",
                      }}
                    >
                      Store Flight#
                    </button>
                    <button
                      className="btn-secondary"
                      aria-label="Clear flight"
                      title="Clear the active flight filter; keep saved favorites"
                      onClick={() => persistAircraftFilters({
                        ...aircraftFilters, priority_flight: "", priority_flight_exclusive: false,
                      })}
                      style={{ flexShrink: 0, fontSize: 10, padding: "2px 3px", whiteSpace: "nowrap", color: "var(--text)", borderColor: "var(--input-border)" }}
                    >
                      Clear
                    </button>
                  </div>
                  <div style={{ fontFamily: "monospace", flexShrink: 0 }}>
                    <DarkSelect
                      ariaLabel="Flight favorites"
                      placeholder="Flight favorites"
                      textColor="var(--text)"
                      width={120}
                      popupWidth={145}
                      value=""
                      options={[
                        ...favoriteFlights.map((favorite, i) => ({
                          value: String(i),
                          label: `${favorite.flight.padEnd(8, "\u00a0")}  Only: ${favorite.only ? "Yes" : "No"}`,
                        })),
                      ]}
                      onChange={(value) => {
                        if (value === "") return;
                        const favorite = favoriteFlights[Number(value)];
                        if (!favorite) return;
                        persistAircraftFilters({
                          ...aircraftFilters,
                          priority_flight: favorite.flight,
                          priority_flight_exclusive: favorite.only,
                        });
                      }}
                    />
                  </div>
              </div>
              <h3 id="flight-data-title" style={{ marginTop: 12 }}>Flight Data</h3>
              <div data-aircraft-sources style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {availableSources.map((source) => {
                  const priority = sourceStatus?.priority ?? ["opensky"];
                  const idx = priority.indexOf(source);
                  const enabled = idx >= 0;
                  return (
                    <div key={source} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                      <button
                        style={{
                          padding: "2px 6px", fontSize: 10, borderRadius: 3,
                          background: enabled ? "rgba(63,185,80,0.12)" : "var(--surface)",
                          color: enabled ? "var(--green)" : "var(--text-muted)",
                          border: `1px solid ${enabled ? "var(--green)" : "var(--border)"}`,
                        }}
                        onClick={() => {
                          if (enabled && priority.length > 1) {
                            updateSourcePriority(priority.filter((s) => s !== source));
                          } else if (!enabled) {
                            updateSourcePriority([...priority, source]);
                          }
                        }}
                      >
                        {enabled ? "✓" : "+"} {source === "adsbexchange" ? "ADS-B Exchange" : source}
                      </button>
                      {enabled && (
                        <>
                          <button
                            style={{ padding: "1px 5px", fontSize: 9, borderRadius: 3 }}
                            disabled={idx <= 0}
                            onClick={() => {
                              if (idx <= 0) return;
                              const next = [...priority];
                              [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
                              updateSourcePriority(next);
                            }}
                          >
                            ↑
                          </button>
                          <button
                            style={{ padding: "1px 5px", fontSize: 9, borderRadius: 3 }}
                            disabled={idx < 0 || idx >= priority.length - 1}
                            onClick={() => {
                              if (idx < 0 || idx >= priority.length - 1) return;
                              const next = [...priority];
                              [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
                              updateSourcePriority(next);
                            }}
                          >
                            ↓
                          </button>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
              <AircraftAccounts onLog={addLog} />
              <Dump1090Source load={loadDump1090Host} save={saveDump1090Host} onLog={addLog} />
              {sourceStatus?.sources?.adsbexchange?.message && (
                <div style={{ fontSize: 10, color: "var(--yellow)" }}>
                  ADS-B Exchange: {sourceStatus.sources.adsbexchange.message}
                </div>
              )}
            </div>
            <div data-flight-status style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
              Source: <span style={{ color: "var(--text)" }}>{activeSource ?? "not set"}</span>
              {" · "}
              <span style={{ color: sourceDotColor }}>{activeSourceState}</span>
              {activeSourceMessage && <span style={{ color: "var(--text-muted)" }}> — {activeSourceMessage}</span>}
              {" · "}
              <span style={{ color: dataFreshness === "fresh" ? "var(--green)" : dataFreshness === "stale" ? "var(--yellow)" : "var(--red)" }}>
                {freshnessLabel}
              </span>
              {cacheAgeSeconds != null && (
                <span style={{ color: "var(--text-muted)" }}> ({cacheAgeSeconds}s ago)</span>
              )}
              {totalAircraftCount > 0 && (
                <span style={{ marginLeft: 6, color: "var(--text)" }}>
                  {filteredAircraftCount}/{totalAircraftCount} shown
                </span>
              )}
              {activeSource === "dump1090" && dump1090Activity && (
                <div style={{ color: "var(--text-muted)" }}>
                  dump1090 {dump1090Activity.host}:{dump1090Activity.port} · {dump1090Activity.messages} messages
                  {" · "}{dump1090Activity.aircraft} aircraft ({dump1090Activity.with_position} with position)
                  {dump1090Activity.last_message_age_s != null
                    ? ` · last message ${Math.round(dump1090Activity.last_message_age_s)}s ago`
                    : " · no messages yet — idle until an aircraft is in range"}
                </div>
              )}
            </div>
            <div className="flight-data-scroll" role="region" aria-labelledby="flight-data-title" ref={flightDataRef}>
            {flightData.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                {dxCallsign
                  ? `Waiting for aircraft data from ${activeSource ?? "selected source"}…`
                  : "Select a DX station to start"}
              </div>
            ) : (
              <table className="ac-table">
                <thead>
                  <tr>
                    <th>Call</th><th>FL</th><th>Score</th>
                    <th>AZ-A</th><th>EL-A</th><th>Off°</th><th>km</th>
                  </tr>
                </thead>
                <tbody>
                  {flightData.map(ac => {
                    const sc = ac.scatter_score;
                    const scColor = sc >= 0.5 ? "var(--green)" : sc >= 0.3 ? "var(--yellow)" : "var(--text-muted)";
                    const isLocked = lockedPlane?.icao24 === ac.icao24;
                    const isFavorite = savedFlights.has(ac.callsign.trim().toUpperCase());
                    return (
                      <tr key={ac.icao24} className={isLocked ? "locked-row" : ""}
                        style={{ cursor: "pointer", background: isFavorite ? "#30363d" : undefined }}
                        data-flight-favorite={isFavorite || undefined}
                        onClick={() => rpc("lock_aircraft", { icao24: ac.icao24 }).catch(e => addLog(`Lock: ${e}`))}>
                        <td style={{ color: isLocked ? "var(--accent)" : "var(--text)", fontWeight: isLocked ? 700 : 400 }}>
                          {ac.callsign}
                        </td>
                        <td>{Math.round(ac.altitude_ft / 100)}</td>
                        <td style={{ color: scColor, fontWeight: 600 }}>{(sc * 100).toFixed(0)}%</td>
                        <td>{ac.az_a.toFixed(0)}°</td>
                        <td style={{ color: ac.el_a > 0 && ac.el_a < 20 ? "var(--green)" : "var(--text-muted)" }}>
                          {ac.el_a.toFixed(0)}°
                        </td>
                        <td style={{ color: ac.off_path_deg < 20 ? "var(--green)" : ac.off_path_deg < 40 ? "var(--yellow)" : "var(--text-muted)" }}>
                          {ac.off_path_deg.toFixed(0)}°
                        </td>
                        <td style={{ color: "var(--text-muted)" }}>{ac.range_a_km}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
            </div>
          </div>

          {/* Event log */}
          <div className="card log-card" ref={logCardRef}>
            <h3 id="log-title">Log</h3>
            <div className="log-list" role="region" aria-labelledby="log-title" ref={logRef}>
              {log.length === 0
                ? <div className="entry">Ready.</div>
                : [...log].reverse().map((e, i) => (
                  <div key={i} className="entry">{e}</div>
                ))
              }
            </div>
          </div>
        </div>
      </div>

      {/* ── Bottom action bar ── */}
      <footer className="bottombar">
        <button className="btn-secondary" style={{ fontSize: 12 }} onClick={async () => {
          if (lockedPlane) {
            try {
              await rpc("lock_aircraft", { icao24: lockedPlane.icao24 });
              addLog(`Session ready — aircraft ${lockedPlane.callsign || lockedPlane.icao24} locked`);
            } catch (e) {
              addLog(`Begin Session: ${e}`);
            }
          } else {
            addLog("Session ready — waiting for aircraft lock");
          }
        }} disabled={state !== "idle" && state !== "completed" && state !== "aborted"}>
          Begin Session
        </button>
        <button className="btn-success" onClick={startQso} disabled={!canStartQso}
          title="Mark the QSO as started in the app. This does not transmit; enable Tx in WSJT-X.">
          ▶ Start QSO
        </button>
        <button className="btn-danger" onClick={() => finishQso(true)}
          title={controlWsjtx
            ? "Mark the QSO as aborted and send Halt Tx to WSJT-X to stop transmitting."
            : "Mark the QSO as aborted in the app only. Use Halt Tx in WSJT-X to stop transmitting (or tick 'Halt WSJT-X Tx on Abort/Finish')."}
          disabled={displayState !== "active_qso" && ["idle", "completed", "aborted"].includes(state)}>
          ■ Abort
        </button>
        <button className="btn-secondary" onClick={() => finishQso(false)} disabled={state !== "active_qso"}
          title={controlWsjtx
            ? "Mark the QSO as done and send Halt Tx to WSJT-X. WSJT-X also completes it automatically when it logs the contact."
            : "Mark the QSO as done in the app only. WSJT-X also completes it automatically when it logs the contact."}>
          ✓ Finish QSO
        </button>
        <div style={{ width: 1, height: 20, background: "var(--border)", margin: "0 4px" }} />
        <button
          style={{
            fontSize: 11, padding: "4px 10px", borderRadius: 10,
            border: `1px solid ${autoLock ? "var(--green)" : "var(--border)"}`,
            background: autoLock ? "rgba(63,185,80,0.1)" : "var(--surface)",
            color: autoLock ? "var(--green)" : "var(--text-muted)", cursor: "pointer",
          }}
          onClick={() => { const v = !autoLock; setAutoLock(v); rpc("auto_lock_set", { enabled: v }).catch(() => {}); }}>
          {autoLock ? "🔒 Auto-lock" : "🔓 Manual"}
        </button>
        <label style={{ fontSize: 11, color: controlWsjtx ? "var(--yellow)" : "var(--text-muted)", display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}
          title="When on, Abort and Finish send Halt Tx to WSJT-X. WSJT-X must have 'Accept UDP requests' ticked (Settings > Reporting). Enable Tx cannot be sent remotely.">
          <input type="checkbox" checked={controlWsjtx} onChange={(e) => setControlWsjtx(e.target.checked)} />
          Halt WSJT-X Tx on Abort/Finish
        </label>
        <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>
          {dxCall && selectedDxGrid && `${myGrid ?? "?"} → ${selectedDxGrid}`}
        </div>
      </footer>
    </div>
  );
}
