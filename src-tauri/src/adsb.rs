//! CRC-checked DF17 ADS-B state, a port of `airwsjtx/adsb.py` for the Android receiver.
//!
//! Airborne barometric Q=1 altitude and global even/odd CPR are supported.
//! Surface, Gillham Q=0, MLAT, and error-corrected frames are deliberately excluded.
//! Times are monotonic seconds supplied by the caller.

use std::collections::HashMap;

const CHARACTERS: &[u8; 64] = b"#ABCDEFGHIJKLMNOPQRSTUVWXYZ#####_###############0123456789######";
pub const POSITION_RETENTION_S: f64 = 120.0;
pub const AIRCRAFT_RETENTION_S: f64 = 180.0;
const VELOCITY_RETENTION_S: f64 = 15.0;

pub fn crc_ok(frame: &[u8]) -> bool {
    if frame.len() != 14 {
        return false;
    }
    let mut value = frame.iter().fold(0u128, |acc, byte| (acc << 8) | u128::from(*byte));
    for shift in (24..=111).rev() {
        if value & (1u128 << shift) != 0 {
            value ^= 0x1FF_F409u128 << (shift - 24);
        }
    }
    value == 0
}

fn nl(latitude: f64) -> i64 {
    let latitude = latitude.abs();
    if latitude >= 87.0 {
        return if latitude == 87.0 { 2 } else { 1 };
    }
    let a = 1.0 - (1.0 - (std::f64::consts::PI / 30.0).cos()) / latitude.to_radians().cos().powi(2);
    (2.0 * std::f64::consts::PI / a.acos()) as i64
}

/// A CPR report: (received at, latitude, longitude).
type Cpr = (f64, u32, u32);

/// Returns a globally unambiguous airborne position from an even/odd pair within 10 s.
pub fn global_cpr(even: Cpr, odd: Cpr) -> Option<(f64, f64)> {
    if (even.0 - odd.0).abs() > 10.0 {
        return None;
    }
    let (ye, yo) = (f64::from(even.1) / 131072.0, f64::from(odd.1) / 131072.0);
    let j = (59.0 * ye - 60.0 * yo + 0.5).floor() as i64;
    let mut lat_e = 6.0 * (j.rem_euclid(60) as f64 + ye);
    let mut lat_o = (360.0 / 59.0) * (j.rem_euclid(59) as f64 + yo);
    if lat_e >= 270.0 {
        lat_e -= 360.0;
    }
    if lat_o >= 270.0 {
        lat_o -= 360.0;
    }
    if !(-90.0..=90.0).contains(&lat_e) || !(-90.0..=90.0).contains(&lat_o) || nl(lat_e) != nl(lat_o) {
        return None;
    }
    let is_odd = odd.0 > even.0;
    let lat = if is_odd { lat_o } else { lat_e };
    let nl = nl(lat);
    let m = (f64::from(even.2) * (nl - 1) as f64 / 131072.0 - f64::from(odd.2) * nl as f64 / 131072.0 + 0.5)
        .floor() as i64;
    let ni = (nl - i64::from(is_odd)).max(1);
    let x = if is_odd { odd.2 } else { even.2 };
    let mut lon = (360.0 / ni as f64) * (m.rem_euclid(ni) as f64 + f64::from(x) / 131072.0);
    if lon >= 180.0 {
        lon -= 360.0;
    }
    Some((lat, lon))
}

#[derive(Default, Clone)]
struct Track {
    callsign: Option<String>,
    lat: Option<f64>,
    lon: Option<f64>,
    altitude_m: Option<f64>,
    velocity_ms: Option<f64>,
    heading_deg: Option<f64>,
    vertical_rate_ms: Option<f64>,
    on_ground: bool,
    last_seen: f64,
    cpr: [Option<Cpr>; 2],
    position_at: Option<f64>,
    velocity_at: Option<f64>,
}

