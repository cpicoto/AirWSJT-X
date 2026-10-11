use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::AppHandle;
use tokio::sync::Mutex;

const TOKEN_URL: &str = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token";

fn epoch_now() -> Result<f64, String> {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|value| value.as_secs_f64())
        .map_err(|_| "System clock is invalid for aircraft quota tracking".into())
}

#[derive(Default)]
struct Session {
    token: Option<(String, Instant)>,
    opensky_request: Option<Instant>,
    adsbexchange_request: Option<Instant>,
    opensky_quota: Quota,
    adsbexchange_quota: Quota,
}

#[derive(Default)]
struct Quota {
    remaining: Option<u64>,
    checked_epoch_s: Option<f64>,
    retry_epoch_s: Option<f64>,
    next_sample: Option<Instant>,
    retry_until: Option<Instant>,
    limited: bool,
    paused: bool,
    error: Option<String>,
}

impl Quota {
    fn snapshot(&self, opensky: bool) -> serde_json::Value {
        serde_json::json!({
            "remaining": self.remaining, "checked_epoch_s": self.checked_epoch_s,
            "retry_epoch_s": self.retry_epoch_s, "quota_supported": opensky,
            "check_interval_s": if self.limited { 1800 } else { 600 }, "error": self.error,
        })
    }

    fn observe(&mut self, headers: &reqwest::header::HeaderMap, status: reqwest::StatusCode, opensky: bool, now: Instant, epoch: f64) {
        let resumed = status.is_success() && self.paused;
        let sample = opensky && (self.next_sample.is_none_or(|next| now >= next)
            || (status.as_u16() == 429 && !self.limited) || resumed);
        if sample {
            self.remaining = None;
            self.error = None;
            if let Some(header) = headers.get("x-rate-limit-remaining") {
                match header.to_str().ok().and_then(|value| value.parse::<u64>().ok()) {
                    Some(value) => self.remaining = Some(value),
                    None => self.error = Some("Invalid remaining-quota response header".into()),
                }
            }
            self.checked_epoch_s = Some(epoch);
        }
        if status.as_u16() == 429 {
            self.limited = true;
            self.paused = true;
            let mut server_delay: Option<f64> = None;
            for name in ["x-rate-limit-retry-after-seconds", "retry-after"] {
                if let Some(header) = headers.get(name) {
                    let value = header.to_str().ok().and_then(|raw| {
                        raw.parse::<f64>().ok().filter(|v| v.is_finite() && *v >= 0.0)
                            .or_else(|| httpdate::parse_http_date(raw).ok()
                                .and_then(|date| date.duration_since(UNIX_EPOCH).ok())
                                .map(|date| (date.as_secs_f64() - epoch).max(0.0)))
                    });
                    if let Some(value) = value { server_delay = Some(server_delay.unwrap_or(0.0).max(value)); }
                    else { self.error = Some("Invalid retry-time response header".into()); }
                }
            }
            let delay = server_delay.unwrap_or(if opensky { 1800.0 } else { 0.0 });
            if let Ok(duration) = Duration::try_from_secs_f64(delay) {
                if let Some(until) = now.checked_add(duration) {
                    self.retry_until = Some(self.retry_until.map_or(until, |previous| previous.max(until)));
                    self.retry_epoch_s = Some(epoch + self.retry_until.unwrap().saturating_duration_since(now).as_secs_f64());
                } else { self.error = Some("Retry time exceeds the supported duration".into()); }
            } else { self.error = Some("Invalid retry-time duration".into()); }
        } else if status.is_success() && (resumed || self.retry_until.is_none_or(|until| now >= until)) {
            self.paused = false;
            self.retry_until = None;
            self.retry_epoch_s = None;
        }
        if sample { self.next_sample = Some(now + Duration::from_secs(if self.limited { 1800 } else { 600 })); }
    }

    fn wait(&self) -> u64 {
        self.retry_until.map_or(0, |until| until.saturating_duration_since(Instant::now()).as_secs_f64().ceil() as u64)
    }
}

#[derive(Default)]
pub struct MobileAircraftState(Mutex<Session>);

impl MobileAircraftState {
    pub async fn invalidate(&self, provider: &str) {
        let mut session = self.0.lock().await;
        if provider == "opensky" {
            session.token = None;
            session.opensky_quota = Quota::default();
        } else {
            session.adsbexchange_quota = Quota::default();
        }
    }

    pub async fn quota_status(&self) -> serde_json::Value {
        let session = self.0.lock().await;
        serde_json::json!({
            "opensky": session.opensky_quota.snapshot(true),
            "adsbexchange": session.adsbexchange_quota.snapshot(false),
        })
    }
}

