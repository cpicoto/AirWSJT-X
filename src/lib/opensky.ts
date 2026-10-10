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
  const now = Date.now();

  return states.map((s) => {
    const geoAlt = s[13] as number | null;
    const baroAlt = s[7] as number | null;
    return {
      icao24: (s[0] as string) || "",
      callsign: ((s[1] as string) || "").trim() || null,
      lat: s[6] as number | null,
      lon: s[5] as number | null,
      altitude_m: geoAlt ?? baroAlt,
      velocity_ms: s[9] as number | null,
      heading_deg: s[10] as number | null,
      vertical_rate_ms: s[11] as number | null,
      on_ground: Boolean(s[8]),
      last_seen: now,
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
  onUpdate: (aircraft: Aircraft[]) => void;
  onError?: (msg: string) => void;
  onRateLimited?: (waitMs: number) => void;
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
      onError: () => {},
      onRateLimited: () => {},
      ...opts,
    };
  }

  start(): void {
    this.stop();
    this.abortCtrl = new AbortController();
    this.poll();
  }

  stop(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.abortCtrl?.abort();
    this.abortCtrl = null;
  }

  private async poll(): Promise<void> {
    if (!this.abortCtrl || this.abortCtrl.signal.aborted) return;
    let waitMs = this.opts.intervalMs;

    try {
      const aircraft = await fetchOpenSky(
        this.opts.latMin, this.opts.lonMin,
        this.opts.latMax, this.opts.lonMax,
        this.abortCtrl.signal,
      );
      this.consecutive429 = 0;
      this.opts.onUpdate(aircraft);
    } catch (e) {
      const msg = String(e instanceof Error ? e.message : e);
      if (msg === "RATE_LIMITED") {
        this.consecutive429++;
        const base = Math.max(this.opts.intervalMs, 10000);
        waitMs = Math.min(base * 2 ** (this.consecutive429 - 1), this.opts.maxBackoffMs);
        this.opts.onRateLimited(waitMs);
      } else if (!this.abortCtrl?.signal.aborted) {
        this.consecutive429 = 0;
        this.opts.onError(msg);
      }
    }

    if (this.abortCtrl && !this.abortCtrl.signal.aborted) {
      this.timer = setTimeout(() => this.poll(), waitMs);
    }
  }
}
