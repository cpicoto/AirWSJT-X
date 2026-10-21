import { useEffect, useState } from "react";
import { isSpeechSupported } from "./lib/speech";

interface RotatorPanelProps {
  currentAz: number;
  currentEl: number;
  targetAz: number | null;
  targetEl: number | null;
  targetInvalid?: boolean;
  connected: boolean;
  dxAz?: number | null;
  dxLabel?: string | null;
  myLabel?: string | null;
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
  onSync?: (az: number, el: number) => void;
  syncDisabled?: boolean;
  clicksBlocked?: boolean;
  onBlockedClick?: () => void;
  onConnect: (host: string, port: number) => void;
}

// ─── Compass Rose SVG ─────────────────────────────────────────────────────────

// Wrap any azimuth into [0, 360); non-finite input becomes 0.
function wrapAz(az: number): number {
  if (!Number.isFinite(az)) return 0;
  const w = ((Math.round(az * 10) / 10) % 360 + 360) % 360;
  return w;
}

const AZ_INPUT_MAX = 450;
const EL_INPUT_MAX = 90;

// Typed azimuth is limited to the rotator travel 0..450; bad input becomes 0.
function clampAzInput(az: number): number {
  if (!Number.isFinite(az)) return 0;
  return Math.min(Math.max(Math.round(az * 10) / 10, 0), AZ_INPUT_MAX);
}

function clampElInput(el: number): number {
  if (!Number.isFinite(el)) return 0;
  return Math.min(Math.max(Math.round(el * 10) / 10, 0), EL_INPUT_MAX);
}