#[derive(Default)]
pub struct Decoder {
    tracks: HashMap<String, Track>,
    pub frames: u64,
    pub crc_errors: u64,
    pub unsupported_frames: u64,
    pub valid_messages: u64,
    pub positions: u64,
    pub position_rejections: u64,
}

impl Decoder {
    /// Applies one 28-hex-digit Mode S frame. Returns true for a valid DF17 message.
    pub fn consume_hex(&mut self, hex: &str, now: f64) -> bool {
        let hex = hex.trim();
        if hex.len() != 28 || !hex.bytes().all(|c| c.is_ascii_hexdigit()) {
            return false;
        }
        let mut frame = [0u8; 14];
        for (i, byte) in frame.iter_mut().enumerate() {
            *byte = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap_or(0);
        }
        self.consume(&frame, now)
    }

    pub fn consume(&mut self, frame: &[u8; 14], now: f64) -> bool {
        self.frames += 1;
        // Other Mode S formats can use address-overlaid parity, not ADS-B's
        // zero CRC remainder. They are unsupported, not proven CRC failures.
        if frame[0] >> 3 != 17 {
            self.unsupported_frames += 1;
            return false;
        }
        if !crc_ok(frame) {
            self.crc_errors += 1;
            return false;
        }
        self.valid_messages += 1;
        let address = format!("{:02x}{:02x}{:02x}", frame[1], frame[2], frame[3]);
        let value = frame.iter().fold(0u128, |acc, byte| (acc << 8) | u128::from(*byte));
        let bits = |start: u32, length: u32| -> u32 { ((value >> (112 - start - length + 1)) & ((1u128 << length) - 1)) as u32 };
        let track = self.tracks.entry(address).or_default();
        track.last_seen = now;
        let tc = bits(33, 5);
        if (1..=4).contains(&tc) {
            let callsign: String = (0..8).map(|i| CHARACTERS[bits(41 + i * 6, 6) as usize] as char).collect();
            let callsign = callsign.replace('_', " ").replace('#', "").trim().to_string();
            track.callsign = (!callsign.is_empty()).then_some(callsign);
        } else if (5..=8).contains(&tc) {
            track.on_ground = true;
            track.lat = None;
            track.lon = None;
            track.altitude_m = None;
            track.cpr = [None, None];
            track.position_at = None;
        } else if (9..=18).contains(&tc) {
            track.on_ground = false;
            let altitude = bits(41, 12);
            track.altitude_m = (altitude & 0x10 != 0).then(|| {
                let n = ((altitude & 0xFE0) >> 1) | (altitude & 0xF);
                (f64::from(n) * 25.0 - 1000.0) * 0.3048
            });
            track.cpr[bits(54, 1) as usize] = Some((now, bits(55, 17), bits(72, 17)));
            if let [Some(even), Some(odd)] = track.cpr {
                if let Some(position) = global_cpr(even, odd) {
                    if !plausible_position(track, position, now) {
                        self.position_rejections += 1;
                        track.cpr = [None, None];
                        return true;
                    }
                    track.lat = Some(position.0);
                    track.lon = Some(position.1);
                    track.position_at = Some(now);
                    self.positions += 1;
                }
            }
        } else if tc == 19 && matches!(bits(38, 3), 1 | 2) {
            let scale = if bits(38, 3) == 2 { 4.0 } else { 1.0 };
            let (ew, ns) = (bits(47, 10), bits(58, 10));
            if ew != 0 && ns != 0 {
                let sign = |bit: u32| if bit != 0 { -1.0 } else { 1.0 };
                let vx = f64::from(ew - 1) * scale * sign(bits(46, 1));
                let vy = f64::from(ns - 1) * scale * sign(bits(57, 1));
                track.velocity_ms = Some(vx.hypot(vy) * 0.514444);
                track.heading_deg = Some(vx.atan2(vy).to_degrees().rem_euclid(360.0));
            } else {
                track.velocity_ms = None;
                track.heading_deg = None;
            }
            let vertical = bits(70, 9);
            track.vertical_rate_ms = (vertical != 0).then(|| {
                f64::from(vertical - 1) * 64.0 * 0.00508 * if bits(69, 1) != 0 { -1.0 } else { 1.0 }
            });
            track.velocity_at = Some(now);
        }
        true
    }

