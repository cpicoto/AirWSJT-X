export interface Aircraft {
  icao24: string;
  callsign: string | null;
  lat: number | null;
  lon: number | null;
  altitude_m: number | null;
  velocity_ms: number | null;
  heading_deg: number | null;
  vertical_rate_ms: number | null;
  on_ground: boolean;
  last_seen: number;
}

const OPENSKY_URL = "https://opensky-network.org/api/states/all";

function parseOpenSky(states: unknown[][]): Aircraft[] {
  const now = Date.now();
  return states.map((s) => {
    const geoAlt = s[13] as number | null;
    const baroAlt = s[7] as number | null;
    return {
      icao24: (s[0] as string) || "",
      callsign: ((s[1] as string) || "").trim() || null,
      lat: s[6] as number | null, lon: s[5] as number | null,
      altitude_m: geoAlt ?? baroAlt, velocity_ms: s[9] as number | null,
      heading_deg: s[10] as number | null, vertical_rate_ms: s[11] as number | null,
      on_ground: Boolean(s[8]), last_seen: now,
    };
  });
}

export async function fetchOpenSky(
  latMin: number,
  lonMin: number,
  latMax: number,
  lonMax: number,
  signal?: AbortSignal,
): Promise<Aircraft[]> {
  const url = `${OPENSKY_URL}?lamin=${latMin}&lomin=${lonMin}&lamax=${latMax}&lomax=${lonMax}`;
  const resp = await fetch(url, {
    headers: { "User-Agent": "AirWSJT-X/0.2 (ham radio scatter tool)" },
    signal,
  });
  if (!resp.ok) {
    if (resp.status === 429) throw new Error("RATE_LIMITED");
    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
  }
  const data = await resp.json();
  const states: unknown[][] = data.states ?? [];
  return parseOpenSky(states);
}

export async function fetchNativeAircraft(
  provider: "opensky" | "adsbexchange",
  latMin: number, lonMin: number, latMax: number, lonMax: number,
): Promise<Aircraft[]> {
  const data = await invoke<{ states?: unknown[][] | null; ac?: Record<string, unknown>[] }>("fetch_mobile_aircraft", {
    provider, latMin, lonMin, latMax, lonMax,
  });
  if (provider === "opensky") return parseOpenSky(data.states ?? []);
  if (!Array.isArray(data.ac)) throw new Error("Invalid ADS-B Exchange aircraft data");
  const number = (value: unknown): number | null => {
    if (value === null || value === undefined || value === "ground") return null;
    if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("Invalid ADS-B Exchange aircraft number");
    return value;
  };
  return data.ac.map(ac => {
    if (typeof ac.hex !== "string") throw new Error("Invalid ADS-B Exchange aircraft address");
    const altitude = number(ac.alt_geom) ?? number(ac.alt_baro);
    const speed = number(ac.gs);
    const rate = number(ac.geom_rate) ?? number(ac.baro_rate);
    return {
      icao24: ac.hex.toLowerCase(),
      callsign: typeof ac.flight === "string" ? ac.flight.trim() || null : null,
      lat: number(ac.lat), lon: number(ac.lon),
      altitude_m: altitude === null ? null : altitude * 0.3048,
      velocity_ms: speed === null ? null : speed * 0.514444,
      heading_deg: number(ac.track),
      vertical_rate_ms: rate === null ? null : rate * 0.00508,
      on_ground: ac.alt_baro === "ground", last_seen: Date.now(),
    };
  });
}

export interface OpenSkyPollerOptions {
  latMin: number;
  lonMin: number;
  latMax: number;
  lonMax: number;
  intervalMs?: number;
  maxBackoffMs?: number;
  minimumIntervalMs?: number;
  onUpdate: (aircraft: Aircraft[]) => void;
  onError?: (msg: string) => void;
  onRateLimited?: (waitMs: number) => void;
  fetchAircraft?: typeof fetchOpenSky;
}

export class OpenSkyPoller {
  private opts: Required<OpenSkyPollerOptions>;
  private abortCtrl: AbortController | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private consecutive429 = 0;

  constructor(opts: OpenSkyPollerOptions) {
    this.opts = {
      intervalMs: 30000,
      maxBackoffMs: 300000,
      minimumIntervalMs: 10000,
      onError: () => {},
      onRateLimited: () => {},
      fetchAircraft: fetchOpenSky,
      ...opts,
    };
  }

  start(): void {
    this.stop();
    this.abortCtrl = new AbortController();
    this.poll();
  }

  setInterval(intervalMs: number): void {
    if (!Number.isFinite(intervalMs) || intervalMs <= 0) throw new Error("Invalid aircraft poll interval");
    this.opts.intervalMs = intervalMs;
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.abortCtrl?.abort();
    this.abortCtrl = null;
  }

  private async poll(): Promise<void> {
    if (!this.abortCtrl || this.abortCtrl.signal.aborted) return;
    const controller = this.abortCtrl;
    const started = Date.now();
    let waitMs = this.opts.intervalMs;

    try {
      const aircraft = await this.opts.fetchAircraft(
        this.opts.latMin, this.opts.lonMin,
        this.opts.latMax, this.opts.lonMax,
        this.abortCtrl.signal,
      );
      if (controller.signal.aborted || this.abortCtrl !== controller) return;
      this.consecutive429 = 0;
      this.opts.onUpdate(aircraft);
    } catch (e) {
      const msg = String(e instanceof Error ? e.message : e);
      if (controller.signal.aborted || this.abortCtrl !== controller) return;
      const rateLimit = /^RATE_LIMITED(?::(\d+))?$/.exec(msg);
      if (rateLimit) {
        this.consecutive429++;
        const base = Math.max(this.opts.intervalMs, 10000);
        waitMs = Math.min(base * 2 ** (this.consecutive429 - 1), this.opts.maxBackoffMs);
        if (rateLimit[1] !== undefined) {
          waitMs = Math.max(this.opts.minimumIntervalMs, Number(rateLimit[1]) * 1000);
        }
        this.opts.onRateLimited(waitMs);
      } else if (!this.abortCtrl?.signal.aborted) {
        this.consecutive429 = 0;
        this.opts.onError(msg);
      }
    }

    if (this.abortCtrl && !this.abortCtrl.signal.aborted) {
      const deadline = Date.now() + (this.consecutive429 ? waitMs : Math.max(0, waitMs - (Date.now() - started)));
      const schedule = () => {
        if (controller.signal.aborted || this.abortCtrl !== controller) return;
        const remaining = deadline - Date.now();
        if (remaining <= 0) { void this.poll(); return; }
        this.timer = setTimeout(schedule, Math.min(remaining, 2147483647));
      };
      schedule();
    }
  }
}
import { invoke } from "@tauri-apps/api/core";