fn check_status(status: reqwest::StatusCode) -> Result<(), String> {
    if status.as_u16() == 429 { return Err("RATE_LIMITED".into()); }
    if !status.is_success() { return Err(format!("Aircraft API returned HTTP {}", status.as_u16())); }
    Ok(())
}

async fn token(client: &reqwest::Client, account: &crate::credentials::Credentials, session: &mut Session) -> Result<String, String> {
    if let Some((token, expires)) = &session.token {
        if Instant::now() < *expires { return Ok(token.clone()); }
    }
    let response = client.post(TOKEN_URL).form(&[
        ("grant_type", "client_credentials"),
        ("client_id", account.client_id.as_str()),
        ("client_secret", account.secret.as_str()),
    ]).send().await.map_err(|_| "Cannot reach OpenSky authentication service")?;
    check_status(response.status())?;
    let data: serde_json::Value = response.json().await.map_err(|_| "Invalid OpenSky authentication response")?;
    let (access_token, lifetime) = parse_token(&data)?;
    session.token = Some((access_token.clone(), Instant::now() + lifetime));
    Ok(access_token)
}

fn parse_token(data: &serde_json::Value) -> Result<(String, Duration), String> {
    let access_token = data["access_token"].as_str().filter(|v| !v.is_empty())
        .ok_or("OpenSky authentication returned no access token")?.to_string();
    let seconds = data["expires_in"].as_u64().filter(|v| *v > 0 && *v <= 86400)
        .ok_or("Invalid OpenSky token lifetime")?;
    let lifetime = Duration::from_secs(seconds) - Duration::from_secs(30.min(seconds / 2));
    Ok((access_token, lifetime))
}

