//! Local RTL-SDR ADS-B receiver for Android (USB OTG).
//!
//! The Kotlin `RtlSdrBridge` obtains USB permission and passes the opened
//! `UsbDeviceConnection` file descriptor here. The receiver thread runs the
//! bundled librtlsdr + dump1090-fa demodulator (`scripts/adsb_receiver.c`) and
//! feeds DF17 frames into the shared Rust ADS-B decoder.

use crate::adsb::Decoder;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::Instant;
use tokio::sync::Mutex;

#[derive(Default)]
struct Shared {
    decoder: StdMutex<Decoder>,
    report: StdMutex<Option<serde_json::Value>>,
    log: StdMutex<Vec<String>>,
    finished: StdMutex<Option<i32>>,
}

struct Receiver {
    shared: Arc<Shared>,
    started: Instant,
    thread: Option<std::thread::JoinHandle<()>>,
}

#[derive(Default)]
pub struct MobileRtlSdrState(Mutex<Option<Receiver>>);

const LOG_LINES: usize = 20;

impl Shared {
    fn push_log(&self, line: &str) {
        let line = line.trim();
        if line.is_empty() {
            return;
        }
        #[cfg(target_os = "android")]
        native::logcat(line);
        let mut log = self.log.lock().unwrap_or_else(|e| e.into_inner());
        log.push(line.to_string());
        let excess = log.len().saturating_sub(LOG_LINES);
        log.drain(..excess);
    }
}

fn snapshot(receiver: &Receiver) -> serde_json::Value {
    let now = receiver.started.elapsed().as_secs_f64();
    let shared = &receiver.shared;
    let mut decoder = shared.decoder.lock().unwrap_or_else(|e| e.into_inner());
    let aircraft = decoder.snapshot(now);
    let finished = *shared.finished.lock().unwrap_or_else(|e| e.into_inner());
    serde_json::json!({
        "aircraft": aircraft,
        "running": finished.is_none(),
        "exit_code": finished,
        "frames": decoder.frames,
        "valid_messages": decoder.valid_messages,
        "crc_errors": decoder.crc_errors,
        "positions": decoder.positions,
        "report": shared.report.lock().unwrap_or_else(|e| e.into_inner()).clone(),
        "log": shared.log.lock().unwrap_or_else(|e| e.into_inner()).clone(),
    })
}

#[cfg(target_os = "android")]
mod native {
    use super::Shared;
    use std::ffi::{c_char, c_void, CStr};

    type FrameFn = extern "C" fn(*mut c_void, *const c_char, u64);
    type TextFn = extern "C" fn(*mut c_void, *const c_char);

    extern "C" {
        fn airwsjtx_adsb_run(
            fd: isize, manual: i32, gain_db: f64,
            on_frame: FrameFn, on_report: TextFn, on_log: TextFn, ctx: *mut c_void,
        ) -> i32;
        fn airwsjtx_adsb_prepare() -> i32;
        fn airwsjtx_adsb_stop();
        fn __android_log_write(priority: i32, tag: *const c_char, text: *const c_char) -> i32;
    }

    pub fn logcat(line: &str) {
        if let Ok(line) = std::ffi::CString::new(line) {
            unsafe { __android_log_write(4, c"AirWSJTX-RTLSDR".as_ptr(), line.as_ptr()) };
        }
    }

    pub struct Context {
        pub shared: std::sync::Arc<Shared>,
        pub started: std::time::Instant,
    }

    fn text(value: *const c_char) -> Option<String> {
        (!value.is_null()).then(|| unsafe { CStr::from_ptr(value) }.to_string_lossy().into_owned())
    }

    extern "C" fn on_frame(ctx: *mut c_void, hex: *const c_char, _timestamp: u64) {
        let context = unsafe { &*(ctx as *const Context) };
        if let Some(hex) = text(hex) {
            let now = context.started.elapsed().as_secs_f64();
            context.shared.decoder.lock().unwrap_or_else(|e| e.into_inner()).consume_hex(&hex, now);
        }
    }

    extern "C" fn on_report(ctx: *mut c_void, json: *const c_char) {
        let context = unsafe { &*(ctx as *const Context) };
        if let Some(value) = text(json).and_then(|json| serde_json::from_str(&json).ok()) {
            *context.shared.report.lock().unwrap_or_else(|e| e.into_inner()) = Some(value);
        }
    }

    extern "C" fn on_log(ctx: *mut c_void, line: *const c_char) {
        let context = unsafe { &*(ctx as *const Context) };
        if let Some(line) = text(line) {
            context.shared.push_log(&line);
        }
    }

    /// Runs the receiver until stopped. Blocks the calling thread.
    pub fn run(context: Box<Context>, fd: i32, gain_db: Option<f64>) -> i32 {
        let raw = Box::into_raw(context);
        let code = unsafe {
            airwsjtx_adsb_run(
                fd as isize, i32::from(gain_db.is_some()), gain_db.unwrap_or(0.0),
                on_frame, on_report, on_log, raw.cast(),
            )
        };
        // A stuck USB reader can still hold the context; leak it rather than risk use-after-free.
        if code != 2 {
            drop(unsafe { Box::from_raw(raw) });
        }
        code
    }

    pub fn stop() {
        unsafe { airwsjtx_adsb_stop() }
    }

    pub fn prepare() -> bool {
        unsafe { airwsjtx_adsb_prepare() != 0 }
    }
}

