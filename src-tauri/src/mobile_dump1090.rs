use std::collections::HashMap;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::Mutex;

const SBS_PORT: u16 = 30003;
const STALE_AFTER: Duration = Duration::from_secs(60);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const IDLE_RECONNECT: Duration = Duration::from_secs(120);
const FIRST_DATA_WAIT: Duration = Duration::from_secs(1);
const RETRY_DELAY: Duration = Duration::from_secs(5);

#[derive(Clone)]
enum Status {
    Connecting,
    Connected,
    Failed(String),
}

#[derive(Default)]
struct Track {
    callsign: Option<String>,
    lat: Option<f64>,
    lon: Option<f64>,
    altitude_m: Option<f64>,
    velocity_ms: Option<f64>,
    heading_deg: Option<f64>,
    vertical_rate_ms: Option<f64>,
    on_ground: bool,
    last_seen: Option<Instant>,
    last_position: Option<Instant>,
}

struct Shared {
    aircraft: StdMutex<HashMap<String, Track>>,
    status: StdMutex<Status>,
}

struct Client {
    host: String,
    shared: Arc<Shared>,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for Client {
    fn drop(&mut self) { self.task.abort(); }
}

#[derive(Default)]
pub struct MobileDump1090State(Mutex<Option<Client>>);

fn validate_host(host: &str) -> Result<&str, String> {
    let host = host.trim();
    if host.is_empty() || host.len() > 253
        || !host.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-')) {
        return Err("Enter a valid IP address or hostname for dump1090.".into());
    }
    Ok(host)
}

fn coerce_float(value: Option<&&str>) -> Option<f64> {
    value.and_then(|text| text.trim().parse::<f64>().ok()).filter(|v| v.is_finite())
}

fn decode_callsign(raw: &str) -> Option<String> {
    let upper = raw.trim().to_ascii_uppercase();
    if (2..=8).contains(&upper.len()) && upper.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        Some(upper.replace('_', " ").trim().to_string())
    } else {
        None
    }
}

/// Applies one SBS (BaseStation) line, mirroring the desktop `Dump1090Feed` parser.
fn apply_sbs_line(aircraft: &mut HashMap<String, Track>, line: &str, now: Instant) {
    let clean = line.trim().trim_matches('\0');
    let clean = clean.strip_prefix('*').unwrap_or(clean);
    let parts: Vec<&str> = clean.split(',').map(str::trim).collect();
    if parts.len() < 5 || !matches!(parts[0], "MSG" | "ES") { return; }
    let icao24 = parts[4].to_ascii_lowercase();
    if icao24.is_empty() { return; }

    let track = aircraft.entry(icao24).or_default();
    track.last_seen = Some(now);
    if let Some(callsign) = parts.get(10).and_then(|raw| decode_callsign(raw)) {
        track.callsign = Some(callsign);
    }
    match parts[1] {
        "3" => {
            if let Some(feet) = coerce_float(parts.get(11)) { track.altitude_m = Some(feet * 0.3048); }
            if let (Some(lat), Some(lon)) = (coerce_float(parts.get(14)), coerce_float(parts.get(15))) {
                track.lat = Some(lat);
                track.lon = Some(lon);
                track.last_position = Some(now);
            }
            if let Some(flag) = parts.get(21) { track.on_ground = flag.trim() == "1"; }
        }
        "4" => {
            if let Some(knots) = coerce_float(parts.get(12)) { track.velocity_ms = Some(knots * 0.514444); }
            if let Some(heading) = coerce_float(parts.get(13)) { track.heading_deg = Some(heading); }
            if let Some(rate) = coerce_float(parts.get(16)) { track.vertical_rate_ms = Some(rate * 0.00508); }
        }
        _ => {}
    }
}

fn set_status(shared: &Shared, status: Status) {
    if let Ok(mut guard) = shared.status.lock() { *guard = status; }
}

/// A shared-receiver host that is serving another program sends `BUSY,<reason>`
/// and then closes the connection.
fn busy_reason(line: &str) -> Option<String> {
    let reason = line.trim().trim_matches('\0').strip_prefix("BUSY,")?.trim();
    Some(if reason.is_empty() { "receiver busy".into() } else { reason.into() })
}

