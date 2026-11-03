import { aircraftBadges, aircraftEmergency, aircraftIconClass, type AircraftMetadata } from "./lib/aircraftMetadata";

// Original silhouettes, pointing north; no third-party artwork or airline branding.
const SHAPES = {
  generic: "M0 -10 L2 -2 L8 2 L8 4 L2 2 L2 7 L5 9 L5 10 L0 8 L-5 10 L-5 9 L-2 7 L-2 2 L-8 4 L-8 2 L-2 -2 Z",
  light: "M0 -9 L1 -2 L9 -1 L9 1 L1 1 L1 7 L4 8 L4 9 L0 8 L-4 9 L-4 8 L-1 7 L-1 1 L-9 1 L-9 -1 L-1 -2 Z",
  "small-jet": "M0 -10 L2 -7 L2 -1 L8 3 L8 5 L2 2 L2 7 L4 9 L0 8 L-4 9 L-2 7 L-2 2 L-8 5 L-8 3 L-2 -1 L-2 -7 Z",
  "medium-jet": "M0 -11 L2 -8 L2 -2 L10 4 L10 6 L2 3 L2 8 L5 10 L0 9 L-5 10 L-2 8 L-2 3 L-10 6 L-10 4 L-2 -2 L-2 -8 Z",
  heavy: "M0 -12 L3 -9 L3 -3 L12 4 L12 7 L3 4 L3 9 L7 11 L0 10 L-7 11 L-3 9 L-3 4 L-12 7 L-12 4 L-3 -3 L-3 -9 Z M5 0 L7 1 L7 5 L5 4 Z M-5 0 L-7 1 L-7 5 L-5 4 Z",
  "fast-jet": "M0 -12 L2 -3 L9 7 L9 9 L3 7 L2 11 L0 9 L-2 11 L-3 7 L-9 9 L-9 7 L-2 -3 Z",
  helicopter: "M-2 -5 Q0 -9 2 -5 L3 2 L1 5 L1 10 L4 10 L4 11 L-4 11 L-4 10 L-1 10 L-1 5 L-3 2 Z M-10 -2 L10 -2 L10 0 L-10 0 Z",
  glider: "M0 -10 L1 -1 L12 1 L12 2 L1 1 L1 8 L5 9 L5 10 L0 9 L-5 10 L-5 9 L-1 8 L-1 1 L-12 2 L-12 1 L-1 -1 Z",
  balloon: "M0 -11 C12 -11 12 0 4 6 L2 8 L-2 8 L-4 6 C-12 0 -12 -11 0 -11 Z M-2 9 L2 9 L2 12 L-2 12 Z",
  drone: "M-2 -2 L2 -2 L2 2 L-2 2 Z M-8 -10 L-4 -10 L-4 -6 L-8 -6 Z M4 -10 L8 -10 L8 -6 L4 -6 Z M-8 6 L-4 6 L-4 10 L-8 10 Z M4 6 L8 6 L8 10 L4 10 Z M-6 -8 L6 8 L5 9 L-7 -7 Z M6 -8 L-6 8 L-5 9 L7 -7 Z",
  ground: "M-8 -4 L8 -4 L8 5 L-8 5 Z M-6 6 L-3 6 L-3 8 L-6 8 Z M3 6 L6 6 L6 8 L3 8 Z",
};

export default function AircraftIcon({ aircraft, size = 24, color = "currentColor" }: {
  aircraft: AircraftMetadata & { heading_deg?: number | null }; size?: number; color?: string;
}) {
  const shape = aircraftIconClass(aircraft);
  const heading = aircraft.heading_deg;
  const angle = shape === "balloon" || shape === "ground" || heading == null || !Number.isFinite(heading) ? 0 : heading;
  const rate = aircraft.vertical_rate_ms;
  return (
    <svg width={size} height={size} viewBox="-16 -16 32 32" aria-hidden="true" data-aircraft-icon={shape}
      style={{ overflow: "visible", flexShrink: 0, verticalAlign: "middle" }}>
      <path d={SHAPES[shape]} transform={`rotate(${angle})`} fill={color}
        stroke={aircraftEmergency(aircraft) ? "var(--red)" : color} strokeWidth={aircraftEmergency(aircraft) ? 2 : 0.5} />
      {rate != null && Number.isFinite(rate) && Math.abs(rate) > 2 && (
        <path data-vertical-direction={rate > 0 ? "climb" : "descent"}
          d={rate > 0 ? "M10 -7 L13 -11 L16 -7" : "M10 -11 L13 -7 L16 -11"}
          fill="none" stroke={color} strokeWidth={2} />
      )}
      {aircraftBadges(aircraft).length > 0 && <circle cx={12} cy={11} r={2} fill="var(--accent)" />}
    </svg>
  );
}
