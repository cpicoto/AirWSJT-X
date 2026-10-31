import { useEffect, useState } from "react";
import { subscribeSidecarEvent } from "./lib/sidecarRpc";

interface ReceiverDevice {
  index: number;
  name: string;
  product: string;
  serial: string;
  available: boolean;
  message: string | null;
  supported_gains_db?: number[];
}

interface ReceiverStatus {
  state: string;
  message: string | null;
  device_index?: number;
  frames?: number;
  crc_errors?: number;
  unsupported_frames?: number;
  valid_messages?: number;
  positions?: number;
  aircraft?: number;
  with_position?: number;
  last_message_age_s?: number | null;
  diagnostics?: string[];
  gain_db?: number | null;
  supported_gains_db?: number[];
  position_rejections?: number;
  last_frames?: {
    frame: string;
    timestamp_12mhz: number;
    crc_syndrome: number;
  }[];
  iq?: {
    samples: number;
    sample_rate: number;
    elapsed_s: number;
    clipped_fraction: number;
    mean_power_dbfs: number;
    signal_power_dbfs: number | null;
    candidate_quiet_magnitude: number | null;
    dropped_buffers: number;
    discontinuities: number;
    usb_overruns: number | null;
    preamble_candidates?: number;
    df17_crc_rejected_phase_trials?: number;
    accepted_other_modes?: number;
    rejected_bad?: number;
    rejected_unknown_icao?: number;
  } | null;
}

