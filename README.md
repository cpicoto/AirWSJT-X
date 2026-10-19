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
| **Windows** (ARM64 / Snapdragon) | `_arm64-setup.exe` | [ARM64 build artifacts](https://github.com/cpicoto/AirWSJT-X/actions/workflows/windows-arm64.yml) (also included in future tagged releases) |
| **Linux** (x64) | `.AppImage` / `.deb` / `.rpm` | [Latest release](https://github.com/cpicoto/AirWSJT-X/releases/latest) |
| **Android** (ARM) | `.apk` (signed, arm64 + armeabi-v7a) | [Download](https://github.com/K7MDL2/AirWSJT-X/releases/latest/download/AirWSJT-X-android-arm.apk) |
| **Android** (x86_64) | `.apk` (signed) | [Download](https://github.com/K7MDL2/AirWSJT-X/releases/latest/download/AirWSJT-X-android-x86_64.apk) |

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

**Start QSO** marks the app's session as active; it does not enable transmission. Press **Enable Tx** in WSJT-X to transmit. By default, **Abort** and **Finish QSO** only update the app's session. The optional **Halt WSJT-X Tx on Abort/Finish** checkbox also sends Halt Tx to the selected WSJT-X instance; enable **Accept UDP requests** in WSJT-X's **Settings > Reporting** to use it. If delivery fails, use **Halt Tx** in WSJT-X itself. A QSO Logged message from the selected instance automatically completes an active app session.

The compass outer ring marks the DX station's bearing and callsign. **Auto** mode follows the locked aircraft; **Manual** mode accepts operator pointing commands instead. **Sync** in Manual mode aligns the displayed target with the reported rotator position without moving it. Turning rotor control off blocks new movement commands; **Stop** remains available to stop an existing move.

**Sky Dome** is an optional, desktop-only polar view of aircraft azimuth and elevation, hidden by default. Use **Show / Hide** beside the **Az / El** display controls; the choice is remembered. It sits above the main columns, alongside the Az / El panel when visible, and scales to the window without clipping the dome. The outer edge is the horizon (0 degrees), the centre is the zenith (90 degrees), and labelled concentric rings mark 22.5, 45, and 67.5 degrees. Click an aircraft to lock it, or a point in the sky to set the rotator target.

Locking an aircraft adds a blue estimated course across the dome: a faint approach
extension, dashed future track, direction arrow, and one-minute markers for the
next five minutes while above the horizon. The curve updates with aircraft
reports using its geographic position, ground track, speed and altitude relative
to MY station, including station altitude and the same effective-Earth-radius
elevation model as tracking. It extends up to 30 minutes each way, clipped at the
radio horizon. Both directions are estimates assuming no turns or altitude/speed
changes, not recorded history or a filed flight plan. Missing station or motion
data is reported instead of drawing a guessed course. Only the locked aircraft's
course is shown, even when it is outside the first 20 candidates; mobile is
unchanged.

### Mobile Mode

The mobile app operates independently — enter your grid locator, select an aircraft data source (OpenSky, ADS-B Exchange, or a dump1090 receiver on your network), and aircraft in your area appear on the compass rose with azimuth, elevation, and distance. Speech callouts guide antenna pointing hands-free.

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
- **dump1090 / readsb** — Connects via SBS TCP (port 30003). On desktop it defaults to a receiver on this computer (`127.0.0.1`); to use a receiver elsewhere on your network (for example a Raspberry Pi), expand **dump1090 source** below **Aircraft accounts**, enter its IP address or hostname, and click **Save dump1090 source**. The port stays 30003. The address is applied immediately and remembered across restarts. On mobile there may not be a local receiver, so the same **dump1090 source** section (in **Settings**) only takes a network address: saving it selects **dump1090 (network)** as the aircraft source, and **Disable dump1090** clears it and returns to OpenSky. The phone must be on the same network as the receiver (iOS asks for Local Network permission on first use).  If you have a tablet for example, with dump1090 and a USB RTL-SDR running, then 127.0.0.1 is valid entry.

Credentials are stored in the OS credential manager (Keychain, Credential Manager, Secret Service, or Android Keystore). Poll intervals default to 30 seconds and are independently configurable per provider.

> **Remote dump1090 not connecting?** The Debian/Raspberry Pi package of `dump1090-mutability` listens on localhost only by default. On the receiver, edit `/etc/default/dump1090-mutability`, set `NET_BIND_ADDRESS="0.0.0.0"` (and keep `NET_SBS_PORT=30003`), then run `sudo systemctl restart dump1090-mutability`. Verify with `ss -ltnp | grep 30003` on the receiver, and check that no firewall blocks port 30003.

> **One RTL-SDR shared between dump1090 and `rtl_tcp` on the receiver?** [docs/remote-dump1090](docs/remote-dump1090/README.md) has a small first-come-wins service that starts dump1090 when AirWSJT-X connects and releases the dongle again when it disconnects, so nothing has to be switched by hand.

## Building from Source

### Prerequisites

- [Node.js](https://nodejs.org/) 22+
- [Rust](https://rustup.rs/) (stable)
- [Python](https://www.python.org/) 3.12+ (desktop only)
- [PyInstaller](https://pyinstaller.org/) (desktop only)

### Desktop

These commands are for ordinary desktop builds, **not macOS TestFlight / Mac App
Store**. Sandboxed macOS distribution requires a signed, bundled Python helper
instead of `--onefile`; follow the
[TestFlight packaging guide and release checks](CONTRIBUTING.md#macos-testflight-packaging)
and `.github/workflows/macos-testflight.yml`.

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

GitHub Actions builds signed Android APKs for pushes and pull requests targeting `main`. Download them from the run's **Artifacts** section. Pull requests without access to repository signing secrets use a temporary key, so those APKs cannot update an installation signed by the release key. Version-tag builds are also attached to the GitHub release.

### Desktop (VS Code)

Open in VS Code and use **Terminal > Run Task**:
- **AirWSJT-X: Desktop Build** — frontend + release desktop binary (`Ctrl+Shift+B`)
- **AirWSJT-X: Desktop Run** — launch the built release binary
- **AirWSJT-X: Desktop Build and Run** — build release binary, then launch
- **AirWSJT-X: Desktop Dev** — launch `tauri dev` with hot-reloading
- **AirWSJT-X: Build Android APK** — build the Android APK (requires Android SDK/NDK setup) (currently untested)

### Windows ARM64

The **Windows ARM64 setup** workflow runs on `windows-11-arm` with ARM64 Node.js, Python 3.12, and Rust. It builds both the desktop app and Python sidecar natively, checks their PE machine types, and exercises the frozen sidecar's JSON-line protocol from an empty working directory. Download `AirWSJT-X-windows-arm64` from the run's **Artifacts** section and extract `AirWSJT-X_<version>_arm64-setup.exe`. Tagged releases attach the same setup alongside the existing x64 installers.

For a local build, use Windows 11 ARM64, native ARM64 Python/Node.js, and Visual Studio's C++ ARM64 build tools:

```powershell
npm ci
python -m pip install pyinstaller certifi
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --onefile --name airwsjtx_bridge --distpath src-tauri\binaries --paths . sidecar\airwsjtx_bridge.py
Move-Item -Force src-tauri\binaries\airwsjtx_bridge.exe src-tauri\binaries\airwsjtx_bridge-aarch64-pc-windows-msvc.exe
rustup target add aarch64-pc-windows-msvc
npx tauri build --ci --target aarch64-pc-windows-msvc --config src-tauri\tauri.desktop.conf.json --config src-tauri\tauri.windows-arm64.conf.json
python scripts\verify_windows_arm64.py --app src-tauri\target\aarch64-pc-windows-msvc\release\airwsjtx.exe --sidecar src-tauri\binaries\airwsjtx_bridge-aarch64-pc-windows-msvc.exe
```

The setup is under `src-tauri\target\aarch64-pc-windows-msvc\release\bundle\nsis`. ARM64 uses NSIS, not WiX/MSI. The NSIS installer bootstrapper may run under Windows emulation; the installed application and Python sidecar are native ARM64. This build does not change the architecture requirements of separately installed WSJT-X or rotctld.

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

### Why are both WSJT-X and NET disconnected on macOS TestFlight?

The Python backend may have failed to start rather than lost network access.
Version 0.3.22 build 18 fixes a sandbox-incompatible Python launcher that blocked
both services; update older affected builds through TestFlight. If the problem
persists, report the version/build number and native sidecar startup errors.
See [diagnostic guidance](CONTRIBUTING.md#diagnosing-simultaneous-wsjtx-and-net-disconnection)
before changing firewall or aircraft-account settings.

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
