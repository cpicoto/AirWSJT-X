import { useCallback, useEffect, useRef, useState } from "react";
import {
  locatorToLatlon,
  bearingDeg,
  elevationDeg,
  haversineKm,
  slantRangeKm,
} from "./lib/geo";
import { Aircraft, OpenSkyPoller, fetchNativeAircraft } from "./lib/opensky";
import { invoke } from "@tauri-apps/api/core";
import AircraftAccounts from "./AircraftAccounts";
import { downloadDb, getDbStatus, DbStatus } from "./lib/callsign";
import { isSpeechSupported, speak } from "./lib/speech";
import { HeadingSmoother, headingFromOrientation, normalizeDegrees, signedHeadingDelta } from "./lib/compass";

// ─── Types ──────────────────────────────────────────────────────────────────

type CompassPermissionState = "unsupported" | "needs-permission" | "listening" | "active" | "denied";

type OrientationEventWithCompass = DeviceOrientationEvent & {
  webkitCompassHeading?: number;
};

interface MobileAircraft {
  icao24: string;
  callsign: string;
  lat: number;
  lon: number;
  altitude_ft: number;
  heading_deg: number | null;
  velocity_kts: number | null;
  az: number;
  el: number;
  range_km: number;
  distance_km: number;
}

function getScreenOrientationAngle(): number {
  if (typeof window === "undefined") return 0;
  if (typeof window.screen?.orientation?.angle === "number") {
    return normalizeDegrees(window.screen.orientation.angle);
  }
  const legacyWindow = window as Window & { orientation?: number };
  if (typeof legacyWindow.orientation === "number") {
    return normalizeDegrees(legacyWindow.orientation);
  }
  return 0;
}

function extractCompassHeading(event: OrientationEventWithCompass): number | null {
  if (typeof event.webkitCompassHeading === "number" && Number.isFinite(event.webkitCompassHeading)) {
    return normalizeDegrees(event.webkitCompassHeading);
  }
  if (typeof event.alpha === "number" && Number.isFinite(event.alpha)) {
    const beta = Number.isFinite(event.beta) ? (event.beta as number) : 0;
    const gamma = Number.isFinite(event.gamma) ? (event.gamma as number) : 0;
    return headingFromOrientation(event.alpha, beta, gamma, getScreenOrientationAngle());
  }
  return null;
}

// ─── Compass Rose (reused design from RotatorPanel) ─────────────────────────

