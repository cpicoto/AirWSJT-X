import { haversineKm, slantRangeKm } from "./geo";
import { predictUsableTime } from "./usableTime";
import type { SkyCourseAircraft, SkyCourseStation } from "./skyCourse";

export const RANKING_OPTIONS = [
  { value: "legacy", label: "Legacy geometry (default)" },
  { value: "range-product", label: "Range product" },
  { value: "window-product", label: "Window-weighted range product" },
] as const;
export type RankingMethod = typeof RANKING_OPTIONS[number]["value"];
export const isRankingMethod = (value: unknown): value is RankingMethod =>
  RANKING_OPTIONS.some(option => option.value === value);
export interface AircraftRanking {
  ranking_value: number | null;
  ranking_reason: string;
  range_gain_db: number | null;
  ranking_window_s: number | null;
  ranking_window_capped: boolean;
}

export function rankGeometry(
  aircraft: SkyCourseAircraft & { position_updated_epoch_s?: number | null },
  a: SkyCourseStation, b: SkyCourseStation, method: RankingMethod,
  legacyScore: number, nowEpochS: number,
): AircraftRanking {
  const result: AircraftRanking = {
    ranking_value: null, ranking_reason: "Outside geometry gates",
    range_gain_db: null, ranking_window_s: null, ranking_window_capped: false,
  };
  if (method === "legacy") {
    return { ...result, ranking_value: legacyScore, ranking_reason: "Legacy geometry score" };
  }
  if (![aircraft.lat, aircraft.lon, aircraft.altitude_ft, a.lat, a.lon, a.altitudeM,
    b.lat, b.lon, b.altitudeM].every(Number.isFinite) || aircraft.altitude_ft <= 0
    || Math.abs(aircraft.lat) > 90 || Math.abs(aircraft.lon) > 180
    || [a, b].some(station => Math.abs(station.lat) > 90 || Math.abs(station.lon) > 180)) {
    return { ...result, ranking_reason: "Invalid geometry" };
  }
  if (legacyScore <= 0) return result;
  const altitudeM = aircraft.altitude_ft * 0.3048;
  const ra = slantRangeKm(haversineKm(a.lat, a.lon, aircraft.lat, aircraft.lon), altitudeM, a.altitudeM);
  const rb = slantRangeKm(haversineKm(b.lat, b.lon, aircraft.lat, aircraft.lon), altitudeM, b.altitudeM);
  if (ra <= 0 || rb <= 0) return { ...result, ranking_reason: "Nonpositive slant range" };
  const gain = 20 * (2 * Math.log10(250) - (Math.log10(ra) + Math.log10(rb)));
  Object.assign(result, { range_gain_db: gain, ranking_value: gain,
    ranking_reason: "Range-only power relative to 250/250 km; not SNR" });
  if (method === "range-product") return result;
  result.ranking_value = null;
  const timestamp = aircraft.position_updated_epoch_s;
  if (timestamp == null || !Number.isFinite(timestamp)) {
    return { ...result, ranking_reason: "Range fallback: position timestamp unavailable" };
  }
  const age = nowEpochS - timestamp;
  if (!Number.isFinite(age) || age < 0 || age > 45) {
    return { ...result, ranking_reason: "Range fallback: stale or future-dated position" };
  }
  const prediction = predictUsableTime(aircraft, a, b);
  if (prediction.seconds === null) {
    return { ...result, ranking_reason: "Range fallback: track or positive speed unavailable" };
  }
  const seconds = Math.max(0, prediction.seconds - age);
  Object.assign(result, { ranking_window_s: seconds, ranking_window_capped: prediction.capped });
  if (seconds <= 0) return { ...result, ranking_reason: "Range fallback: predicted window ended" };
  return { ...result, ranking_value: gain + 10 * Math.log10(seconds / 60),
    ranking_reason: "Window index: current range potential times remaining geometry minutes; "
      + "constant motion, 30-minute cap; not QSO probability" };
}

export function compareRanking(
  a: AircraftRanking & { scatter_score: number }, b: AircraftRanking & { scatter_score: number },
  method: RankingMethod,
): number {
  if (method === "legacy") return b.scatter_score - a.scatter_score;
  const key = (ac: typeof a) => [
    Number(ac.scatter_score > 0), Number(ac.ranking_value !== null),
    ac.ranking_value ?? ac.range_gain_db ?? -1e300, ac.range_gain_db ?? -1e300,
  ];
  const ka = key(a), kb = key(b);
  for (let i = 0; i < ka.length; i++) {
    if (ka[i] !== kb[i]) return kb[i] - ka[i];
  }
  return 0;
}

export function formatRanking(ranking: AircraftRanking, method: RankingMethod): string {
  if (method === "legacy") return "";
  const value = ranking.ranking_value;
  if (value === null) return ranking.range_gain_db === null ? "\u2014" : "Range fallback";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)} ${method === "range-product" ? "dB" : "index"}`;
}
