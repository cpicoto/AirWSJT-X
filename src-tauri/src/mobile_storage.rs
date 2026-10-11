use tauri::{plugin::{Builder, PluginHandle, TauriPlugin}, AppHandle, Manager, Runtime};
use tauri_plugin_secure_storage::{GetItemResponse, OptionsRequest};

struct MobileStorage<R: Runtime>(PluginHandle<R>);

// Keep secret-reading methods native-only; the webview only receives account status.
pub fn init<R: Runtime>() -> TauriPlugin<R> {
    Builder::new("secure-storage")
        .setup(|app, api| {
            #[cfg(target_os = "android")]
            let handle = api.register_android_plugin("nl.thatzokay.secureStorage", "SecureStoragePlugin")?;
            app.manage(MobileStorage(handle));
            Ok(())
        })
        .build()
}

fn options(provider: &str, data: Option<String>) -> OptionsRequest {
    OptionsRequest {
        prefixed_key: Some(format!("com.airwsjtx.app.aircraft.{provider}")),
        data,
        sync: Some(false),
        keychain_access: Some(0),
    }
}

pub fn load(app: &AppHandle, provider: &str) -> Result<Option<String>, String> {
    let result: GetItemResponse = app.state::<MobileStorage<tauri::Wry>>().0
        .run_mobile_plugin("getItem", options(provider, None))
        .map_err(|_| "Cannot read aircraft credentials from secure device storage".to_string())?;
    Ok(result.data)
}

pub fn store(app: &AppHandle, provider: &str, value: Option<String>) -> Result<(), String> {
    let method = if value.is_some() { "setItem" } else { "removeItem" };
    // The Android plugin returns an object rather than its Rust wrapper's boolean.
    app.state::<MobileStorage<tauri::Wry>>().0
        .run_mobile_plugin::<serde_json::Value>(method, options(provider, value.clone()))
        .map_err(|_| "Cannot save/clear aircraft credentials in secure device storage".to_string())?;
    if load(app, provider)? != value {
        return Err("Device storage did not confirm the aircraft credential change".into());
    }
    Ok(())
}