function CompassRose({
  bearing,
  deviceHeading = null,
  size = 220,
  aircraft,
}: {
  bearing: number | null;
  deviceHeading?: number | null;
  size?: number;
  aircraft?: { az: number; el: number; callsign: string }[];
}) {
  const cx = size / 2, cy = size / 2;
  const outerR = size / 2 - 4;
  const innerR = outerR - 18;
  const cardinals = ["N", "E", "S", "W"];

  function azToXY(az: number, r: number) {
    const rad = ((az - 90) * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  }

  const ticks = [];
  for (let i = 0; i < 360; i += 5) {
    const isMajor = i % 90 === 0;
    const isMid = i % 30 === 0;
    const tickOuter = outerR;
    const tickInner = isMajor ? outerR - 14 : isMid ? outerR - 8 : outerR - 4;
    const renderAz = deviceHeading === null ? i : normalizeDegrees(i - deviceHeading);
    const p1 = azToXY(renderAz, tickOuter);
    const p2 = azToXY(renderAz, tickInner);
    ticks.push(
      <line key={i} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
        stroke={isMajor ? "#58a6ff" : "#30363d"} strokeWidth={isMajor ? 1.5 : 0.8} />
    );
  }
  for (let ci = 0; ci < 4; ci++) {
    const renderAz = deviceHeading === null ? ci * 90 : normalizeDegrees(ci * 90 - deviceHeading);
    const lp = azToXY(renderAz, outerR - 24);
    ticks.push(
      <text key={`c${ci}`} x={lp.x} y={lp.y} textAnchor="middle" dominantBaseline="central"
        fontSize={14} fill="#c9d1d9" fontWeight={700}>{cardinals[ci]}</text>
    );
  }

  const displayBearing = bearing === null
    ? null
    : deviceHeading === null
      ? normalizeDegrees(bearing)
      : normalizeDegrees(bearing - deviceHeading);

  return (
    <svg width={size} height={size} style={{ userSelect: "none" }}>
      <circle cx={cx} cy={cy} r={outerR} fill="#0d1117" stroke="#30363d" strokeWidth={1.5} />
      <circle cx={cx} cy={cy} r={innerR} fill="none" stroke="#1c2128" strokeWidth={1} />
      {ticks}
      {aircraft?.map((ac, idx) => {
        const renderAz = deviceHeading === null ? ac.az : normalizeDegrees(ac.az - deviceHeading);
        const p = azToXY(renderAz, innerR * 0.7);
        return (
          <g key={idx}>
            <circle cx={p.x} cy={p.y} r={4} fill="#d29922" opacity={0.8} />
            <text x={p.x} y={p.y - 7} textAnchor="middle" fontSize={8} fill="#d29922">
              {ac.callsign}
            </text>
          </g>
        );
      })}
      {displayBearing !== null && (
        <g>
          <line
            x1={cx} y1={cy}
            x2={azToXY(displayBearing, innerR - 8).x}
            y2={azToXY(displayBearing, innerR - 8).y}
            stroke="#58a6ff" strokeWidth={3} strokeLinecap="round"
          />
          <circle cx={azToXY(displayBearing, innerR - 8).x} cy={azToXY(displayBearing, innerR - 8).y}
            r={5} fill="#58a6ff" />
        </g>
      )}
      {deviceHeading !== null && (
        <polygon
          points={`${cx},${cy - innerR - 4} ${cx - 8},${cy - innerR + 12} ${cx + 8},${cy - innerR + 12}`}
          fill="#f85149"
        />
      )}
      <circle cx={cx} cy={cy} r={6} fill="#161b22" stroke="#58a6ff" strokeWidth={2} />
      {displayBearing !== null && (
        <text x={cx} y={cy + innerR * 0.56} textAnchor="middle" fontSize={24} fontWeight={700}
          fill="#c9d1d9" fontFamily="monospace">{displayBearing.toFixed(1)}°</text>
      )}
    </svg>
  );
}

// ─── Elevation Arc (reused design) ──────────────────────────────────────────

function ElevationArc({
  current,
  width = 220,
  height = 120,
}: {
  current: number | null;
  width?: number;
  height?: number;
}) {
  const cx = 18;
  const cy = height - 16;
  const R = Math.max(22, Math.min(width - 30, height - 24));

  function elToXY(el: number, r: number) {
    const rad = (el * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy - r * Math.sin(rad) };
  }

  const arcStart = elToXY(0, R);
  const arcEnd = elToXY(90, R);

  const ticks = [];
  for (let e = 0; e <= 90; e += 5) {
    const isMajor = e % 30 === 0;
    const isMid = e % 10 === 0;
    const p1 = elToXY(e, R);
    const p2 = elToXY(e, isMajor ? R - 14 : isMid ? R - 9 : R - 5);
    ticks.push(
      <line key={e} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
        stroke={isMajor ? "#58a6ff" : "#30363d"} strokeWidth={isMajor ? 1.5 : 0.8} />
    );
    if (isMajor) {
      const lp = elToXY(e, R - 24);
      ticks.push(
        <text key={`l${e}`} x={lp.x} y={lp.y} textAnchor="middle" dominantBaseline="central"
          fontSize={9} fill="#8b949e">{e}°</text>
      );
    }
  }

  return (
    <svg width={width} height={height} style={{ userSelect: "none" }}>
      <path d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 0 ${arcEnd.x} ${arcEnd.y}`}
        fill="none" stroke="#1c2128" strokeWidth={18} strokeLinecap="round" />
      <path d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 0 ${arcEnd.x} ${arcEnd.y}`}
        fill="none" stroke="#30363d" strokeWidth={1.5} />
      {current !== null && (() => {
        const fillEnd = elToXY(Math.max(0.5, current), R);
        return (
          <path
            d={`M ${arcStart.x} ${arcStart.y} A ${R} ${R} 0 0 0 ${fillEnd.x} ${fillEnd.y}`}
            fill="none" stroke="rgba(63,185,80,0.22)" strokeWidth={16} strokeLinecap="round"
          />
        );
      })()}
      {ticks}
      {current !== null && (
        <>
          <line x1={cx} y1={cy}
            x2={elToXY(current, R - 2).x} y2={elToXY(current, R - 2).y}
            stroke="#3fb950" strokeWidth={3} strokeLinecap="round" />
          <circle cx={elToXY(current, R - 2).x} cy={elToXY(current, R - 2).y}
            r={5} fill="#3fb950" />
        </>
      )}
      <circle cx={cx} cy={cy} r={6} fill="#161b22" stroke="#3fb950" strokeWidth={2} />
      {current !== null && (
        <text x={cx + R * 0.55} y={cy - R * 0.52} textAnchor="middle" fontSize={20} fontWeight={700}
          fill="#c9d1d9" fontFamily="monospace">{current.toFixed(1)}°</text>
      )}
    </svg>
  );
}

