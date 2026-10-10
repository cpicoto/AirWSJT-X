export interface StationInfo {
  callsign: string;
  name: string | null;
  grid: string | null;
  lat: number | null;
  lon: number | null;
  country: string | null;
  source: string;
}

const DB_URL = "https://ad7np.us/airwsjtx/callsign-grid-db.json";
const DB_MANIFEST_URL = "https://ad7np.us/airwsjtx/callsign-grid-db-version.json";
const DB_STORE_NAME = "callsign_db";
const DB_META_STORE = "callsign_meta";
const IDB_NAME = "airwsjtx_callsign";
const IDB_VERSION = 1;
const FRESHNESS_DAYS = 30;

function openIDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_NAME, IDB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DB_STORE_NAME))
        db.createObjectStore(DB_STORE_NAME, { keyPath: "callsign" });
      if (!db.objectStoreNames.contains(DB_META_STORE))
        db.createObjectStore(DB_META_STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function getMeta(key: string): Promise<string | null> {
  const db = await openIDB();
  return new Promise((resolve) => {
    const tx = db.transaction(DB_META_STORE, "readonly");
    const req = tx.objectStore(DB_META_STORE).get(key);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => resolve(null);
  });
}

async function setMeta(key: string, value: string): Promise<void> {
  const db = await openIDB();
  return new Promise((resolve) => {
    const tx = db.transaction(DB_META_STORE, "readwrite");
    tx.objectStore(DB_META_STORE).put(value, key);
    tx.oncomplete = () => resolve();
  });
}

export async function lookupCallsign(callsign: string): Promise<StationInfo | null> {
  const call = callsign.trim().toUpperCase();
  try {
    const db = await openIDB();
    return new Promise((resolve) => {
      const tx = db.transaction(DB_STORE_NAME, "readonly");
      const req = tx.objectStore(DB_STORE_NAME).get(call);
      req.onsuccess = () => resolve(req.result ?? null);
      req.onerror = () => resolve(null);
    });
  } catch {
    return null;
  }
}

export async function lookupCallsignOnline(callsign: string): Promise<StationInfo | null> {
  const call = callsign.trim().toUpperCase();
  try {
    const resp = await fetch(`https://callook.info/${call}/json`, {
      headers: { "User-Agent": "AirWSJT-X/0.2" },
    });
    if (!resp.ok) return null;
    const d = await resp.json();
    if (d.status !== "VALID") return null;
    const loc = d.location ?? {};
    const lat = loc.latitude ? parseFloat(loc.latitude) : null;
    const lon = loc.longitude ? parseFloat(loc.longitude) : null;
    const grid = loc.gridsquare || null;
    const nameParts = d.name ?? {};
    const name = typeof nameParts === "object"
      ? `${nameParts.first ?? ""} ${nameParts.last ?? ""}`.trim() || null
      : String(nameParts) || null;
    return { callsign: call, name, grid, lat, lon, country: "USA", source: "callook.info" };
  } catch {
    return null;
  }
}

export interface DbStatus {
  version: string | null;
  downloaded_at: string | null;
  entries: number;
  fresh: boolean;
}

export async function getDbStatus(): Promise<DbStatus> {
  const version = await getMeta("version");
  const downloadedAt = await getMeta("downloaded_at");
  const entriesStr = await getMeta("entries");
  const entries = entriesStr ? parseInt(entriesStr, 10) : 0;
  let fresh = false;
  if (downloadedAt) {
    const dlDate = new Date(downloadedAt);
    fresh = Date.now() - dlDate.getTime() < FRESHNESS_DAYS * 86400000;
  }
  return { version, downloaded_at: downloadedAt, entries, fresh };
}

export async function downloadDb(
  onProgress?: (loaded: number, total: number) => void,
): Promise<DbStatus> {
  const resp = await fetch(DB_URL, {
    headers: { "User-Agent": "AirWSJT-X/0.2" },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const payload = await resp.json();
  const entries = payload.entries as Record<string, Record<string, unknown>> | undefined;
  if (!entries || typeof entries !== "object") throw new Error("Invalid DB format");

  const db = await openIDB();
  const callsigns = Object.keys(entries);
  const batchSize = 500;

  for (let i = 0; i < callsigns.length; i += batchSize) {
    const batch = callsigns.slice(i, i + batchSize);
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(DB_STORE_NAME, "readwrite");
      const store = tx.objectStore(DB_STORE_NAME);
      for (const call of batch) {
        const raw = entries[call];
        const info: StationInfo = {
          callsign: call.toUpperCase(),
          name: raw.name ? String(raw.name) : null,
          grid: raw.grid ? String(raw.grid) : null,
          lat: raw.lat != null ? Number(raw.lat) : null,
          lon: raw.lon != null ? Number(raw.lon) : null,
          country: raw.country ? String(raw.country) : null,
          source: "local-db",
        };
        store.put(info);
      }
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    onProgress?.(Math.min(i + batchSize, callsigns.length), callsigns.length);
  }

  const version = payload.version ? String(payload.version) : new Date().toISOString();
  const now = new Date().toISOString();
  await setMeta("version", version);
  await setMeta("downloaded_at", now);
  await setMeta("entries", String(callsigns.length));

  return { version, downloaded_at: now, entries: callsigns.length, fresh: true };
}

export async function checkForUpdate(): Promise<{
  updateAvailable: boolean;
  remoteVersion: string | null;
  remoteEntries: number | null;
}> {
  try {
    const resp = await fetch(DB_MANIFEST_URL, {
      headers: { "User-Agent": "AirWSJT-X/0.2" },
    });
    if (!resp.ok) return { updateAvailable: false, remoteVersion: null, remoteEntries: null };
    const manifest = await resp.json();
    const remoteVersion = manifest.version ? String(manifest.version) : null;
    const remoteEntries = manifest.entries ? Number(manifest.entries) : null;
    const localVersion = await getMeta("version");
    return {
      updateAvailable: remoteVersion !== null && remoteVersion !== localVersion,
      remoteVersion,
      remoteEntries,
    };
  } catch {
    return { updateAvailable: false, remoteVersion: null, remoteEntries: null };
  }
}

const PREFIX_GRID: Record<string, string> = {
  W1: "FN42", K1: "FN42", N1: "FN42",
  W2: "FN20", K2: "FN20", N2: "FN20",
  W3: "FM19", K3: "FM19",
  W4: "EM73", K4: "EM73",
  W5: "EM30", K5: "EM30",
  W6: "DM04", K6: "DM04",
  W7: "CN87", K7: "CN87",
  W8: "EN82", K8: "EN82",
  W9: "EN61", K9: "EN61",
  W0: "DN70", K0: "DN70",
  VE1: "FN74", VA1: "FN74",
  VE2: "FN35", VA2: "FN35",
  VE3: "EN93", VA3: "EN93",
  VE7: "CN89", VA7: "CN89",
  G: "IO91", DL: "JO51", F: "JN03", I: "JN53",
  PA: "JO22", SM: "JP80", OH: "KP30", CT: "IM57", EA: "IM98",
};

export function prefixFallback(callsign: string): StationInfo | null {
  const call = callsign.toUpperCase();
  for (const [prefix, grid] of Object.entries(PREFIX_GRID)) {
    if (call.startsWith(prefix)) {
      return { callsign: call, name: null, grid, lat: null, lon: null, country: null, source: "prefix-fallback" };
    }
  }
  return null;
}