    /// Expires old tracks and returns aircraft in the mobile dump1090 JSON shape.
    pub fn snapshot(&mut self, now: f64) -> Vec<serde_json::Value> {
        self.tracks.retain(|_, track| now - track.last_seen <= AIRCRAFT_RETENTION_S);
        self.tracks
            .iter()
            .map(|(icao24, track)| {
                let fresh_position = track.position_at.filter(|at| now - at < POSITION_RETENTION_S);
                let fresh_velocity = track.velocity_at.is_some_and(|at| now - at <= VELOCITY_RETENTION_S);
                let position = |value: Option<f64>| fresh_position.and(value);
                let velocity = |value: Option<f64>| if fresh_velocity { value } else { None };
                serde_json::json!({
                    "icao24": icao24, "callsign": track.callsign,
                    "lat": position(track.lat), "lon": position(track.lon),
                    "altitude_m": position(track.altitude_m),
                    "velocity_ms": velocity(track.velocity_ms), "heading_deg": velocity(track.heading_deg),
                    "vertical_rate_ms": velocity(track.vertical_rate_ms),
                    "on_ground": track.on_ground,
                    "position_age_s": fresh_position.map(|at| (now - at).max(0.0)),
                })
            })
            .collect()
    }
}

/// Rejects implausible jumps only against a recent established position.
fn plausible_position(track: &Track, position: (f64, f64), now: f64) -> bool {
    let (Some(at), Some(lat), Some(lon)) = (track.position_at, track.lat, track.lon) else { return true };
    if now - at > 15.0 {
        return true;
    }
    let elapsed = now - at;
    if elapsed < 0.0 {
        return false;
    }
    // Great-circle distance; allow a generous 500 m base error and at least
    // 1200 knots. A fresh reported higher speed increases, never tightens, it.
    let (lat1, lat2) = (lat.to_radians(), position.0.to_radians());
    let dlat = lat2 - lat1;
    let dlon = (position.1 - lon).to_radians();
    let hav = (dlat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (dlon / 2.0).sin().powi(2);
    let distance = 6_371_000.0 * 2.0 * hav.clamp(0.0, 1.0).sqrt().asin();
    let mut speed = 1200.0 * 0.514444;
    if let (Some(velocity_at), Some(velocity)) = (track.velocity_at, track.velocity_ms) {
        if (0.0..=15.0).contains(&(now - velocity_at)) {
            speed = f64::max(speed, velocity * 1.5);
        }
    }
    distance <= 500.0 + (elapsed + 1.0) * speed
}

#[cfg(test)]
mod tests {
    use super::*;

    const EVEN: &str = "8D40621D58C382D690C8AC2863A7";
    const ODD: &str = "8D40621D58C386435CC412692AD6";
    const IDENTITY: &str = "8D4840D6202CC371C32CE0576098";
    const VELOCITY: &str = "8D485020994409940838175B284F";

    fn first(decoder: &mut Decoder, now: f64) -> serde_json::Value {
        decoder.snapshot(now).into_iter().next().expect("one aircraft")
    }

    /// Replaces `length` bits at 1-based `start` and recomputes the CRC, like the Python tests.
    fn change_bits(hex: &str, start: u32, length: u32, value: u128) -> String {
        let mut frame = u128::from_str_radix(hex, 16).unwrap();
        let shift = 112 - start - length + 1;
        frame = (frame & !(((1u128 << length) - 1) << shift)) | (value << shift);
        let mut data = frame >> 24;
        let mut remainder = data << 24;
        for bit in (24..=111).rev() {
            if remainder & (1u128 << bit) != 0 {
                remainder ^= 0x1FF_F409u128 << (bit - 24);
            }
        }
        data = (data << 24) | remainder;
        format!("{data:028X}")
    }

    #[test]
    fn identification_decodes_callsign_without_position() {
        let mut decoder = Decoder::default();
        assert!(decoder.consume_hex(IDENTITY, 1.0));
        let aircraft = first(&mut decoder, 1.0);
        assert_eq!(aircraft["icao24"], "4840d6");
        assert_eq!(aircraft["callsign"], "KLM1023");
        assert!(aircraft["lat"].is_null());
    }

    #[test]
    fn published_airborne_pair_newest_even_and_odd() {
        let mut decoder = Decoder::default();
        decoder.consume_hex(ODD, 0.0);
        assert!(first(&mut decoder, 0.0)["lat"].is_null());
        decoder.consume_hex(EVEN, 1.0);
        let aircraft = first(&mut decoder, 1.0);
        assert!((aircraft["lat"].as_f64().unwrap() - 52.257202).abs() < 1e-5);
        assert!((aircraft["lon"].as_f64().unwrap() - 3.919373).abs() < 1e-5);
        assert!((aircraft["altitude_m"].as_f64().unwrap() - 38000.0 * 0.3048).abs() < 1e-6);
        assert_eq!(aircraft["position_age_s"], 0.0);
        assert_eq!(decoder.positions, 1);

        let mut decoder = Decoder::default();
        decoder.consume_hex(EVEN, 0.0);
        decoder.consume_hex(ODD, 1.0);
        let aircraft = first(&mut decoder, 1.0);
        assert!((aircraft["lat"].as_f64().unwrap() - 52.265780).abs() < 1e-5);
        assert!((aircraft["lon"].as_f64().unwrap() - 3.938913).abs() < 1e-5);
    }

    #[test]
    fn pair_age_and_track_position_expiry() {
        let mut decoder = Decoder::default();
        decoder.consume_hex(EVEN, 0.0);
        decoder.consume_hex(ODD, 11.0);
        assert!(first(&mut decoder, 11.0)["lat"].is_null());
        decoder.consume_hex(EVEN, 12.0);
        assert!(!first(&mut decoder, 131.9)["lat"].is_null());
        decoder.consume_hex(&change_bits(IDENTITY, 9, 24, 0x40621D), 28.0);
        assert!(first(&mut decoder, 132.0)["lat"].is_null());
        assert_eq!(decoder.snapshot(208.0).len(), 1);
        assert!(decoder.snapshot(208.1).is_empty());
    }

    #[test]
    fn ground_transition_and_unsupported_altitude() {
        let mut decoder = Decoder::default();
        decoder.consume_hex(EVEN, 0.0);
        decoder.consume_hex(ODD, 1.0);
        decoder.consume_hex(&change_bits(EVEN, 48, 1, 0), 2.0);
        assert!(first(&mut decoder, 2.0)["altitude_m"].is_null());
        decoder.consume_hex(&change_bits(EVEN, 33, 5, 5), 3.0);
        let aircraft = first(&mut decoder, 3.0);
        assert_eq!(aircraft["on_ground"], true);
        assert!(aircraft["lat"].is_null());
        decoder.consume_hex(EVEN, 4.0);
        assert!(first(&mut decoder, 4.0)["lat"].is_null());
    }

    #[test]
    fn published_ground_speed_and_vertical_rate() {
        let mut decoder = Decoder::default();
        decoder.consume_hex(VELOCITY, 0.0);
        let aircraft = first(&mut decoder, 0.0);
        assert!((aircraft["velocity_ms"].as_f64().unwrap() / 0.514444 - 159.201).abs() < 1e-3);
        assert!((aircraft["heading_deg"].as_f64().unwrap() - 182.88).abs() < 1e-2);
        assert!((aircraft["vertical_rate_ms"].as_f64().unwrap() + 832.0 * 0.00508).abs() < 1e-9);
        assert!(first(&mut decoder, 16.0)["velocity_ms"].is_null());
    }

    #[test]
    fn invalid_frames_crc_errors_and_unsupported_formats() {
        let mut decoder = Decoder::default();
        for bad in ["bad", "00", "not hex not hex not hex not h", "8D40621D"] {
            assert!(!decoder.consume_hex(bad, 0.0));
        }
        assert!(!decoder.consume_hex(&format!("{}0", &EVEN[..27]), 0.0));
        assert!(!decoder.consume_hex(&change_bits(EVEN, 1, 5, 18), 0.0));
        decoder.consume_hex(&change_bits(VELOCITY, 47, 10, 0), 1.0);
        assert!(first(&mut decoder, 1.0)["velocity_ms"].is_null());
        assert_eq!(decoder.crc_errors, 1);
        assert_eq!(decoder.valid_messages, 1);
        assert_eq!(decoder.unsupported_frames, 1);
    }

    #[test]
    fn global_cpr_southern_hemisphere_dateline_and_poles() {
        fn encode(lat: f64, lon: f64, parity: u32, at: f64) -> Cpr {
            let mut zones = 1;
            for n in (2..=59).rev() {
                let boundary = ((1.0 - (std::f64::consts::PI / 30.0).cos())
                    / (1.0 - (2.0 * std::f64::consts::PI / f64::from(n)).cos())).sqrt().acos().to_degrees();
                if lat.abs() < boundary {
                    zones = n;
                    break;
                }
            }
            let dlat = 360.0 / f64::from(60 - parity);
            let dlon = 360.0 / f64::from((zones - parity).max(1));
            let y = (131072.0 * (lat.rem_euclid(dlat) / dlat)).round() as u32 % 131072;
            let x = (131072.0 * (lon.rem_euclid(dlon) / dlon)).round() as u32 % 131072;
            (at, y, x)
        }
        for (lat, lon) in [(-33.8, 151.2), (45.1, -179.99), (-88.1, -40.5), (0.1, 0.1)] {
            let position = global_cpr(encode(lat, lon, 0, 0.0), encode(lat, lon, 1, 1.0)).expect("position");
            assert!((position.0 - lat).abs() < 1e-3, "{lat}");
            assert!((position.1 - lon).abs() < 1e-2, "{lon}");
        }
    }

    #[test]
    fn published_crc_and_single_bit_corruptions() {
        let frame = u128::from_str_radix(EVEN, 16).unwrap();
        let bytes = |value: u128| -> [u8; 14] { value.to_be_bytes()[2..].try_into().unwrap() };
        assert!(crc_ok(&bytes(frame)));
        for bit in 0..112 {
            assert!(!crc_ok(&bytes(frame ^ (1u128 << bit))), "{bit}");
        }
        assert!(!crc_ok(&[]));
    }

    #[test]
    fn recent_position_jump_rejected_then_accepted_after_expiry() {
        let mut decoder = Decoder::default();
        decoder.consume_hex(EVEN, 0.0);
        decoder.consume_hex(ODD, 1.0);
        let old = first(&mut decoder, 1.0)["lat"].clone();
        let zero_even = change_bits(&change_bits(EVEN, 55, 17, 0), 72, 17, 0);
        let zero_odd = change_bits(&change_bits(ODD, 55, 17, 0), 72, 17, 0);
        decoder.consume_hex(&zero_even, 2.0);
        decoder.consume_hex(&zero_odd, 3.0);
        assert!(decoder.position_rejections > 0);
        assert_eq!(decoder.positions, 1);
        assert_eq!(first(&mut decoder, 3.0)["lat"], old);
        decoder.consume_hex(&zero_even, 30.0);
        decoder.consume_hex(&zero_odd, 31.0);
        assert_eq!(first(&mut decoder, 31.0)["lat"].as_f64(), Some(0.0));
    }
}