// ─── Mobile App ─────────────────────────────────────────────────────────────

const SETTINGS_KEY = "airwsjtx.mobile.settings";

interface MobileSettings {
  myCall: string;
  myGrid: string;
  dxCall: string;
  dxGrid: string;
  speechEnabled: boolean;
  aircraftSource?: "opensky" | "adsbexchange";
}

function loadSettings(): MobileSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return { myCall: "", myGrid: "", dxCall: "", dxGrid: "", speechEnabled: false };
}

export default function MobileApp() {
  const [settings, setSettings] = useState<MobileSettings>(loadSettings);
  const [showSettings, setShowSettings] = useState(false);
  const [aircraft, setAircraft] = useState<MobileAircraft[]>([]);
  const [selectedAc, setSelectedAc] = useState<MobileAircraft | null>(null);
  const [deviceHeading, setDeviceHeading] = useState<number | null>(null);
  const [compassPermissionState, setCompassPermissionState] = useState<CompassPermissionState>("unsupported");
  const [viewport, setViewport] = useState(() => ({
    width: typeof window !== "undefined" ? window.innerWidth : 390,
    height: typeof window !== "undefined" ? window.innerHeight : 844,
  }));
  const [status, setStatus] = useState("Configure your station");
  const [lastUpdate, setLastUpdate] = useState<number | null>(null);
  const [dbStatus, setDbStatus] = useState<DbStatus | null>(null);
  const [dbBusy, setDbBusy] = useState(false);
  const [accountRevision, setAccountRevision] = useState(0);
  const accountsChanged = useCallback(() => setAccountRevision(value => value + 1), []);
  const accountLog = useCallback((message: string) => setStatus(message), []);
  const pollerRef = useRef<OpenSkyPoller | null>(null);
  const speechLastRef = useRef<{ az: number; el: number; at: number; icao24: string } | null>(null);
  const compassHeadingRef = useRef<number | null>(null);

  const saveSettings = useCallback((next: MobileSettings) => {
    setSettings(next);
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(next));
  }, []);

  const hasStation = settings.myGrid.length >= 4;
  const isLandscape = viewport.width > viewport.height;
  const trackedAc = selectedAc ?? aircraft[0] ?? null;

  const enableCompass = useCallback(async () => {
    if (typeof window === "undefined" || typeof window.DeviceOrientationEvent === "undefined") {
      setCompassPermissionState("unsupported");
      return;
    }
    const orientationApi = window.DeviceOrientationEvent as typeof DeviceOrientationEvent & {
      requestPermission?: () => Promise<"granted" | "denied">;
    };
    if (typeof orientationApi.requestPermission === "function") {
      try {
        const permission = await orientationApi.requestPermission();
        if (permission !== "granted") {
          setCompassPermissionState("denied");
          return;
        }
      } catch {
        setCompassPermissionState("denied");
        return;
      }
    }
    setCompassPermissionState("listening");
  }, []);

  useEffect(() => {
    getDbStatus().then(setDbStatus).catch(() => {});
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const syncViewport = () => {
      setViewport({ width: window.innerWidth, height: window.innerHeight });
    };

    syncViewport();
    window.addEventListener("resize", syncViewport);
    window.screen.orientation?.addEventListener?.("change", syncViewport);

    return () => {
      window.removeEventListener("resize", syncViewport);
      window.screen.orientation?.removeEventListener?.("change", syncViewport);
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.DeviceOrientationEvent === "undefined") {
      setCompassPermissionState("unsupported");
      return;
    }
    const orientationApi = window.DeviceOrientationEvent as typeof DeviceOrientationEvent & {
      requestPermission?: () => Promise<"granted" | "denied">;
    };
    if (typeof orientationApi.requestPermission === "function") {
      setCompassPermissionState((current) => current === "denied" ? current : "needs-permission");
      return;
    }
    setCompassPermissionState((current) => current === "unsupported" ? "listening" : current);
  }, []);

  useEffect(() => {
    if (compassPermissionState !== "listening" && compassPermissionState !== "active") return;

    const smoother = new HeadingSmoother();
    // Android fires both events; "deviceorientation" is relative there (arbitrary zero),
    // so once absolute data is seen the relative stream must be ignored or the two fight.
    let sawAbsolute = false;

    const handle = (event: Event, absolute: boolean) => {
      const orientation = event as OrientationEventWithCompass;
      const isNorthReferenced = absolute || orientation.absolute === true
        || typeof orientation.webkitCompassHeading === "number";
      if (isNorthReferenced && !sawAbsolute) {
        sawAbsolute = true;
        smoother.reset();
      } else if (!isNorthReferenced && sawAbsolute) {
        return;
      }
      const heading = extractCompassHeading(orientation);
      if (heading === null) return;
      const next = smoother.push(heading, performance.now());
      if (next === null) return;
      compassHeadingRef.current = next;
      setDeviceHeading(next);
      setCompassPermissionState("active");
    };
    const onAbsolute = (event: Event) => handle(event, true);
    const onRelative = (event: Event) => handle(event, false);

    window.addEventListener("deviceorientationabsolute", onAbsolute, true);
    window.addEventListener("deviceorientation", onRelative, true);

    return () => {
      window.removeEventListener("deviceorientationabsolute", onAbsolute, true);
      window.removeEventListener("deviceorientation", onRelative, true);
    };
  }, [compassPermissionState]);

  useEffect(() => {
    pollerRef.current?.stop();
    pollerRef.current = null;

    if (!hasStation) {
      setAircraft([]);
      setStatus("Configure your station grid");
      return;
    }

    let myLat: number, myLon: number;
    try {
      [myLat, myLon] = locatorToLatlon(settings.myGrid);
    } catch {
      setStatus("Invalid grid locator");
      return;
    }

    const bbox = 3;
    const source = settings.aircraftSource ?? "opensky";
    let disposed = false;
    const poller = new OpenSkyPoller({
      latMin: Math.max(-90, myLat - bbox),
      lonMin: Math.max(-180, myLon - bbox * 2),
      latMax: Math.min(90, myLat + bbox),
      lonMax: Math.min(180, myLon + bbox * 2),
      intervalMs: 30000,
      minimumIntervalMs: source === "opensky" ? 10000 : 5000,
      fetchAircraft: (latMin, lonMin, latMax, lonMax) => fetchNativeAircraft(source, latMin, lonMin, latMax, lonMax),
      onUpdate: (raw: Aircraft[]) => {
        const withPos = raw.filter(
          (a) => a.lat != null && a.lon != null && a.altitude_m != null && a.altitude_m > 0 && !a.on_ground,
        );
        const mapped: MobileAircraft[] = withPos.map((a) => {
          const dist = haversineKm(myLat, myLon, a.lat!, a.lon!);
          const az = bearingDeg(myLat, myLon, a.lat!, a.lon!);
          const el = elevationDeg(dist, a.altitude_m!);
          const range = slantRangeKm(dist, a.altitude_m!);
          return {
            icao24: a.icao24,
            callsign: a.callsign || a.icao24,
            lat: a.lat!,
            lon: a.lon!,
            altitude_ft: Math.round(a.altitude_m! * 3.28084),
            heading_deg: a.heading_deg,
            velocity_kts: a.velocity_ms != null ? Math.round(a.velocity_ms * 1.94384) : null,
            az: Math.round(az * 10) / 10,
            el: Math.round(el * 10) / 10,
            range_km: Math.round(range * 10) / 10,
            distance_km: Math.round(dist * 10) / 10,
          };
        });
        mapped.sort((a, b) => a.distance_km - b.distance_km);
        setAircraft(mapped);
        setLastUpdate(Date.now());
        setStatus(`${mapped.length} aircraft in range`);
      },
      onError: (msg) => setStatus(`Error: ${msg}`),
      onRateLimited: (ms) => setStatus(`Rate limited — retry in ${Math.round(ms / 1000)}s`),
    });
    pollerRef.current = poller;
    setStatus("Loading aircraft settings...");
    invoke<{ opensky_poll_interval_s: number; adsbexchange_poll_interval_s: number }>("get_source_credentials_status")
      .then(value => {
        if (disposed) return;
        const interval = source === "opensky" ? value.opensky_poll_interval_s : value.adsbexchange_poll_interval_s;
        poller.setInterval(interval * 1000);
        poller.start();
        setStatus("Fetching aircraft...");
      }).catch(() => { if (!disposed) setStatus("Cannot read secure aircraft account settings"); });

    return () => { disposed = true; poller.stop(); };
  }, [hasStation, settings.myGrid, settings.aircraftSource, accountRevision]);

  // Speech
  useEffect(() => {
    if (!settings.speechEnabled || !trackedAc) return;
    if (!isSpeechSupported()) return;
    const now = Date.now();
    const last = speechLastRef.current;
    // When the selected aircraft changes, bypass the time cooldown so the new
    // aircraft is announced immediately (the position-delta guard still applies).
    const sameAircraft = last?.icao24 === trackedAc.icao24;
    if (sameAircraft && last && now - last.at < 3000) return;
    if (sameAircraft && last && Math.abs(trackedAc.az - last.az) < 3 && Math.abs(trackedAc.el - last.el) < 2) return;
    speak(`${trackedAc.callsign}, Azimuth ${Math.round(trackedAc.az)}, Elevation ${Math.round(trackedAc.el)}`);
    speechLastRef.current = { az: trackedAc.az, el: trackedAc.el, at: now, icao24: trackedAc.icao24 };
  }, [trackedAc, settings.speechEnabled]);

  // Update selected aircraft position from latest data
  useEffect(() => {
    if (!selectedAc) return;
    const updated = aircraft.find((a) => a.icao24 === selectedAc.icao24);
    if (updated) setSelectedAc(updated);
    else setSelectedAc(null);
  }, [aircraft, selectedAc?.icao24]);

  async function handleDownloadDb() {
    setDbBusy(true);
    try {
      const result = await downloadDb();
      setDbStatus(result);
    } catch (e) {
      setStatus(`DB download failed: ${e}`);
    }
    setDbBusy(false);
  }

  const compassAircraft = aircraft.slice(0, 12).map((a) => ({
    az: a.az, el: a.el, callsign: a.callsign,
  }));
  const compassSize = isLandscape
    ? Math.max(210, Math.min(viewport.width * 0.42, 320))
    : Math.max(220, Math.min(viewport.width - 12, 320));
  const elevationWidth = isLandscape
    ? Math.max(220, Math.min(viewport.width * 0.42, 320))
    : 164;
  const elevationHeight = isLandscape ? 150 : 92;
  const turnDelta = trackedAc && deviceHeading !== null ? signedHeadingDelta(trackedAc.az, deviceHeading) : null;
  const turnLabel = turnDelta === null
    ? null
    : Math.abs(turnDelta) < 5
      ? "On target"
      : turnDelta > 0
        ? `${Math.round(turnDelta)}° right`
        : `${Math.round(Math.abs(turnDelta))}° left`;
  const compassStatus = compassPermissionState === "unsupported"
    ? "Compass unavailable"
    : compassPermissionState === "needs-permission"
      ? "Enable compass"
      : compassPermissionState === "denied"
        ? "Compass denied"
        : compassPermissionState === "active"
          ? (deviceHeading !== null ? `Phone ${Math.round(deviceHeading)}°` : "Compass active")
          : "Compass starting…";

  if (showSettings) {
    return (
      <div style={containerStyle}>
        <div style={headerStyle}>
          <span style={{ fontSize: 18, fontWeight: 700, color: "#c9d1d9" }}>Settings</span>
          <button style={backBtnStyle} onClick={() => setShowSettings(false)}>Done</button>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: 16, flex: 1, minHeight: 0, overflowY: "auto" }}>
          <SettingsField label="My Callsign" value={settings.myCall}
            onChange={(v) => saveSettings({ ...settings, myCall: v.toUpperCase() })} placeholder="AD7NP" />
          <SettingsField label="My Grid" value={settings.myGrid}
            onChange={(v) => saveSettings({ ...settings, myGrid: v.toUpperCase() })} placeholder="CN87" />
          <SettingsField label="DX Callsign" value={settings.dxCall}
            onChange={(v) => saveSettings({ ...settings, dxCall: v.toUpperCase() })} placeholder="VE7NX" />
          <SettingsField label="DX Grid" value={settings.dxGrid}
            onChange={(v) => saveSettings({ ...settings, dxGrid: v.toUpperCase() })} placeholder="CN89oh" />
          <label style={{ display: "flex", alignItems: "center", gap: 10, color: "#c9d1d9", fontSize: 15 }}>
            <input type="checkbox" checked={settings.speechEnabled}
              disabled={!isSpeechSupported()}
              onChange={(e) => saveSettings({ ...settings, speechEnabled: e.target.checked })} />
            Speech callouts
            {!isSpeechSupported() && (
              <span style={{ fontSize: 11, color: "#8b949e" }} title="Web Speech API not available on this platform">(unavailable)</span>
            )}
          </label>
          <label style={{ color: "#c9d1d9" }}>Aircraft source
            <select aria-label="Aircraft source" value={settings.aircraftSource ?? "opensky"}
              onChange={e => saveSettings({ ...settings, aircraftSource: e.target.value === "adsbexchange" ? "adsbexchange" : "opensky" })}>
              <option value="opensky">OpenSky</option>
              <option value="adsbexchange">ADS-B Exchange</option>
            </select>
          </label>
          <AircraftAccounts onLog={accountLog} onChanged={accountsChanged} />
          <div style={{ borderTop: "1px solid #30363d", paddingTop: 16 }}>
            <div style={{ fontSize: 14, color: "#8b949e", marginBottom: 8 }}>Callsign Database</div>
            {dbStatus && (
              <div style={{ fontSize: 13, color: "#8b949e", marginBottom: 8 }}>
                {dbStatus.entries} entries · {dbStatus.fresh ? "fresh" : "stale"}
                {dbStatus.downloaded_at && ` · updated ${new Date(dbStatus.downloaded_at).toLocaleDateString()}`}
              </div>
            )}
            <button style={actionBtnStyle} disabled={dbBusy} onClick={handleDownloadDb}>
              {dbBusy ? "Downloading..." : dbStatus?.entries ? "Update Database" : "Download Database"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      {/* Header */}
      <div style={headerStyle}>
        <div>
          <span style={{ fontSize: 16, fontWeight: 700, color: "#c9d1d9" }}>AirWSJT-X</span>
          <span style={{ fontSize: 12, color: "#8b949e", marginLeft: 8 }}>{settings.myCall || "—"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            fontSize: 11, padding: "2px 8px", borderRadius: 10,
            background: lastUpdate && Date.now() - lastUpdate < 60000
              ? "rgba(63,185,80,0.15)" : "rgba(248,81,73,0.15)",
            color: lastUpdate && Date.now() - lastUpdate < 60000 ? "#3fb950" : "#f85149",
          }}>
            {status}
          </span>
          <button style={settingsBtnStyle} aria-label="Settings" onClick={() => setShowSettings(true)}>⚙</button>
        </div>
      </div>

      <div style={compassToolbarStyle}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span style={{ fontSize: 12, color: "#c9d1d9", fontWeight: 600 }}>Phone compass</span>
          <span style={{
            fontSize: 11,
            color: compassPermissionState === "active" ? "#3fb950" : "#8b949e",
            fontFamily: "monospace",
          }}>
            {compassStatus}
          </span>
          {turnLabel && (
            <span style={{ fontSize: 11, color: "#d29922", fontWeight: 600 }}>
              Turn {turnLabel}
            </span>
          )}
        </div>
        {compassPermissionState === "needs-permission" && (
          <button style={compassEnableBtnStyle} onClick={() => { void enableCompass(); }}>
            Enable compass
          </button>
        )}
      </div>

      {/* Pointing display */}
      <div
        role="group"
        aria-label="Tracking dials"
        style={{
          display: "flex",
          flexDirection: isLandscape ? "row" : "column",
          alignItems: "center",
          justifyContent: "center",
          gap: isLandscape ? 20 : 2,
          padding: "10px 6px 4px",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Azimuth
          </div>
          <CompassRose
            bearing={trackedAc?.az ?? null}
            deviceHeading={deviceHeading}
            size={compassSize}
            aircraft={isLandscape ? undefined : compassAircraft}
          />
        </div>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", marginTop: isLandscape ? 0 : -6 }}>
          <div style={{ fontSize: 11, color: "#8b949e", textTransform: "uppercase", letterSpacing: "0.08em", alignSelf: "flex-start" }}>
            Elevation
          </div>
          <ElevationArc current={trackedAc?.el ?? null} width={elevationWidth} height={elevationHeight} />
        </div>
      </div>

      {/* Selected aircraft detail */}
      {trackedAc && (
        <div style={selectedCardStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span style={{ fontWeight: 700, fontSize: 16, color: "#58a6ff", fontFamily: "monospace" }}>
              {trackedAc.callsign}
            </span>
            {selectedAc && (
              <button style={{ ...backBtnStyle, fontSize: 11, padding: "2px 8px" }}
                onClick={() => setSelectedAc(null)}>Auto</button>
            )}
          </div>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13, color: "#c9d1d9", fontFamily: "monospace" }}>
            <span>AZ {trackedAc.az.toFixed(1)}°</span>
            <span>EL {trackedAc.el.toFixed(1)}°</span>
            <span>FL{Math.round(trackedAc.altitude_ft / 100)}</span>
            <span>{trackedAc.distance_km.toFixed(0)} km</span>
            {deviceHeading !== null && <span>HDG {Math.round(deviceHeading)}°</span>}
            {turnLabel && <span>{turnLabel}</span>}
          </div>
        </div>
      )}

      {/* Aircraft list */}
      {!isLandscape && (
      <div style={listContainerStyle}>
        <div style={{ fontSize: 12, color: "#8b949e", padding: "4px 12px", borderBottom: "1px solid #21262d" }}>
          {aircraft.length} aircraft · sorted by distance · tap to track
        </div>
        {aircraft.map((ac) => (
          <div
            key={ac.icao24}
            onClick={() => setSelectedAc(ac)}
            style={{
              ...listItemStyle,
              background: selectedAc?.icao24 === ac.icao24 ? "rgba(88,166,255,0.1)" : "transparent",
              borderLeft: selectedAc?.icao24 === ac.icao24 ? "3px solid #58a6ff" : "3px solid transparent",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontWeight: 600, fontSize: 13, color: "#c9d1d9", fontFamily: "monospace" }}>
                {ac.callsign}
              </span>
              <span style={{ fontSize: 12, color: "#8b949e" }}>
                {ac.distance_km.toFixed(0)} km
              </span>
            </div>
            <div style={{ display: "flex", gap: 10, fontSize: 11, color: "#8b949e", fontFamily: "monospace" }}>
              <span>AZ {ac.az.toFixed(0)}°</span>
              <span>EL {ac.el.toFixed(1)}°</span>
              <span>FL{Math.round(ac.altitude_ft / 100)}</span>
              {ac.heading_deg != null && <span>HDG {ac.heading_deg.toFixed(0)}°</span>}
            </div>
          </div>
        ))}
        {aircraft.length === 0 && hasStation && (
          <div style={{ padding: 24, textAlign: "center", color: "#8b949e", fontSize: 14 }}>
            No aircraft in range. Data refreshes every 30 seconds.
          </div>
        )}
        {!hasStation && (
          <div style={{ padding: 24, textAlign: "center", color: "#8b949e", fontSize: 14 }}>
            Tap ⚙ to set your grid locator and start tracking.
          </div>
        )}
      </div>
      )}
    </div>
  );
}