async fn run(host: String, shared: Arc<Shared>) {
    loop {
        match tokio::time::timeout(CONNECT_TIMEOUT, TcpStream::connect((host.as_str(), SBS_PORT))).await {
            Ok(Ok(stream)) => {
                let mut reader = BufReader::new(stream);
                let mut buffer = Vec::new();
                let mut end = Status::Failed(format!("Connection to dump1090 at {host}:{SBS_PORT} was lost"));
                // A host that refuses us closes right away or says why first, so only
                // report "connected" once it has not. An idle receiver stays quiet.
                let first = tokio::time::timeout(FIRST_DATA_WAIT, reader.read_until(b'\n', &mut buffer)).await;
                let refused = match first {
                    Ok(Ok(0)) | Ok(Err(_)) => {
                        end = Status::Failed(format!("dump1090 at {host}:{SBS_PORT} closed the connection"));
                        true
                    }
                    Ok(Ok(_)) => {
                        let text = String::from_utf8_lossy(&buffer).into_owned();
                        buffer.clear();
                        match busy_reason(&text) {
                            Some(reason) => {
                                end = Status::Failed(format!("dump1090 busy: {reason}"));
                                true
                            }
                            None => {
                                if let Ok(mut aircraft) = shared.aircraft.lock() {
                                    apply_sbs_line(&mut aircraft, &text, Instant::now());
                                }
                                false
                            }
                        }
                    }
                    Err(_) => false,
                };
                if !refused {
                    set_status(&shared, Status::Connected);
                    // Keep any bytes already read; a timed-out read can be resumed.
                    loop {
                        match tokio::time::timeout(IDLE_RECONNECT, reader.read_until(b'\n', &mut buffer)).await {
                            Ok(Ok(0)) | Ok(Err(_)) | Err(_) => break,
                            Ok(Ok(_)) => {
                                let line = String::from_utf8_lossy(&buffer);
                                if let Ok(mut aircraft) = shared.aircraft.lock() {
                                    apply_sbs_line(&mut aircraft, &line, Instant::now());
                                }
                                buffer.clear();
                            }
                        }
                    }
                }
                set_status(&shared, end);
            }
            Ok(Err(error)) => set_status(&shared, Status::Failed(
                format!("Cannot connect to dump1090 at {host}:{SBS_PORT}: {error}"))),
            Err(_) => set_status(&shared, Status::Failed(
                format!("Timed out connecting to dump1090 at {host}:{SBS_PORT}"))),
        }
        tokio::time::sleep(RETRY_DELAY).await;
    }
}

fn snapshot(shared: &Shared, now: Instant) -> serde_json::Value {
    let mut aircraft = shared.aircraft.lock().unwrap_or_else(|e| e.into_inner());
    aircraft.retain(|_, track| track.last_seen.is_some_and(|seen| now.duration_since(seen) <= STALE_AFTER));
    let list: Vec<serde_json::Value> = aircraft.iter().map(|(icao24, track)| serde_json::json!({
        "icao24": icao24, "callsign": track.callsign, "lat": track.lat, "lon": track.lon,
        "altitude_m": track.altitude_m, "velocity_ms": track.velocity_ms,
        "heading_deg": track.heading_deg, "vertical_rate_ms": track.vertical_rate_ms,
        "on_ground": track.on_ground,
        "position_age_s": track.last_position.map(|seen| now.saturating_duration_since(seen).as_secs_f64()),
    })).collect();
    serde_json::json!({ "aircraft": list })
}