#[tauri::command]
pub async fn fetch_mobile_aircraft(
    app: AppHandle,
    state: tauri::State<'_, MobileAircraftState>,
    provider: String,
    lat_min: f64, lon_min: f64, lat_max: f64, lon_max: f64,
) -> Result<serde_json::Value, String> {
    if !(-90.0..=90.0).contains(&lat_min) || !(-90.0..=90.0).contains(&lat_max)
        || !(-180.0..=180.0).contains(&lon_min) || !(-180.0..=180.0).contains(&lon_max)
        || lat_min > lat_max || lon_min > lon_max {
        return Err("Invalid aircraft query bounds".into());
    }
    let account = crate::credentials::load(&app, &provider)?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none())
        .user_agent("AirWSJT-X")
        .build().map_err(|_| "Cannot initialize aircraft API connection")?;
    let mut session = state.0.lock().await;
    let wait = if provider == "opensky" { session.opensky_quota.wait() } else { session.adsbexchange_quota.wait() };
    if wait > 0 { return Err(format!("RATE_LIMITED:{wait}")); }
    let previous = if provider == "opensky" { session.opensky_request } else { session.adsbexchange_request };
    let minimum = Duration::from_secs(if provider == "opensky" { 10 } else { 5 });
    if let Some(previous) = previous {
        if let Some(wait) = minimum.checked_sub(previous.elapsed()) {
            tokio::time::sleep(wait).await;
        }
    }
    if provider == "adsbexchange" {
        let account = account.ok_or("Save an ADS-B Exchange API key before selecting this source")?;
        session.adsbexchange_request = Some(Instant::now());
        let latitude = (lat_min + lat_max) / 2.0;
        let longitude = (lon_min + lon_max) / 2.0;
        // The radius encloses the mobile tracker's existing rectangular search area.
        let latitude_span = (lat_max - lat_min) / 2.0 * 111.32;
        let longitude_span = (lon_max - lon_min) / 2.0 * 111.32 * latitude.to_radians().cos();
        let radius_nm = latitude_span.hypot(longitude_span) / 1.852;
        let url = format!("https://gateway.adsbexchange.com/api/aircraft/v2/lat/{latitude}/lon/{longitude}/dist/{radius_nm}");
        let response = client.get(url).header("x-api-key", account.secret)
            .header("Accept-Encoding", "gzip").send().await
            .map_err(|_| "Cannot reach ADS-B Exchange")?;
        session.adsbexchange_quota.observe(response.headers(), response.status(), false, Instant::now(),
            epoch_now()?);
        if response.status().as_u16() == 429 {
            return Err(format!("RATE_LIMITED:{}", session.adsbexchange_quota.wait()));
        }
        check_status(response.status())?;
        let data: serde_json::Value = response.json().await.map_err(|_| "Invalid ADS-B Exchange response")?;
        if !data["ac"].is_array() { return Err("ADS-B Exchange response is missing aircraft data".into()); }
        return Ok(data);
    }
    let url = format!("https://opensky-network.org/api/states/all?lamin={lat_min}&lomin={lon_min}&lamax={lat_max}&lomax={lon_max}");
    for attempt in 0..2 {
        let mut request = client.get(&url);
        if let Some(account) = &account {
            request = request.bearer_auth(token(&client, account, &mut session).await?);
        }
        session.opensky_request = Some(Instant::now());
        let response = request.send().await.map_err(|_| "Cannot reach OpenSky aircraft API")?;
        session.opensky_quota.observe(response.headers(), response.status(), true, Instant::now(),
            epoch_now()?);
        if response.status().as_u16() == 429 {
            return Err(format!("RATE_LIMITED:{}", session.opensky_quota.wait()));
        }
        if response.status().as_u16() == 401 && account.is_some() && attempt == 0 {
            session.token = None;
            continue;
        }
        check_status(response.status())?;
        let data: serde_json::Value = response.json().await.map_err(|_| "Invalid OpenSky aircraft response")?;
        if !data["states"].is_null() && !data["states"].is_array() {
            return Err("Invalid OpenSky aircraft data".into());
        }
        return Ok(data);
    }
    Err("OpenSky authentication retry failed".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quota_sampling_and_server_reset_resume_are_exact() {
        let mut quota = Quota::default();
        let now = Instant::now();
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert("x-rate-limit-remaining", "4000".parse().unwrap());
        quota.observe(&headers, reqwest::StatusCode::OK, true, now, 1000.0);
        headers.insert("x-rate-limit-remaining", "3900".parse().unwrap());
        quota.observe(&headers, reqwest::StatusCode::OK, true, now + Duration::from_secs(599), 1599.0);
        assert_eq!(quota.remaining, Some(4000));
        quota.observe(&headers, reqwest::StatusCode::OK, true, now + Duration::from_secs(600), 1600.0);
        assert_eq!(quota.remaining, Some(3900));
        headers.insert("x-rate-limit-remaining", "0".parse().unwrap());
        headers.insert("x-rate-limit-retry-after-seconds", "120".parse().unwrap());
        quota.observe(&headers, reqwest::StatusCode::TOO_MANY_REQUESTS, true, now + Duration::from_secs(601), 1601.0);
        assert_eq!(quota.retry_epoch_s, Some(1721.0));
        assert_eq!(quota.remaining, Some(0));
        headers.insert("x-rate-limit-remaining", "4000".parse().unwrap());
        quota.observe(&headers, reqwest::StatusCode::OK, true, now + Duration::from_secs(721), 1721.0);
        assert!(quota.retry_until.is_none());
        assert!(quota.retry_epoch_s.is_none());
        assert_eq!(quota.remaining, Some(4000));
        assert_eq!(quota.snapshot(true)["check_interval_s"], 1800);
    }

    #[test]
    fn missing_server_retry_uses_thirty_minutes_and_longer_retry_is_honored() {
        for (retry, expected) in [(None, 1800.0), (Some("3600"), 3600.0)] {
            let mut quota = Quota::default();
            let mut headers = reqwest::header::HeaderMap::new();
            if let Some(retry) = retry { headers.insert("retry-after", retry.parse().unwrap()); }
            quota.observe(&headers, reqwest::StatusCode::TOO_MANY_REQUESTS, true, Instant::now(), 1000.0);
            assert_eq!(quota.retry_epoch_s, Some(1000.0 + expected));
        }
    }

    #[test]
    fn token_lifetime_renews_before_expiry_without_exposing_bad_payloads() {
        let (token, lifetime) = parse_token(&serde_json::json!({
            "access_token": "fake-test-token", "expires_in": 1800
        })).unwrap();
        assert_eq!(token, "fake-test-token");
        assert_eq!(lifetime, Duration::from_secs(1770));
        for data in [
            serde_json::json!({"access_token": "fake-test-token", "expires_in": 0}),
            serde_json::json!({"access_token": "fake-test-token", "expires_in": true}),
            serde_json::json!({"access_token": null, "expires_in": 1800}),
        ] {
            let error = parse_token(&data).unwrap_err();
            assert!(!error.contains("fake-test-token"));
        }
    }

    #[test]
    fn rate_limit_and_http_errors_are_explicit() {
        assert!(check_status(reqwest::StatusCode::OK).is_ok());
        assert_eq!(check_status(reqwest::StatusCode::TOO_MANY_REQUESTS).unwrap_err(), "RATE_LIMITED");
        assert!(check_status(reqwest::StatusCode::UNAUTHORIZED).unwrap_err().contains("401"));
    }

    #[tokio::test]
    async fn credential_change_invalidates_cached_token_but_preserves_request_budget() {
        let state = MobileAircraftState::default();
        {
            let mut session = state.0.lock().await;
            session.token = Some(("fake-test-token".into(), Instant::now() + Duration::from_secs(1800)));
            session.opensky_request = Some(Instant::now());
        }
        state.invalidate("opensky").await;
        let session = state.0.lock().await;
        assert!(session.token.is_none());
        assert!(session.opensky_request.is_some());
    }
}
