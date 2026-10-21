interface LockState {
  icao24: string;
  callsign: string;
  altitude_ft: number;
  range_a_km?: number | null;
  range_b_km?: number | null;
  az_a: number;
  el_a: number;
  az_b: number;
  el_b: number;
  scatter_score: number;
  off_path_deg: number;
  state: string;
  rotator_az?: number | null;
  rotator_el?: number | null;
  position_updated_epoch_s?: number | null;
}

interface PlaneLockPanelProps {
  locked: LockState | null;
  autoLock: boolean;
  sessionState: string;
  onAutoLockToggle: (v: boolean) => void;
  onUnlock: () => void;
  onLockAircraft: (icao24: string) => void;
  aircraft: {
    icao24: string; callsign: string; altitude_ft: number;
    scatter_score: number; az_a: number; el_a: number;
    az_b: number; el_b: number; off_path_deg: number;
  }[];
  sourceStatusText?: string;
  priorityFlight?: string;
  nowEpochS: number;
  pollIntervalS: number;
}

const STATE_LABELS: Record<string, { label: string; color: string }> = {
  idle:                 { label: "Idle",            color: "#8b949e" },
  stations_ready:       { label: "Stations Ready",  color: "#d29922" },
  candidates_available: { label: "Candidates",      color: "#d29922" },
  plane_locked:         { label: "Plane Locked",    color: "#58a6ff" },
  tracking:             { label: "Tracking…",       color: "#58a6ff" },
  ready:                { label: "Ready for QSO!",  color: "#3fb950" },
  active_qso:           { label: "QSO Active",      color: "#3fb950" },
  completed:            { label: "Completed ✓",     color: "#3fb950" },
  aborted:              { label: "Aborted",         color: "#f85149" },
};

function ScoreRing({ score }: { score: number }) {
  const r = 28, cx = 32, cy = 32;
  const circ = 2 * Math.PI * r;
  const dash = score * circ;
  const color = score >= 0.5 ? "#3fb950" : score >= 0.3 ? "#d29922" : "#f85149";
  return (
    <svg width={44} height={44} viewBox="0 0 64 64" style={{ flexShrink: 0 }}>
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1c2128" strokeWidth={6} />
      <circle cx={cx} cy={cy} r={r} fill="none" stroke={color} strokeWidth={6}
        strokeDasharray={`${dash} ${circ}`}
        strokeDashoffset={circ / 4}
        strokeLinecap="round" />
      <text x={cx} y={cy + 1} textAnchor="middle" dominantBaseline="central"
        fontSize={13} fontWeight={700} fill={color} fontFamily="monospace">
        {(score * 100).toFixed(0)}%
      </text>
    </svg>
  );
}

function AzElBadge({ label, az, el, rotAz, rotEl, rangeKm }:
  { label: string; az: number; el: number; rotAz?: number | null; rotEl?: number | null; rangeKm?: number | null }) {
  const azErr = rotAz != null ? Math.abs(((rotAz - az + 540) % 360) - 180) : null;
  const elErr = rotEl != null ? Math.abs(rotEl - el) : null;
  const onTarget = azErr != null && elErr != null && azErr < 3 && elErr < 3;
  return (
    <div className="locked-station" style={{
      padding: "4px 5px", borderRadius: 6, border: "1px solid #30363d",
      background: onTarget ? "rgba(63,185,80,0.08)" : "#0d1117",
      minWidth: 0,
    }}>
      <div style={{ fontSize: 10, color: "#8b949e", marginBottom: 2 }}>{label}</div>
      <div style={{ fontFamily: "monospace", fontSize: 13 }}>
        <span style={{ color: "#c9d1d9" }}>AZ </span>
        <span style={{ color: "#58a6ff", fontWeight: 700 }}>{az.toFixed(1)}°</span>
      </div>
      <div style={{ fontFamily: "monospace", fontSize: 13, marginTop: 2, whiteSpace: "nowrap" }}>
        <span style={{ color: "#c9d1d9" }}>EL </span>
        <span style={{ color: "#58a6ff", fontWeight: 700 }}>{el.toFixed(1)}°</span>
      </div>
      <div style={{ fontFamily: "monospace", fontSize: 13, marginTop: 2, whiteSpace: "nowrap" }}>
        <span style={{ color: "#c9d1d9" }}>km </span>
        <span style={{ color: "#58a6ff", fontWeight: 700 }}>{rangeKm != null && Number.isFinite(rangeKm) ? rangeKm.toFixed(1) : "--"}</span>
      </div>
      {onTarget && <div style={{ fontSize: 10, color: "#3fb950", marginTop: 3 }}>● On target</div>}
    </div>
  );
}

