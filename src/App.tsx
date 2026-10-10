import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useCallback, useEffect, useRef, useState } from "react";
import RotatorPanel from "./RotatorPanel";
import PlaneLockPanel from "./PlaneLockPanel";

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
}

interface AircraftFilterSettings {
  lat_min: number | null;
  lat_max: number | null;
  lon_min: number | null;
  lon_max: number | null;
  altitude_min_ft: number | null;
  heading_tolerance_deg: number;
}

const DEFAULT_AIRCRAFT_FILTERS: AircraftFilterSettings = {
  lat_min: null,
  lat_max: null,
  lon_min: null,
  lon_max: null,
  altitude_min_ft: null,
  heading_tolerance_deg: 30,
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
const PORTABLE_VIEW_KEY = "airwsjtx.portableViewMode";
const PORTABLE_SCALE_KEY = "airwsjtx.portableScale";
const PORTABLE_MIN_FONT_PX = 28;
const PORTABLE_BASE_FONT_PX = 72;

type PortableViewMode = "hidden" | "panel" | "overlay";

function normalizeGrid(grid: string | null | undefined): string | null {
  const normalized = (grid ?? "").trim().toUpperCase();
  return normalized.length > 0 ? normalized : null;
}

// ─── App ──────────────────────────────────────────────────────────────────────

export default function App() {
  const [status, setStatus] = useState<{ state: string } | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [wsjtxLive, setWsjtxLive] = useState<WsjtxLive>({
    connected: false, app_id: "", freq_mhz: null, mode: null,
    sub_mode: null, de_call: null, de_grid: null, dx_call: null,
    transmitting: false, decoding: false, tr_period: null,
    rx_df: null, tx_df: null, config_name: null,
  });
  const [decodes, setDecodes] = useState<WsjtxDecodeEntry[]>([]);
  const [airscout, setAirscout] = useState<AirscoutUpdate | null>(null);
  const [sourceStatus, setSourceStatus] = useState<AirscoutSourceState | null>(null);
  const [rotatorConnected, setRotatorConnected] = useState(false);
  const [rotatorAz, setRotatorAz] = useState(0);
  const [rotatorEl, setRotatorEl] = useState(0);
  const [rotatorTargetAz, setRotatorTargetAz] = useState<number | null>(null);
  const [rotatorTargetEl, setRotatorTargetEl] = useState<number | null>(null);
  const [dxCallsign, setDxCallsign] = useState<string | null>(null);
  const [dxGrid, setDxGrid] = useState<string | null>(null);
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
  const [myGridInput, setMyGridInput] = useState("");
  const [dxGridInput, setDxGridInput] = useState("");
  const [myGridEditing, setMyGridEditing] = useState(false);
  const [dxGridEditing, setDxGridEditing] = useState(false);
  const [nowEpochS, setNowEpochS] = useState(() => Math.floor(Date.now() / 1000));
  const [autoLock, setAutoLock] = useState(true);
  const [wsjtxRigFilter, setWsjtxRigFilter] = useState("");
  const [portableView, setPortableView] = useState<PortableViewMode>(() => {
    const saved = localStorage.getItem(PORTABLE_VIEW_KEY) as PortableViewMode | null;
    return saved === "panel" || saved === "overlay" || saved === "hidden" ? saved : "panel";
  });
  const [portableScale, setPortableScale] = useState<number>(() => {
    const saved = Number(localStorage.getItem(PORTABLE_SCALE_KEY) ?? "100");
    return Number.isFinite(saved) && saved >= 70 && saved <= 180 ? saved : 100;
  });
  const [aircraftFilters, setAircraftFilters] = useState<AircraftFilterSettings>(() => loadAircraftFilters(null, null));
  const [lockedPlane, setLockedPlane] = useState<{
    icao24: string; callsign: string; altitude_ft: number;
    az_a: number; el_a: number; az_b: number; el_b: number;
    scatter_score: number; off_path_deg: number; state: string;
    rotator_az?: number | null; rotator_el?: number | null;
  } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  const addLog = useCallback((msg: string) => {
    setLog((l) => [...l.slice(-49), `${new Date().toLocaleTimeString()} ${msg}`]);
  }, []);

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
            setWsjtxLive(w => ({ ...w, connected: true, app_id: evtData.app_id as string }));
          } else if (evt === "wsjtx_disconnected") {
            addLog("WSJT-X disconnected");
            setWsjtxLive(w => ({ ...w, connected: false }));
          } else if (evt === "wsjtx_status") {
            setWsjtxLive(w => ({
              ...w,
              connected: true,
              freq_mhz: evtData.freq_mhz as number,
              mode: evtData.mode as string | null,
              sub_mode: evtData.sub_mode as string | null,
              de_call: evtData.de_call as string | null,
              de_grid: evtData.de_grid as string | null,
              dx_call: evtData.dx_call as string | null,
              transmitting: evtData.transmitting as boolean,
              decoding: evtData.decoding as boolean,
              tr_period: evtData.tr_period as number | null,
              rx_df: evtData.rx_df as number | null,
              tx_df: evtData.tx_df as number | null,
              config_name: evtData.config_name as string | null,
            }));
          } else if (evt === "wsjtx_decode") {
            const entry: WsjtxDecodeEntry = {
              message: evtData.message as string | null,
              snr: evtData.snr as number,
              delta_freq_hz: evtData.delta_freq_hz as number,
              mode: evtData.mode as string | null,
              ts: new Date().toLocaleTimeString(),
            };
            setDecodes(d => [entry, ...d.slice(0, 49)]);
            addLog(`DX: ${entry.message ?? "?"} SNR ${entry.snr}dB`);
          } else if (evt === "airscout_update") {
            setAirscout(evtData as unknown as AirscoutUpdate);
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
          } else if (evt === "rotator_error") {
            addLog(`Rotator: ${evtData.message}`);
          } else if (evt === "plane_locked") {
            const d = evtData as typeof lockedPlane;
            setLockedPlane(d);
            setStatus(s => s ? { ...s, state: (evtData.state as string) } : s);
            addLog(`🔒 Locked: ${evtData.callsign} FL${Math.round((evtData.altitude_ft as number)/100)} score=${((evtData.scatter_score as number)*100).toFixed(0)}%`);
          } else if (evt === "plane_tracking") {
            setLockedPlane(prev => prev ? {
              ...prev,
              az_a: evtData.az_a as number,
              el_a: evtData.el_a as number,
              scatter_score: evtData.scatter_score as number,
              state: evtData.state as string,
              rotator_az: evtData.rotator_az as number | null,
              rotator_el: evtData.rotator_el as number | null,
            } : null);
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
        const s = await rpc("get_status") as { state: string };
        setStatus(s);
      } catch { /* sidecar may not be up yet */ }
    }, 2000);
    return () => clearInterval(id);
  }, []);

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

  // Scroll log to bottom
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  async function startQso() {
    try {
      const r = await rpc("start_qso") as { state: string };
      setStatus((s) => s ? { ...s, state: r.state } : s);
      addLog("QSO started — PTT ON");
    } catch (e) { addLog(`Error: ${e}`); }
  }

  async function finishQso(abort = false) {
    try {
      const r = await rpc("finish_qso", { abort }) as { state: string };
      setStatus((s) => s ? { ...s, state: r.state } : s);
      addLog(abort ? "QSO aborted — PTT OFF" : "QSO completed — PTT OFF");
    } catch (e) { addLog(`Error: ${e}`); }
  }

  async function applyWsjtxRigFilter(rawValue: string) {
    const value = rawValue.trim();
    try {
      await rpc("wsjtx_set_rig_name_filter", { rig_name: value || null });
      localStorage.setItem("wsjtxRigNameFilter", value);
      setWsjtxRigFilter(value);
      addLog(value ? `WSJT-X rig filter: ${value}` : "WSJT-X rig filter cleared");
    } catch (e) {
      addLog(`WSJT-X filter error: ${e}`);
    }
  }

  const state = status?.state ?? "idle";
  const stateColor = STATE_COLOR[state] ?? "#8b949e";
  const aircraft = airscout?.aircraft ?? [];
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
    state === "ready" ||
    (!autoLock && !!lockedPlane && (state === "plane_locked" || state === "tracking"))
  );
  const activeSource = sourceStatus?.active_source ?? airscout?.active_source ?? null;
  const activeSourceState = activeSource ? sourceStatus?.sources?.[activeSource]?.state ?? "unknown" : "stopped";
  const activeSourceMessage = activeSource ? sourceStatus?.sources?.[activeSource]?.message ?? null : null;
  const openskyState = sourceStatus?.sources?.opensky?.state ?? "stopped";
  const connectivityState = activeSource === "dump1090" && activeSourceState === "connected"
    ? "local"
    : openskyState === "connected"
      ? "online"
      : ["backoff", "connecting", "disconnected"].includes(openskyState)
        ? "degraded"
        : "offline";
  const connectivityColor = connectivityState === "online" || connectivityState === "local"
    ? "var(--green)"
    : connectivityState === "degraded"
      ? "var(--yellow)"
      : "var(--red)";
  const connectivityLabel = connectivityState === "local"
    ? "LOCAL ADS-B"
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
    : activeSourceState === "backoff" || activeSourceState === "connecting"
      ? "var(--yellow)"
      : "var(--red)";
  const availableSources = sourceStatus ? Object.keys(sourceStatus.sources) : ["opensky", "dump1090"];

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
      heading_tolerance_deg: Number.isFinite(nextFilters.heading_tolerance_deg) ? nextFilters.heading_tolerance_deg : DEFAULT_AIRCRAFT_FILTERS.heading_tolerance_deg,
    };
    const storageKey = getAircraftFilterStorageKey(airscout?.station_a, airscout?.station_b);
    localStorage.setItem(storageKey, JSON.stringify(normalized));
    setAircraftFilters(normalized);
    rpc("airscout_apply_filters", { filters: normalized }).catch(() => {});
  }, [airscout?.station_a, airscout?.station_b]);

  const myCallsign = wsjtxLive.de_call?.trim().toUpperCase() ?? null;
  const dxCall = dxCallsign?.trim().toUpperCase() ?? null;
  const myGrid = (myCallsign ? stationGridOverrides[myCallsign] : null) ?? wsjtxLive.de_grid;
  const selectedDxGrid = (dxCall ? stationGridOverrides[dxCall] : null) ?? dxGrid;

  useEffect(() => {
    localStorage.setItem(PORTABLE_VIEW_KEY, portableView);
  }, [portableView]);

  useEffect(() => {
    localStorage.setItem(PORTABLE_SCALE_KEY, String(portableScale));
  }, [portableScale]);

  useEffect(() => {
    if (!myGridEditing) setMyGridInput(myGrid ?? "");
  }, [myGrid, myGridEditing]);
  useEffect(() => {
    if (!dxGridEditing) setDxGridInput(selectedDxGrid ?? "");
  }, [selectedDxGrid, dxGridEditing]);
  useEffect(() => {
    const stored = loadAircraftFilters(airscout?.station_a, airscout?.station_b);
    setAircraftFilters(stored);
    rpc("airscout_apply_filters", { filters: stored }).catch(() => {});
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

  const currentAz = lockedPlane ? (Number.isFinite(lockedPlane.az_a) ? lockedPlane.az_a : rotatorAz) : rotatorAz;
  const currentEl = lockedPlane ? (Number.isFinite(lockedPlane.el_a) ? lockedPlane.el_a : rotatorEl) : rotatorEl;
  const portableFontPx = Math.max(PORTABLE_MIN_FONT_PX, PORTABLE_BASE_FONT_PX * portableScale / 100);
  const portableReadout = (
  <div className={portableView === "overlay" ? "portable-overlay" : "portable-panel"}>
    <div className="portable-header">
      <span>Az / El</span>
      <div className="portable-controls">
        <button className="portable-mode" onClick={() => setPortableView("panel")}>Panel</button>
        <button className="portable-mode" onClick={() => setPortableView("overlay")}>Overlay</button>
        <button className="portable-mode" onClick={() => setPortableView("hidden")}>Hide</button>
      </div>
    </div>
    <div className="portable-grid" style={{ fontSize: `${portableFontPx}px` }}>
      <div className="portable-readout">
        <span className="portable-label">AZ</span>
        <span className="portable-value">{Number.isFinite(currentAz) ? currentAz.toFixed(1) : "--.-"}°</span>
      </div>
      <div className="portable-readout">
        <span className="portable-label">EL</span>
        <span className="portable-value">{Number.isFinite(currentEl) ? currentEl.toFixed(1) : "--.-"}°</span>
      </div>
    </div>
    <div className="portable-footer">
      <label>
        Scale
        <input
          type="range"
          min={70}
          max={180}
          step={5}
          value={portableScale}
          onChange={(e) => setPortableScale(Number(e.target.value))}
        />
      </label>
    </div>
  </div>
  );

  const portableDisplay = portableView === "hidden" ? null : portableReadout;

  return (
    <div className="app">
      {portableView === "overlay" && portableDisplay}

      {/* ── Top bar ── */}
      <header className="topbar">
        <h1>AirWSJT-X</h1>
        <span className="badge" style={{ background: stateColor + "22", color: stateColor }}>
          {state.replace(/_/g, " ")}
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
            <input
              value={wsjtxRigFilter}
              placeholder="all"
              style={{
                width: 92,
                fontSize: 10,
                padding: "2px 6px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--surface)",
                color: "var(--text)",
              }}
              onChange={(e) => setWsjtxRigFilter(e.target.value)}
              onBlur={() => { void applyWsjtxRigFilter(wsjtxRigFilter); }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  (e.currentTarget as HTMLInputElement).blur();
                }
              }}
            />
          </label>
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

      {portableView === "panel" && portableDisplay}

      {/* ── 3-column content ── */}
      <div className="content">

        {/* LEFT: Stations + Plane Lock */}
        <div className="col">

          {/* Compact station cards */}
          <div className="card">
            <h3>Stations</h3>
            <div className="station-compact">
              <div className="station-pill" style={{ borderColor: "#58a6ff44" }}>
                <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 1 }}>MY</div>
                <div className="call" style={{ color: "var(--accent)" }}>{myCallsign ?? "—"}</div>
                <div className="grid">{myGrid ?? "—"}</div>
              </div>
              <div style={{ color: "var(--border)", fontSize: 16 }}>→</div>
              <div className="station-pill" style={{ borderColor: "#3fb95044" }}>
                <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 1 }}>DX</div>
                <div className="call" style={{ color: "var(--green)" }}>{dxCall ?? "—"}</div>
                <div className="grid">{selectedDxGrid ?? "—"}</div>
              </div>
            </div>
            <div style={{ display: "grid", gap: 4, marginTop: 6 }}>
              <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontSize: 10, color: "var(--text-muted)", minWidth: 22 }}>MY</span>
                <input
                  value={myGridInput}
                  onChange={(e) => setMyGridInput(e.target.value.toUpperCase())}
                  onFocus={() => setMyGridEditing(true)}
                  onBlur={() => setMyGridEditing(false)}
                  placeholder={wsjtxLive.de_grid ?? "Grid"}
                  style={{
                    background: "var(--surface)",
                    color: "var(--text)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: "2px 6px",
                    fontSize: 11,
                    width: 86,
                    fontFamily: "monospace",
                  }}
                />
                <button className="btn-secondary" style={{ fontSize: 10, padding: "2px 8px" }}
                  onClick={() => {
                    const grid = normalizeGrid(myGridInput);
                    const locatorA = grid ?? wsjtxLive.de_grid;
                    savePortableGrid(myCallsign, grid);
                    if (locatorA && selectedDxGrid) {
                      rpc("airscout_set_stations", { locator_a: locatorA, locator_b: selectedDxGrid }).catch(() => {});
                    }
                    addLog(`MY grid ${grid ? `override → ${grid}` : "reset to WSJT-X"}`);
                  }}>
                  Apply
                </button>
              </div>
              <div style={{ display: "flex", gap: 4, alignItems: "center", flexWrap: "wrap" }}>
                <span style={{ fontSize: 10, color: "var(--text-muted)", minWidth: 22 }}>DX</span>
                <input
                  value={dxGridInput}
                  onChange={(e) => setDxGridInput(e.target.value.toUpperCase())}
                  onFocus={() => setDxGridEditing(true)}
                  onBlur={() => setDxGridEditing(false)}
                  placeholder={dxGrid ?? "Grid"}
                  style={{
                    background: "var(--surface)",
                    color: "var(--text)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: "2px 6px",
                    fontSize: 11,
                    width: 86,
                    fontFamily: "monospace",
                  }}
                />
                <button className="btn-secondary" style={{ fontSize: 10, padding: "2px 8px" }}
                  onClick={() => {
                    if (!dxCall) {
                      addLog("Set DX callsign before applying DX grid");
                      return;
                    }
                    const grid = normalizeGrid(dxGridInput);
                    const locatorB = grid ?? dxGrid;
                    const locatorA = normalizeGrid(myGridInput) ?? wsjtxLive.de_grid;
                    savePortableGrid(dxCall, grid);
                    if (locatorA && locatorB) {
                      rpc("airscout_set_stations", { locator_a: locatorA, locator_b: locatorB }).catch(() => {});
                    }
                    addLog(`DX grid ${grid ? `override → ${grid}` : "reset to callbook"}`);
                  }}>
                  Apply
                </button>
              </div>
            </div>
            {/* Quick-select */}
            <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
              {[{ call: "VE7NX", grid: "CN89oh" }, { call: "W7PUA", grid: "CN84jq" }].map(s => (
                <button key={s.call} className="btn-secondary"
                  style={{ fontSize: 10, padding: "3px 8px",
                    border: dxCall === s.call ? "1px solid var(--green)" : undefined,
                    color: dxCall === s.call ? "var(--green)" : undefined }}
                  onClick={() => {
                    setDxCallsign(s.call); setDxGrid(s.grid.toUpperCase());
                    const dxLocator = stationGridOverrides[s.call] ?? s.grid.toUpperCase();
                    const locatorA = myGrid ?? wsjtxLive.de_grid;
                    if (myCallsign) {
                      rpc("set_station_calls", { call_a: myCallsign, call_b: s.call }).catch(() => {});
                    }
                    if (locatorA && dxLocator) {
                      rpc("airscout_set_stations", { locator_a: locatorA, locator_b: dxLocator }).catch(() => {});
                    }
                    addLog(`DX → ${s.call} ${dxLocator}`);
                  }}>
                  {s.call}
                </button>
              ))}
              <button className="btn-secondary" style={{ fontSize: 10, padding: "3px 8px" }}
                onClick={async () => {
                  const call = prompt("DX callsign:");
                  if (!call) return;
                  const info = await rpc("callsign_lookup", { callsign: call }) as { callsign: string; grid: string | null; name: string | null };
                  const nextCall = info.callsign.trim().toUpperCase();
                  const lookedUpGrid = normalizeGrid(info.grid);
                  const dxLocator = stationGridOverrides[nextCall] ?? lookedUpGrid;
                  const locatorA = myGrid ?? wsjtxLive.de_grid;
                  setDxCallsign(nextCall);
                  setDxGrid(lookedUpGrid);
                  if (myCallsign) {
                    rpc("set_station_calls", { call_a: myCallsign, call_b: nextCall }).catch(() => {});
                  }
                  if (locatorA && dxLocator) {
                    rpc("airscout_set_stations", { locator_a: locatorA, locator_b: dxLocator }).catch(() => {});
                  }
                  addLog(
                    dxLocator
                      ? `DX → ${nextCall} ${dxLocator}${info.name ? ` (${info.name})` : ""}`
                      : `DX → ${nextCall} (enter portable grid manually)`,
                  );
                }}>
                + Other
              </button>
            </div>
          </div>

          {/* Plane Lock — flex fills remaining space */}
          <div className="card flex1">
            <PlaneLockPanel
              locked={lockedPlane}
              autoLock={autoLock}
              sessionState={state}
              onAutoLockToggle={(v) => {
                setAutoLock(v);
                rpc("auto_lock_set", { enabled: v }).catch(() => {});
              }}
              onUnlock={() => { setLockedPlane(null); rpc("unlock_aircraft", {}).catch(() => {}); }}
              onLockAircraft={(icao24) => rpc("lock_aircraft", { icao24 }).catch(e => addLog(`Lock: ${e}`))}
              sourceStatusText={activeSource ? `${activeSource} ${activeSourceState}` : undefined}
              aircraft={aircraft.slice(0, 5).map(ac => ({
                icao24: ac.icao24, callsign: ac.callsign, altitude_ft: ac.altitude_ft,
                scatter_score: ac.scatter_score, az_a: ac.az_a, el_a: ac.el_a,
                az_b: ac.az_b, el_b: ac.el_b, off_path_deg: ac.off_path_deg,
              }))}
            />
          </div>
        </div>

        {/* CENTRE: Rotator */}
        <div className="col" style={{ alignItems: "center", justifyContent: "center" }}>
          <div className="card" style={{ width: "100%", alignSelf: "stretch" }}>
            <RotatorPanel
              currentAz={rotatorAz} currentEl={rotatorEl}
              targetAz={lockedPlane ? lockedPlane.az_a : rotatorTargetAz}
              targetEl={lockedPlane ? lockedPlane.el_a : rotatorTargetEl}
              connected={rotatorConnected}
              onSetPosition={(az, el) => {
                setRotatorTargetAz(az); setRotatorTargetEl(el);
                rpc("rotator_set", { azimuth: az, elevation: el }).catch(e => addLog(`Rotator: ${e}`));
              }}
              onStop={() => rpc("rotator_stop", {}).catch(e => addLog(`Rotator: ${e}`))}
              onPark={() => { setRotatorTargetAz(0); setRotatorTargetEl(0); rpc("rotator_park", {}).catch(e => addLog(`Rotator: ${e}`)); }}
              onConnect={(host, port) => rpc("rotator_connect", { host, port }).catch(e => addLog(`Rotator: ${e}`))}
            />
          </div>
        </div>

        {/* RIGHT: Decodes + Aircraft + Log */}
        <div className="col">

          {/* WSJT-X Decodes */}
          <div className="card" style={{ flexShrink: 0 }}>
            <h3>WSJT-X Decodes {wsjtxLive.connected && <span style={{ color: "var(--green)" }}>● live</span>}</h3>
            <div className="decode-list">
              {decodes.length === 0
                ? <div style={{ color: "var(--text-muted)", fontSize: 11 }}>No decodes yet</div>
                : decodes.slice(0, 8).map((d, i) => (
                  <div key={i} className="decode-row">
                    <span style={{ color: "var(--text-muted)" }}>{d.ts}</span>
                    <span style={{ color: d.snr >= -10 ? "var(--green)" : d.snr >= -20 ? "var(--yellow)" : "var(--text-muted)" }}>
                      {d.snr >= 0 ? `+${d.snr}` : d.snr}dB
                    </span>
                    <span style={{ color: "var(--text-muted)" }}>{d.delta_freq_hz}Hz</span>
                    <span style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis" }}>{d.message ?? ""}</span>
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
                  onClick={() => rpc("airscout_fetch_now", {}).catch(() => {})}>
                  ↻
                </button>
              </span>}
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 8 }}>
              <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
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
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(100px, 1fr))", gap: 4 }}>
                {[
                  ["lat_min", "Min lat", "number"],
                  ["lat_max", "Max lat", "number"],
                  ["lon_min", "Min lon", "number"],
                  ["lon_max", "Max lon", "number"],
                  ["altitude_min_ft", "Min alt ft", "number"],
                  ["heading_tolerance_deg", "Heading ±°", "number"],
                ].map(([key, label, type]) => {
                  const value = aircraftFilters[key as keyof AircraftFilterSettings];
                  return (
                    <label key={key} style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 9, color: "var(--text-muted)" }}>
                      <span>{label}</span>
                      <input
                        type={type}
                        value={value == null ? "" : value}
                        onChange={(e) => {
                          const next = { ...aircraftFilters };
                          const parsed = parseAircraftFilterNumber(e.target.value);
                          (next as Record<string, number | null>)[key] = parsed;
                          if (key === "heading_tolerance_deg") {
                            next.heading_tolerance_deg = parsed ?? DEFAULT_AIRCRAFT_FILTERS.heading_tolerance_deg;
                          }
                          persistAircraftFilters(next);
                        }}
                        style={{
                          background: "var(--surface)", color: "var(--text)", border: "1px solid var(--border)",
                          borderRadius: 4, padding: "2px 5px", fontSize: 10,
                        }}
                      />
                    </label>
                  );
                })}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {availableSources.map((source) => {
                  const priority = sourceStatus?.priority ?? ["opensky", "dump1090"];
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
                        {enabled ? "✓" : "+"} {source}
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
            </div>
            {aircraft.length === 0 ? (
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
                  {aircraft.slice(0, 5).map(ac => {
                    const sc = ac.scatter_score;
                    const scColor = sc >= 0.5 ? "var(--green)" : sc >= 0.3 ? "var(--yellow)" : "var(--text-muted)";
                    const isLocked = lockedPlane?.icao24 === ac.icao24;
                    return (
                      <tr key={ac.icao24} className={isLocked ? "locked-row" : ""}
                        style={{ cursor: "pointer" }}
                        onClick={() => rpc("lock_aircraft", { icao24: ac.icao24 }).catch(() => {})}>
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

          {/* Event log — fills remaining space */}
          <div className="card flex1">
            <h3>Log</h3>
            <div className="log-list" ref={logRef}>
              {log.length === 0
                ? <div className="entry">Ready.</div>
                : [...log].reverse().slice(0, 8).map((e, i) => (
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
          addLog("Session ready — waiting for aircraft lock");
        }} disabled={state !== "idle" && state !== "completed" && state !== "aborted"}>
          Begin Session
        </button>
        <button className="btn-success" onClick={startQso} disabled={!canStartQso}>
          ▶ Start QSO
        </button>
        <button className="btn-danger" onClick={() => finishQso(true)}
          disabled={["idle", "completed", "aborted"].includes(state)}>
          ■ Abort
        </button>
        <button className="btn-secondary" onClick={() => finishQso(false)} disabled={state !== "active_qso"}>
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
        <div style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-muted)" }}>
          {dxCall && selectedDxGrid && `${myGrid ?? "?"} → ${selectedDxGrid}`}
        </div>
      </footer>
    </div>
  );
}
