# AirWSJT-X

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Release](https://img.shields.io/github/v/release/cpicoto/AirWSJT-X)](https://github.com/cpicoto/AirWSJT-X/releases/latest)

Aircraft Scatter automation for WSJT-X on 23 cm and above — with a standalone mobile tracker for field use.

## Overview

AirWSJT-X merges [AirScout](http://www.movingmap.de/en/) aircraft tracking with [WSJT-X](https://wsjt.sourceforge.io/) weak-signal digital modes to automate aircraft scatter contacts on 1296 MHz (23 cm) and higher microwave bands.

It treats commercial aircraft as dynamic, low-orbit reflectors. When a viable aircraft is detected between two stations, AirWSJT-X locks the rotator, configures WSJT-X for Q65-15s operation, and manages the entire QSO sequence — from airplane selection through contact completion.

### Mobile App (v0.2.0)

The mobile app brings standalone aircraft tracking to the field — a compass rose and elevation arc for hand-pointing antennas at aircraft, with speech callouts for hands-free operation. No WSJT-X or rotator required.

## Downloads

| Platform | Download |
|----------|----------|
| macOS (Apple Silicon) | [AirWSJT-X_0.2.0_aarch64.dmg](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| Windows (x64) | [AirWSJT-X_0.2.0_x64_en-US.msi](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| Android | [AirWSJT-X-android.apk](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| iOS | Coming soon |

## Architecture

AirWSJT-X is a [Tauri 2](https://tauri.app/) desktop application (Rust + React frontend) with a Python backend providing core radio and tracking logic. The mobile app runs standalone TypeScript — no Python sidecar needed.

### Desktop Mode

The desktop app is implemented as a supervisor layer that keeps WSJT-X mostly untouched and coordinates:

- Airplane tracking/selection (`airwsjtx.tracker`)
- Contact state machine (`airwsjtx.state_machine`)
- Rotator lock control (`airwsjtx.rotator`)
- WSJT-X control adapter for 23cm+ (`airwsjtx.wsjtx`)
- PSK Reporter interoperability profile (`airwsjtx.psk`)
- End-to-end orchestration (`airwsjtx.supervisor`)

Q65-15s operation is enabled only when:

1. Two stations are configured (your station + target station)
2. A viable aircraft is selected and locked
3. Rotator lock remains stable
4. WSJT-X is configured for a supported 23cm+ band
5. The operator explicitly starts the QSO

When multiple WSJT-X instances are on the same network, use the **Rig** dropdown in the top bar to select a discovered WSJT-X instance. The list shows instance IDs and configuration names when available, refreshes every two seconds, and includes rigs seen during this application session even while another rig is selected. Selecting an entry applies and saves the filter immediately; **Any rig** clears it and accepts the first source. A saved selection remains available before that rig is seen again.

### WSJT-X Connection Troubleshooting

In **Stations**, enter **MY callsign** and **MY grid** and select **Apply** to use your station without WSJT-X reporting, or to override its values. Overrides are saved across restarts and are not overwritten by WSJT-X status updates. Empty fields use WSJT-X values; **Use WSJT-X** clears both overrides. Grids accept valid 4, 6, or 8 character Maidenhead locators. This does not bypass WSJT-X or frequency requirements for starting a radio QSO.

- In WSJT-X **Settings > Reporting**, set the UDP server to `224.0.0.1` and port `2237`.
- Select **Any rig** in the AirWSJT-X **Rig** dropdown to accept the first WSJT-X source, or select a discovered instance.
- On Windows, allow AirWSJT-X and its Python sidecar through Windows Firewall on the network you use.
- Listener startup errors (for example, a port already in use) are reported as **WSJT-X error** in the application log and in the sidecar's stderr.
- If the app reports **Rig discovery unavailable**, rebuild the Python sidecar and restart the desktop app. This indicates that the frontend is running with an older backend; the app remains usable, but discovered rigs cannot be listed until both are updated.

### Aircraft Accounts and API Polling

The desktop and mobile apps support the authenticated [ADS-B Exchange v2 API](https://gateway.adsbexchange.com/api/aircraft/v2/docs/index.html). An API key with access to the geographic radius endpoint is required; there is no anonymous access.

Expand **Aircraft accounts** in the desktop aircraft panel or mobile **Settings** to paste and save credentials:

- **OpenSky:** paste the `clientId` and `clientSecret` values from your OpenSky credential file into the separate fields. The app uses OAuth2 client-credentials authentication, caches the access token, renews it before expiry, and retries once with a new token on HTTP 401. Clear the account to return to anonymous requests.
- **ADS-B Exchange:** paste the API key; this provider's documented v2 API does not use a Client ID/Client Secret pair.
- **Save** uses Windows Credential Manager, macOS/iOS Keychain, Linux Secret Service (requires an unlocked desktop keyring), or Android Keystore-protected encrypted storage. Credentials are restored when the app starts. **Clear** removes the saved credentials. Secrets are masked, cleared from input fields after saving, and never returned to the frontend. Mobile API authentication and requests run in native code rather than exposing secrets to browser requests.
- **Poll interval (seconds)** appears beside each provider's credentials. Set it independently without re-entering the secret. Both default to **30 seconds**; OpenSky requires at least **10 seconds**, ADS-B Exchange at least **5 seconds**. Intervals are saved separately in the app's configuration directory, restored on startup, and applied to the active feed. An in-progress desktop wait completes before the new cadence takes effect; rate-limit cooldowns take precedence. dump1090 remains at five seconds.
- Existing environment-variable ADS-B Exchange keys remain a desktop fallback after clearing the saved key. Remove the environment variable and restart to disable that fallback.

#### Where to Get Credentials

**OpenSky Network**

1. Create an account at [OpenSky Network](https://opensky-network.org/) and sign in.
2. Open your [OpenSky Account page](https://opensky-network.org/my-opensky/account), create an API client, and download its credentials file (`credentials.json`).
3. Open the file locally in a text editor. Copy the value of `clientId` into **OpenSky Client ID** and the value of `clientSecret` into **OpenSky Client Secret** under **Aircraft accounts**. Copy only the values, without quotation marks; do not paste the entire JSON file.
4. Select **Save OpenSky**. The app handles access-token requests and renewal automatically; do not enter your OpenSky login password or an access token in these fields.

**ADS-B Exchange**

Visit [ADSBexchange.com on RapidAPI](https://rapidapi.com/adsbx/api/adsbexchange-com1) for the API listing, subscription options, and credentials offered through RapidAPI. There is a charge for a subscription.

**Compatibility note:** the current app connects directly to the [ADS-B Exchange v2 gateway](https://gateway.adsbexchange.com/api/aircraft/v2/docs/index.html) using an `x-api-key` header. RapidAPI credentials use a different gateway and authentication headers; a RapidAPI `X-RapidAPI-Key` is not interchangeable with the direct-gateway key. Obtain a key entitled to the direct v2 geographic radius endpoint from ADS-B Exchange, paste it into **ADS-B Exchange API key**, and select **Save ADS-B Exchange**. RapidAPI gateway support is not currently implemented.

#### Quota and Server Reset Handling

- OpenSky's `X-Rate-Limit-Remaining` is a response header, not a separate quota-query endpoint. While OpenSky is active, the app samples it from ordinary aircraft responses initially and every **10 minutes** (at the next response if polling is slower). This adds **no extra API requests**.
- The first HTTP 429 triggers an immediate quota sample. Aircraft polling and manual refresh then pause until the server's `X-Rate-Limit-Retry-After-Seconds` or `Retry-After` deadline, including when it is shorter than 30 minutes. Without a valid server deadline, the pause is **30 minutes**.
- A successful aircraft response after the deadline confirms that requests are accepted again, clears the pause, and resumes the configured poll rate. Its quota header is sampled to confirm the reset; subsequent quota sampling uses **30-minute** intervals for that account session.
- The account panel displays remaining credits, the last quota sample time, and the retry deadline. Missing or invalid headers are shown as unknown/error, never guessed.
- The ADS-B Exchange v2 documentation exposes neither an equivalent quota endpoint nor a remaining-quota header. The app does not invent one or spend extra requests probing for it. It retains token-bucket backoff and honors `Retry-After` when supplied; the panel explicitly marks remaining quota as unavailable.

Alternatively, set the ADS-B Exchange key in the environment **before launching the app**. For PowerShell, prompt for it without putting the key into command history:

```powershell
$key = Read-Host "ADS-B Exchange API key" -AsSecureString
$env:ADSBEXCHANGE_API_KEY = [System.Net.NetworkCredential]::new('', $key).Password
npm run tauri -- dev --config src-tauri\tauri.desktop.conf.json
```

Configure MY grid (and DX grid when available), enable **ADS-B Exchange** in the aircraft source controls, and move it above OpenSky/dump1090 using the priority arrows. Existing source defaults remain unchanged. The query uses a circle centered on MY station covering the station path plus a 400 km margin. Your API plan must permit the requested radius.

- Polls every **30 seconds by default** (configurable above), with gzip compression and the `x-api-key` header.
- Converts API altitude (feet), speed (knots), and vertical rate (feet/minute) to the shared aircraft model.
- Preserves position age using the API server timestamp and `seen_pos`.
- Backs off on HTTP 429; authentication/network errors are surfaced in source status and the app log, with fallback to the next enabled source.
- Manual refresh is limited to at least five seconds between requests within a feed session. Your key's limits are shared with other applications.
- Saved keys stay in the OS credential store and are passed privately to the backend; environment keys remain in the backend environment. They are not returned to the frontend.

Rebuild the Python sidecar and restart the desktop app after updating. Rebuild native mobile packages for the new storage and authenticated-request commands; browser-only previews cannot access secure device storage. Android/iOS device builds require their respective SDKs.

### Mobile Mode

The mobile app operates independently:

- Enter your grid locator in settings
- Select OpenSky or ADS-B Exchange in Settings; aircraft in your area appear on the compass rose
- Tap an aircraft to see its azimuth, elevation, and distance
- Speech callouts guide antenna pointing hands-free
- Offline callsign database via callook.info / IndexedDB

### Tech Stack

- **Frontend:** React 19, TypeScript
- **Desktop shell:** Tauri 2.x (Rust)
- **Backend (desktop):** Python — PyInstaller sidecar
- **Backend (mobile):** TypeScript tracking with native Rust authenticated HTTP/secure storage, no Python sidecar
- **Aircraft data:** OpenSky Network and ADS-B Exchange REST APIs, local dump1090/readsb on desktop
- **Rotator control:** Hamlib
- **Digital modes:** WSJT-X UDP protocol, Q65-15s

## Building from Source

### Prerequisites

- [Node.js](https://nodejs.org/) 22+
- [Rust](https://rustup.rs/) (stable)
- [Python](https://www.python.org/) 3.12+ (desktop only)
- [PyInstaller](https://pyinstaller.org/) (desktop only)

### Desktop

```bash
npm ci
pip install pyinstaller
pyinstaller --onefile --name airwsjtx_bridge --distpath src-tauri/binaries --paths . sidecar/airwsjtx_bridge.py
npx tauri build --config src-tauri/tauri.desktop.conf.json
```

#### VS Code on Linux (no package installation)

After installing the prerequisites and Node dependencies (`npm ci`), open this
repository folder in VS Code and use **Terminal > Run Task**:

- **AirWSJT-X: Build** builds the frontend and release desktop executable with
  `npm run tauri -- build --no-bundle`. It does not generate or install a `.deb`.
  This is also the default **Ctrl+Shift+B** task.
- **AirWSJT-X: Run** launches the existing
  `src-tauri/target/release/airwsjtx` executable. Build it first; this task does
  not rebuild.
- **AirWSJT-X: Build and Run** completes the build successfully before launching
  the executable.

These tasks run from the repository root. The Linux source-sidecar wrapper
requires `python3` on PATH and the source checkout to remain available.
Close the application to finish the Run task. For live frontend development
instead, run `npm run tauri -- dev` from the repository root.

### Android

```bash
npm ci
npx tauri android init
npx tauri android build --apk
```

### Running Tests

```bash
python -m unittest discover -s tests -v
```

## PSK Reporter Strategy

AirWSJT-X uses WSJT-X native reporting as the primary PSK Reporter path. The AirScatter profile provides operating conventions for discoverability while avoiding non-standard reporter protocol hacks.

## UI Reference

The **Stations** tile shows the total MY–DX great-circle distance in kilometers, using the centers of the active station grids (including manual overrides). Valid 4, 6, and 8 character Maidenhead locators are supported; missing or invalid grids show `-- km`.

Beside **Aircraft altitude**, the locked-aircraft tile shows **Distance from MY** in kilometers. This is the altitude-inclusive slant range used by the candidate table and refreshes with each locked-aircraft position update. Rebuild the Python sidecar after updating to include this value; older sidecars show `-- km`.

The locked-aircraft tile keeps **From My Station** and **From DX Station** side-by-side. The panel sizes to its contents rather than filling unused column space and scrolls when needed in shorter windows.

### WSJT-X Status Strip TX Indicator

The **Az / El** toolbar below the title bar is always available. Select **Panel** for an inline readout, **Overlay** for a floating readout, or **Hide** to dismiss it. The **Scale** slider adjusts the readout from 70% to 180%, even while hidden. Display mode and scale are remembered across restarts.

The locked-aircraft tile refreshes callsign, altitude, both stations' Az/El, score, and off-path angle on aircraft feed updates. OpenSky and ADS-B Exchange default to a configurable 30-second polling cadence; dump1090 reports approximately every five seconds. The tile shows **Last aircraft position update** age, updated each second, and marks data **stale** only when its age is greater than the active source's configured poll interval plus five seconds. For a 30-second poll, 35 seconds is not stale; an age greater than 35 seconds is stale. For dump1090, the threshold is greater than 10 seconds. Changing a poll rate or switching sources updates the threshold immediately. This age comes from the aircraft position report, not from rereading cached data. Rate-limit pauses do not extend the configured freshness threshold; unknown timestamps are explicitly shown as unknown.

- `TX○` — WSJT-X is idle (not transmitting)
- Red `TX●` — WSJT-X is currently transmitting (PTT ON)
- `TX <value>Hz` — current TX frequency offset (`tx_df`) reported by WSJT-X

## FAQ

### Is AirWSJT-X safe for airplanes?

Yes — AirWSJT-X has absolutely no effect on aircraft or aviation systems.

AirWSJT-X is a **passive** ham radio tool. It reads publicly available aircraft position data (the same data used by flight-tracking apps like Flightradar24 and ADS-B Exchange) to identify airplanes flying between two ham radio stations. When a plane is in the right geometric position, operators can bounce weak VHF/UHF radio signals off the aircraft's metal fuselage to communicate with each other — a technique called **aircraft scatter**.

- **No equipment is installed on the airplane.** The aircraft is simply a passive reflector, like a building or a mountain.
- **No frequencies are shared with aviation.** Ham radio bands are completely separate from aircraft communication and navigation frequencies.
- **No signals are transmitted *to* the airplane.** The radio energy that reaches the fuselage is extremely weak (far less than a typical WiFi router) and reflects naturally off the metal surface.
- **No aircraft systems are affected.** The airplane's crew, passengers, avionics, and navigation are completely unaware of and unaffected by the process.

Aircraft scatter is a well-established technique in amateur radio that has been used safely for decades.

### What's the difference between desktop and mobile?

The **desktop app** is the full aircraft scatter automation system — it connects to WSJT-X, controls your rotator, and manages the complete QSO workflow. The **mobile app** is a standalone aircraft tracker designed for field use — it shows you where aircraft are relative to your position, with a compass display and speech callouts for hand-pointing antennas.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Copyright (C) 2026 Carlos Picoto (AD7NP)

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

See [LICENSE](LICENSE) for the full text.

## Author

**Carlos Picoto — AD7NP**

- Website: [ad7np.us](https://cpicoto.github.io/ad7np/)
- QRZ: [AD7NP](https://www.qrz.com/db/AD7NP)
- Club: [Western Washington Amateur Television Society (WW7ATS)](https://www.qsl.net/ww7ats)
