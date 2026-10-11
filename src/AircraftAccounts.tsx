import { invoke } from "@tauri-apps/api/core";
import { useEffect, useState } from "react";

interface CredentialStatus {
  opensky_client_id: string;
  opensky_configured: boolean;
  adsbexchange_configured: boolean;
  application_error?: string | null;
  opensky_poll_interval_s: number;
  adsbexchange_poll_interval_s: number;
}

interface QuotaStatus {
  remaining: number | null;
  checked_epoch_s: number | null;
  retry_epoch_s: number | null;
  check_interval_s: number;
  error?: string | null;
}

function QuotaInfo({ provider, value }: { provider: "opensky" | "adsbexchange"; value?: QuotaStatus }) {
  return <div style={{ fontSize: 11 }}>
    {provider === "adsbexchange" ? "Remaining quota: not exposed by the documented API"
      : `Remaining credits: ${value?.remaining ?? "not reported"}`}
    {provider === "opensky" && value?.checked_epoch_s != null
      && ` · checked ${new Date(value.checked_epoch_s * 1000).toLocaleTimeString()} · refresh ${value.check_interval_s / 60} min`}
    {value?.retry_epoch_s != null && ` · retry no earlier than ${new Date(value.retry_epoch_s * 1000).toLocaleString()}`}
    {value?.error && <div>{value.error}</div>}
  </div>;
}

