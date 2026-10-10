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

### Mobile Mode

The mobile app operates independently:

- Enter your grid locator in settings
- Aircraft in your area appear on the compass rose (via OpenSky Network)
- Tap an aircraft to see its azimuth, elevation, and distance
- Speech callouts guide antenna pointing hands-free
- Offline callsign database via callook.info / IndexedDB

### Tech Stack

- **Frontend:** React 19, TypeScript
- **Desktop shell:** Tauri 2.x (Rust)
- **Backend (desktop):** Python — PyInstaller sidecar
- **Backend (mobile):** TypeScript — standalone, no sidecar
- **Aircraft data:** OpenSky Network REST API
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

### WSJT-X Status Strip TX Indicator

The **Az / El** toolbar below the title bar is always available. Select **Panel** for an inline readout, **Overlay** for a floating readout, or **Hide** to dismiss it. The **Scale** slider adjusts the readout from 70% to 180%, even while hidden. Display mode and scale are remembered across restarts.

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
