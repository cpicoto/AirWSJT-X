import { bearingDeg, destinationLatlon, elevationDeg, haversineKm, headingDeltaDeg } from "./geo";
import type { SkyCourseAircraft, SkyCourseStation } from "./skyCourse";

export interface UsableTime {
  seconds: number | null;
  capped: boolean;
  reason: string;
}

const LIMIT_SECONDS = 1800;
const STEP_SECONDS = 5;

export function predictUsableTime(
  aircraft: SkyCourseAircraft,
  stationA: SkyCourseStation | null,
  stationB: SkyCourseStation | null,
): UsableTime {
  const validStation = (station: SkyCourseStation | null): station is SkyCourseStation =>
    station !== null && Number.isFinite(station.lat) && Math.abs(station.lat) <= 90
    && Number.isFinite(station.lon) && Math.abs(station.lon) <= 180
    && Number.isFinite(station.altitudeM);
  if (!validStation(stationA) || !validStation(stationB)) {
    return { seconds: null, capped: false, reason: "Both station grids and applied altitudes are required" };
  }
  if (!Number.isFinite(aircraft.lat) || Math.abs(aircraft.lat) > 90
    || !Number.isFinite(aircraft.lon) || Math.abs(aircraft.lon) > 180
    || !Number.isFinite(aircraft.altitude_ft) || aircraft.altitude_ft <= 0) {
    return { seconds: null, capped: false, reason: "Aircraft position or altitude is unavailable" };
  }
  const altitudeM = aircraft.altitude_ft * 0.3048;
  const bearingAB = bearingDeg(stationA.lat, stationA.lon, stationB.lat, stationB.lon);
  const bearingBA = bearingDeg(stationB.lat, stationB.lon, stationA.lat, stationA.lon);
  const distinctPath = haversineKm(stationA.lat, stationA.lon, stationB.lat, stationB.lon) > 0;
  const usable = (lat: number, lon: number) =>
    elevationDeg(haversineKm(stationA.lat, stationA.lon, lat, lon), altitudeM, stationA.altitudeM) > 0
    && elevationDeg(haversineKm(stationB.lat, stationB.lon, lat, lon), altitudeM, stationB.altitudeM) > 0
    && (!distinctPath || (
      headingDeltaDeg(bearingDeg(stationA.lat, stationA.lon, lat, lon), bearingAB) < 90
      && headingDeltaDeg(bearingDeg(stationB.lat, stationB.lon, lat, lon), bearingBA) < 90
    ));
  if (!usable(aircraft.lat, aircraft.lon)) {
    return { seconds: 0, capped: false, reason: "Currently outside the mutual geometry window; future entry is not predicted" };
  }
  if (aircraft.heading_deg == null || !Number.isFinite(aircraft.heading_deg)
    || aircraft.velocity_kts == null || !Number.isFinite(aircraft.velocity_kts) || aircraft.velocity_kts <= 0) {
    return { seconds: null, capped: false, reason: "Aircraft track or positive ground speed is unavailable" };
  }
  const heading = aircraft.heading_deg;
  const speed = aircraft.velocity_kts * 1.852 / 3600;
  const usableAt = (seconds: number) =>
    usable(...destinationLatlon(aircraft.lat, aircraft.lon, heading, speed * seconds));
  for (let seconds = STEP_SECONDS; seconds <= LIMIT_SECONDS; seconds += STEP_SECONDS) {
    if (!usableAt(seconds)) {
      let low = seconds - STEP_SECONDS;
      let high = seconds;
      while (high - low > 0.1) {
        const middle = (low + high) / 2;
        if (usableAt(middle)) low = middle;
        else high = middle;
      }
      return { seconds: (low + high) / 2, capped: false, reason: "Estimated time to first exit from the mutual geometry window" };
    }
  }
  return { seconds: LIMIT_SECONDS, capped: true, reason: "Still within the mutual geometry window at the 30-minute prediction limit" };
}

export function formatUsableTime(prediction: UsableTime): string {
  if (prediction.seconds === null) return "\u2014";
  if (prediction.capped) return "\u226530m";
  const seconds = Math.floor(prediction.seconds);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
