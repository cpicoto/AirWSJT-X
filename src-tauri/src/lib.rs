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
            if let Some(window) = app.get_webview_window("main") {
                if let Some(icon) = app.default_window_icon().cloned() {
                    let _ = window.set_icon(icon);
                }
            }

            let sidecar_state = Arc::new(Mutex::new(SidecarState { tx: None }));
            let state_clone = sidecar_state.clone();
            app.manage(sidecar_state);

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let sidecar_result = handle
                    .shell()
                    .sidecar("airwsjtx_bridge")
                    .and_then(|cmd| cmd.spawn());

                let (mut rx, child) = match sidecar_result {
                    Ok(spawned) => spawned,
                    Err(sidecar_err) => {
                        eprintln!("failed to spawn packaged sidecar: {}", sidecar_err);

                        let bridge_script = concat!(env!("CARGO_MANIFEST_DIR"), "\\..\\sidecar\\airwsjtx_bridge.py");
                        let mut launched = None;

                        let attempts: [(&str, &[&str]); 3] = [
                            (
                                "C:\\Users\\cpico\\AppData\\Local\\Programs\\Python\\Python312\\python.exe",
                                &[bridge_script],
                            ),
                            ("py", &["-3", bridge_script]),
                            ("python", &[bridge_script]),
                        ];

                        for (program, args) in attempts {
                            match handle.shell().command(program).args(args).spawn() {
                                Ok(spawned) => {
                                    launched = Some(spawned);
                                    eprintln!("launched python bridge via {}", program);
                                    break;
                                }
                                Err(err) => {
                                    eprintln!("python bridge attempt failed ({}): {}", program, err);
                                }
                            }
                        }

                        match launched {
                            Some(spawned) => spawned,
                            None => {
                                eprintln!("python sidecar fallback unavailable");
                                return;
                            }
                        }
                    }
                };

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

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![send_to_sidecar])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
