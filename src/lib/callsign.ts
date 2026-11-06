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

export async function listCallsigns(): Promise<StationInfo[]> {
  try {
    const db = await openIDB();
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(DB_STORE_NAME, "readonly");
      const req = tx.objectStore(DB_STORE_NAME).getAll();
      req.onsuccess = () => {
        const stations = (req.result as StationInfo[]).slice()
          .sort((a, b) => a.callsign.localeCompare(b.callsign));
        resolve(stations);
      };
      req.onerror = () => reject(req.error);
    });
  } catch {
    return [];
  }
}

export async function lookupCallsignOnline(callsign: string): Promise<StationInfo | null> {
  const call = callsign.trim().toUpperCase();
  try {
    // Keep browser requests "simple". Setting User-Agent from WebKit triggers
    // a CORS preflight, and static Azure websites reject OPTIONS with 405.
    const resp = await fetch(`https://callook.info/${call}/json`);
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
  const resp = await fetch(DB_URL);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const payload = await resp.json();
  const entries = payload.entries as Record<string, Record<string, unknown>> | undefined;
  if (!entries || typeof entries !== "object") throw new Error("Invalid DB format");

  const callsigns = Object.keys(entries);
  const version = payload.version ? String(payload.version) : new Date().toISOString();
  const now = new Date().toISOString();
  // Validate and materialize the replacement before touching the usable cache.
  const replacement = callsigns.map(call => {
    const raw = entries[call];
    if (!raw || typeof raw !== "object") throw new Error(`Invalid DB entry: ${call}`);
    return {
      callsign: call.toUpperCase(),
      name: raw.name ? String(raw.name) : null,
      grid: raw.grid ? String(raw.grid) : null,
      lat: raw.lat != null ? Number(raw.lat) : null,
      lon: raw.lon != null ? Number(raw.lon) : null,
      country: raw.country ? String(raw.country) : null,
      source: "local-db",
    } satisfies StationInfo;
  });

  const db = await openIDB();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction([DB_STORE_NAME, DB_META_STORE], "readwrite");
    const store = tx.objectStore(DB_STORE_NAME);
    const meta = tx.objectStore(DB_META_STORE);
    store.clear();
    for (const info of replacement) store.put(info);
    meta.put(version, "version");
    meta.put(now, "downloaded_at");
    meta.put(String(replacement.length), "entries");
    tx.oncomplete = () => resolve();
    tx.onabort = () => reject(tx.error ?? new Error("Database refresh aborted"));
    tx.onerror = () => reject(tx.error ?? new Error("Database refresh failed"));
  });
  onProgress?.(replacement.length, replacement.length);

  return { version, downloaded_at: now, entries: callsigns.length, fresh: true };
}

export async function checkForUpdate(): Promise<{
  updateAvailable: boolean;
  remoteVersion: string | null;
  remoteEntries: number | null;
}> {
  try {
    const resp = await fetch(DB_MANIFEST_URL);
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