export default function AircraftAccounts({ onLog, onChanged }: {
  onLog: (message: string) => void; onChanged?: () => void;
}) {
  const [status, setStatus] = useState<CredentialStatus | null>(null);
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [openSkyInterval, setOpenSkyInterval] = useState("30");
  const [adsbInterval, setAdsbInterval] = useState("30");
  const [quotas, setQuotas] = useState<Partial<Record<"opensky" | "adsbexchange", QuotaStatus>>>({});

  useEffect(() => {
    let disposed = false;
    const refresh = () => {
      invoke<Partial<Record<"opensky" | "adsbexchange", QuotaStatus>>>("get_source_rate_limit_status")
        .then(value => { if (!disposed) setQuotas(value); })
        .catch(() => { if (!disposed) onLog("Cannot read aircraft API quota status."); });
    };
    refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [onLog]);

  useEffect(() => {
    invoke<CredentialStatus>("get_source_credentials_status")
      .then(value => {
        setStatus(value);
        setClientId(value.opensky_client_id);
        setOpenSkyInterval(String(value.opensky_poll_interval_s));
        setAdsbInterval(String(value.adsbexchange_poll_interval_s));
        if (value.application_error) {
          setMessage(value.application_error);
          onLog(value.application_error);
        }
      })
      .catch(() => {
        const text = "Cannot read aircraft accounts from the OS credential store";
        setMessage(text);
        onLog(text);
      });
  }, [onLog]);

  async function save(provider: "opensky" | "adsbexchange", clear = false) {
    const secret = provider === "opensky" ? clientSecret : apiKey;
    if (!clear && (!secret.trim() || (provider === "opensky" && !clientId.trim()))) {
      setMessage("Paste all required account fields before saving.");
      return;
    }
    setBusy(true);
    try {
      await invoke("save_source_credentials", {
        provider, clientId: provider === "opensky" ? clientId : "",
        secret: clear ? null : secret,
      });
      if (provider === "opensky") setClientSecret("");
      else setApiKey("");
      const value = await invoke<CredentialStatus>("get_source_credentials_status");
      setStatus(value);
      setClientId(value.opensky_client_id);
      onChanged?.();
      const text = `${provider === "opensky" ? "OpenSky" : "ADS-B Exchange"} account ${clear ? "cleared" : "saved securely"} and applied. See aircraft source status for connection results.`;
      setMessage(text);
      onLog(text);
    } catch (error) {
      const text = typeof error === "string" ? error : "Unable to save/apply aircraft credentials. Check OS credential-store access and backend availability.";
      setMessage(text);
      onLog(text);
      try {
        const value = await invoke<CredentialStatus>("get_source_credentials_status");
        setStatus(value);
      } catch {
        onLog("Cannot refresh aircraft account status from the OS credential store.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function setInterval(provider: "opensky" | "adsbexchange") {
    const seconds = Number(provider === "opensky" ? openSkyInterval : adsbInterval);
    const minimum = provider === "opensky" ? 10 : 5;
    if (!Number.isInteger(seconds) || seconds < minimum || seconds > 4294967295) {
      setMessage(`Enter a whole-number poll interval of at least ${minimum} seconds.`);
      return;
    }
    setBusy(true);
    try {
      await invoke("set_source_poll_interval", { provider, seconds });
      onChanged?.();
      const text = `${provider === "opensky" ? "OpenSky" : "ADS-B Exchange"} poll interval saved: ${seconds} seconds. Rate-limit backoff still applies.`;
      setMessage(text);
      onLog(text);
    } catch (error) {
      const text = typeof error === "string" ? error : "Cannot save/apply aircraft poll interval.";
      setMessage(text);
      onLog(text);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="aircraft-accounts">
      <summary>Aircraft accounts</summary>
      <div className="account-fields">
        <strong>OpenSky {status ? (status.opensky_configured ? "(account saved)" : "(anonymous)") : "(account status unavailable)"}</strong>
        <QuotaInfo provider="opensky" value={quotas.opensky} />
        <label>OpenSky Client ID
          <input value={clientId} onChange={e => setClientId(e.target.value)} autoComplete="off" disabled={busy} />
        </label>
        <label>OpenSky Client Secret
          <input type="password" value={clientSecret} onChange={e => setClientSecret(e.target.value)}
            autoComplete="new-password" placeholder={status?.opensky_configured ? "Saved - paste to replace" : "Paste clientSecret"}
            disabled={busy} />
        </label>
        <div className="account-poll-rate">
          <label>OpenSky poll interval (seconds)
            <input type="number" min={10} step={1} value={openSkyInterval}
              onChange={e => setOpenSkyInterval(e.target.value)} disabled={busy} />
          </label>
          <button className="btn-secondary" disabled={busy} onClick={() => { void setInterval("opensky"); }}>Set OpenSky poll rate</button>
        </div>
        <div>
          <button className="btn-secondary" disabled={busy} onClick={() => { void save("opensky"); }}>Save OpenSky</button>
          <button className="btn-secondary" disabled={busy} onClick={() => { void save("opensky", true); }}>Clear OpenSky</button>
        </div>
        <strong>ADS-B Exchange {status?.adsbexchange_configured ? "(API key saved)" : ""}</strong>
        <QuotaInfo provider="adsbexchange" value={quotas.adsbexchange} />
        <label>ADS-B Exchange API key
          <input type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
            autoComplete="new-password" placeholder={status?.adsbexchange_configured ? "Saved - paste to replace" : "Paste API key"}
            disabled={busy} />
        </label>
        <div className="account-poll-rate">
          <label>ADS-B Exchange poll interval (seconds)
            <input type="number" min={5} step={1} value={adsbInterval}
              onChange={e => setAdsbInterval(e.target.value)} disabled={busy} />
          </label>
          <button className="btn-secondary" disabled={busy} onClick={() => { void setInterval("adsbexchange"); }}>Set ADS-B Exchange poll rate</button>
        </div>
        <div>
          <button className="btn-secondary" disabled={busy} onClick={() => { void save("adsbexchange"); }}>Save ADS-B Exchange</button>
          <button className="btn-secondary" disabled={busy} onClick={() => { void save("adsbexchange", true); }}>Clear ADS-B Exchange</button>
        </div>
        <span>Secrets use Windows Credential Manager, Apple Keychain, Linux Secret Service, or Android Keystore-protected storage, not browser settings. Poll rates save separately and default to 30 seconds. Clearing a saved ADS-B key on desktop still allows an environment-variable key.</span>
        {message && <div role="status">{message}</div>}
      </div>
    </details>
  );
}
