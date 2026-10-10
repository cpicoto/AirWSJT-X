interface LockState {
  icao24: string;
  callsign: string;
  altitude_ft: number;
  az_a: number;
  el_a: number;
  az_b: number;
  el_b: number;
  scatter_score: number;
  off_path_deg: number;
  state: string;
  rotator_az?: number | null;
  rotator_el?: number | null;
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
    <svg width={64} height={64}>
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

function AzElBadge({ label, az, el, rotAz, rotEl }:
  { label: string; az: number; el: number; rotAz?: number | null; rotEl?: number | null }) {
  const azErr = rotAz != null ? Math.abs(((rotAz - az + 540) % 360) - 180) : null;
  const elErr = rotEl != null ? Math.abs(rotEl - el) : null;
  const onTarget = azErr != null && elErr != null && azErr < 3 && elErr < 3;
  return (
    <div style={{
      padding: "6px 10px", borderRadius: 6, border: "1px solid #30363d",
      background: onTarget ? "rgba(63,185,80,0.08)" : "#0d1117",
      minWidth: 118,
    }}>
      <div style={{ fontSize: 10, color: "#8b949e", marginBottom: 4 }}>{label}</div>
      <div style={{ fontFamily: "monospace", fontSize: 13, whiteSpace: "nowrap" }}>
        <span style={{ color: "#c9d1d9" }}>AZ </span>
        <span style={{ color: "#58a6ff", fontWeight: 700 }}>{az.toFixed(1)}°</span>
      </div>
      <div style={{ fontFamily: "monospace", fontSize: 13, marginTop: 2, whiteSpace: "nowrap" }}>
        <span style={{ color: "#c9d1d9" }}>EL </span>
        <span style={{ color: "#58a6ff", fontWeight: 700 }}>{el.toFixed(1)}°</span>
      </div>
      {onTarget && <div style={{ fontSize: 10, color: "#3fb950", marginTop: 3 }}>● On target</div>}
    </div>
  );
}

export default function PlaneLockPanel({
  locked, autoLock, sessionState, onAutoLockToggle, onUnlock, onLockAircraft, aircraft,
  sourceStatusText,
}: PlaneLockPanelProps) {
  const stateInfo = STATE_LABELS[sessionState] ?? STATE_LABELS.idle;

  // State machine progression steps
  const steps = ["idle", "plane_locked", "tracking", "ready", "active_qso"];
  const stepIdx = steps.indexOf(sessionState);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>

      {/* State machine progress bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
        {["Idle", "Locked", "Tracking", "Ready", "QSO"].map((label, i) => {
          const active = i <= stepIdx;
          const current = i === stepIdx;
          return (
            <div key={label} style={{ display: "flex", alignItems: "center", flex: 1 }}>
              <div style={{
                flex: 1, height: 4,
                background: i === 0 ? "transparent" : active ? stateInfo.color : "#30363d",
                transition: "background 0.3s",
              }} />
              <div style={{
                width: 8, height: 8, borderRadius: "50%",
                background: active ? stateInfo.color : "#30363d",
                outline: current ? `2px solid ${stateInfo.color}` : "none",
                outlineOffset: 2,
                transition: "all 0.3s",
              }} />
              <div style={{
                fontSize: 9, color: active ? stateInfo.color : "#8b949e",
                position: "absolute", marginTop: 16, textAlign: "center",
                width: 40, marginLeft: -16,
              }}>{label}</div>
            </div>
          );
        })}
      </div>

      <div style={{ height: 8 }} />

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
        <div style={{
          padding: "12px 14px", borderRadius: 8,
          border: `2px solid ${sessionState === "ready" ? "#3fb950" : "#58a6ff"}44`,
          background: sessionState === "ready"
            ? "rgba(63,185,80,0.06)" : "rgba(88,166,255,0.06)",
        }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
            <ScoreRing score={locked.scatter_score} />
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                <span style={{ fontSize: 18, fontWeight: 700, fontFamily: "monospace", color: "#58a6ff" }}>
                  {locked.callsign}
                </span>
                <span style={{ fontSize: 12, color: "#8b949e" }}>
                  FL{Math.round(locked.altitude_ft / 100)} · {locked.off_path_deg.toFixed(0)}° off-path
                </span>
                <button
                  style={{
                    marginLeft: "auto", padding: "2px 8px", fontSize: 10, borderRadius: 4,
                    background: "#f8514922", color: "#f85149", border: "1px solid #f8514944",
                    cursor: "pointer",
                  }}
                  onClick={onUnlock}
                >✕ Unlock</button>
              </div>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <AzElBadge label="From My Station"
                  az={locked.az_a} el={locked.el_a}
                  rotAz={locked.rotator_az} rotEl={locked.rotator_el} />
                <AzElBadge label="From DX Station"
                  az={locked.az_b} el={locked.el_b} />
              </div>
              {sessionState === "ready" && (
                <div style={{
                  marginTop: 8, padding: "6px 10px", borderRadius: 4,
                  background: "rgba(63,185,80,0.15)", color: "#3fb950",
                  fontSize: 13, fontWeight: 700,
                }}>
                  ✓ Rotator on target — ready to start QSO
                </div>
              )}
            </div>
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
              const scoreColor = ac.scatter_score >= 0.5 ? "#3fb950"
                : ac.scatter_score >= 0.3 ? "#d29922" : "#8b949e";
              return (
                <div
                  key={ac.icao24}
                  onClick={() => onLockAircraft(ac.icao24)}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "24px 1fr 50px 60px 60px 50px",
                    gap: 6, alignItems: "center",
                    padding: "5px 8px", borderRadius: 6, cursor: "pointer",
                    border: `1px solid ${isLocked ? "#58a6ff" : "#30363d"}`,
                    background: isLocked ? "rgba(88,166,255,0.08)" : "transparent",
                    fontSize: 12,
                  }}
                >
                  <span style={{ color: "#8b949e", textAlign: "center" }}>{i + 1}</span>
                  <span style={{ fontFamily: "monospace", fontWeight: isLocked ? 700 : 400,
                    color: isLocked ? "#58a6ff" : "#c9d1d9" }}>
                    {ac.callsign}
                  </span>
                  <span style={{ color: "#8b949e", textAlign: "center" }}>
                    FL{Math.round(ac.altitude_ft / 100)}
                  </span>
                  <span style={{ fontFamily: "monospace", color: scoreColor, fontWeight: 600,
                    textAlign: "center" }}>
                    {(ac.scatter_score * 100).toFixed(0)}%
                  </span>
                  <span style={{ color: "#8b949e", fontFamily: "monospace", textAlign: "center" }}>
                    {ac.az_a.toFixed(0)}°/{ac.el_a.toFixed(0)}°
                  </span>
                  <span style={{ color: ac.off_path_deg < 20 ? "#3fb950"
                    : ac.off_path_deg < 40 ? "#d29922" : "#8b949e",
                    fontFamily: "monospace", textAlign: "center" }}>
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
