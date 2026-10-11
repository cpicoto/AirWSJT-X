use tauri::{Emitter, Manager};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::Mutex;
use std::sync::Arc;
use std::collections::HashMap;
mod credentials;
#[cfg(target_os = "android")]
mod mobile_storage;
#[cfg(any(mobile, test))]
#[cfg_attr(not(mobile), allow(dead_code))]
mod mobile_aircraft;

pub struct SidecarState {
    pub tx: Option<tauri_plugin_shell::process::CommandChild>,
    pub credential_updates: HashMap<String, tokio::sync::oneshot::Sender<Result<(), String>>>,
    pub credential_error: Option<String>,
    pub rate_limits: HashMap<String, serde_json::Value>,
}

/// Frames an RPC message for the sidecar by appending a newline terminator.
/// The sidecar reads newline-delimited JSON from stdin, so every message
/// must end with exactly one `\n`.
pub(crate) fn frame_rpc_message(message: &str) -> String {
    format!("{}\n", message)
}

#[tauri::command]
async fn send_to_sidecar(
    message: String,
    state: tauri::State<'_, Arc<Mutex<SidecarState>>>,
) -> Result<(), String> {
    let mut guard = state.lock().await;
    if let Some(child) = &mut guard.tx {
        child
            .write(frame_rpc_message(&message).as_bytes())
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default();
    #[cfg(mobile)]
    let builder = builder.manage(mobile_aircraft::MobileAircraftState::default());
    #[cfg(target_os = "android")]
    let builder = builder.plugin(mobile_storage::init());
    let builder = builder
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let sidecar_state = Arc::new(Mutex::new(SidecarState {
                tx: None,
                credential_updates: HashMap::new(),
                credential_error: None,
                rate_limits: HashMap::new(),
            }));
            let state_clone = sidecar_state.clone();
            app.manage(sidecar_state);

            // On mobile, skip sidecar — the frontend runs standalone
            #[cfg(not(mobile))]
            {
                if let Some(window) = app.get_webview_window("main") {
                    if let Some(version) = &app.config().version {
                        window.set_title(&format!("AirWSJT-X v{version}"))?;
                    }
                }
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let sidecar_cmd = handle
                        .shell()
                        .sidecar("airwsjtx_bridge")
                        .expect("failed to create sidecar command");
                    let (mut rx, mut child) = sidecar_cmd.spawn().expect("failed to spawn sidecar");

                    let restore_result = credentials::restore(&handle, &mut child);
                    let restore_error = match restore_result {
                        Ok(None) => None,
                        Ok(Some(warning)) => {
                            eprintln!("Aircraft credentials warning: {}", warning);
                            let payload = serde_json::json!({
                                "event": "source_credentials_error", "data": { "message": warning }
                            });
                            let _ = handle.emit("sidecar-message", payload.to_string());
                            Some(warning)
                        }
                        Err(message) => {
                            eprintln!("Aircraft credentials: {}", message);
                            let payload = serde_json::json!({
                                "event": "source_credentials_error", "data": { "message": message }
                            });
                            let _ = handle.emit("sidecar-message", payload.to_string());
                            Some(message)
                        }
                    };

                    {
                        let mut guard = state_clone.lock().await;
                        guard.tx = Some(child);
                        guard.credential_error = restore_error;
                    }

                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line) => {
                                if let Ok(payload) = serde_json::from_slice::<serde_json::Value>(&line) {
                                    let mut guard = state_clone.lock().await;
                                    if let Some(id) = payload.get("id").and_then(|id| id.as_str()) {
                                        if let Some(reply) = guard.credential_updates.remove(id) {
                                            let result = if payload.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                                                Ok(())
                                            } else {
                                                Err("Credentials stored, but the backend rejected the update. Rebuild the sidecar and restart the app.".into())
                                            };
                                            guard.credential_error = result.as_ref().err().cloned();
                                            let _ = reply.send(result);
                                            continue;
                                        }
                                    }
                                    if payload.get("event").and_then(|v| v.as_str()) == Some("source_credentials_error") {
                                        guard.credential_error = Some("Unable to apply saved aircraft credentials. Check the backend and restart the app.".into());
                                    }
                                    if payload.get("event").and_then(|v| v.as_str()) == Some("source_rate_limit_update") {
                                        if let Some(provider) = payload["data"]["provider"].as_str() {
                                            guard.rate_limits.insert(provider.to_string(), payload["data"].clone());
                                        }
                                    }
                                }
                                let _ = handle.emit("sidecar-message", String::from_utf8_lossy(&line).to_string());
                            }
                            CommandEvent::Stderr(line) => {
                                eprintln!("sidecar stderr: {}", String::from_utf8_lossy(&line));
                            }
                            CommandEvent::Error(e) => {
                                eprintln!("sidecar error: {}", e);
                                break;
                            }
                            CommandEvent::Terminated(_) => {
                                eprintln!("sidecar terminated");
                                break;
                            }
                            _ => {}
                        }
                    }
                    let mut guard = state_clone.lock().await;
                    guard.tx = None;
                    guard.credential_updates.clear();
                });
            }
            #[cfg(mobile)]
            let _ = state_clone;

            Ok(())
        });
    #[cfg(not(mobile))]
    let builder = builder.invoke_handler(tauri::generate_handler![
            send_to_sidecar,
            credentials::get_source_credentials_status,
            credentials::save_source_credentials,
            credentials::set_source_poll_interval,
            credentials::get_source_rate_limit_status,
        ]);
    #[cfg(mobile)]
    let builder = builder.invoke_handler(tauri::generate_handler![
        send_to_sidecar,
        credentials::get_source_credentials_status,
        credentials::save_source_credentials,
        credentials::set_source_poll_interval,
        credentials::get_source_rate_limit_status,
        mobile_aircraft::fetch_mobile_aircraft,
    ]);
    builder.run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::frame_rpc_message;

    #[test]
    fn frame_message_appends_newline() {
        assert_eq!(frame_rpc_message("hello"), "hello\n");
    }

    #[test]
    fn frame_message_preserves_json_content() {
        let json = r#"{"id":"1","method":"get_status","params":{}}"#;
        let framed = frame_rpc_message(json);
        assert!(framed.ends_with('\n'));
        assert_eq!(&framed[..framed.len() - 1], json);
    }

    #[test]
    fn frame_message_empty_string_gives_newline_only() {
        assert_eq!(frame_rpc_message(""), "\n");
    }

    #[test]
    fn frame_message_unicode_content_is_preserved() {
        let msg = "日本語テスト";
        let framed = frame_rpc_message(msg);
        assert!(framed.starts_with(msg));
        assert!(framed.ends_with('\n'));
    }

    #[test]
    fn frame_message_does_not_strip_existing_content() {
        // Framing is additive — it always appends exactly one '\n'.
        let framed = frame_rpc_message("a\nb");
        assert_eq!(framed, "a\nb\n");
    }
}