fn stop_receiver(receiver: &mut Receiver) -> Option<i32> {
    if let Some(thread) = receiver.thread.take() {
        #[cfg(target_os = "android")]
        native::stop();
        let _ = thread.join();
    }
    *receiver.shared.finished.lock().unwrap_or_else(|e| e.into_inner())
}

impl Drop for Receiver {
    fn drop(&mut self) {
        let _ = stop_receiver(self);
    }
}

async fn stop_existing(slot: &mut Option<Receiver>) -> Result<(), String> {
    if let Some(mut receiver) = slot.take() {
        let (receiver, finished) = tauri::async_runtime::spawn_blocking(move || {
            let finished = stop_receiver(&mut receiver);
            (receiver, finished)
        })
        .await
        .map_err(|e| e.to_string())?;
        if finished == Some(2) {
            // The detached libusb reader still owns the wrapped Android file
            // descriptor. Retain the receiver marker so no later start can
            // close/reuse that descriptor while native code is still using it.
            *slot = Some(receiver);
            return Err("The RTL-SDR USB reader is stuck; restart the app before reconnecting the device.".into());
        }
    }
    Ok(())
}

/// Stops any prior worker and proves native USB state is safe before the
/// WebView opens (or replaces) Android's UsbDeviceConnection.
#[tauri::command]
pub async fn prepare_mobile_rtlsdr(state: tauri::State<'_, MobileRtlSdrState>) -> Result<(), String> {
    let mut guard = state.0.lock().await;
    stop_existing(&mut guard).await?;
    #[cfg(target_os = "android")]
    {
        if !native::prepare() {
            return Err("The previous RTL-SDR reader did not shut down; restart the app before reconnecting the device.".into());
        }
        Ok(())
    }
    #[cfg(not(target_os = "android"))]
    Err("Local RTL-SDR over USB is only available on Android.".into())
}

/// Starts receiving from an already-opened Android USB file descriptor.
#[tauri::command]
pub async fn start_mobile_rtlsdr(
    state: tauri::State<'_, MobileRtlSdrState>,
    fd: i32,
    gain_db: Option<f64>,
) -> Result<(), String> {
    if fd < 0 {
        return Err("Invalid RTL-SDR USB file descriptor.".into());
    }
    if let Some(gain) = gain_db {
        if !gain.is_finite() || !(0.0..=60.0).contains(&gain) {
            return Err("RTL-SDR gain must be between 0 and 60 dB.".into());
        }
    }
    // The async mutex is deliberately held until the previous native worker has
    // joined and the replacement is published. This serializes every start/stop
    // transition without blocking an async-runtime worker on the native join.
    let mut guard = state.0.lock().await;
    stop_existing(&mut guard).await?;
    let shared = Arc::new(Shared::default());
    let started = Instant::now();
    #[cfg(target_os = "android")]
    let thread = {
        // Clear stale cancellation before spawning. A stop cannot pass the state
        // mutex until the new Receiver is stored, so cancellation after this call
        // cannot be lost by the C worker during startup.
        if !native::prepare() {
            return Err("The previous RTL-SDR reader did not shut down; reconnect the device.".into());
        }
        let context = Box::new(native::Context { shared: shared.clone(), started });
        let thread_shared = shared.clone();
        std::thread::Builder::new()
            .name("rtlsdr-adsb".into())
            .spawn(move || {
                let code = native::run(context, fd, gain_db);
                if code != 0 {
                    thread_shared.push_log(&format!("Receiver stopped with code {code}"));
                }
                *thread_shared.finished.lock().unwrap_or_else(|e| e.into_inner()) = Some(code);
            })
            .map_err(|e| e.to_string())?
    };
    #[cfg(not(target_os = "android"))]
    {
        let _ = (shared, started, guard);
        Err("Local RTL-SDR over USB is only available on Android.".into())
    }
    #[cfg(target_os = "android")]
    {
        *guard = Some(Receiver { shared, started, thread: Some(thread) });
        Ok(())
    }
}

#[tauri::command]
pub async fn fetch_mobile_rtlsdr(state: tauri::State<'_, MobileRtlSdrState>) -> Result<serde_json::Value, String> {
    let guard = state.0.lock().await;
    guard.as_ref().map(snapshot).ok_or_else(|| "The RTL-SDR receiver is not running.".into())
}

#[tauri::command]
pub async fn stop_mobile_rtlsdr(state: tauri::State<'_, MobileRtlSdrState>) -> Result<(), String> {
    // Keep the lifecycle mutex until join completes. A new start therefore
    // cannot observe an empty slot while the old receiver still owns native/USB
    // state, and an old Drop cannot race a replacement.
    let mut guard = state.0.lock().await;
    stop_existing(&mut guard).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_reports_decoder_counters_and_aircraft() {
        let receiver = Receiver { shared: Arc::new(Shared::default()), started: Instant::now(), thread: None };
        receiver.shared.decoder.lock().unwrap().consume_hex("8D4840D6202CC371C32CE0576098", 0.0);
        for i in 0..25 {
            receiver.shared.push_log(&format!("line {i}"));
        }
        let value = snapshot(&receiver);
        assert_eq!(value["running"], true);
        assert_eq!(value["valid_messages"], 1);
        assert_eq!(value["aircraft"][0]["callsign"], "KLM1023");
        assert_eq!(value["log"].as_array().unwrap().len(), LOG_LINES);
        assert_eq!(value["log"][0], "line 5");
    }
}
