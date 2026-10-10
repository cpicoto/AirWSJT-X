import { useEffect, useState } from "react";

interface RotatorPanelProps {
  currentAz: number;
  currentEl: number;
  targetAz: number | null;
  targetEl: number | null;
  connected: boolean;
  speechEnabled: boolean;
  speechThresholdDeg: number;
  speechIntervalSec: number;
  onSpeechEnabledChange: (enabled: boolean) => void;
  onSpeechThresholdDegChange: (deg: number) => void;
  onSpeechIntervalSecChange: (sec: number) => void;
  mode?: "rotator" | "manual";
  aircraftLabel?: string | null;
  targetLabel?: string | null;
  onSetPosition: (az: number, el: number) => void;
  onStop: () => void;
  onPark: () => void;
  onConnect: (host: string, port: number) => void;
}

// ─── Compass Rose SVG ─────────────────────────────────────────────────────────

function CompassRose({
  current,
  target,
  size = 260,
  onClick,
}: {
  current: number;
  target: number | null;
  size?: number;
  onClick?: (az: number) => void;
}) {
  const cx = size / 2, cy = size / 2;
  const outerR = size / 2 - 4;
  const innerR = outerR - 22;
  const needleLen = innerR - 12;
  const cardinals = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];

  function azToXY(az: number, r: number) {
    const rad = ((az - 90) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  }

  function needle(az: number, len: number, tailLen: number) {
    const tip = azToXY(az, len);
    const tail = azToXY((az + 180) % 360, tailLen);
    return `${tip.x},${tip.y} ${tail.x},${tail.y}`;
  }

  // Tick marks
  const ticks = [];
  for (let i = 0; i < 360; i += 5) {
    const isMajor = i % 30 === 0;
    const isMid = i % 10 === 0;
    const tickOuter = outerR;
    const tickInner = isMajor ? outerR - 16 : isMid ? outerR - 10 : outerR - 6;
    const p1 = azToXY(i, tickOuter);
    const p2 = azToXY(i, tickInner);
    ticks.push(
      <line
        key={i}
        x1={p1.x} y1={p1.y}
        x2={p2.x} y2={p2.y}
        stroke={isMajor ? "#58a6ff" : "#30363d"}
        strokeWidth={isMajor ? 1.5 : 0.8}
      />
    );
    if (isMajor && i % 30 === 0) {
      const lp = azToXY(i, outerR - 28);
      ticks.push(
        <text
          key={`t${i}`}
          x={lp.x} y={lp.y}
          textAnchor="middle" dominantBaseline="central"
          fontSize={i % 90 === 0 ? 11 : 8}
          fill={i % 90 === 0 ? "#c9d1d9" : "#8b949e"}
          fontWeight={i % 90 === 0 ? 700 : 400}
        >
          {cardinals[i / 45]}
        </text>
      );
    }
  }

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!onClick) return;
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const dx = e.clientX - rect.left - cx;
    const dy = e.clientY - rect.top - cy;
    const az = ((Math.atan2(dy, dx) * 180) / Math.PI + 90 + 360) % 360;
    onClick(Math.round(az));
  }

  return (
    <svg
      width={size} height={size}
      style={{ cursor: onClick ? "crosshair" : "default", userSelect: "none" }}
      onClick={handleClick}
    >
      {/* Background rings */}
      <circle cx={cx} cy={cy} r={outerR} fill="#0d1117" stroke="#30363d" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={innerR} fill="none" stroke="#1c2128" strokeWidth={1} />
      <circle cx={cx} cy={cy} r={innerR * 0.5} fill="none" stroke="#1c2128" strokeWidth={0.5} />

      {/* Tick marks and cardinal labels */}
      {ticks}

      {/* Target needle (orange, dashed) */}
      {target !== null && (
        <g>
          <polygon
            points={needle(target, needleLen * 0.85, needleLen * 0.2)}
            fill="none"
            stroke="#d29922"
            strokeWidth={2}
            strokeDasharray="6 3"
            opacity={0.8}
          />
          {(() => {
            const tip = azToXY(target, needleLen * 0.85);
            return (
              <circle cx={tip.x} cy={tip.y} r={4} fill="#d29922" opacity={0.9} />
            );
          })()}
        </g>
      )}

      {/* Current needle (blue/white, solid) */}
      <g>
        {/* Shadow */}
        <polygon
          points={needle(current, needleLen, needleLen * 0.25)}
          fill="rgba(0,0,0,0.4)"
          stroke="none"
          transform={`translate(1,1)`}
        />
        {/* White tip half */}
        <polygon
          points={needle(current, needleLen, 0)}
          fill="#58a6ff"
          stroke="#58a6ff"
          strokeWidth={1}
          opacity={0.9}
        />
        {/* Red tail half */}
        <polygon
          points={needle((current + 180) % 360, needleLen * 0.25, 0)}
          fill="#f85149"
          stroke="#f85149"
          strokeWidth={1}
          opacity={0.9}
        />
      </g>

      {/* Center hub */}
      <circle cx={cx} cy={cy} r={8} fill="#161b22" stroke="#58a6ff" strokeWidth={2} />
      <circle cx={cx} cy={cy} r={3} fill="#58a6ff" />

      {/* Current bearing text */}
      <text x={cx} y={cy + innerR * 0.62} textAnchor="middle" fontSize={22} fontWeight={700}
        fill="#c9d1d9" fontFamily="monospace">
        {current.toFixed(1)}°
      </text>
      {target !== null && (
        <text x={cx} y={cy + innerR * 0.62 + 22} textAnchor="middle" fontSize={12}
          fill="#d29922" fontFamily="monospace">
          ▶ {target.toFixed(1)}°
        </text>
      )}
    </svg>
  );
}

