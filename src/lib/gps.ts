export interface GpsPosition {
  lat: number;
  lon: number;
  altitudeM: number | null;
}

/** Watches the device GPS position used by the mobile station endpoint. */
export function watchGpsPosition(
  onPosition: (position: GpsPosition) => void,
  onError?: (error: GeolocationPositionError) => void,
): () => void {
  if (typeof navigator === "undefined" || !navigator.geolocation) return () => {};
  const id = navigator.geolocation.watchPosition(
    (pos) => {
      const alt = pos.coords.altitude;
      onPosition({
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        altitudeM: typeof alt === "number" && Number.isFinite(alt) ? alt : null,
      });
    },
    error => onError?.(error),
    { enableHighAccuracy: true, maximumAge: 60000, timeout: 30000 },
  );
  return () => navigator.geolocation.clearWatch(id);
}

/** Watches device GPS and reports altitude above sea level (metres), or null when unavailable. */
export function watchGpsAltitude(onAltitude: (altitudeM: number | null) => void): () => void {
  return watchGpsPosition(
    position => onAltitude(position.altitudeM),
    () => onAltitude(null),
  );
}
