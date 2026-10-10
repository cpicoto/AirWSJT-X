use tauri::{Emitter, Manager};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::Mutex;
use std::sync::Arc;

pub struct SidecarState {
    pub tx: Option<tauri_plugin_shell::process::CommandChild>,
}

#[tauri::command]
async fn send_to_sidecar(
    message: String,
    state: tauri::State<'_, Arc<Mutex<SidecarState>>>,
) -> Result<(), String> {
    let mut guard = state.lock().await;
    if let Some(child) = &mut guard.tx {
        child
            .write(format!("{}\n", message).as_bytes())
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let sidecar_state = Arc::new(Mutex::new(SidecarState { tx: None }));
            let state_clone = sidecar_state.clone();
            app.manage(sidecar_state);

            // On mobile, skip sidecar — the frontend runs standalone
            #[cfg(not(mobile))]
            {
                let handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let sidecar_cmd = handle
                        .shell()
                        .sidecar("airwsjtx_bridge")
                        .expect("failed to create sidecar command");
                    let (mut rx, child) = sidecar_cmd.spawn().expect("failed to spawn sidecar");

                    {
                        let mut guard = state_clone.lock().await;
                        guard.tx = Some(child);
                    }

                    while let Some(event) = rx.recv().await {
                        match event {
                            CommandEvent::Stdout(line) => {
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
                });
            }
            #[cfg(mobile)]
            let _ = state_clone;

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![send_to_sidecar])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
