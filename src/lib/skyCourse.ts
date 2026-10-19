import { bearingDeg, destinationLatlon, elevationDeg, haversineKm } from "./geo";

export interface SkyCourseAircraft {
  lat: number;
  lon: number;
  altitude_ft: number;
  heading_deg: number | null;
  velocity_kts: number | null;
}

export interface SkyCourseStation {
  lat: number;
  lon: number;
  altitudeM: number;
}

export interface SkyCoursePoint {
  seconds: number;
  az: number;
  el: number;
}

interface SkyCourse {
  points: SkyCoursePoint[];
  unavailableReason: string | null;
}

const COURSE_LIMIT_SECONDS = 30 * 60;
const SAMPLE_SECONDS = 5;

export function predictSkyCourse(
  aircraft: SkyCourseAircraft,
  station: SkyCourseStation | null,
): SkyCourse {
  if (!station || !Number.isFinite(station.lat) || Math.abs(station.lat) > 90
    || !Number.isFinite(station.lon) || Math.abs(station.lon) > 180
    || !Number.isFinite(station.altitudeM)) {
    return { points: [], unavailableReason: "Set a valid MY station grid and altitude" };
  }
  if (!Number.isFinite(aircraft.lat) || Math.abs(aircraft.lat) > 90
    || !Number.isFinite(aircraft.lon) || Math.abs(aircraft.lon) > 180
    || !Number.isFinite(aircraft.altitude_ft)) {
    return { points: [], unavailableReason: "Aircraft position or altitude is unavailable" };
  }
  if (aircraft.heading_deg === null || !Number.isFinite(aircraft.heading_deg)
    || aircraft.velocity_kts === null || !Number.isFinite(aircraft.velocity_kts)
    || aircraft.velocity_kts <= 0) {
    return { points: [], unavailableReason: "Aircraft track or ground speed is unavailable" };
  }

  const altitudeM = aircraft.altitude_ft * 0.3048;
  if (altitudeM <= station.altitudeM) {
    return { points: [], unavailableReason: "Aircraft is not above the station horizon" };
  }
  const speedKmPerSecond = aircraft.velocity_kts * 1.852 / 3600;
  const points: SkyCoursePoint[] = [];
  let previous: SkyCoursePoint | null = null;
  for (let seconds = -COURSE_LIMIT_SECONDS; seconds <= COURSE_LIMIT_SECONDS; seconds += SAMPLE_SECONDS) {
    const [lat, lon] = destinationLatlon(
      aircraft.lat, aircraft.lon, aircraft.heading_deg, speedKmPerSecond * seconds,
    );
    const point = {
      seconds,
      az: bearingDeg(station.lat, station.lon, lat, lon),
      el: elevationDeg(haversineKm(station.lat, station.lon, lat, lon), altitudeM, station.altitudeM),
    };
    if (previous && (previous.el < 0) !== (point.el < 0)) {
      // End the visible course at the horizon, not along the clamped outer ring.
      const fraction = previous.el / (previous.el - point.el);
      const azDelta = ((point.az - previous.az + 540) % 360) - 180;
      points.push({
        seconds: previous.seconds + fraction * SAMPLE_SECONDS,
        az: (previous.az + fraction * azDelta + 360) % 360,
        el: 0,
      });
    }
    if (point.el >= 0) points.push(point);
    previous = point;
  }
  return {
    points,
    unavailableReason: points.length > 1 ? null : "No estimated course above the horizon within 30 minutes",
  };
}