function CompassRose({
  current,
  target,
  size = 260,
  dxAz = null,
  dxLabel = null,
  myLabel = null,
  onClick,
  active = true,
}: {
  current: number;
  target: number | null;
  size?: number;
  dxAz?: number | null;
  dxLabel?: string | null;
  myLabel?: string | null;
  onClick?: (az: number) => void;
  active?: boolean;
}) {
  const rotorColor = active ? "#58a6ff" : "#6e7681";
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
      width={size} height={size} overflow="visible"
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
          fill={rotorColor}
          stroke={rotorColor}
          strokeWidth={1}
          opacity={0.9}
        />
        {/* Red tail half */}
        <polygon
          points={needle((current + 180) % 360, needleLen * 0.25, 0)}
          fill={active ? "#f85149" : "#6e7681"}
          stroke={active ? "#f85149" : "#6e7681"}
          strokeWidth={1}
          opacity={0.9}
        />
      </g>

      {/* Center hub */}
      <circle cx={cx} cy={cy} r={8} fill="#161b22" stroke={rotorColor} strokeWidth={2} />
      <circle cx={cx} cy={cy} r={3} fill={rotorColor} />

      {/* DX station direction marker on outer ring, label just outside */}
      {dxAz !== null && (() => {
        const dot = azToXY(dxAz, outerR);
        const lp = azToXY(dxAz, outerR + 10);
        const sin = Math.sin((dxAz * Math.PI) / 180);
        const anchor = sin > 0.3 ? "start" : sin < -0.3 ? "end" : "middle";
        const cos = Math.cos((dxAz * Math.PI) / 180);
        const dy = anchor === "middle" ? (cos > 0 ? -6 : 8) : 3;
        return (
          <g pointerEvents="none">
            <circle cx={dot.x} cy={dot.y} r={4} fill="#3fb950" stroke="#0d1117" strokeWidth={1} />
            {dxLabel && (
              <text x={lp.x} y={lp.y + dy} textAnchor={anchor} fontSize={9} fontWeight={700} fill="#3fb950">
                {dxLabel}
              </text>
            )}
          </g>
        );
      })()}

      {/* MY marker on the reciprocal of the DX bearing */}
      {dxAz !== null && myLabel && (() => {
        const az = (dxAz + 180) % 360;
        const dot = azToXY(az, outerR);
        return (
          <g pointerEvents="none">
            <circle cx={dot.x} cy={dot.y} r={4} fill="#58a6ff" stroke="#0d1117" strokeWidth={1} />
          </g>
        );
      })()}
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
  active = true,
}: {
  current: number;
  target: number | null;
  width?: number;
  height?: number;
  onClick?: (el: number) => void;
  active?: boolean;
}) {
  const rotorColor = active ? "#58a6ff" : "#6e7681";
  const cx = 18;
  const cy = height - 16;
  const R = Math.max(22, Math.min(width - 30, height - 24));

  // Quarter dial: 0° = horizon (right), 90° = zenith (up)
  function elToXY(el: number, r: number) {
    const rad = (el * Math.PI) / 180;
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
    const dy = cy - (e.clientY - rect.top);
    const angle = (Math.atan2(dy, dx) * 180) / Math.PI;
    const el = Math.max(0, Math.min(90, angle));
    onClick(Math.round(el));
  }

  return (
    <svg width={width} height={height} style={{ cursor: onClick ? "crosshair" : "default" }}
      onClick={handleClick}>
      {/* Background arc */}
      <path d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 0 ${arcEnd.x} ${arcEnd.y}`}
        fill="none" stroke="#1c2128" strokeWidth={20} strokeLinecap="round" />
      <path d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 0 ${arcEnd.x} ${arcEnd.y}`}
        fill="none" stroke="#30363d" strokeWidth={1.5} />

      {/* Fill arc up to current elevation */}
      {(() => {
        const fillEnd = elToXY(Math.max(0.5, current), R);
        return (
          <path
            d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 0 ${fillEnd.x} ${fillEnd.y}`}
            fill="none" stroke={active ? "rgba(88,166,255,0.25)" : "rgba(110,118,129,0.25)"} strokeWidth={18} strokeLinecap="round"
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
      <path d={needle(current, R - 2)} stroke={rotorColor} strokeWidth={3} strokeLinecap="round" />
      {(() => {
        const cp = elToXY(current, R - 2);
        return <circle cx={cp.x} cy={cp.y} r={6} fill={rotorColor} />;
      })()}

      {/* Hub */}
      <circle cx={cx} cy={cy} r={8} fill="#161b22" stroke={rotorColor} strokeWidth={2} />
      <circle cx={cx} cy={cy} r={3} fill={rotorColor} />

      {/* Labels */}
      <text x={arcStart.x} y={cy + 12} textAnchor="middle" fontSize={10} fill="#8b949e">0°</text>
      <text x={arcEnd.x} y={arcEnd.y - 8} textAnchor="middle" fontSize={10} fill="#8b949e">90°</text>

    </svg>
  );
}

// ─── Main RotatorPanel ────────────────────────────────────────────────────────

export default function RotatorPanel({
  currentAz, currentEl, targetAz, targetEl, targetInvalid = false, connected, dxAz = null, dxLabel = null, myLabel = null,
  speechEnabled,
  speechThresholdDeg,
  speechIntervalSec,
  onSpeechEnabledChange,
  onSpeechThresholdDegChange,
  onSpeechIntervalSecChange,
  mode = "rotator",
  onSetPosition, onStop, onPark, onConnect, onSync, syncDisabled = false, clicksBlocked = false, onBlockedClick,
}: RotatorPanelProps) {
  const [manualAz, setManualAz] = useState(currentAz.toFixed(1));
  const [manualEl, setManualEl] = useState(currentEl.toFixed(1));
  const [host, setHost] = useState(() => localStorage.getItem("rotatorHost") || "127.0.0.1");
  const [port, setPort] = useState(() => localStorage.getItem("rotatorPort") || "4533");

  // Sync manual fields when target changes
  useEffect(() => {
    if (targetAz !== null) setManualAz(targetAz.toFixed(1));
  }, [targetAz]);
  useEffect(() => {
    if (targetEl !== null) setManualEl(targetEl.toFixed(1));
  }, [targetEl]);

  function handleCompassClick(az: number) {
    if (mode === "manual" && clicksBlocked) { onBlockedClick?.(); return; }
    const wrapped = wrapAz(az);
    setManualAz(wrapped.toFixed(1));
    if (mode === "manual") {
      onSetPosition(wrapped, clampElInput(parseFloat(manualEl)));
    }
  }
  function handleElevationClick(el: number) {
    if (mode === "manual" && clicksBlocked) { onBlockedClick?.(); return; }
    const clamped = clampElInput(el);
    setManualEl(clamped.toFixed(1));
    if (mode === "manual") {
      onSetPosition(clampAzInput(parseFloat(manualAz)), clamped);
    }
  }
  function handleGo() {
    const az = clampAzInput(parseFloat(manualAz));
    const el = clampElInput(parseFloat(manualEl));
    setManualAz(az.toFixed(1));
    setManualEl(el.toFixed(1));
    onSetPosition(az, el);
  }

  function handleSync() {
    setManualAz(currentAz.toFixed(1));
    setManualEl(currentEl.toFixed(1));
    onSync?.(currentAz, currentEl);
  }

  // Error delta indicators
  const azErr = targetAz !== null ? ((currentAz - targetAz + 540) % 360 - 180) : null;
  const elErr = targetEl !== null ? currentEl - targetEl : null;

  const compassSize = 170;
  const elevWidth = 170;
  const elevHeight = 108;

  const isManualMode = mode === "manual";
  const displayAz = targetAz ?? currentAz;
  const displayEl = targetEl ?? currentEl;
  const speechSupported = isSpeechSupported();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>

      {/* Connection bar */}
      {!connected && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "nowrap" }}>
          <input value={host} onChange={e => setHost(e.target.value)}
            placeholder="host" style={{ ...inputStyle, flex: 1, minWidth: 0 }} />
          <input value={port} inputMode="numeric" maxLength={5}
            onChange={e => setPort(e.target.value.replace(/\D/g, "").slice(0, 5))}
            placeholder="port" style={{ ...inputStyle, width: 60, flexShrink: 0 }} />
          <button className="btn-secondary" style={{ ...smBtn, flexShrink: 0 }}
            onClick={() => {
              localStorage.setItem("rotatorHost", host);
              localStorage.setItem("rotatorPort", port);
              onConnect(host, parseInt(port) || 4533);
            }}>
            Connect
          </button>
        </div>
      )}

      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr)",
        gap: 2,
        padding: "5px 10px",
        background: isManualMode ? "rgba(88,166,255,0.08)" : "rgba(255,255,255,0.02)",
        borderRadius: 6,
        border: isManualMode ? "1px solid #58a6ff44" : "1px solid #30363d",
        opacity: targetInvalid ? 0.35 : 1,
        filter: targetInvalid ? "grayscale(1)" : undefined,
      }}>
        <div style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          fontFamily: "monospace",
        }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
            <span style={{ fontSize: 11, color: "#8b949e" }}>AZ</span>
            <span style={{ fontSize: 26, lineHeight: 1.05, fontWeight: 700, color: "#58a6ff" }}>{displayAz.toFixed(1)}°</span>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
            <span style={{ fontSize: 11, color: "#8b949e" }}>EL</span>
            <span style={{ fontSize: 26, lineHeight: 1.05, fontWeight: 700, color: "#3fb950" }}>{displayEl.toFixed(1)}°</span>
          </div>
        </div>
      </div>

      {isManualMode && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "center" }}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
            <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>Azimuth</div>
            <CompassRose current={currentAz} target={targetAz} size={compassSize} dxAz={dxAz} dxLabel={dxLabel} myLabel={myLabel} onClick={handleCompassClick} active={connected} />
          </div>
          <div style={{ position: "relative", width: elevWidth, alignSelf: "center", marginTop: 14 }}>
            <div style={{ position: "absolute", right: 0, top: "50%", transform: "translateY(-50%)", fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em", pointerEvents: "none" }}>Elevation</div>
            <ElevationArc current={currentEl} target={targetEl} width={elevWidth} height={elevHeight} onClick={handleElevationClick} active={connected} />
          </div>
        </div>
      )}

      {!isManualMode && (
        <>
          {/* Dials */}
          <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "center", opacity: targetInvalid ? 0.35 : 1, filter: targetInvalid ? "grayscale(1)" : undefined }}>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2 }}>
              <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Azimuth
              </div>
              <CompassRose current={currentAz} target={targetAz} size={compassSize} dxAz={dxAz} dxLabel={dxLabel} myLabel={myLabel}
                onClick={connected ? handleCompassClick : undefined} active={connected} />
            </div>

            {/* Elevation arc */}
            <div style={{ position: "relative", width: elevWidth, alignSelf: "center", marginTop: 14 }}>
              <div style={{ position: "absolute", right: 0, top: "50%", transform: "translateY(-50%)", fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em", pointerEvents: "none" }}>
                Elevation
              </div>
              <ElevationArc current={currentEl} target={targetEl} width={elevWidth} height={elevHeight}
                onClick={connected ? handleElevationClick : undefined} active={connected} />
            </div>
          </div>

          {!targetInvalid && (azErr !== null || elErr !== null) && (
            <div style={{
              display: "flex",
              gap: 10,
              flexWrap: "wrap",
              justifyContent: "center",
              fontSize: 12,
              fontFamily: "monospace",
              color: "#8b949e",
            }}>
              {azErr !== null && (
                <span style={{ color: Math.abs(azErr) < 2 ? "#3fb950" : "#d29922" }}>
                  ΔAZ {azErr > 0 ? "+" : ""}{azErr.toFixed(1)}°
                </span>
              )}
              {elErr !== null && (
                <span style={{ color: Math.abs(elErr) < 2 ? "#3fb950" : "#d29922" }}>
                  ΔEL {elErr > 0 ? "+" : ""}{elErr.toFixed(1)}°
                </span>
              )}
            </div>
          )}
        </>
      )}

      <div style={{
          display: "flex", flexDirection: "column", gap: 6,
          padding: "6px 8px", background: "rgba(255,255,255,0.02)",
          borderRadius: 6, border: "1px solid #30363d",
        }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "#8b949e", width: 20 }}>AZ</span>
            <input value={manualAz}
              onChange={e => setManualAz(e.target.value.replace("-", ""))}
              onBlur={() => setManualAz(clampAzInput(parseFloat(manualAz)).toFixed(1))}
              style={numInput} type="number" min={0} max={AZ_INPUT_MAX} step={0.1} />
            <span style={{ color: "#8b949e", fontSize: 11 }}>°</span>
          </label>
          <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ color: "#8b949e", width: 20 }}>EL</span>
            <input value={manualEl}
              onChange={e => setManualEl(e.target.value.replace("-", ""))}
              onBlur={() => setManualEl(clampElInput(parseFloat(manualEl)).toFixed(1))}
              style={numInput} type="number" min={0} max={EL_INPUT_MAX} step={0.1} />
            <span style={{ color: "#8b949e", fontSize: 11 }}>°</span>
          </label>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "nowrap" }}>
          <button className="btn-primary" style={rowBtn} disabled={!connected} onClick={handleGo}>
            ▶ Go
          </button>
          <button className="btn-secondary" style={rowBtn} disabled={!connected} onClick={onStop}>
            ■ Stop
          </button>
          <button className="btn-secondary" style={rowBtn} disabled={!connected} onClick={onPark}>
            ⌂ Park
          </button>
          <button className="btn-secondary" style={rowBtn}
            disabled={!connected || syncDisabled}
            title={syncDisabled
              ? "Disabled while rotctld control is ON (tracking owns the target)"
              : "Update the display to match the actual rotator position"}
            onClick={handleSync}>
            ⟳ Sync
          </button>
        </div>
      </div>

      {/* Speech settings and hint */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          padding: "4px 8px", borderRadius: 6, border: "1px solid #30363d",
          background: "rgba(255,255,255,0.02)", fontSize: 11,
        }}>
          <label style={{ display: "flex", alignItems: "center", gap: 6, color: "#c9d1d9" }}>
            <input
              type="checkbox"
              checked={speechEnabled}
              onChange={(e) => onSpeechEnabledChange(e.target.checked)}
              disabled={!speechSupported}
            />
            Speech callouts
            {!speechSupported && (
              <span style={{ fontSize: 10, color: "#8b949e" }} title="Web Speech API not available on this platform">(unavailable)</span>
            )}
          </label>
          <label style={{ display: "flex", alignItems: "center", gap: 4, color: "#8b949e" }} title="Minimum azimuth/elevation change to announce">
            Min Δ°
            <input
              type="number"
              min={0.1}
              step={0.1}
              value={speechThresholdDeg}
              style={{ ...numInput, width: 52, padding: "2px 4px", fontSize: 12 }}
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
              style={{ ...numInput, width: 52, padding: "2px 4px", fontSize: 12 }}
              onChange={(e) => onSpeechIntervalSecChange(Math.max(0, parseFloat(e.target.value) || 0))}
            />
          </label>
        </div>
        <div style={{ fontSize: 11, color: "#8b949e" }}>
          {isManualMode
            ? "Use the compass or arc for quick hand-pointing. Speech callouts announce the target azimuth/elevation, and hovering Speech on previews the current values. Orange = target · Blue = current."
            : connected
              ? "Click the compass or elevation arc to set target position. Orange dashed = target · Blue solid = current."
              : "Enable speech now and the app will announce live azimuth/elevation once rotctld reports position updates. Hovering Speech on previews the current values."}
        </div>
      </div>
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
  fontSize: 12, padding: "4px 9px",
};
const rowBtn: React.CSSProperties = {
  fontSize: 12, padding: "4px 4px", flex: 1, whiteSpace: "nowrap",
};