export default function PlaneLockPanel({
  locked, autoLock, sessionState, onAutoLockToggle, onUnlock, onLockAircraft, aircraft,
  sourceStatusText,
  priorityFlight = "",
  nowEpochS,
  pollIntervalS,
}: PlaneLockPanelProps) {
  const stateInfo = STATE_LABELS[sessionState] ?? STATE_LABELS.idle;
  const updateAge = locked?.position_updated_epoch_s != null
    ? Math.max(0, Math.floor(nowEpochS - locked.position_updated_epoch_s)) : null;
  const positionStale = locked?.position_updated_epoch_s != null
    && nowEpochS - locked.position_updated_epoch_s > pollIntervalS + 5;

  // State machine progression steps
  const steps = ["idle", "plane_locked", "tracking", "ready", "active_qso"];
  // Pre-lock and finished states all show the first step
  const stepIdx = Math.max(0, steps.indexOf(sessionState));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>

      {/* State machine progress bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 0, padding: "0 20px", marginBottom: 14 }}>
        {["Idle", "Locked", "Tracking", "Ready", "QSO"].map((label, i) => {
          const active = i <= stepIdx;
          const current = i === stepIdx;
          return (
            <div key={label} style={{ display: "flex", alignItems: "center", flex: i === 0 ? "0 0 8px" : 1 }}>
              {i > 0 && (
                <div style={{
                  flex: 1, height: 4,
                  background: active ? stateInfo.color : "#30363d",
                  transition: "background 0.3s",
                }} />
              )}
              <div style={{
                position: "relative", flexShrink: 0,
                width: 8, height: 8, borderRadius: "50%",
                background: active ? stateInfo.color : "#30363d",
                outline: current ? `2px solid ${stateInfo.color}` : "none",
                outlineOffset: 2,
                transition: "background 0.3s",
              }}>
                <span style={{
                  position: "absolute", top: 14, left: "50%", transform: "translateX(-50%)",
                  fontSize: 9, whiteSpace: "nowrap", color: active ? stateInfo.color : "#8b949e",
                }}>{label}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* Auto-lock toggle */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          style={{
            padding: "4px 12px", borderRadius: 12, fontSize: 11, fontWeight: 600,
            border: `1px solid ${autoLock ? "#3fb950" : "#30363d"}`,
            background: autoLock ? "rgba(63,185,80,0.12)" : "#161b22",
            color: autoLock ? "#3fb950" : "#8b949e", cursor: "pointer",
          }}
          onClick={() => onAutoLockToggle(!autoLock)}
        >
          {autoLock ? "🔒 Auto-Lock ON" : "🔓 Auto-Lock OFF"}
        </button>
        <div style={{ fontSize: 11, color: stateInfo.color, fontWeight: 600 }}>
          {stateInfo.label}
        </div>
      </div>

      {/* Locked aircraft card */}
      {locked ? (
        <div className="locked-plane" style={{
          padding: "6px 8px", borderRadius: 8,
          border: `2px solid ${sessionState === "ready" ? "#3fb950" : "#58a6ff"}44`,
          background: sessionState === "ready"
            ? "rgba(63,185,80,0.06)" : "rgba(88,166,255,0.06)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <ScoreRing score={locked.scatter_score} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span title={locked.callsign} style={{ fontSize: 18, fontWeight: 700, fontFamily: "monospace", color: "#58a6ff",
                  flex: 1, minWidth: 0, textAlign: "center", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {locked.callsign}
                </span>
                <button
                  style={{
                    flexShrink: 0, padding: "2px 5px", fontSize: 10, borderRadius: 4,
                    background: "#f8514922", color: "#f85149", border: "1px solid #f8514944",
                    cursor: "pointer",
                  }}
                  onClick={onUnlock}
                >✕ Unlock</button>
              </div>
              <div style={{ fontSize: 11, color: "#8b949e", marginTop: 2 }}>
                {locked.off_path_deg.toFixed(0)}° off-path
              </div>
            </div>
          </div>
          <div style={{ fontSize: 12, color: "#8b949e", marginTop: 4 }}>
            Aircraft altitude: {Number.isFinite(locked.altitude_ft) ? `${Math.round(locked.altitude_ft).toLocaleString("en-US")} ft` : "-- ft"}
          </div>
          <div className="locked-stations" style={{
            display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 6, marginTop: 6,
          }}>
            <AzElBadge label="From My Station"
              az={locked.az_a} el={locked.el_a}
              rotAz={locked.rotator_az} rotEl={locked.rotator_el} rangeKm={locked.range_a_km} />
            <AzElBadge label="From DX Station"
              az={locked.az_b} el={locked.el_b} rangeKm={locked.range_b_km} />
          </div>
          {sessionState === "ready" && (
            <div style={{
              marginTop: 6, padding: "4px 6px", borderRadius: 4,
              background: "rgba(63,185,80,0.15)", color: "#3fb950",
              fontSize: 12, fontWeight: 700,
            }}>
              ✓ Rotator on target — ready to start QSO
            </div>
          )}
          <div className="locked-update-age" style={{
            marginTop: 4, fontSize: 11,
            color: updateAge === null || positionStale ? "#d29922" : "#8b949e",
          }}>
            {updateAge === null
              ? "Last aircraft position update: unknown"
              : `Last aircraft position update: ${updateAge}s ago${positionStale ? " — stale" : ""}`}
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "#8b949e", padding: "8px 0" }}>
          {sessionState === "idle"
            ? `Waiting for aircraft data${sourceStatusText ? ` (${sourceStatusText})` : ""}… Auto-lock will select the best candidate.`
            : "No aircraft locked."}
        </div>
      )}

      {/* Top candidates for manual override */}
      {aircraft.length > 0 && (
        <div>
          <div style={{ fontSize: 10, color: "#8b949e", textTransform: "uppercase",
            letterSpacing: "0.06em", marginBottom: 6 }}>
            Top Candidates {locked ? "(click to override)" : "(click to lock)"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {aircraft.slice(0, 5).map((ac, i) => {
              const isLocked = locked?.icao24 === ac.icao24;
              const isPriorityFlight = Boolean(priorityFlight.trim())
                && ac.callsign.trim().toUpperCase() === priorityFlight.trim().toUpperCase();
              const scoreColor = ac.scatter_score >= 0.5 ? "#3fb950"
                : ac.scatter_score >= 0.3 ? "#d29922" : "#8b949e";
              return (
                <div
                  key={ac.icao24}
                  className="plane-candidate"
                  data-priority-flight={isPriorityFlight || undefined}
                  onClick={() => onLockAircraft(ac.icao24)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "18px minmax(0, 1fr) auto auto",
                    gap: 6, alignItems: "center",
                    padding: "5px 8px", borderRadius: 6, cursor: "pointer",
                    border: `1px solid ${isLocked ? "#58a6ff" : "#30363d"}`,
                    background: isLocked ? "rgba(88,166,255,0.08)" : "transparent",
                    fontSize: 12,
                  }}
                >
                  <span style={{ color: "#8b949e", textAlign: "center" }}>{i + 1}</span>
                  <span title={ac.callsign} style={{ fontFamily: "monospace", fontWeight: isLocked ? 700 : 400,
                    textDecoration: isPriorityFlight ? "underline" : undefined,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    color: isLocked ? "#58a6ff" : "#c9d1d9" }}>
                    {ac.callsign}
                  </span>
                  <span title="Altitude in feet" style={{ color: "#8b949e", textAlign: "center", whiteSpace: "nowrap" }}>
                    {Number.isFinite(ac.altitude_ft) ? `${Math.round(ac.altitude_ft).toLocaleString("en-US")} ft` : "-- ft"}
                  </span>
                  <span style={{ fontFamily: "monospace", color: scoreColor, fontWeight: 600,
                    textAlign: "center" }}>
                    {(ac.scatter_score * 100).toFixed(0)}%
                  </span>
                  <span style={{ color: "#8b949e", fontFamily: "monospace", gridColumn: "2 / 4" }}>
                    AZ/EL {ac.az_a.toFixed(0)}°/{ac.el_a.toFixed(0)}°
                  </span>
                  <span style={{ color: ac.off_path_deg < 20 ? "#3fb950"
                    : ac.off_path_deg < 40 ? "#d29922" : "#8b949e",
                    fontFamily: "monospace", textAlign: "right", gridColumn: "4" }}
                    title="Off-path angle">
                    {ac.off_path_deg.toFixed(0)}°
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
