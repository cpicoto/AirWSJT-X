import { useId, useMemo, type ReactNode } from "react";
import { isValidLocator, locatorToLatlon } from "./lib/geo";
import { predictSkyCourse, type SkyCourseAircraft } from "./lib/skyCourse";

export interface SkyDomeAircraft extends SkyCourseAircraft {
  icao24: string;
  callsign: string;
  az_a: number;
  el_a: number;
  scatter_score: number;
  range_a_km: number;
  off_path_deg: number;
}

interface SkyDomeProps {
  aircraft: SkyDomeAircraft[];
  selectedAircraftId: string | null;
  stationLocator: string | null;
  stationAltitudeM: number;
  rotatorAz: number;
  rotatorEl: number;
  targetAz: number | null;
  targetEl: number | null;
  pathBearing: number | null;
  dxLabel?: string | null;
  myLabel?: string | null;
  size?: number;
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
  selectedAircraftId,
  stationLocator,
  stationAltitudeM,
  rotatorAz,
  rotatorEl,
  targetAz,
  targetEl,
  pathBearing,
  dxLabel = null,
  myLabel = null,
  size = 400,
  onClickAircraft,
  onClickSky,
}: SkyDomeProps) {
  const cx = size / 2;
  const cy = size / 2;
  const outerR = size / 2 - 8;
  const courseId = useId();
  const selectedAircraft = aircraft.find(ac => ac.icao24 === selectedAircraftId);
  const course = useMemo(() => {
    if (!selectedAircraft) return null;
    const coordinates = isValidLocator(stationLocator) ? locatorToLatlon(stationLocator) : null;
    return predictSkyCourse(selectedAircraft, coordinates ? {
      lat: coordinates[0], lon: coordinates[1], altitudeM: stationAltitudeM,
    } : null);
  }, [selectedAircraft, stationLocator, stationAltitudeM]);

  // Polar projection: elevation 0° = edge, 90° = center
  function project(az: number, el: number): { x: number; y: number } {
    const r = outerR * (1 - Math.max(0, Math.min(90, el)) / 90);
    const rad = ((az - 90) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  }

  const elevationRings = useMemo(() => {
    const rings: ReactNode[] = [];
    for (const el of [0, 22.5, 45, 67.5]) {
      const r = outerR * (1 - el / 90);
      rings.push(
        <circle
          key={`ring-${el}`}
          cx={cx} cy={cy} r={r}
          fill="none"
          stroke={el === 0 || el === 45 ? "#6e7681" : "#484f58"}
          strokeWidth={el === 0 || el === 45 ? 1.5 : 1}
          strokeDasharray={el === 22.5 || el === 67.5 ? "4 5" : "none"}
        />,
      );
      if (el > 0) {
        rings.push(
          <text
            key={`elbl-${el}`}
            x={cx + 6} y={cy - r + 13}
            fontSize={11} fill="#c9d1d9"
            stroke="#0a0e14" strokeWidth={3} paintOrder="stroke"
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
    const transform = e.currentTarget.getScreenCTM();
    if (!transform) return;
    const point = new DOMPoint(e.clientX, e.clientY).matrixTransform(transform.inverse());
    const dx = point.x - cx;
    const dy = point.y - cy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist > outerR) return;
    const az = ((Math.atan2(dy, dx) * 180) / Math.PI + 90 + 360) % 360;
    const el = 90 * (1 - dist / outerR);
    onClickSky(Math.round(az), Math.round(el));
  }

  return (
    <div className="skydome-container">
      <svg
        aria-label="Sky Dome polar projection"
        width={size + 140} height={size + 30}
        viewBox={`${-70} ${-15} ${size + 140} ${size + 30}`}
        style={{ cursor: onClickSky ? "crosshair" : "default", userSelect: "none" }}
        onClick={handleClick}
      >
        <defs>
          <clipPath id={`${courseId}-clip`}>
            <circle cx={cx} cy={cy} r={outerR} />
          </clipPath>
          <marker id={`${courseId}-arrow`} viewBox="0 0 8 8"
            refX={7} refY={4} markerWidth={7} markerHeight={7} orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 Z" fill="var(--accent)" />
          </marker>
        </defs>
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

        {/* DX station direction marker on outer ring, label just outside */}
        {pathBearing !== null && (() => {
          const dot = project(pathBearing, 0);
          const rad = ((pathBearing - 90) * Math.PI) / 180;
          const sin = Math.sin((pathBearing * Math.PI) / 180);
          const cos = Math.cos((pathBearing * Math.PI) / 180);
          const labelR = outerR + 10;
          const lx = cx + labelR * Math.cos(rad);
          const ly = cy + labelR * Math.sin(rad);
          const side = Math.abs(sin) > 0.3;
          const anchor = side ? (sin > 0 ? "start" : "end") : "middle";
          const dy = anchor === "middle" ? (cos > 0 ? -6 : 8) : 3;
          return (
            <g pointerEvents="none" data-dx-marker>
              <circle cx={dot.x} cy={dot.y} r={5} fill="#3fb950" stroke="#0d1117" strokeWidth={1} />
              {dxLabel && (
                <text x={lx} y={ly + dy} textAnchor={anchor} fontSize={11} fontWeight={700} fill="#3fb950"
                  stroke="#0a0e14" strokeWidth={3} paintOrder="stroke">
                  {dxLabel}
                </text>
              )}
            </g>
          );
        })()}

        {/* MY station label on outer ring, on the reciprocal of the DX bearing */}
        {pathBearing !== null && myLabel && (() => {
          const az = (pathBearing + 180) % 360;
          const dot = project(az, 0);
          const rad = ((az - 90) * Math.PI) / 180;
          const sin = Math.sin((az * Math.PI) / 180);
          const cos = Math.cos((az * Math.PI) / 180);
          const labelR = outerR + 10;
          const anchor = Math.abs(sin) > 0.3 ? (sin > 0 ? "start" : "end") : "middle";
          const dy = anchor === "middle" ? (cos > 0 ? -6 : 8) : 3;
          return (
            <g pointerEvents="none" data-my-marker>
              <circle cx={dot.x} cy={dot.y} r={5} fill="#58a6ff" stroke="#0d1117" strokeWidth={1} />
              <text x={cx + labelR * Math.cos(rad)} y={cy + labelR * Math.sin(rad) + dy} textAnchor={anchor}
                fontSize={11} fontWeight={700} fill="#58a6ff" stroke="#0a0e14" strokeWidth={3} paintOrder="stroke">
                {myLabel}
              </text>
            </g>
          );
        })()}

        {selectedAircraft && course && !course.unavailableReason && (() => {
          const projected = course.points.map(point => ({ ...point, ...project(point.az, point.el) }));
          const past = projected.filter(point => point.seconds <= 0);
          const future = projected.filter(point => point.seconds >= 0);
          const markers = future.filter(point =>
            point.seconds >= 60 && point.seconds <= 300 && point.seconds % 60 === 0);
          const arrowEnd = future.findIndex(point => point.seconds >= 60);
          const arrowIndex = arrowEnd >= 1 ? arrowEnd : future.length - 1;
          return (
            <g role="img" aria-label={`Estimated course for ${selectedAircraft.callsign || selectedAircraft.icao24}`}
              data-aircraft-course={selectedAircraft.icao24}
              clipPath={`url(#${courseId}-clip)`} pointerEvents="none">
              <title>Estimated visible course up to 30 minutes either side of the latest report;
                constant ground track, speed and altitude, not recorded history or a flight plan.</title>
              <polyline aria-label="Estimated approach course"
                points={past.map(point => `${point.x},${point.y}`).join(" ")}
                fill="none" stroke="var(--accent)" strokeWidth={1.5} opacity={0.45} />
              <polyline aria-label="Predicted future course"
                points={future.map(point => `${point.x},${point.y}`).join(" ")}
                fill="none" stroke="var(--accent)" strokeWidth={2} strokeDasharray="5 4" />
              {arrowIndex >= 1 && (
                <line x1={future[arrowIndex - 1].x} y1={future[arrowIndex - 1].y}
                  x2={future[arrowIndex].x} y2={future[arrowIndex].y}
                  stroke="var(--accent)" strokeWidth={2} markerEnd={`url(#${courseId}-arrow)`} />
              )}
              {markers.map(point => (
                <g key={point.seconds} aria-label={`Predicted position in ${point.seconds / 60} minutes`}>
                  <circle cx={point.x} cy={point.y} r={2.5}
                    fill="var(--accent)" stroke="var(--bg)" strokeWidth={1} />
                  <text x={point.x + (point.x > cx ? -5 : 5)}
                    y={point.y + (point.seconds / 60 % 2 === 0 ? -7 : 14)}
                    textAnchor={point.x > cx ? "end" : "start"}
                    fontSize={9} fill="var(--accent)"
                    stroke="var(--bg)" strokeWidth={3} paintOrder="stroke">
                    +{point.seconds / 60}m
                  </text>
                </g>
              ))}
            </g>
          );
        })()}

        {/* Aircraft dots */}
        {aircraft.map((ac) => {
          const pos = project(ac.az_a, ac.el_a);
          const color = scoreColor(ac.scatter_score);
          const glow = scoreGlow(ac.scatter_score);
          const locked = ac.icao24 === selectedAircraftId;
          const r = locked ? 8 : ac.scatter_score > 0.4 ? 6 : 4;
          return (
            <g
              key={`ac-${ac.icao24}`}
              data-aircraft-id={ac.icao24}
              style={{ cursor: onClickAircraft ? "pointer" : "default" }}
              onClick={(e) => {
                e.stopPropagation();
                onClickAircraft?.(ac.icao24);
              }}
              onKeyDown={(e) => {
                if (onClickAircraft && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  e.stopPropagation();
                  onClickAircraft(ac.icao24);
                }
              }}
            >
              {/* Glow ring for high-score aircraft */}
              {glow !== "none" && (
                <circle cx={pos.x} cy={pos.y} r={r + 4} fill={glow} />
              )}
              {/* Locked ring */}
              {locked && (
                <circle cx={pos.x} cy={pos.y} r={r + 3}
                  fill="none" stroke="#f0883e" strokeWidth={2} />
              )}
              {/* Aircraft dot */}
              <circle cx={pos.x} cy={pos.y} r={r} fill={color} />
              {/* Heading indicator */}
              {ac.heading_deg !== null && !(locked && course && !course.unavailableReason) && (
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
                fontSize={locked ? 10 : 8}
                fontWeight={locked ? 700 : 400}
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
              {onClickAircraft && (
                <circle cx={pos.x} cy={pos.y} r={r + 4} fill="transparent"
                  role="button" aria-label={`Lock ${ac.callsign || ac.icao24}`} tabIndex={0} />
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
      <div className="skydome-course-note" aria-live="polite">
        {!selectedAircraftId ? "Select an aircraft to show its estimated course"
          : !selectedAircraft ? "Course unavailable: selected aircraft is no longer in the current feed"
          : course?.unavailableReason ? `Course unavailable: ${course.unavailableReason}`
          : "Estimated course: constant track, speed & altitude; markers +1 to +5 min"}
      </div>
    </div>
  );
}
