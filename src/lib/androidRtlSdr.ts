import { invoke } from "@tauri-apps/api/core";
import type { Aircraft } from "./opensky";

/** USB access to RTL2832U receivers, injected by MainActivity as `window.AndroidRtlSdr`. */
interface AndroidRtlSdrBridge {
  listDevices(): string;
  hasPermission(name: string): boolean;
  requestPermission(name: string): boolean;
  open(name: string): number;
  close(): void;
}

interface UsbReceiver {
  name: string;
  vendorId: number;
  productId: number;
  product: string;
  manufacturer: string;
}

export interface RtlSdrStats {
  device: string;
  validMessages: number;
  crcErrors: number;
  positions: number;
}

interface FetchResponse {
  aircraft?: (Omit<Aircraft, "last_seen"> & { position_age_s?: number | null })[];
  running: boolean;
  exit_code: number | null;
  valid_messages: number;
  crc_errors: number;
  positions: number;
  log: string[];
}

const PERMISSION_TIMEOUT_MS = 60000;

// The Kotlin bridge and native demodulator are process-global. Queue complete
// open/start and stop/close transitions so React effect replacement cannot let
// an old source close or stop the receiver owned by its replacement.
let rtlSdrLifecycleTail: Promise<void> = Promise.resolve();

function withRtlSdrLifecycle<T>(operation: () => Promise<T>): Promise<T> {
  const run = rtlSdrLifecycleTail.then(operation, operation);
  rtlSdrLifecycleTail = run.then(() => undefined, () => undefined);
  return run;
}

function bridge(): AndroidRtlSdrBridge | null {
  if (typeof window === "undefined") return null;
  const value = (window as unknown as { AndroidRtlSdr?: AndroidRtlSdrBridge }).AndroidRtlSdr;
  return value && typeof value.open === "function" ? value : null;
}

/** True on Android builds that include the native USB bridge. */
export function isAndroidRtlSdrSupported(): boolean {
  return bridge() !== null;
}

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * Local RTL-SDR receiver over USB OTG. The first fetch asks for USB permission,
 * opens the device, and starts the native receiver; later fetches poll decoded aircraft.
 * A receiver that stops (for example after unplugging) is restarted on the next fetch.
 */
export class AndroidRtlSdrSource {
  private starting: Promise<void> | null = null;
  private device = "";
  private disposed = false;
  stats: RtlSdrStats | null = null;

  constructor(private readonly onStatus: (message: string) => void) {}

  private start(): Promise<void> {
    return withRtlSdrLifecycle(() => this.startExclusive());
  }

  private async startExclusive(): Promise<void> {
    const usb = bridge();
    if (!usb) throw new Error("Local RTL-SDR requires the Android app");
    if (this.disposed) throw new Error("RTL-SDR stopped");
    // Prove the previous native reader released its wrapped descriptor before
    // Kotlin open() closes/replaces the UsbDeviceConnection.
    await invoke("prepare_mobile_rtlsdr");
    const devices = JSON.parse(usb.listDevices()) as UsbReceiver[];
    const receiver = devices[0];
    if (!receiver) throw new Error("No RTL-SDR found — connect it with a USB OTG adapter");
    this.device = receiver.product || `${receiver.vendorId.toString(16).padStart(4, "0")}:${receiver.productId.toString(16).padStart(4, "0")}`;
    if (!usb.requestPermission(receiver.name)) {
      this.onStatus(`Allow USB access to ${this.device}...`);
      const deadline = Date.now() + PERMISSION_TIMEOUT_MS;
      while (!usb.hasPermission(receiver.name)) {
        if (this.disposed) throw new Error("RTL-SDR stopped");
        if (Date.now() > deadline) throw new Error("USB permission for the RTL-SDR was not granted");
        await delay(500);
      }
    }
    if (this.disposed) throw new Error("RTL-SDR stopped");
    const fd = usb.open(receiver.name);
    if (fd < 0) throw new Error(`Cannot open ${this.device}`);
    try {
      await invoke("start_mobile_rtlsdr", { fd });
    } catch (error) {
      usb.close();
      throw error;
    }
    this.onStatus(`RTL-SDR: listening on ${this.device}...`);
  }

  async fetchAircraft(): Promise<Aircraft[]> {
    if (!this.starting) {
      const starting = this.start();
      this.starting = starting;
      starting.catch(() => { if (this.starting === starting) this.starting = null; });
    }
    await this.starting;
    const data = await invoke<FetchResponse>("fetch_mobile_rtlsdr");
    if (!data.running) {
      await this.release();
      const detail = data.log?.[data.log.length - 1];
      throw new Error(`RTL-SDR receiver stopped${detail ? `: ${detail}` : ""}`);
    }
    this.stats = {
      device: this.device, validMessages: data.valid_messages,
      crcErrors: data.crc_errors, positions: data.positions,
    };
    if (!Array.isArray(data.aircraft)) throw new Error("Invalid RTL-SDR aircraft data");
    const now = Date.now();
    return data.aircraft.map(ac => ({
      ...ac, last_seen: now,
      position_updated_epoch_s: typeof ac.position_age_s === "number" && Number.isFinite(ac.position_age_s) && ac.position_age_s >= 0
        ? now / 1000 - ac.position_age_s : null,
    }));
  }

  private release(): Promise<void> {
    return withRtlSdrLifecycle(async () => {
      // A fetch from a disposed source may resolve after its replacement has
      // started. Its stale response must not stop or close the new receiver.
      if (this.disposed) return;
      await this.releaseExclusive();
    });
  }

  private async releaseExclusive(): Promise<void> {
    this.starting = null;
    this.stats = null;
    // A stuck native reader must retain the Android file descriptor. Propagate
    // that failure and deliberately leave the UsbDeviceConnection open.
    try {
      await invoke("stop_mobile_rtlsdr");
    } catch {
      return;
    }
    bridge()?.close();
  }

  async stop(): Promise<void> {
    this.disposed = true;
    await withRtlSdrLifecycle(async () => {
      // A queued/running start precedes this operation in the same lifecycle
      // chain. By the time this callback runs it has settled, so teardown and
      // USB close complete before any replacement source can open the bridge.
      await this.starting?.catch(() => {});
      await this.releaseExclusive();
    });
  }
}
