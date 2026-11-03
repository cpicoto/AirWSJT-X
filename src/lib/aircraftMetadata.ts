export interface AircraftMetadata {
  category?: string | null;
  type_code?: string | null;
  registration?: string | null;
  description?: string | null;
  squawk?: string | null;
  emergency?: string | null;
  db_flags?: number | null;
  vertical_rate_ms?: number | null;
  on_ground?: boolean;
  position_source?: string | null;
  position_updated_epoch_s?: number | null;
  last_seen_epoch_s?: number | null;
}

export function normalizeCategory(value: unknown): string | null {
  const category = typeof value === "string" ? value.trim().toUpperCase() : "";
  return /^[ABC][1-7]$/.test(category) ? category : null;
}

export function networkMetadata(item: Record<string, unknown>): AircraftMetadata {
  const text = (key: string): string | null => {
    const value = item[key];
    if (value == null) return null;
    if (typeof value !== "string") throw new Error(`Invalid aircraft metadata field: ${key}`);
    return value.trim() || null;
  };
  const flags = item.dbFlags;
  if (flags != null && (typeof flags !== "number" || !Number.isSafeInteger(flags) || flags < 0)) {
    throw new Error("Invalid aircraft metadata field: dbFlags");
  }
  return {
    category: normalizeCategory(item.category), type_code: text("t"), registration: text("r"),
    description: text("desc"), squawk: text("squawk"), emergency: text("emergency"),
    db_flags: typeof flags === "number" ? flags : null,
  };
}

export function scatterEligible(ac: AircraftMetadata): boolean {
  return !ac.on_ground && !normalizeCategory(ac.category)?.startsWith("C");
}

export const SCATTER_CATEGORY_DB: Readonly<Record<string, number>> = {
  A1: 0, A2: 0.2, A3: 0.4, A4: 0.5, A5: 0.6, A6: 0, A7: -3,
};
export const SCATTER_POLICY = {
  lowValueDb: -3, climbThresholdMs: 2, climbPenaltyDb: 0.2, staleAfterS: 45, stalePenaltyDb: 0.6,
} as const;

export function scatterAdjustment(ac: AircraftMetadata, nowEpochS: number): number {
  const category = normalizeCategory(ac.category);
  let adjustment = category ? SCATTER_CATEGORY_DB[category] ?? (category.startsWith("B") ? SCATTER_POLICY.lowValueDb : 0) : 0;
  if (ac.vertical_rate_ms != null && Number.isFinite(ac.vertical_rate_ms)
    && Math.abs(ac.vertical_rate_ms) > SCATTER_POLICY.climbThresholdMs) adjustment -= SCATTER_POLICY.climbPenaltyDb;
  if (ac.position_updated_epoch_s != null && Number.isFinite(ac.position_updated_epoch_s)
    && Number.isFinite(nowEpochS) && nowEpochS - ac.position_updated_epoch_s > SCATTER_POLICY.staleAfterS) {
    adjustment -= SCATTER_POLICY.stalePenaltyDb;
  }
  return adjustment;
}

export type AircraftIconClass = "generic" | "light" | "small-jet" | "medium-jet" | "heavy"
  | "fast-jet" | "helicopter" | "glider" | "balloon" | "drone" | "ground";

export function aircraftIconClass(ac: AircraftMetadata): AircraftIconClass {
  if (ac.on_ground || normalizeCategory(ac.category)?.startsWith("C")) return "ground";
  const category = normalizeCategory(ac.category);
  const classes: Record<string, AircraftIconClass> = {
    A1: "light", A2: "small-jet", A3: "medium-jet", A4: "medium-jet", A5: "heavy",
    A6: "fast-jet", A7: "helicopter", B1: "glider", B2: "balloon", B4: "light", B6: "drone",
  };
  const shape = category ? classes[category] : undefined;
  if ((!shape || shape === "medium-jet") && /^(A33|A34|A35|A38|B74|B76|B77|B78)/.test(ac.type_code?.toUpperCase() ?? "")) {
    return "heavy";
  }
  return shape ?? "generic";
}

export function aircraftEmergency(ac: AircraftMetadata): boolean {
  return ["7500", "7600", "7700"].includes(ac.squawk ?? "")
    || !!(ac.emergency && !["none", "no emergency", "0"].includes(ac.emergency.trim().toLowerCase()));
}

export function aircraftBadges(ac: AircraftMetadata): string[] {
  const flags = ac.db_flags ?? 0;
  return [[1, "MIL"], [2, "INT"], [4, "PIA"], [8, "LADD"]]
    .filter(([bit]) => (flags & Number(bit)) !== 0).map(([, label]) => String(label));
}

export function aircraftSourceLabel(source?: string | null): string {
  if (source === "rtlsdr") return "RTL-SDR";
  if (source === "dump1090") return "dump1090";
  if (source === "adsbexchange") return "ADSBExchange";
  if (source === "opensky") return "OpenSky";
  if (source === "airscout") return "AirScout";
  return source || "";
}

export function aircraftOpacity(ac: AircraftMetadata, nowEpochS: number): number {
  const timestamps = [ac.position_updated_epoch_s, ac.last_seen_epoch_s]
    .filter((timestamp): timestamp is number => timestamp != null && Number.isFinite(timestamp));
  const timestamp = timestamps.length ? Math.min(...timestamps) : null;
  if (timestamp == null || !Number.isFinite(timestamp) || !Number.isFinite(nowEpochS) || nowEpochS < timestamp) return 1;
  return Math.max(0.35, 1 - Math.max(0, nowEpochS - timestamp - 15) / 150);
}

export function aircraftTooltip(ac: AircraftMetadata): string {
  return [
    ac.category ? `Category ${ac.category}` : "Category unknown",
    ac.type_code, ac.registration, ac.description,
    ac.on_ground ? "On ground" : ac.vertical_rate_ms != null && Number.isFinite(ac.vertical_rate_ms)
      ? `Vertical rate ${ac.vertical_rate_ms.toFixed(1)} m/s` : null,
    ac.squawk ? `Squawk ${ac.squawk}` : null,
    aircraftEmergency(ac) ? `Emergency: ${ac.emergency || ac.squawk}` : null,
    ...aircraftBadges(ac), aircraftSourceLabel(ac.position_source),
  ].filter(Boolean).join(" · ");
}