// ─── Settings field ─────────────────────────────────────────────────────────

function SettingsField({
  label, value, onChange, placeholder,
}: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 13, color: "#8b949e" }}>{label}</span>
      <input
        value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={settingsInputStyle}
      />
    </label>
  );
}

// ─── Styles ─────────────────────────────────────────────────────────────────

const containerStyle: React.CSSProperties = {
  display: "flex", flexDirection: "column", height: "100vh",
  background: "#0d1117", color: "#c9d1d9",
  fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  overflow: "hidden",
};

const headerStyle: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  padding: "8px 12px",
  paddingTop: "max(8px, env(safe-area-inset-top))",
  background: "#161b22", borderBottom: "1px solid #21262d",
  flexShrink: 0,
};

const compassToolbarStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  flexWrap: "wrap",
  padding: "8px 12px",
  background: "#0f141b",
  borderBottom: "1px solid #21262d",
  flexShrink: 0,
};

const settingsBtnStyle: React.CSSProperties = {
  background: "none", border: "1px solid #30363d", borderRadius: 6,
  color: "#8b949e", fontSize: 16, padding: "4px 10px", cursor: "pointer",
};

const compassEnableBtnStyle: React.CSSProperties = {
  background: "rgba(88,166,255,0.12)",
  border: "1px solid #58a6ff44",
  borderRadius: 6,
  color: "#58a6ff",
  fontSize: 12,
  padding: "4px 10px",
  cursor: "pointer",
  fontWeight: 600,
};

