import { useMemo, type ReactNode } from "react";

export interface SkyDomeAircraft {
  icao24: string;
  callsign: string;
  az_a: number;
  el_a: number;
  scatter_score: number;
  heading_deg: number | null;
  velocity_kts: number | null;
  altitude_ft: number;
  range_a_km: number;
  off_path_deg: number;
  locked?: boolean;
}

interface SkyDomeProps {
  aircraft: SkyDomeAircraft[];
  rotatorAz: number;
  rotatorEl: number;
  targetAz: number | null;
  targetEl: number | null;
  pathBearing: number | null;
  size?: number;
  showTrails?: boolean;
  onClickAircraft?: (icao24: string) => void;
  onClickSky?: (az: number, el: number) => void;
}

function scoreColor(score: number): string {
  if (score >= 0.7) return "#3fb950";
  if (score >= 0.4) return "#d29922";
  if (score >= 0.15) return "#db6d28";
  return "#484f58";
}

function scoreGlow(score: number): string {
  if (score >= 0.7) return "rgba(63,185,80,0.4)";
  if (score >= 0.4) return "rgba(210,153,34,0.3)";
  return "none";
}

export default function SkyDome({
  aircraft,
  rotatorAz,
  rotatorEl,
  targetAz,
  targetEl,
  pathBearing,
  size = 400,
  showTrails = true,
  onClickAircraft,
  onClickSky,
}: SkyDomeProps) {
  const cx = size / 2;
  const cy = size / 2;
  const outerR = size / 2 - 8;

  // Polar projection: elevation 0° = edge, 90° = center
  function project(az: number, el: number): { x: number; y: number } {
    const r = outerR * (1 - Math.max(0, Math.min(90, el)) / 90);
    const rad = ((az - 90) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  }

  // Predict position ~3 minutes ahead from heading + velocity
  function predictTrail(
    ac: SkyDomeAircraft,
    minutes: number,
  ): { x: number; y: number } | null {
    if (ac.heading_deg === null || ac.velocity_kts === null) return null;
    const speedDegPerMin = (ac.velocity_kts * 1.852) / 111.0 / 60;
    const dAz = speedDegPerMin * minutes * Math.sin((ac.heading_deg * Math.PI) / 180) * 3;
    const dEl = -speedDegPerMin * minutes * 0.3;
    return project(ac.az_a + dAz, Math.max(0, ac.el_a + dEl));
  }

  const elevationRings = useMemo(() => {
    const rings: ReactNode[] = [];
    for (const el of [0, 15, 30, 45, 60, 75, 90]) {
      const r = outerR * (1 - el / 90);
      rings.push(
        <circle
          key={`ring-${el}`}
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke={el === 0 ? "#30363d" : "#1c2128"}
          strokeWidth={el === 0 ? 1.5 : 0.5}
          strokeDasharray={el > 0 && el < 90 ? "3 6" : "none"}
        />,
      );
      if (el > 0 && el < 90 && el % 30 === 0) {
        rings.push(
          <text
            key={`elbl-${el}`}
            x={cx + 3} y={cy - r + 10}
            fontSize={8} fill="#484f58"
          >
            {el}°
          </text>,
        );
      }
    }
    return rings;
  }, [cx, cy, outerR]);

  const cardinalLabels = useMemo(() => {
    const labels: ReactNode[] = [];
    const dirs = [
      { label: "N", az: 0 }, { label: "NE", az: 45 },
      { label: "E", az: 90 }, { label: "SE", az: 135 },
      { label: "S", az: 180 }, { label: "SW", az: 225 },
      { label: "W", az: 270 }, { label: "NW", az: 315 },
    ];
    for (const { label, az } of dirs) {
      const isCardinal = az % 90 === 0;
      const r = outerR + (isCardinal ? 14 : 12);
      const rad = ((az - 90) * Math.PI) / 180;
      labels.push(
        <text
          key={`card-${az}`}
          x={cx + r * Math.cos(rad)}
          y={cy + r * Math.sin(rad)}
          textAnchor="middle" dominantBaseline="central"
          fontSize={isCardinal ? 12 : 9}
          fontWeight={isCardinal ? 700 : 400}
          fill={isCardinal ? "#c9d1d9" : "#6e7681"}
        >
          {label}
        </text>,
      );
    }
    return labels;
  }, [cx, cy, outerR]);

  // Azimuth tick marks
  const ticks = useMemo(() => {
    const t: ReactNode[] = [];
    for (let az = 0; az < 360; az += 5) {
      const isMajor = az % 30 === 0;
      const isMid = az % 10 === 0;
      const r2 = isMajor ? outerR - 8 : isMid ? outerR - 5 : outerR - 3;
      const p1 = project(az, 0);
      const rad = ((az - 90) * Math.PI) / 180;
      const p2 = { x: cx + r2 * Math.cos(rad), y: cy + r2 * Math.sin(rad) };
      t.push(
        <line
          key={`tick-${az}`}
          x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
          stroke={isMajor ? "#58a6ff" : "#30363d"}
          strokeWidth={isMajor ? 1.2 : 0.6}
        />,
      );
    }
    return t;
  }, [cx, cy, outerR]);

  // Scatter corridor band
  const corridorPath = useMemo(() => {
    if (pathBearing === null) return null;
    const halfWidth = 20;
    const elements: ReactNode[] = [];
    const points: string[] = [];
    for (let el = 0; el <= 90; el += 2) {
      const p = project((pathBearing + halfWidth) % 360, el);
      points.push(`${p.x},${p.y}`);
    }
    for (let el = 90; el >= 0; el -= 2) {
      const p = project((pathBearing - halfWidth + 360) % 360, el);
      points.push(`${p.x},${p.y}`);
    }
    elements.push(
      <polygon
        key="corridor-near"
        points={points.join(" ")}
        fill="rgba(56,139,253,0.08)"
        stroke="rgba(56,139,253,0.15)"
        strokeWidth={0.5}
      />,
    );
    // Also draw the reciprocal side
    const points2: string[] = [];
    const reciprocal = (pathBearing + 180) % 360;
    for (let el = 0; el <= 90; el += 2) {
      const p = project((reciprocal + halfWidth) % 360, el);
      points2.push(`${p.x},${p.y}`);
    }
    for (let el = 90; el >= 0; el -= 2) {
      const p = project((reciprocal - halfWidth + 360) % 360, el);
      points2.push(`${p.x},${p.y}`);
    }
    elements.push(
      <polygon
        key="corridor-far"
        points={points2.join(" ")}
        fill="rgba(56,139,253,0.05)"
        stroke="rgba(56,139,253,0.1)"
        strokeWidth={0.5}
      />,
    );
    // Corridor center line
    const lineStart = project(pathBearing, 0);
    const lineEnd = project((pathBearing + 180) % 360, 0);
    elements.push(
      <line
        key="corridor-line"
        x1={lineStart.x} y1={lineStart.y}
        x2={lineEnd.x} y2={lineEnd.y}
        stroke="rgba(56,139,253,0.25)"
        strokeWidth={1}
        strokeDasharray="6 4"
      />,
    );
    return elements;
  }, [pathBearing, outerR, cx, cy]);

  // Sweet spot highlight (1-20° elevation along path bearing)
  const sweetSpot = useMemo(() => {
    if (pathBearing === null) return null;
    const spots: ReactNode[] = [];
    for (const bearing of [pathBearing, (pathBearing + 180) % 360]) {
      const rOuter = outerR * (1 - 1 / 90);
      const r2 = outerR * (1 - 20 / 90);
      const halfArc = 12;
      const points: string[] = [];
      for (let a = bearing - halfArc; a <= bearing + halfArc; a += 1) {
        const rad = ((a - 90) * Math.PI) / 180;
        points.push(`${cx + rOuter * Math.cos(rad)},${cy + rOuter * Math.sin(rad)}`);
      }
      for (let a = bearing + halfArc; a >= bearing - halfArc; a -= 1) {
        const rad = ((a - 90) * Math.PI) / 180;
        points.push(`${cx + r2 * Math.cos(rad)},${cy + r2 * Math.sin(rad)}`);
      }
      spots.push(
        <polygon
          key={`sweet-${bearing}`}
          points={points.join(" ")}
          fill="rgba(63,185,80,0.12)"
          stroke="rgba(63,185,80,0.3)"
          strokeWidth={0.5}
        />,
      );
    }
    return spots;
  }, [pathBearing, outerR, cx, cy]);

  function handleClick(e: React.MouseEvent<SVGSVGElement>) {
    if (!onClickSky) return;
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const dx = e.clientX - rect.left - cx;
    const dy = e.clientY - rect.top - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > outerR) return;
    const az = ((Math.atan2(dy, dx) * 180) / Math.PI + 90 + 360) % 360;
    const el = 90 * (1 - dist / outerR);
    onClickSky(Math.round(az), Math.round(el));
  }

  return (
    <div className="skydome-container">
      <svg
        width={size + 30} height={size + 30}
        viewBox={`${-15} ${-15} ${size + 30} ${size + 30}`}
        style={{ cursor: onClickSky ? "crosshair" : "default", userSelect: "none" }}
        onClick={handleClick}
      >
        {/* Background */}
        <circle cx={cx} cy={cy} r={outerR} fill="#0a0e14" stroke="#30363d" strokeWidth={1.5} />

        {/* Elevation rings */}
        {elevationRings}

        {/* Ticks */}
        {ticks}

        {/* Cardinal labels */}
        {cardinalLabels}

        {/* Scatter corridor */}
        {corridorPath}

        {/* Sweet spot zones */}
        {sweetSpot}

        {/* Aircraft trails (predicted path) */}
        {showTrails && aircraft.map((ac) => {
          const pos = project(ac.az_a, ac.el_a);
          const future = predictTrail(ac, 3);
          if (!future) return null;
          return (
            <line
              key={`trail-${ac.icao24}`}
              x1={pos.x} y1={pos.y}
              x2={future.x} y2={future.y}
              stroke={scoreColor(ac.scatter_score)}
              strokeWidth={1}
              strokeDasharray="3 3"
              opacity={0.4}
            />
          );
        })}

        {/* Aircraft dots */}
        {aircraft.map((ac) => {
          const pos = project(ac.az_a, ac.el_a);
          const color = scoreColor(ac.scatter_score);
          const glow = scoreGlow(ac.scatter_score);
          const r = ac.locked ? 8 : ac.scatter_score > 0.4 ? 6 : 4;
          return (
            <g
              key={`ac-${ac.icao24}`}
              style={{ cursor: onClickAircraft ? "pointer" : "default" }}
              onClick={(e) => {
                e.stopPropagation();
                onClickAircraft?.(ac.icao24);
              }}
            >
              {/* Glow ring for high-score aircraft */}
              {glow !== "none" && (
                <circle cx={pos.x} cy={pos.y} r={r + 4} fill={glow} />
              )}
              {/* Locked ring */}
              {ac.locked && (
                <circle cx={pos.x} cy={pos.y} r={r + 3}
                  fill="none" stroke="#f0883e" strokeWidth={2} />
              )}
              {/* Aircraft dot */}
              <circle cx={pos.x} cy={pos.y} r={r} fill={color} />
              {/* Heading indicator */}
              {ac.heading_deg !== null && (
                <line
                  x1={pos.x} y1={pos.y}
                  x2={pos.x + (r + 4) * Math.cos(((ac.heading_deg - 90) * Math.PI) / 180)}
                  y2={pos.y + (r + 4) * Math.sin(((ac.heading_deg - 90) * Math.PI) / 180)}
                  stroke={color} strokeWidth={1.5} opacity={0.7}
                />
              )}
              {/* Label */}
              <text
                x={pos.x + r + 4} y={pos.y - r - 2}
                fontSize={ac.locked ? 10 : 8}
                fontWeight={ac.locked ? 700 : 400}
                fill={color}
              >
                {ac.callsign || ac.icao24}
              </text>
              {/* Score badge */}
              {ac.scatter_score >= 0.15 && (
                <text
                  x={pos.x + r + 4} y={pos.y + r + 6}
                  fontSize={7} fill="#6e7681"
                >
                  {(ac.scatter_score * 100).toFixed(0)}%
                </text>
              )}
            </g>
          );
        })}

        {/* Rotator current position (red crosshair) */}
        {(() => {
          const rp = project(rotatorAz, rotatorEl);
          return (
            <g>
              <line x1={rp.x - 8} y1={rp.y} x2={rp.x + 8} y2={rp.y}
                stroke="#f85149" strokeWidth={1.5} />
              <line x1={rp.x} y1={rp.y - 8} x2={rp.x} y2={rp.y + 8}
                stroke="#f85149" strokeWidth={1.5} />
              <circle cx={rp.x} cy={rp.y} r={3} fill="#f85149" />
            </g>
          );
        })()}

        {/* Rotator target position (orange crosshair, dashed) */}
        {targetAz !== null && targetEl !== null && (() => {
          const tp = project(targetAz, targetEl);
          return (
            <g>
              <line x1={tp.x - 6} y1={tp.y} x2={tp.x + 6} y2={tp.y}
                stroke="#d29922" strokeWidth={1.5} strokeDasharray="2 2" />
              <line x1={tp.x} y1={tp.y - 6} x2={tp.x} y2={tp.y + 6}
                stroke="#d29922" strokeWidth={1.5} strokeDasharray="2 2" />
            </g>
          );
        })()}

        {/* Zenith marker */}
        <circle cx={cx} cy={cy} r={2} fill="#30363d" />
      </svg>

      {/* Legend */}
      <div className="skydome-legend">
        <span style={{ color: "#3fb950" }}>● &gt;70%</span>
        <span style={{ color: "#d29922" }}>● 40-70%</span>
        <span style={{ color: "#db6d28" }}>● 15-40%</span>
        <span style={{ color: "#484f58" }}>● &lt;15%</span>
        <span style={{ color: "#f85149" }}>✛ rotator</span>
      </div>
    </div>
  );
}
