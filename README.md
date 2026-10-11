# AirWSJT-X

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Tests](https://github.com/cpicoto/AirWSJT-X/actions/workflows/test.yml/badge.svg)](https://github.com/cpicoto/AirWSJT-X/actions/workflows/test.yml)
[![Release](https://img.shields.io/github/v/release/cpicoto/AirWSJT-X)](https://github.com/cpicoto/AirWSJT-X/releases/latest)

Aircraft scatter automation for [WSJT-X](https://wsjt.sourceforge.io/) on 23 cm and above — plus a standalone mobile aircraft tracker for portable/field use.

## What It Does

AirWSJT-X uses real-time aircraft position data to find commercial airplanes flying between two ham radio stations. When a viable reflector is detected, it locks your rotator, configures WSJT-X for Q65-15s, and manages the QSO sequence — from airplane selection through contact completion.

The **mobile app** brings standalone aircraft tracking to the field: a compass rose and elevation arc for hand-pointing antennas at aircraft, with speech callouts for hands-free operation. No WSJT-X or rotator required.

**Website:** [ad7np.us](https://ad7np.us)

## Downloads

All platforms are built from CI on every tagged release.

| Platform | Format | Download |
|----------|--------|----------|
| **macOS** (Apple Silicon) | `.dmg` | [Latest release](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| **Windows** (x64) | `.msi` / `.exe` | [Latest release](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| **Linux** (x64) | `.AppImage` / `.deb` / `.rpm` | [Latest release](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| **Android** | `.apk` (signed) | [Latest release](https://github.com/cpicoto/AirWSJT-X/releases/latest) |

## Architecture

```
┌─────────────────────────────────────────────────┐
│  React 19 + TypeScript Frontend                 │
├─────────────────────────────────────────────────┤
│  Tauri 2.x (Rust)                               │
│  ├── Desktop: Python sidecar (PyInstaller)      │
│  │   ├── airwsjtx.tracker      Aircraft scoring │
│  │   ├── airwsjtx.supervisor   QSO orchestrator │
│  │   ├── airwsjtx.wsjtx        WSJT-X UDP ctrl  │
│  │   ├── airwsjtx.rotator      Hamlib rotator   │
│  │   └── airwsjtx.airscout     Feed management  │
│  └── Mobile: native Rust HTTP + secure storage  │
├─────────────────────────────────────────────────┤
│  Aircraft Data Sources                          │
│  ├── OpenSky Network (OAuth2 or anonymous)      │
│  ├── ADS-B Exchange v2 (API key)                │
│  └── dump1090 / readsb SBS (desktop, local)     │
└─────────────────────────────────────────────────┘
```

### Desktop Mode

The desktop app coordinates WSJT-X, your rotator, and aircraft tracking into an automated scatter workflow. Q65-15s operation requires two configured stations, a locked aircraft, stable rotator lock, a supported 23 cm+ band, and explicit operator start.

When multiple WSJT-X instances are on the network, the **Rig** dropdown selects a discovered instance by ID or config name. **Any rig** accepts the first source.

### Mobile Mode

The mobile app operates independently — enter your grid locator, select an aircraft data source (OpenSky or ADS-B Exchange), and aircraft in your area appear on the compass rose with azimuth, elevation, and distance. Speech callouts guide antenna pointing hands-free.

### Remote Station Database

AirWSJT-X fetches a shared station database at startup from [ad7np.us/RoverAIM/DB](https://ad7np.us/RoverAIM/DB) to auto-resolve DX station grid squares. Entries use `callsign@location` format (e.g., `AD7NP@home`, `W7PUA@MarysPeak`). User-defined grid overrides in the Stations panel take priority over the remote database.

## Quick Start

### WSJT-X Setup

1. In WSJT-X **Settings > Reporting**, set the UDP server to `224.0.0.1` and port `2237`
2. In AirWSJT-X, select your rig from the **Rig** dropdown (or **Any rig**)
3. Configure **MY callsign** and **MY grid** in the Stations panel (or let WSJT-X provide them)
4. On Windows, allow AirWSJT-X and its Python sidecar through Windows Firewall

### Aircraft Data Sources

Expand **Aircraft accounts** in the desktop aircraft panel (or mobile **Settings**) to configure credentials:

- **OpenSky Network** — Create an account at [opensky-network.org](https://opensky-network.org/), create an API client, and paste the `clientId` and `clientSecret` values. The app handles OAuth2 token management automatically.
- **ADS-B Exchange** — Obtain an API key from [ADS-B Exchange](https://gateway.adsbexchange.com/api/aircraft/v2/docs/index.html) for the direct v2 geographic radius endpoint. Note: RapidAPI keys use a different gateway and are not interchangeable.
- **dump1090 / readsb** — Desktop only. Connects via SBS TCP (port 30003) to a local receiver.

Credentials are stored in the OS credential manager (Keychain, Credential Manager, Secret Service, or Android Keystore). Poll intervals default to 30 seconds and are independently configurable per provider.

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
npx tauri build
```

### Android

```bash
npm ci
npx tauri android init
npx tauri android build --apk
```

### Linux (VS Code)

Open in VS Code and use **Terminal > Run Task**:
- **AirWSJT-X: Build** — frontend + release binary (`Ctrl+Shift+B`)
- **AirWSJT-X: Run** — launch `tauri dev`
- **AirWSJT-X: Build and Run** — build then launch

### Running Tests

```bash
# Python backend tests (cross-platform)
python -m unittest discover -s tests -v

# TypeScript type checking
npx tsc --noEmit

# Rust unit tests
cargo test --manifest-path src-tauri/Cargo.toml --lib
```

## FAQ

### Is AirWSJT-X safe for airplanes?

Yes — AirWSJT-X has absolutely no effect on aircraft or aviation systems.

It is a **passive** ham radio tool that reads publicly available aircraft position data (the same data used by Flightradar24) to identify airplanes flying between two stations. Operators bounce weak VHF/UHF signals off the aircraft's metal fuselage — a technique called **aircraft scatter** that has been used safely for decades.

- No equipment is installed on the airplane — it is simply a passive reflector
- No frequencies are shared with aviation — ham radio bands are completely separate
- No signals are transmitted *to* the airplane — the reflected energy is far weaker than WiFi
- No aircraft systems are affected — crew, passengers, and avionics are completely unaware

### Desktop vs. Mobile?

The **desktop app** is the full automation system — WSJT-X integration, rotator control, QSO state machine. The **mobile app** is a standalone field tracker — compass display and speech callouts for hand-pointing antennas at aircraft.

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Copyright (C) 2026 Carlos Picoto (AD7NP)

GNU General Public License v3.0 — see [LICENSE](LICENSE) for details.

## Author

**Carlos Picoto — AD7NP**

- Website: [ad7np.us](https://ad7np.us)
- QRZ: [AD7NP](https://www.qrz.com/db/AD7NP)
- Club: [Western Washington Amateur Television Society (WW7ATS)](https://www.qsl.net/ww7ats)
