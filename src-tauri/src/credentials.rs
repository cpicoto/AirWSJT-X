use crate::SidecarState;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::sync::Mutex;
use tauri::{AppHandle, Manager};

#[cfg(not(target_os = "android"))]
const SERVICE: &str = "com.airwsjtx.app.aircraft";
#[cfg(not(mobile))]
static UPDATE_ID: AtomicU64 = AtomicU64::new(0);

#[derive(Serialize, Deserialize)]
pub(crate) struct Credentials {
    pub client_id: String,
    pub secret: String,
}

#[derive(Serialize)]
pub struct CredentialStatus {
    opensky_client_id: String,
    opensky_configured: bool,
    adsbexchange_configured: bool,
    application_error: Option<String>,
    opensky_poll_interval_s: u32,
    adsbexchange_poll_interval_s: u32,
}

fn validate_provider(provider: &str) -> Result<(), String> {
    if provider != "opensky" && provider != "adsbexchange" {
        return Err("Unsupported aircraft credential provider".into());
    }
    Ok(())
}

#[cfg(not(target_os = "android"))]
fn entry(provider: &str) -> Result<keyring::Entry, String> {
    validate_provider(provider)?;
    keyring::Entry::new(SERVICE, provider)
        .map_err(|_| "Cannot access the OS credential store".into())
}

pub(crate) fn load(app: &AppHandle, provider: &str) -> Result<Option<Credentials>, String> {
    validate_provider(provider)?;
    #[cfg(target_os = "android")]
    let encoded = crate::mobile_storage::load(app, provider)?;
    #[cfg(not(target_os = "android"))]
    let encoded = {
        let _ = app;
        match entry(provider)?.get_password() {
            Ok(value) => Some(value),
            Err(keyring::Error::NoEntry) => None,
            Err(e) => return Err(format!("Cannot read {provider} credentials from the OS credential store: {e}")),
        }
    };
    encoded.map(|value| serde_json::from_str(&value)
        .map_err(|_| format!("Invalid {provider} credential-store entry"))).transpose()
}

/// Like `load`, but treats keychain access errors as "no credentials" rather than
/// a fatal error. On macOS the Keychain can return `PlatformFailure` (locked,
/// permission-prompt dismissed, Gatekeeper restrictions) even when no entry
/// exists. Falling back to anonymous/empty credentials is always safer than
/// blocking the entire aircraft backend.
#[cfg(not(target_os = "android"))]
fn load_or_none(app: &AppHandle, provider: &str) -> (Option<Credentials>, Option<String>) {
    match load(app, provider) {
        Ok(v) => (v, None),
        Err(e) => {
            eprintln!("aircraft credentials ({provider}): {e}");
            (None, Some(e))
        }
    }
}

#[derive(Serialize, Deserialize)]
#[serde(default)]
struct PollIntervals {
    opensky: u32,
    adsbexchange: u32,
}

impl Default for PollIntervals {
    fn default() -> Self { Self { opensky: 30, adsbexchange: 30 } }
}

fn validate_interval(provider: &str, seconds: u32) -> Result<(), String> {
    validate_provider(provider)?;
    let minimum = if provider == "opensky" { 10 } else { 5 };
    if seconds < minimum {
        return Err(format!("{provider} poll interval must be at least {minimum} seconds"));
    }
    Ok(())
}