type Request = (method: string, params?: Record<string, unknown>) => Promise<unknown>;
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function LocalReceiver({ request, onChanged, onLog }: {
  request: Request;
  onChanged: () => void;
  onLog: (message: string) => void;
}) {
  const [devices, setDevices] = useState<ReceiverDevice[]>([]);
  const [selected, setSelected] = useState(0);
  const [status, setStatus] = useState<ReceiverStatus | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [discovered, setDiscovered] = useState(false);
  const [gain, setGain] = useState("auto");
  const [gainDirty, setGainDirty] = useState(false);
  const [gains, setGains] = useState<number[]>([]);

  useEffect(() => {
    let disposed = false;
    let unsubscribe: (() => void) | undefined;
    void subscribeSidecarEvent("rtlsdr_update", data => {
      if (disposed || !data || typeof data !== "object" || !("state" in data)) return;
      const next = data as ReceiverStatus;
      setStatus(next);
      if (next.supported_gains_db?.length) setGains(next.supported_gains_db);
    }).then(stop => {
      if (disposed) stop();
      else unsubscribe = stop;
    }).catch(reason => {
      if (!disposed) setError(`Cannot subscribe to receiver updates: ${errorText(reason)}`);
    });
    return () => { disposed = true; unsubscribe?.(); };
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await request("rtlsdr_status") as ReceiverStatus;
        if (!disposed) {
          setStatus(next);
          if (next.supported_gains_db?.length) setGains(next.supported_gains_db);
        }
      } catch (reason) {
        if (!disposed) setError(`Cannot read receiver status: ${errorText(reason)}`);
      }
      if (!disposed) timer = setTimeout(() => { void poll(); }, 2000);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [request]);

  useEffect(() => {
    if (!gainDirty) setGain(status?.gain_db == null ? "auto" : String(status.gain_db));
  }, [status?.gain_db, gainDirty]);

  const discover = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await request("rtlsdr_discover") as { devices: ReceiverDevice[] };
      setDevices(result.devices);
      setDiscovered(true);
      const index = result.devices.some(device => device.index === selected)
        ? selected : result.devices[0]?.index ?? 0;
      setSelected(index);
      const device = result.devices.find(candidate => candidate.index === index);
      setGains(device?.available ? device.supported_gains_db ?? [] : []);
    } catch (reason) {
      const message = errorText(reason);
      setError(message);
      onLog(`Local RTL-SDR discovery failed: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  const control = async (start: boolean) => {
    setBusy(true);
    setError("");
    try {
      const result = await request(start ? "rtlsdr_start" : "rtlsdr_stop",
        start ? { device_index: selected, gain_db: gain === "auto" ? null : Number(gain) } : {}) as ReceiverStatus;
      setStatus(result);
      if (result.supported_gains_db?.length) setGains(result.supported_gains_db);
      setGainDirty(false);
      onChanged();
      onLog(`Local RTL-SDR: ${result.message ?? result.state}`);
    } catch (reason) {
      const message = errorText(reason);
      setError(message);
      onLog(`Local RTL-SDR ${start ? "start" : "stop"} failed: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  const applyGain = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await request("rtlsdr_set_gain",
        { gain_db: gain === "auto" ? null : Number(gain) }) as ReceiverStatus;
      setStatus(result);
      setGainDirty(false);
      onChanged();
      onLog(`Local RTL-SDR gain: ${result.gain_db == null ? "automatic" : `${result.gain_db} dB`}. Receiver restarted; counters reset.`);
    } catch (reason) {
      const message = errorText(reason);
      setError(message);
      onLog(`Cannot change local RTL-SDR gain: ${message}`);
    } finally {
      setBusy(false);
    }
  };

  const running = status?.state === "connecting" || status?.state === "connected";
  return (
    <details style={{ width: "100%", fontSize: 11, marginTop: 6 }}>
      <summary>Local RTL-SDR receiver (USB, Windows x64)</summary>
      <p>Receive ADS-B directly at 1090 MHz. Startup automatically discovers and starts the first accessible receiver.
        Stop keeps USB reception off for this app run; Discover and Start can retry.
        No external dump1090 service or address is needed.
        Use a 1090 MHz antenna; bias tee is off. Starting selects this as the aircraft source.</p>
      <button className="btn-secondary" disabled={busy || running}
        onClick={() => { void discover(); }}>Discover USB receivers</button>
      {devices.length > 0 && (
        <label> USB receiver
          <select aria-label="USB receiver" value={selected} disabled={busy || running}
            onChange={event => {
              const index = Number(event.target.value);
              setSelected(index);
              const device = devices.find(candidate => candidate.index === index);
              setGains(device?.available ? device.supported_gains_db ?? [] : []);
              setGain("auto");
              setGainDirty(false);
            }}>
            {devices.map(device => (
              <option key={device.index} value={device.index}>
                {device.product || device.name} (serial {device.serial || "unknown"}, index {device.index})
                {!device.available ? " - unavailable / busy" : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {discovered && devices.length === 0 && (
        <p role="status">No accessible RTL-SDR receiver found. Check the USB connection, WinUSB for
          Bulk-In Interface 0, and whether another SDR app owns the device. Discover again after connecting.
          The app never installs or replaces drivers.</p>
      )}
      {devices.find(device => device.index === selected)?.message && (
        <p>{devices.find(device => device.index === selected)?.message}</p>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button disabled={busy || running || !devices.some(device => device.index === selected && device.available)}
          onClick={() => { void control(true); }}>Start local ADS-B</button>
        <button className="btn-secondary" disabled={busy || !status || status.state === "stopped"}
          onClick={() => { void control(false); }}>Stop local receiver</button>
      </div>
        <div style={{ marginTop: 6 }}>
          <label>Tuner gain
            <select aria-label="Tuner gain" value={gain} disabled={busy}
              onChange={event => { setGain(event.target.value); setGainDirty(true); }}>
              <option value="auto">Automatic (default)</option>
              {gains.map(value => (
                <option key={value} value={value}>{value.toFixed(1)} dB (manual)</option>
              ))}
            </select>
          </label>
          <button disabled={busy || !running || !gainDirty} onClick={() => { void applyGain(); }}>
            Apply gain / restart
          </button>
          {!gains.length && <p>Automatic gain is ready. Discover an attached receiver to load its supported manual dB steps.</p>}
        </div>
      <div role="status" aria-label="Local receiver status" style={{ marginTop: 6 }}>
        {status?.state ?? "Reading status"}{status?.message ? `: ${status.message}` : ""}
        {status?.frames != null && (
          <div>{status.valid_messages} CRC-valid ADS-B;
            {" "}{status.aircraft} aircraft;
            {" "}{status.with_position} positioned; {status.positions} position fixes.
            {" "}{status.position_rejections ?? 0} implausible position jumps rejected.
            {status.last_message_age_s != null ? ` Last valid message ${status.last_message_age_s}s ago.` : ""}</div>
        )}
      </div>
      <details><summary>Detailed IQ measurements</summary>
      {status?.iq ? (
        <p aria-label="IQ receive measurements">
          {!running && "Saved measurements from the last receiver run. "}
          {status.iq.samples.toLocaleString()} processed IQ samples at {(status.iq.sample_rate / 1e6).toFixed(1)} MS/s;
          {" "}{(status.iq.clipped_fraction * 100).toFixed(3)}% ADC components clipped;
          {" "}mean input power {status.iq.mean_power_dbfs.toFixed(1)} dBFS.
          {" "}Validated-message power {status.iq.signal_power_dbfs == null ? "not available" : `${status.iq.signal_power_dbfs.toFixed(1)} dBFS`}.
          {status.iq.preamble_candidates != null && <>
            {" "}{status.iq.preamble_candidates} preamble candidates;
            {" "}{status.iq.df17_crc_rejected_phase_trials} DF17 CRC-rejected phase trials;
            {" "}{status.iq.accepted_other_modes} accepted other Mode S messages;
            {" "}{status.iq.rejected_bad} bad phase/candidate rejections;
            {" "}{status.iq.rejected_unknown_icao} unknown-address candidate rejections.
            {" "}Trials can overlap and are not counts of received transmissions.
          </>}
          {" "}{status.iq.dropped_buffers} application queue buffers dropped;
          {" "}{status.iq.discontinuities} detected discontinuities. USB hardware overruns:
          {" "}{status.iq.usb_overruns ?? "not measurable"}.
          {" "}Input power includes noise and interference; it is not ADS-B signal strength.
        </p>
      ) : <p>IQ measurements are not available while the receiver is stopped or before its first report.</p>}
      </details>
      {error && <p role="alert" style={{ color: "var(--red)" }}>{error}</p>}
      {!!status?.diagnostics?.length && (
        <details><summary>Receiver diagnostics</summary>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 10 }}>{status.diagnostics.join("\n")}</pre>
        </details>
      )}
      {!!status?.last_frames?.length && (
        <details><summary>Last validated DF17 payloads (up to 32, retained after Stop)</summary>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 10 }}>
            {status.last_frames.map(frame =>
              `${frame.frame} | 12 MHz timestamp ${frame.timestamp_12mhz} | CRC syndrome ${frame.crc_syndrome}`
            ).join("\n")}
          </pre>
        </details>
      )}
      <p>Stop releases the USB device and returns to configured network sources.
        Only CRC-valid DF17 airborne ADS-B with paired CPR positions is used; local coverage is not a global feed.</p>
    </details>
  );
}
