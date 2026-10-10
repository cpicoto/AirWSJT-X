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
  const [rotatorConnected, setRotatorConnected] = useState(false);
  const [rotatorAz, setRotatorAz] = useState(0);
  const [rotatorEl, setRotatorEl] = useState(0);
  const [rotatorTargetAz, setRotatorTargetAz] = useState<number | null>(null);
  const [rotatorTargetEl, setRotatorTargetEl] = useState<number | null>(null);
  const [dxCallsign, setDxCallsign] = useState<string | null>(null);
  const [dxGrid, setDxGrid] = useState<string | null>(null);
  const [autoLock, setAutoLock] = useState(true);
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

  const state = status?.state ?? "idle";
  const stateColor = STATE_COLOR[state] ?? "#8b949e";
  const aircraft = airscout?.aircraft ?? [];
  const txOffsetTitle = wsjtxLive.tx_df != null ? `TX frequency offset: ${wsjtxLive.tx_df}Hz.` : null;
  const txOffset = txOffsetTitle ? ` ${txOffsetTitle}` : "";
  const txIndicatorTitle = wsjtxLive.transmitting
    ? `Red TX means transmitting (PTT ON).${txOffset}`
    : `TX○ means idle (not transmitting).${txOffset}`;
  const canStartQso =
    (state === "plane_locked" || state === "tracking" || state === "ready") &&
    wsjtxLive.connected &&
    wsjtxLive.mode === "Q65" &&
    wsjtxLive.tr_period === 15 &&
    (wsjtxLive.freq_mhz === null || wsjtxLive.freq_mhz >= 1240);

  return (
    <div className="app">

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
                <div className="call" style={{ color: "var(--accent)" }}>{wsjtxLive.de_call ?? "—"}</div>
                <div className="grid">{wsjtxLive.de_grid ?? "—"}</div>
              </div>
              <div style={{ color: "var(--border)", fontSize: 16 }}>→</div>
              <div className="station-pill" style={{ borderColor: "#3fb95044" }}>
                <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 1 }}>DX</div>
                <div className="call" style={{ color: "var(--green)" }}>{dxCallsign ?? "—"}</div>
                <div className="grid">{dxGrid ?? "—"}</div>
              </div>
            </div>
            {/* Quick-select */}
            <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
              {[{ call: "VE7NX", grid: "CN89oh" }, { call: "W7PUA", grid: "CN84jq" }].map(s => (
                <button key={s.call} className="btn-secondary"
                  style={{ fontSize: 10, padding: "3px 8px",
                    border: dxCallsign === s.call ? "1px solid var(--green)" : undefined,
                    color: dxCallsign === s.call ? "var(--green)" : undefined }}
                  onClick={() => {
                    setDxCallsign(s.call); setDxGrid(s.grid);
                    rpc("set_station_calls", { call_a: wsjtxLive.de_call, call_b: s.call }).catch(() => {});
                    rpc("airscout_set_stations", { locator_a: wsjtxLive.de_grid, locator_b: s.grid }).catch(() => {});
                    addLog(`DX → ${s.call} ${s.grid}`);
                  }}>
                  {s.call}
                </button>
              ))}
              <button className="btn-secondary" style={{ fontSize: 10, padding: "3px 8px" }}
                onClick={async () => {
                  const call = prompt("DX callsign:");
                  if (!call) return;
                  const info = await rpc("callsign_lookup", { callsign: call }) as { callsign: string; grid: string | null; name: string | null };
                  if (info.grid) {
                    setDxCallsign(info.callsign); setDxGrid(info.grid);
                    rpc("set_station_calls", { call_a: wsjtxLive.de_call, call_b: info.callsign }).catch(() => {});
                    rpc("airscout_set_stations", { locator_a: wsjtxLive.de_grid, locator_b: info.grid }).catch(() => {});
                    addLog(`DX → ${info.callsign} ${info.grid}${info.name ? ` (${info.name})` : ""}`);
                  } else {
                    addLog(`Lookup: ${call} not found`);
                  }
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
            {aircraft.length === 0 ? (
              <div style={{ color: "var(--text-muted)", fontSize: 11 }}>
                {dxCallsign ? "Waiting for aircraft data…" : "Select a DX station to start"}
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
          {dxCallsign && dxGrid && `${wsjtxLive.de_grid ?? "?"} → ${dxGrid}`}
        </div>
      </footer>
    </div>
  );
}