/// Returns the current aircraft from a persistent SBS connection to `host`:30003,
/// (re)starting the connection when the address changes.
#[tauri::command]
pub async fn fetch_mobile_dump1090(
    state: tauri::State<'_, MobileDump1090State>,
    host: String,
) -> Result<serde_json::Value, String> {
    let host = validate_host(&host)?.to_string();
    let (shared, fresh) = {
        let mut client = state.0.lock().await;
        let reusable = client.as_ref().is_some_and(|c| c.host == host);
        if !reusable {
            let shared = Arc::new(Shared {
                aircraft: StdMutex::new(HashMap::new()),
                status: StdMutex::new(Status::Connecting),
            });
            let task = tokio::spawn(run(host.clone(), shared.clone()));
            *client = Some(Client { host, shared, task });
        }
        (client.as_ref().map(|c| c.shared.clone()).ok_or("dump1090 client unavailable")?, !reusable)
    };
    if fresh {
        // Give a new connection a moment so the first poll reports a real result.
        for _ in 0..60 {
            if !matches!(shared.status.lock().map(|s| s.clone()), Ok(Status::Connecting)) { break; }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    }
    let status = shared.status.lock().map(|s| s.clone()).unwrap_or(Status::Connecting);
    match status {
        Status::Failed(message) => Err(message),
        Status::Connecting => Err("Connecting to dump1090...".into()),
        Status::Connected => Ok(snapshot(&shared, Instant::now())),
    }
}

#[tauri::command]
pub async fn stop_mobile_dump1090(state: tauri::State<'_, MobileDump1090State>) -> Result<(), String> {
    *state.0.lock().await = None;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // The tests that need the fixed SBS port take turns.
    static PORT_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

    #[test]
    fn position_and_velocity_messages_merge_into_one_track() {
        let mut aircraft = HashMap::new();
        let now = Instant::now();
        apply_sbs_line(&mut aircraft, "MSG,1,1,1,A1B2C3,1,2024/01/01,00:00:00.000,2024/01/01,00:00:00.000,ual123  ,,,,,,,,,,,0", now);
        apply_sbs_line(&mut aircraft, "MSG,3,1,1,A1B2C3,1,d,t,d,t,,10000,,,47.5,-122.25,,,0,0,0,0", now);
        apply_sbs_line(&mut aircraft, "MSG,4,1,1,A1B2C3,1,d,t,d,t,,,400,90,,,64,,0,0,0,0", now);
        let track = &aircraft["a1b2c3"];
        assert_eq!(track.callsign.as_deref(), Some("UAL123"));
        assert!((track.altitude_m.unwrap() - 3048.0).abs() < 0.01);
        assert_eq!((track.lat, track.lon), (Some(47.5), Some(-122.25)));
        assert!((track.velocity_ms.unwrap() - 205.7776).abs() < 0.001);
        assert_eq!(track.heading_deg, Some(90.0));
        assert!((track.vertical_rate_ms.unwrap() - 0.32512).abs() < 0.0001);
        assert!(!track.on_ground);
        assert_eq!(track.last_position, Some(now));
    }

    #[test]
    fn velocity_and_partial_positions_do_not_refresh_position_age() {
        let mut aircraft = HashMap::new();
        let now = Instant::now();
        apply_sbs_line(&mut aircraft, "MSG,3,1,1,ABC123,1,d,t,d,t,,10000,,,47.5,-122.25,,,0,0,0,0", now);
        apply_sbs_line(&mut aircraft, "MSG,4,1,1,ABC123,1,d,t,d,t,,,400,90,,,64,,0,0,0,0", now + Duration::from_secs(50));
        apply_sbs_line(&mut aircraft, "MSG,3,1,1,ABC123,1,d,t,d,t,,10000,,,48,,,,0,0,0,0", now + Duration::from_secs(50));
        let shared = Shared { aircraft: StdMutex::new(aircraft), status: StdMutex::new(Status::Connected) };
        let data = snapshot(&shared, now + Duration::from_secs(50));
        assert_eq!(data["aircraft"][0]["position_age_s"], 50.0);
        assert_eq!(data["aircraft"][0]["lat"], 47.5);
    }

    #[test]
    fn malformed_lines_are_ignored() {
        let mut aircraft = HashMap::new();
        for line in ["", "garbage", "MSG,3", "MSG,3,1,1,,1", "STA,6,1,1,ABC123,1"] {
            apply_sbs_line(&mut aircraft, line, Instant::now());
        }
        assert!(aircraft.is_empty());
    }

    #[test]
    fn host_validation_accepts_addresses_and_rejects_injection() {
        assert_eq!(validate_host(" 192.168.2.58 ").unwrap(), "192.168.2.58");
        assert!(validate_host("pi5.local").is_ok());
        assert!(validate_host("fe80::1").is_ok());
        assert_eq!(validate_host("127.0.0.1").unwrap(), "127.0.0.1");
        assert!(validate_host("localhost").is_ok());
        for bad in ["", "   ", "bad host", "a;b", "http://pi", "host/path"] {
            assert!(validate_host(bad).is_err(), "{bad}");
        }
    }

    #[tokio::test]
    async fn loopback_receiver_on_the_same_device_is_reachable() {
        use tokio::io::AsyncWriteExt;
        let _guard = PORT_LOCK.lock().await;
        // The port is fixed at 30003; skip if a real receiver already holds it.
        let Ok(listener) = tokio::net::TcpListener::bind(("127.0.0.1", SBS_PORT)).await else { return };
        let server = tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let _ = socket.write_all(b"MSG,3,1,1,ABC123,1,d,t,d,t,,5000,,,47.5,-122.25,,,0,0,0,0\n").await;
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        });
        let shared = Arc::new(Shared {
            aircraft: StdMutex::new(HashMap::new()),
            status: StdMutex::new(Status::Connecting),
        });
        let task = tokio::spawn(run("127.0.0.1".into(), shared.clone()));
        let mut count = 0;
        for _ in 0..30 {
            tokio::time::sleep(Duration::from_millis(100)).await;
            count = snapshot(&shared, Instant::now())["aircraft"].as_array().map_or(0, |a| a.len());
            if count > 0 { break; }
        }
        task.abort();
        server.abort();
        assert!(matches!(shared.status.lock().unwrap().clone(), Status::Connected));
        assert_eq!(count, 1);
    }

    #[test]
    fn busy_line_is_recognised_and_ordinary_lines_are_not() {
        assert_eq!(busy_reason("BUSY,RTL-SDR in use by rtl_tcp; free about 30s\r\n").as_deref(),
                   Some("RTL-SDR in use by rtl_tcp; free about 30s"));
        assert_eq!(busy_reason("BUSY,\r\n").as_deref(), Some("receiver busy"));
        assert!(busy_reason("MSG,3,1,1,ABC123,1").is_none());
        assert!(busy_reason("").is_none());
    }

    /// Serves `script` to the first client on 127.0.0.1:30003 and returns the status
    /// the client settles on, or None when a real receiver already holds the port.
    async fn status_against(script: &'static [u8]) -> Option<(Status, usize)> {
        use tokio::io::AsyncWriteExt;
        let _guard = PORT_LOCK.lock().await;
        let listener = tokio::net::TcpListener::bind(("127.0.0.1", SBS_PORT)).await.ok()?;
        let server = tokio::spawn(async move {
            if let Ok((mut socket, _)) = listener.accept().await {
                let _ = socket.write_all(script).await;
                let _ = socket.shutdown().await;
            }
        });
        let shared = Arc::new(Shared {
            aircraft: StdMutex::new(HashMap::new()),
            status: StdMutex::new(Status::Connecting),
        });
        let task = tokio::spawn(run("127.0.0.1".into(), shared.clone()));
        tokio::time::sleep(Duration::from_millis(1500)).await;
        task.abort();
        server.abort();
        let status = shared.status.lock().unwrap().clone();
        let count = shared.aircraft.lock().unwrap().len();
        Some((status, count))
    }

    #[tokio::test]
    async fn busy_host_is_reported_as_busy_not_connected() {
        let Some((status, count)) = status_against(b"BUSY,RTL-SDR in use by rtl_tcp\r\n").await else { return };
        match status {
            Status::Failed(message) => assert_eq!(message, "dump1090 busy: RTL-SDR in use by rtl_tcp"),
            _ => panic!("expected a busy failure"),
        }
        assert_eq!(count, 0);
    }

    #[tokio::test]
    async fn host_that_closes_immediately_is_not_reported_as_connected() {
        let Some((status, _)) = status_against(b"").await else { return };
        assert!(matches!(status, Status::Failed(message) if message.contains("closed the connection")));
    }

    #[tokio::test]
    async fn first_data_line_is_not_lost_when_connecting() {
        let line = b"MSG,3,1,1,ABC123,1,d,t,d,t,,5000,,,47.5,-122.25,,,0,0,0,0\r\nMSG,3,1,1,DEF456,1,d,t,d,t,,6000,,,47.6,-122.35,,,0,0,0,0\r\n";
        let Some((status, count)) = status_against(line).await else { return };
        assert_eq!(count, 2);
        // The server then closed, so the client has moved on from "connected".
        assert!(matches!(status, Status::Failed(_)));
    }

    #[test]
    fn snapshot_drops_stale_aircraft() {
        let shared = Shared {
            aircraft: StdMutex::new(HashMap::new()),
            status: StdMutex::new(Status::Connected),
        };
        let start = Instant::now();
        apply_sbs_line(&mut shared.aircraft.lock().unwrap(), "MSG,3,1,1,ABC123,1,d,t,d,t,,5000,,,1,2,,,0,0,0,0", start);
        assert_eq!(snapshot(&shared, start)["aircraft"].as_array().unwrap().len(), 1);
        assert!(snapshot(&shared, start + STALE_AFTER + Duration::from_secs(1))["aircraft"].as_array().unwrap().is_empty());
    }
}
