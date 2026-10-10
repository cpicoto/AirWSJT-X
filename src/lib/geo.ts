const R_KM = 6371.0;
const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

export function locatorToLatlon(locator: string): [number, number] {
  const loc = locator.trim().toUpperCase();
  if (loc.length < 4) throw new Error(`Locator too short: ${locator}`);
  let lon = (loc.charCodeAt(0) - 65) * 20 - 180;
  let lat = (loc.charCodeAt(1) - 65) * 10 - 90;
  lon += parseInt(loc[2]) * 2;
  lat += parseInt(loc[3]) * 1;
  if (loc.length >= 6) {
    lon += (loc.charCodeAt(4) - 65) * (2.0 / 24);
    lat += (loc.charCodeAt(5) - 65) * (1.0 / 24);
    lon += 1.0 / 24;
    lat += 0.5 / 24;
  } else {
    lon += 1.0;
    lat += 0.5;
  }
  return [lat, lon];
}

export function latlonToGrid(lat: number, lon: number): string {
  const lon2 = lon + 180;
  const lat2 = lat + 90;
  const fLon = String.fromCharCode(65 + Math.floor(lon2 / 20));
  const fLat = String.fromCharCode(65 + Math.floor(lat2 / 10));
  const sqLon = Math.floor((lon2 % 20) / 2);
  const sqLat = Math.floor(lat2 % 10);
  const subLon = String.fromCharCode(97 + Math.floor((lon2 % 2) / (2 / 24)));
  const subLat = String.fromCharCode(97 + Math.floor((lat2 % 1) / (1 / 24)));
  return `${fLon}${fLat}${sqLon}${sqLat}${subLon}${subLat}`;
}

export function haversineKm(
  lat1: number, lon1: number,
  lat2: number, lon2: number,
): number {
  const dlat = (lat2 - lat1) * DEG2RAD;
  const dlon = (lon2 - lon1) * DEG2RAD;
  const a =
    Math.sin(dlat / 2) ** 2 +
    Math.cos(lat1 * DEG2RAD) * Math.cos(lat2 * DEG2RAD) *
    Math.sin(dlon / 2) ** 2;
  return 2 * R_KM * Math.asin(Math.sqrt(a));
}

export function bearingDeg(
  lat1: number, lon1: number,
  lat2: number, lon2: number,
): number {
  const lat1r = lat1 * DEG2RAD;
  const lat2r = lat2 * DEG2RAD;
  const dlon = (lon2 - lon1) * DEG2RAD;
  const x = Math.sin(dlon) * Math.cos(lat2r);
  const y =
    Math.cos(lat1r) * Math.sin(lat2r) -
    Math.sin(lat1r) * Math.cos(lat2r) * Math.cos(dlon);
  return (Math.atan2(x, y) * RAD2DEG + 360) % 360;
}

export function headingDeltaDeg(source: number, target: number): number {
  return Math.abs(((source - target + 180) % 360 + 360) % 360 - 180);
}

export function headingWithinTolerance(
  pathBearing: number,
  aircraftHeading: number,
  tolerance: number = 30,
): boolean {
  return headingDeltaDeg(aircraftHeading, pathBearing) <= Math.abs(tolerance);
}

export function elevationDeg(surfaceKm: number, altitudeM: number): number {
  if (surfaceKm <= 0) return 90.0;
  const altKm = altitudeM / 1000.0;
  return Math.atan2(altKm, surfaceKm) * RAD2DEG;
}

export function slantRangeKm(surfaceKm: number, altitudeM: number): number {
  const altKm = altitudeM / 1000.0;
  return Math.sqrt(surfaceKm ** 2 + altKm ** 2);
}

export interface ScatterResult {
  az_a: number;
  el_a: number;
  range_a_km: number;
  az_b: number;
  el_b: number;
  range_b_km: number;
  dist_ab_km: number;
  off_path_deg: number;
  scatter_score: number;
}

export function scatterGeometry(
  latA: number, lonA: number,
  latB: number, lonB: number,
  latP: number, lonP: number,
  altitudeM: number,
): ScatterResult {
  const dA = haversineKm(latA, lonA, latP, lonP);
  const dB = haversineKm(latB, lonB, latP, lonP);
  const dAB = haversineKm(latA, lonA, latB, lonB);
  const azA = bearingDeg(latA, lonA, latP, lonP);
  const azB = bearingDeg(latB, lonB, latP, lonP);
  const azAB = bearingDeg(latA, lonA, latB, lonB);
  const elA = elevationDeg(dA, altitudeM);
  const elB = elevationDeg(dB, altitudeM);
  const srA = slantRangeKm(dA, altitudeM);
  const srB = slantRangeKm(dB, altitudeM);
  const offPath = Math.abs(((azA - azAB + 180) % 360 + 360) % 360 - 180);

  let score: number;
  if (elA <= 0 || elB <= 0) {
    score = 0;
  } else {
    const elScore = (el: number) => {
      if (el <= 0) return 0;
      if (el <= 20) return el / 20;
      return Math.max(0, 1 - (el - 20) / 20);
    };
    const pathScore = Math.max(0, 1 - offPath / 45);
    const rangeScore = Math.max(0, 1 - Math.max(srA, srB) / 800);
    score =
      elScore(elA) * 0.3 +
      elScore(elB) * 0.3 +
      pathScore * 0.25 +
      rangeScore * 0.15;
  }

  return {
    az_a: Math.round(azA * 10) / 10,
    el_a: Math.round(elA * 10) / 10,
    range_a_km: Math.round(srA * 10) / 10,
    az_b: Math.round(azB * 10) / 10,
    el_b: Math.round(elB * 10) / 10,
    range_b_km: Math.round(srB * 10) / 10,
    dist_ab_km: Math.round(dAB * 10) / 10,
    off_path_deg: Math.round(offPath * 10) / 10,
    scatter_score: Math.round(score * 1000) / 1000,
  };
}
