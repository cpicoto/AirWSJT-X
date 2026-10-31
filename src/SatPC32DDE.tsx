import { useEffect, useState } from "react";
import { rpc } from "./lib/sidecarRpc";

interface DDEStatus {
  supported: boolean;
  enabled: boolean;
  service: string;
  topic: string;
  item: string;
  target: string;
  error: string | null;
  station_grid?: string | null;
  station_altitude_m?: number;
}

export type OutputMode = "off" | "hamlib" | "dde";
export interface PublishedTarget { az: number; el: number }
interface OutputStatus {
  mode: OutputMode;
  hamlib_connected: boolean;
  dde: DDEStatus;
}

export default function SatPC32DDE({ onModeChange, onTargetChange }: {
  onModeChange: (mode: OutputMode) => void;
  onTargetChange: (target: PublishedTarget | null) => void;
}) {
  const [output, setOutput] = useState<OutputStatus | null>(null);
  const status = output?.dde;
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const accept = (next: OutputStatus) => {
    setOutput(next);
    onModeChange(next.mode);
    const match = next.mode === "dde" && next.dde.enabled
      ? /^SN\S+ AZ(\d+(?:\.\d+)?) EL(\d+(?:\.\d+)?) /.exec(next.dde.target) : null;
    const az = match ? Number(match[1]) : NaN;
    const el = match ? Number(match[2]) : NaN;
    onTargetChange(Number.isFinite(az) && az >= 0 && az < 360
      && Number.isFinite(el) && el >= 0 && el <= 90 ? { az, el } : null);
  };
  useEffect(() => {
    let active = true;
    const refresh = () => {
      rpc("rotator_output_status", {}).then(result => {
        if (active) {
          const next = result as OutputStatus;
          accept(next);
          localStorage.removeItem("airwsjtx.rotatorControlEnabled");
        }
      }).catch(reason => { if (active) { setError(String(reason)); onTargetChange(null); } });
    };
    refresh();
    const timer = window.setInterval(refresh, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, [onModeChange, onTargetChange]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div style={{ marginBottom: 8 }}>
    <label>Rotator output{" "}
      <select aria-label="Rotator output" value={output?.mode ?? "off"} disabled={!output || busy}
        onChange={async event => {
          const mode = event.target.value as OutputMode;
          onTargetChange(null);
          setBusy(true);
          try {
            const next = await rpc("rotator_output_set", { mode }) as OutputStatus;
            accept(next);
            localStorage.setItem("airwsjtx.rotatorOutputPreference", next.mode);
            setError(null);
          } catch (reason) {
            setError(String(reason));
            try {
              const next = await rpc("rotator_output_status", {}) as OutputStatus;
              accept(next);
            } catch (refreshError) { setError(`${String(reason)}; status: ${String(refreshError)}`); }
          } finally { setBusy(false); }
        }}>
        <option value="off">Off</option>
        <option value="hamlib">Hamlib (rotctld)</option>
        {status?.supported && <option value="dde">SatPC32 DDE (PST Rotator)</option>}
      </select>
    </label>
    <div role="status" aria-label="Rotator output status" style={{ fontSize: 11 }}>
      {output?.mode === "hamlib"
        ? `Hamlib selected; rotctld ${output.hamlib_connected ? "connected" : "disconnected"}`
        : output?.mode === "dde" ? "SatPC32 DDE selected; Hamlib coordinate output disabled"
        : "Output off. Each launch starts Off; select an output explicitly."}
    </div>
    {status?.supported && <details style={{ marginTop: 8 }}>
    <summary>SatPC32 DDE output (Windows / PST Rotator)</summary>
    <p style={{ fontSize: 11, color: "var(--text-muted)" }}>
      Opt-in aircraft tracking output. In PST Rotator select SatPC32 as Tracker
      and Tracking mode. Close SatPC32 first. Radio fields are zero-Hz
      compatibility placeholders; no CAT/PTT commands are sent.
      Choose SatPC32 DDE above; manual mode and expired positions suppress output.
      Enabling this can move a connected rotator through PST Rotator.
    </p>
    <div role="status" aria-label="SatPC32 DDE status" style={{ fontSize: 11, marginTop: 6 }}>
      {status?.enabled ? "DDE server enabled" : "DDE output off (not saved across launches)"}
      {status?.enabled && <div>{status.service} / {status.topic} / {status.item}<br />{status.target}</div>}
      {status?.enabled && <div>
        Reference: MY station (A), {status.station_grid || "grid not set"}
        {status.station_altitude_m != null ? `, ${status.station_altitude_m} m` : ""}.
        AZ is degrees clockwise from true north; EL is degrees above the radio horizon.
        SN is the selected flight identifier/callsign (ICAO address if unavailable), without spaces.
        Angles follow the selected aircraft; its callsign remains in the aircraft UI.
        {status.target === "** NO SATELLITE **"
          ? " No current valid DDE target. A retained lock may show older coordinates; those are not transmitted."
          : " The line above is the exact current DDE item text."}
      </div>}
      {status?.error && <div style={{ color: "var(--red)" }}>{status.error}</div>}
    </div>
  </details>}
  {error && <div role="alert" style={{ color: "var(--red)" }}>{error}</div>}
  </div>;
}