// ─── Elevation Arc ────────────────────────────────────────────────────────────

function ElevationArc({
  current,
  target,
  width = 260,
  height = 140,
  onClick,
}: {
  current: number;
  target: number | null;
  width?: number;
  height?: number;
  onClick?: (el: number) => void;
}) {
  const cx = width / 2;
  const cy = height - 16;
  const R = height - 24;

  // 0° = left end, 90° = top centre
  function elToXY(el: number, r: number) {
    const rad = Math.PI - (el / 90) * Math.PI;
    return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
  }

  // Arc path from 0° to 90°
  const arcStart = elToXY(0, R);
  const arcEnd = elToXY(90, R);

  // Tick marks
  const ticks = [];
  for (let e = 0; e <= 90; e += 5) {
    const isMajor = e % 30 === 0;
    const isMid = e % 10 === 0;
    const p1 = elToXY(e, R);
    const p2 = elToXY(e, isMajor ? R - 14 : isMid ? R - 9 : R - 5);
    ticks.push(
      <line key={e} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
        stroke={isMajor ? "#58a6ff" : "#30363d"} strokeWidth={isMajor ? 1.5 : 0.8} />
    );
    if (isMajor) {
      const lp = elToXY(e, R - 26);
      ticks.push(
        <text key={`l${e}`} x={lp.x} y={lp.y} textAnchor="middle" dominantBaseline="central"
          fontSize={9} fill="#8b949e">{e}°</text>
      );
    }
  }

  // Needle line from centre to arc
  function needle(el: number, r: number) {
    const end = elToXY(el, r);
    return `M ${cx} ${cy} L ${end.x} ${end.y}`;
  }

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!onClick) return;
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const dx = e.clientX - rect.left - cx;
    const dy = e.clientY - rect.top - cy;
    const angle = Math.atan2(-dy, -dx) * 180 / Math.PI; // 180° = left
    const el = Math.max(0, Math.min(90, (180 - (angle + 180) % 360) * 90 / 180));
    onClick(Math.round(el));
  }

  return (
    <svg width={width} height={height} style={{ cursor: onClick ? "crosshair" : "default" }}
      onClick={handleClick}>
      {/* Background arc */}
      <path d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 1 ${arcEnd.x} ${arcEnd.y}`}
        fill="none" stroke="#1c2128" strokeWidth={20} strokeLinecap="round" />
      <path d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 1 ${arcEnd.x} ${arcEnd.y}`}
        fill="none" stroke="#30363d" strokeWidth={1.5} />

      {/* Fill arc up to current elevation */}
      {(() => {
        const fillEnd = elToXY(Math.max(0.5, current), R);
        return (
          <path
            d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 ${current > 45 ? 1 : 0} 1 ${fillEnd.x} ${fillEnd.y}`}
            fill="none" stroke="rgba(88,166,255,0.25)" strokeWidth={18} strokeLinecap="round"
          />
        );
      })()}

      {ticks}

      {/* Target needle */}
      {target !== null && (
        <path d={needle(target, R - 4)} stroke="#d29922" strokeWidth={2}
          strokeDasharray="6 3" opacity={0.85} />
      )}
      {target !== null && (() => {
        const tp = elToXY(target, R - 4);
        return <circle cx={tp.x} cy={tp.y} r={5} fill="#d29922" opacity={0.9} />;
      })()}

      {/* Current needle */}
      <path d={needle(current, R - 2)} stroke="#58a6ff" strokeWidth={3} strokeLinecap="round" />
      {(() => {
        const cp = elToXY(current, R - 2);
        return <circle cx={cp.x} cy={cp.y} r={6} fill="#58a6ff" />;
      })()}

      {/* Hub */}
      <circle cx={cx} cy={cy} r={8} fill="#161b22" stroke="#58a6ff" strokeWidth={2} />
      <circle cx={cx} cy={cy} r={3} fill="#58a6ff" />

      {/* Labels */}
      <text x={arcStart.x - 4} y={cy + 12} textAnchor="end" fontSize={10} fill="#8b949e">0°</text>
      <text x={arcEnd.x + 4} y={cy + 12} textAnchor="start" fontSize={10} fill="#8b949e">90°</text>

      {/* Current value */}
      <text x={cx} y={cy - R * 0.4} textAnchor="middle" fontSize={20} fontWeight={700}
        fill="#c9d1d9" fontFamily="monospace">{current.toFixed(1)}°</text>
      {target !== null && (
        <text x={cx} y={cy - R * 0.4 + 20} textAnchor="middle" fontSize={11}
          fill="#d29922" fontFamily="monospace">▶ {target.toFixed(1)}°</text>
      )}
    </svg>
  );
}

// ─── Main RotatorPanel ────────────────────────────────────────────────────────

export default function RotatorPanel({
  currentAz, currentEl, targetAz, targetEl,
  connected,
  speechEnabled,
  speechThresholdDeg,
  speechIntervalSec,
  onSpeechEnabledChange,
  onSpeechThresholdDegChange,
  onSpeechIntervalSecChange,
  mode = "rotator", aircraftLabel = null, targetLabel = null,
  onSetPosition, onStop, onPark, onConnect,
}: RotatorPanelProps) {
  const [manualAz, setManualAz] = useState(currentAz.toFixed(1));
  const [manualEl, setManualEl] = useState(currentEl.toFixed(1));
  const [host, setHost] = useState("127.0.0.1");
  const [port, setPort] = useState("4533");

  // Sync manual fields when target changes
  useEffect(() => {
    if (targetAz !== null) setManualAz(targetAz.toFixed(1));
  }, [targetAz]);
  useEffect(() => {
    if (targetEl !== null) setManualEl(targetEl.toFixed(1));
  }, [targetEl]);

  function handleCompassClick(az: number) {
    setManualAz(az.toFixed(1));
    if (mode === "manual") {
      onSetPosition(az, parseFloat(manualEl) || 0);
    }
  }
  function handleElevationClick(el: number) {
    setManualEl(el.toFixed(1));
    if (mode === "manual") {
      onSetPosition(parseFloat(manualAz) || 0, el);
    }
  }
  function handleGo() {
    onSetPosition(parseFloat(manualAz) || 0, parseFloat(manualEl) || 0);
  }

  // Error delta indicators
  const azErr = targetAz !== null ? ((currentAz - targetAz + 540) % 360 - 180) : null;
  const elErr = targetEl !== null ? currentEl - targetEl : null;

  const compassSize = 130;
  const elevWidth = 130;
  const elevHeight = 73;

  const isManualMode = mode === "manual";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>

      {/* Connection bar */}
      {!isManualMode && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "3px 10px", borderRadius: 6, fontSize: 12,
            background: connected ? "rgba(63,185,80,0.12)" : "rgba(248,81,73,0.12)",
            border: `1px solid ${connected ? "#3fb950" : "#f85149"}`,
            color: connected ? "#3fb950" : "#f85149", fontWeight: 600,
          }}>
            <span style={{ fontSize: 8 }}>{connected ? "●" : "○"}</span>
            {connected ? "rotctld connected" : "rotctld offline"}
          </div>
          {!connected && (
            <>
              <input value={host} onChange={e => setHost(e.target.value)}
                placeholder="host" style={inputStyle} />
              <input value={port} onChange={e => setPort(e.target.value)}
                placeholder="port" style={{ ...inputStyle, width: 60 }} />
              <button className="btn-secondary" style={smBtn}
                onClick={() => onConnect(host, parseInt(port) || 4533)}>
                Connect
              </button>
            </>
          )}
        </div>
      )}
      {isManualMode && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "3px 10px", borderRadius: 6, fontSize: 12,
            background: "rgba(88,166,255,0.12)",
            border: "1px solid #58a6ff44",
            color: "#58a6ff", fontWeight: 600,
          }}>
            <span style={{ fontSize: 8 }}>●</span>
            Manual pointing mode
          </div>
        </div>
      )}

      {isManualMode && (
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 10px", background: "rgba(88,166,255,0.08)", borderRadius: 6, border: "1px solid #58a6ff44" }}>
            <div style={{ fontSize: 10, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>Pointing</div>
            <div style={{ fontSize: 12, fontWeight: 700, color: "#c9d1d9", fontFamily: "monospace" }}>{aircraftLabel || targetLabel || "Manual"}</div>
          </div>

          <div style={{ display: "flex", gap: 12, justifyContent: "center", alignItems: "center" }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
              <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>Azimuth</div>
              <CompassRose current={currentAz} target={targetAz} size={150} onClick={isManualMode ? handleCompassClick : connected ? handleCompassClick : undefined} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
              <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>Elevation</div>
              <ElevationArc current={currentEl} target={targetEl} width={150} height={95} onClick={isManualMode ? handleElevationClick : connected ? handleElevationClick : undefined} />
            </div>
          </div>
        </div>
      )}

      {!isManualMode && (
        <>
          {/* Dials row */}
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", justifyContent: "center" }}>

            {/* Azimuth compass */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
              <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Azimuth
              </div>
              <CompassRose current={currentAz} target={targetAz} size={compassSize}
                onClick={connected ? handleCompassClick : undefined} />
              {azErr !== null && (
                <div style={{ fontSize: 12, color: Math.abs(azErr) < 2 ? "#3fb950" : "#d29922", fontFamily: "monospace" }}>
                  Δ {azErr > 0 ? "+" : ""}{azErr.toFixed(1)}°
                </div>
              )}
            </div>

            {/* Elevation arc */}
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
              <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Elevation
              </div>
              <ElevationArc current={currentEl} target={targetEl} width={elevWidth} height={elevHeight}
                onClick={connected ? handleElevationClick : undefined} />
              {elErr !== null && (
                <div style={{ fontSize: 12, color: Math.abs(elErr) < 2 ? "#3fb950" : "#d29922", fontFamily: "monospace" }}>
                  Δ {elErr > 0 ? "+" : ""}{elErr.toFixed(1)}°
                </div>
              )}
            </div>
          </div>
        </>
      )}

      {!isManualMode && (
        <div style={{
          display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
          padding: "10px 12px", background: "rgba(255,255,255,0.02)",
          borderRadius: 6, border: "1px solid #30363d",
        }}>
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "#8b949e", width: 20 }}>AZ</span>
            <input value={manualAz} onChange={e => setManualAz(e.target.value)}
              style={numInput} type="number" min={0} max={360} step={0.1} />
            <span style={{ color: "#8b949e", fontSize: 11 }}>°</span>
          </label>
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "#8b949e", width: 20 }}>EL</span>
            <input value={manualEl} onChange={e => setManualEl(e.target.value)}
              style={numInput} type="number" min={0} max={90} step={0.1} />
            <span style={{ color: "#8b949e", fontSize: 11 }}>°</span>
          </label>
          <button className="btn-primary" style={smBtn} disabled={!connected} onClick={handleGo}>
            ▶ Go
          </button>
          <button className="btn-secondary" style={smBtn} disabled={!connected} onClick={onStop}>
            ■ Stop
          </button>
          <button className="btn-secondary" style={smBtn} disabled={!connected} onClick={onPark}>
            ⌂ Park
          </button>
        </div>
      )}

      {/* Hint */}
      {connected && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
            padding: "8px 10px", borderRadius: 6, border: "1px solid #30363d",
            background: "rgba(255,255,255,0.02)", fontSize: 11,
          }}>
            <label style={{ display: "flex", alignItems: "center", gap: 6, color: "#c9d1d9" }}>
              <input
                type="checkbox"
                checked={speechEnabled}
                onChange={(e) => onSpeechEnabledChange(e.target.checked)}
              />
              Speech
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 4, color: "#8b949e" }} title="Minimum azimuth/elevation change to announce">
              Min Δ°
              <input
                type="number"
                min={0.1}
                step={0.1}
                value={speechThresholdDeg}
                style={{ ...numInput, width: 58 }}
                onChange={(e) => onSpeechThresholdDegChange(parseFloat(e.target.value) || 0.1)}
              />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 4, color: "#8b949e" }} title="Minimum time between speech announcements">
              Min interval (s)
              <input
                type="number"
                min={0}
                step={0.1}
                value={speechIntervalSec}
                style={{ ...numInput, width: 58 }}
                onChange={(e) => onSpeechIntervalSecChange(Math.max(0, parseFloat(e.target.value) || 0))}
              />
            </label>
          </div>
          <div style={{ fontSize: 11, color: "#8b949e" }}>
            {isManualMode
              ? "Use the compass or arc for quick hand-pointing. Orange = target · Blue = current."
              : "Click the compass or elevation arc to set target position. Orange dashed = target · Blue solid = current."}
          </div>
        </div>
      )}
      {!connected && isManualMode && (
        <div style={{ fontSize: 11, color: "#8b949e" }}>
          Manual mode does not require rotctld; use the compass and arc to keep the antenna aligned.
        </div>
      )}
      {!connected && !isManualMode && (
        <div style={{ fontSize: 11, color: "#8b949e" }}>
          Start rotctld: <code style={{ background: "#161b22", padding: "1px 5px", borderRadius: 3 }}>
            rotctld -m 1 -r /dev/ttyUSB0 -s 9600 -T 127.0.0.1 -t 4533
          </code>
        </div>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  background: "#161b22", color: "#c9d1d9",
  border: "1px solid #30363d", borderRadius: 4,
  padding: "3px 8px", width: 100, fontSize: 12,
};
const numInput: React.CSSProperties = {
  background: "#161b22", color: "#c9d1d9",
  border: "1px solid #30363d", borderRadius: 4,
  padding: "3px 6px", width: 72, fontSize: 13,
  fontFamily: "monospace", textAlign: "right",
};
const smBtn: React.CSSProperties = {
  fontSize: 12, padding: "5px 12px",
};