const backBtnStyle: React.CSSProperties = {
  background: "rgba(88,166,255,0.1)", border: "1px solid #58a6ff44",
  borderRadius: 6, color: "#58a6ff", fontSize: 14, padding: "6px 16px",
  cursor: "pointer", fontWeight: 600,
};

const actionBtnStyle: React.CSSProperties = {
  background: "rgba(88,166,255,0.12)", border: "1px solid #58a6ff44",
  borderRadius: 6, color: "#58a6ff", fontSize: 14, padding: "8px 16px",
  cursor: "pointer", fontWeight: 600, width: "100%",
};

const selectedCardStyle: React.CSSProperties = {
  margin: "0 12px", padding: "8px 12px", borderRadius: 8,
  background: "rgba(88,166,255,0.06)", border: "1px solid #58a6ff44",
  display: "flex", flexDirection: "column", gap: 4, flexShrink: 0,
};

const listContainerStyle: React.CSSProperties = {
  flex: 1, overflowY: "auto", borderTop: "1px solid #21262d",
  WebkitOverflowScrolling: "touch",
};

const listItemStyle: React.CSSProperties = {
  padding: "6px 12px", borderBottom: "1px solid #21262d",
  cursor: "pointer", display: "flex", flexDirection: "column", gap: 1,
};

const settingsInputStyle: React.CSSProperties = {
  background: "#0d1117", color: "#c9d1d9",
  border: "1px solid #30363d", borderRadius: 6,
  padding: "10px 12px", fontSize: 16, fontFamily: "monospace",
};