fn intervals(app: &AppHandle) -> Result<PollIntervals, String> {
    let path = app.path().app_config_dir().map_err(|_| "Cannot locate aircraft settings")?
        .join("aircraft-poll-intervals.json");
    match std::fs::read(path) {
        Ok(bytes) => {
            let value: PollIntervals = serde_json::from_slice(&bytes).map_err(|_| "Invalid aircraft poll settings")?;
            validate_interval("opensky", value.opensky)?;
            validate_interval("adsbexchange", value.adsbexchange)?;
            Ok(value)
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(PollIntervals::default()),
        Err(_) => Err("Cannot read aircraft poll settings".into()),
    }
}

#[cfg(not(mobile))]
fn message(provider: &str, value: Option<&Credentials>, id: Option<&str>, poll_interval_s: u32) -> Result<Vec<u8>, String> {
    let payload = serde_json::json!({
        "id": id,
        "method": "source_credentials_set",
        "params": {
            "provider": provider,
            "client_id": value.map(|v| v.client_id.as_str()).unwrap_or(""),
            "secret": value.map(|v| v.secret.as_str()).unwrap_or(""),
            "poll_interval_s": poll_interval_s,
        }
    });
    let mut bytes = serde_json::to_vec(&payload)
        .map_err(|_| "Cannot encode aircraft credentials".to_string())?;
    bytes.push(b'\n');
    Ok(bytes)
}

#[cfg(not(mobile))]
pub fn restore(app: &AppHandle, child: &mut tauri_plugin_shell::process::CommandChild) -> Result<Option<String>, String> {
    let rates = intervals(app)?;
    let mut credential_warning: Option<String> = None;
    for provider in ["opensky", "adsbexchange"] {
        // Use load_or_none so that a macOS Keychain access error (PlatformFailure,
        // locked keychain, Gatekeeper prompt dismissed) falls back to anonymous
        // access instead of aborting startup entirely.
        let (value, warning) = load_or_none(app, provider);
        if credential_warning.is_none() { credential_warning = warning; }
        let seconds = if provider == "opensky" { rates.opensky } else { rates.adsbexchange };
        child.write(&message(provider, value.as_ref(), None, seconds)?)
            .map_err(|_| "Cannot restore aircraft settings to the backend".to_string())?;
    }
    Ok(credential_warning)
}

#[tauri::command]
pub async fn get_source_credentials_status(
    app: AppHandle,
    state: tauri::State<'_, Arc<Mutex<SidecarState>>>,
) -> Result<CredentialStatus, String> {
    // Use load_or_none so a macOS Keychain access error surfaces as
    // application_error rather than making the whole command fail.
    #[cfg(not(target_os = "android"))]
    let (opensky, load_error) = load_or_none(&app, "opensky");
    #[cfg(target_os = "android")]
    let (opensky, load_error) = (load(&app, "opensky")?, None::<String>);
    #[cfg(not(target_os = "android"))]
    let (adsbexchange, _) = load_or_none(&app, "adsbexchange");
    #[cfg(target_os = "android")]
    let adsbexchange = load(&app, "adsbexchange")?;
    let rates = intervals(&app)?;
    let sidecar_error = state.lock().await.credential_error.clone();
    Ok(CredentialStatus {
        opensky_client_id: opensky.as_ref().map(|v| v.client_id.clone()).unwrap_or_default(),
        opensky_configured: opensky.is_some(),
        adsbexchange_configured: adsbexchange.is_some(),
        application_error: load_error.or(sidecar_error),
        opensky_poll_interval_s: rates.opensky,
        adsbexchange_poll_interval_s: rates.adsbexchange,
    })
}

#[tauri::command]
pub async fn save_source_credentials(
    app: AppHandle,
    provider: String,
    client_id: String,
    secret: Option<String>,
    state: tauri::State<'_, Arc<Mutex<SidecarState>>>,
) -> Result<(), String> {
    validate_provider(&provider)?;
    #[cfg(not(mobile))]
    if state.lock().await.tx.is_none() {
        return Err("Aircraft backend is not ready".into());
    }

    let value = if let Some(secret) = secret {
        let client_id = client_id.trim().to_string();
        if secret.trim().is_empty() || (provider == "opensky" && client_id.is_empty()) {
            return Err("Enter all required credential fields".into());
        }
        Some(Credentials { client_id, secret: secret.trim().to_string() })
    } else {
        None
    };
    let encoded = value.as_ref().map(serde_json::to_string).transpose()
        .map_err(|_| "Cannot encode aircraft credentials".to_string())?;
    #[cfg(target_os = "android")]
    crate::mobile_storage::store(&app, &provider, encoded)?;
    #[cfg(not(target_os = "android"))]
    {
        let credential_entry = entry(&provider)?;
        if let Some(encoded) = encoded {
            credential_entry.set_password(&encoded)
                .map_err(|_| "Cannot save credentials in the OS credential store".to_string())?;
        } else {
            match credential_entry.delete_credential() {
                Ok(()) | Err(keyring::Error::NoEntry) => {}
                Err(_) => return Err("Cannot clear credentials from the OS credential store".into()),
            }
        }
    }
    #[cfg(mobile)]
    {
        app.state::<crate::mobile_aircraft::MobileAircraftState>().invalidate(&provider).await;
        state.lock().await.credential_error = None;
        Ok(())
    }
    #[cfg(not(mobile))]
    {
        let rates = intervals(&app)?;
        let seconds = if provider == "opensky" { rates.opensky } else { rates.adsbexchange };
        apply(&state, |id| message(&provider, value.as_ref(), Some(id), seconds)).await
    }
}

#[cfg(not(mobile))]
async fn apply(
    state: &Arc<Mutex<SidecarState>>,
    encode: impl FnOnce(&str) -> Result<Vec<u8>, String>,
) -> Result<(), String> {
    let mut guard = state.lock().await;
    if guard.tx.is_none() {
        return Err("Aircraft settings saved, but backend is not ready. Restart the app.".into());
    }
    let id = format!("credential-update-{}", UPDATE_ID.fetch_add(1, Ordering::Relaxed));
    let bytes = encode(&id)?;
    let (reply, response) = tokio::sync::oneshot::channel();
    guard.credential_updates.insert(id.clone(), reply);
    if guard.tx.as_mut().ok_or("Aircraft backend is not ready")?.write(&bytes).is_err() {
        guard.credential_updates.remove(&id);
        let error = "Credentials stored, but backend update failed. Restart the app.".to_string();
        guard.credential_error = Some(error.clone());
        return Err(error);
    }
    drop(guard);
    match tokio::time::timeout(Duration::from_secs(10), response).await {
        Ok(Ok(result)) => result,
        _ => {
            let mut guard = state.lock().await;
            guard.credential_updates.remove(&id);
            let error = "Credentials stored, but the backend did not confirm the update. Rebuild the sidecar and restart the app.".to_string();
            guard.credential_error = Some(error.clone());
            Err(error)
        }
    }
}

#[tauri::command]
pub async fn get_source_rate_limit_status(
    app: AppHandle,
    state: tauri::State<'_, Arc<Mutex<SidecarState>>>,
) -> Result<serde_json::Value, String> {
    #[cfg(mobile)]
    {
        let _ = state;
        Ok(app.state::<crate::mobile_aircraft::MobileAircraftState>().quota_status().await)
    }
    #[cfg(not(mobile))]
    {
        let _ = app;
        Ok(serde_json::json!(state.lock().await.rate_limits))
    }
}

#[tauri::command]
pub async fn set_source_poll_interval(
    app: AppHandle, provider: String, seconds: u32,
    state: tauri::State<'_, Arc<Mutex<SidecarState>>>,
) -> Result<(), String> {
    validate_interval(&provider, seconds)?;
    let mut rates = intervals(&app)?;
    if provider == "opensky" { rates.opensky = seconds; } else { rates.adsbexchange = seconds; }
    let directory = app.path().app_config_dir().map_err(|_| "Cannot locate aircraft settings")?;
    std::fs::create_dir_all(&directory).map_err(|_| "Cannot create aircraft settings directory")?;
    let bytes = serde_json::to_vec(&rates).map_err(|_| "Cannot encode aircraft poll settings")?;
    std::fs::write(directory.join("aircraft-poll-intervals.json"), bytes)
        .map_err(|_| "Cannot save aircraft poll settings")?;
    #[cfg(not(mobile))]
    {
        apply(&state, |id| {
            let mut bytes = serde_json::to_vec(&serde_json::json!({
                "id": id, "method": "source_poll_interval_set",
                "params": { "provider": provider, "seconds": seconds }
            })).map_err(|_| "Cannot encode poll settings")?;
            bytes.push(b'\n');
            Ok(bytes)
        }).await
    }
    #[cfg(mobile)]
    {
        let _ = state;
        Ok(())
    }
}

#[cfg(all(test, not(mobile)))]
mod tests {
    use super::*;

    #[test]
    fn internal_update_is_newline_delimited_and_acknowledged() {
        let value = Credentials { client_id: "test-client".into(), secret: "fake-test-secret".into() };
        let bytes = message("opensky", Some(&value), Some("credential-update-1"), 30).unwrap();
        assert_eq!(bytes.last(), Some(&b'\n'));
        let payload: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(payload["id"], "credential-update-1");
        assert_eq!(payload["params"]["client_id"], "test-client");
        assert_eq!(payload["params"]["secret"], "fake-test-secret");
    }

    #[test]
    fn clearing_sends_empty_credentials() {
        let payload: serde_json::Value = serde_json::from_slice(&message("opensky", None, None, 30).unwrap()).unwrap();
        assert_eq!(payload["params"]["client_id"], "");
        assert_eq!(payload["params"]["secret"], "");
    }

    #[test]
    fn rejects_unknown_provider_without_accessing_os_store() {
        assert!(entry("unknown").is_err());
    }

    #[test]
    fn poll_settings_default_to_thirty_seconds_and_enforce_provider_minimums() {
        let defaults = PollIntervals::default();
        assert_eq!(defaults.opensky, 30);
        assert_eq!(defaults.adsbexchange, 30);
        assert!(validate_interval("opensky", 9).is_err());
        assert!(validate_interval("opensky", 10).is_ok());
        assert!(validate_interval("adsbexchange", 4).is_err());
        assert!(validate_interval("adsbexchange", 5).is_ok());
        assert!(validate_interval("unknown", 30).is_err());
        let encoded = serde_json::to_string(&PollIntervals { opensky: 10, adsbexchange: 5 }).unwrap();
        let restored: PollIntervals = serde_json::from_str(&encoded).unwrap();
        assert_eq!(restored.opensky, 10);
        assert_eq!(restored.adsbexchange, 5);
    }
}
