/** Shared visual encoding for aircraft scatter quality across desktop and mobile. */
export function aircraftScoreColor(score: number): string {
  if (score >= 0.7) return "#3fb950";
  if (score >= 0.4) return "#d29922";
  if (score >= 0.15) return "#db6d28";
  return "#484f58";
}

export function aircraftScoreGlow(score: number): string {
  if (score >= 0.7) return "rgba(63,185,80,0.4)";
  if (score >= 0.4) return "rgba(210,153,34,0.3)";
  return "none";
}
