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

The compass outer ring marks the DX station's bearing and callsign. Candidate aircraft use the legacy geometry ranking by default; **Best-path ranking** also offers range-product and window-weighted alternatives. See [Plane scoring](docs/plane-scoring.md) for the criteria and limitations. The [User guide](docs/user-guide.md) covers the Stations panel and DX favorites list. **Auto** mode follows the locked aircraft; **Manual** mode accepts operator pointing commands instead. **Sync** in Manual mode aligns the displayed target with the reported rotator position without moving it. Turning rotor control off blocks new movement commands; **Stop** remains available to stop an existing move.

The **WSJT-X Decodes (UTC)** panel shows each decode's original WSJT-X time as six-digit `HHMMSS`, including delayed or replayed packets. Missing or invalid source times display an em dash instead of a browser-local arrival time.

**Sky Dome** is an optional, desktop-only polar view of aircraft azimuth and elevation, hidden by default. Use the **Open / Close** button beside the **Az / El** display controls; the choice is remembered. It opens as a floating, resizable window (a separate native window on desktop that can leave the app window), as does the Az / El readout, whose text scales as you drag its frame larger or smaller. The outer edge is the horizon (0 degrees), the centre is the zenith (90 degrees), and labelled concentric rings mark 22.5, 45, and 67.5 degrees. Click an aircraft to lock it, or a point in the sky to set the rotator target.

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

#### Sharing WSJT-X with Log4OM or another UDP application

Two applications cannot receive the same unicast stream when one exclusively
owns UDP port `2237`. AirWSJT-X reports a bind error and retries that port; moving
its listener to a random port would not redirect WSJT-X's packets.

For Log4OM versions with UDP proxy support, configure its incoming **JT message**
connection as a **UDP proxy** listening on `127.0.0.1:2238` and forwarding to
`127.0.0.1:2237`. Keep its existing QSO-processing options. In WSJT-X
**Settings > Reporting**, set the UDP server to `127.0.0.1`, port `2238`.
AirWSJT-X continues listening on `2237`. Keep Log4OM running for this route:
`WSJT-X -> Log4OM :2238 -> AirWSJT-X :2237`.

Alternatively, use the same multicast group and port in every receiver, but only
if all installed applications support multicast. Do not assume an older logger
supports it. Receiving status and decodes does not require enabling **Accept UDP
requests**; leave that option unchanged unless you need the optional Halt Tx
control described above. A one-way proxy does not establish that Halt Tx works.

### Aircraft Data Sources

Expand **Aircraft accounts** in the desktop aircraft panel (or mobile **Settings**) to configure credentials:

- **OpenSky Network** — Create an account at [opensky-network.org](https://opensky-network.org/), create an API client, and paste the `clientId` and `clientSecret` values. The app handles OAuth2 token management automatically.
- **ADS-B Exchange** — Obtain an API key from [ADS-B Exchange](https://gateway.adsbexchange.com/api/aircraft/v2/docs/index.html) for the direct v2 geographic radius endpoint. Note: RapidAPI keys use a different gateway and are not interchangeable.
- **Local RTL-SDR (Windows x64, macOS)** — Plug in a compatible RTL-SDR with a 1090 MHz antenna before launching; the app automatically discovers and starts the first accessible receiver. To retry or choose another dongle, expand **Local RTL-SDR receiver (USB, Windows x64 / macOS)** in Flight Data, click **Discover USB receivers**, select it, then **Start local ADS-B**. No external dump1090 service, account, hostname, or TCP port is required. The bundled app-owned native demodulator receives at 1090 MHz / 2.4 MS/s with automatic tuner gain, digital AGC on and bias tee **off**. Close other SDR applications using the dongle first. The app does not install or replace drivers: if discovery is empty, check the physical connection and, on Windows, the WinUSB driver for **Bulk-In Interface 0** (not a different USB device). macOS needs no driver; the sandboxed Mac App Store/TestFlight build cannot access USB receivers, so use the direct-download app. **Stop local receiver** releases the dongle and returns to configured network sources; closing the app also releases it. Native startup/USB errors and candidate-frame, rejected-CRC, valid-message, aircraft, and position counters appear in this panel. A running receiver with no valid messages is not proof of RF reception; antenna placement and local traffic determine coverage.
- **dump1090 / readsb** — Connects via SBS TCP (port 30003). On desktop it defaults to a receiver on this computer (`127.0.0.1`); to use a receiver elsewhere on your network (for example a Raspberry Pi), expand **dump1090 source** below **Aircraft accounts**, enter its IP address or hostname, and click **Save dump1090 source**. The port stays 30003. The address is applied immediately and remembered across restarts. On mobile there may not be a local receiver, so the same **dump1090 source** section (in **Settings**) only takes a network address: saving it selects **dump1090 (network)** as the aircraft source, and **Disable dump1090** clears it and returns to OpenSky. The phone must be on the same network as the receiver (iOS asks for Local Network permission on first use).  If you have a tablet for example, with dump1090 and a USB RTL-SDR running, then 127.0.0.1 is valid entry.

Credentials are stored in the OS credential manager (Keychain, Credential Manager, Secret Service, or Android Keystore). Poll intervals default to 30 seconds and are independently configurable per provider.

#### Validated RTL-SDR receivers

Live receive-only ADS-B (CRC-valid DF17 frames and decoded aircraft positions)
has been confirmed with the following hardware. Other RTL2832U-based dongles
supported by librtlsdr are expected to work but have not been tested.

| Receiver | USB ID | Tuner | Operating system | Driver | Result |
|---|---|---|---|---|---|
| Nooelec NESDR SMArt v5 | `0BDA:2838` | R820T | macOS 26.5.1 (Apple Silicon, arm64), direct-download build | None needed | 176 valid messages, 0 CRC errors, 54 positions, 5 aircraft in about 40 s |
| Nooelec NESDR SMArt v5 | `0BDA:2838` | R820T | Windows x64 | WinUSB (Zadig) on Bulk-In Interface 0 | Discovered, CRC-valid frames received |
| Generic RTL2832U dongle | `0BDA:2832` | R820T | Windows x64 | WinUSB (Zadig) on Bulk-In Interface 0 | 148 valid messages, 16 aircraft (10 positioned), 37 position fixes in one session |

The sandboxed Mac App Store/TestFlight build has no USB access, so local RTL-SDR
reception is unavailable there; Intel macOS (x86_64) is supported but not yet
validated with hardware.

Local reception accepts CRC-valid DF17 ADS-B only, without bit-error correction.
The app-owned helper now uses FlightAware dump1090's pinned
[`465e063` 2.4 MS/s demodulator](https://github.com/flightaware/dump1090/blob/465e06330372ab0586a6a5063c66b60a8750f2b6/demod_2400.c):
its phase-specific preamble shapes, relative signal/quiet checks, five
correlation slicers, format-score selection, packet skipping and overlap.
The former fractional-window demodulator is no longer used. The adapter uses
upstream no-correction score ordering: DF17/18 require zero CRC syndrome,
DF11 checks interrogator parity, and address-parity formats require a recently
validated ICAO. Only validated DF17 messages reach the aircraft decoder;
other accepted Mode S formats are counted separately, not turned into aircraft.
There is no bit correction, DF24, Mode A/C, or automatic gain adaptation.
A bounded queue separates capture from demodulation; queue gaps reset overlap.
The panel separates preamble candidates, failed DF17 phase trials, accepted other
Mode S messages and bad/unknown-address rejections. Phase trials can overlap:
these counters do not partition RF transmissions, and are not comparable with
the old helper's unvalidated-candidate counts. Raw DF17 payloads, zero CRC
syndromes and 12 MHz sample-clock timestamps (at the end of bit 56) are retained
in a bounded last-32 list, visible in the panel even after Stop. This allows
subsequent payload checks; it does not by itself prove a message's RF origin.
Aircraft identification and ground velocity are decoded directly; positioned
aircraft require an airborne even/odd global CPR pair within 10 seconds and
supported Q=1 barometric altitude (converted from feet to metres, not geometric
altitude). Local positions remain visible for less than 120 seconds; velocities expire after 15
seconds, and aircraft records after 180
seconds. Surface positioning, Q=0 Gillham altitude, DF18/TIS-B, and MLAT are not
implemented. This local USB path supports Windows x64 and direct-download macOS; other platforms retain
their existing network sources. Receiver indices are discovered for the current
USB arrangement; rediscover and select again after changing multiple dongles.
On Windows and macOS, app startup makes one bounded discovery attempt (three seconds)
and starts receive-only ADS-B on the first accessible RTL-SDR. Configured network
sources remain saved and are available on failure or Stop; automatic startup
does not overwrite their priority on disk. Multiple receivers use discovery
order; Stop, Discover and select another device if needed. Stop is respected
for the rest of that app run: there is no automatic retry loop.
There is no persisted auto-start opt-out setting. Disconnect the dongle before
launch for network-only operation, or Stop after launch. Existing gain is used;
gain is not saved across launches, so startup uses automatic gain, digital AGC
on and bias tee off. Discovery does not install drivers or grant USB permissions.
Windows security prompts may still depend on the unsigned preview executable
and its changing path; auto-start is not a way to bypass them.
Verbose sample/power/candidate counters are hidden under **Detailed IQ measurements**;
normal receiver status, errors and validated-message/aircraft counts remain visible.
Positions are checked against a recent established aircraft position with a
generous motion margin (at least 1200 knots plus 500 metres and timing allowance;
a fresh higher reported velocity increases the margin). Implausible jumps discard
the CPR pair but do not erase the previous valid fix. Stale references do not
block reacquisition. No DX-corridor filter or guessed receiver-range limit is used.
For local reception, a selected aircraft remains locked across temporary CPR
position gaps while its aircraft record is retained (up to 180 seconds without
a valid message). Local coordinates are accepted for DDE or Hamlib pointing
until 120 seconds after their actual position timestamp, then suppressed.
This user-selected two-minute tolerance retains an old coordinate: it is not a
live or predicted position, and no extrapolation is applied. Other aircraft's
messages and receiver/cache freshness never refresh that coordinate's timestamp.
Fresh position messages resume output.
Manual selection on any feed is retained through candidate-filter and score
changes; the manual selection policy is described below. Automatic selection
still uses the scatter window.

### Windows: PST Rotator via SatPC32 DDE

The rotator card includes **SatPC32 DDE output (Windows / PST Rotator)**.
Close SatPC32 (or another SatPC32-compatible publisher) first. In PST Rotator,
select **SatPC32** as the tracker and enable its **Tracking** mode. In
AirWSJT-X, explicitly select **SatPC32 DDE** in **Rotator output**, use
aircraft/rotator mode (not manual pointing), and lock an aircraft with a fresh
position. Enabling this can move a physical rotator controlled by PST Rotator.
Do not configure Hamlib and PST Rotator to drive the same hardware concurrently.

AirWSJT-X acts as the DDE **server**, using service `SatPC32`, topic
`SatPcDdeConv`, item `SatPcDdeItem`, and NUL-terminated `CF_TEXT`, for example
`SNACA314 AZ56.7 EL6.8 UP0 UMUSB DN0 DMUSB MA0.0`.
It supports both requests and hot-link advise updates.
Only the selected aircraft's station-A azimuth/elevation carries tracking data.
The trailing fields preserve the complete conventional SatPC32 record shape:
uplink/downlink are zero Hz, mode placeholders are USB, mean anomaly is zero.
They are not aircraft radio data or CAT/PTT commands. Do not use this output
for radio frequency tracking. There is no physical
rotator feedback or alignment confirmation from DDE.
`SN` follows the selected aircraft's flight identifier/callsign, not a fixed
satellite name. Whitespace is removed and letters are uppercased; blank or invalid
identifiers fall back to the ICAO address. Only ASCII letters, digits and hyphens
are allowed in this token. The aircraft callsign remains unchanged in the normal
UI. `AZ` is degrees clockwise
from **true north at MY station (A)**, not aircraft heading or the DX bearing.
`EL` is the same station-A radio-path elevation shown by the app, using its
4/3-effective-Earth model and configured station altitude. This is not a
vacuum/astronomical satellite elevation calculation. Values have one decimal
place with an ASCII decimal point; north is `AZ0.0`, never a wrapped `AZ360.0`.
The DDE panel shows the exact item text and station reference. A retained
published target aligns both dial hands in DDE mode and suppresses Hamlib
feedback deltas. These hands visualize the published command, not measured
PST/antenna position or confirmation of movement. No valid published target
shows an invalid readout rather than invented feedback; Hamlib retains measured
feedback and target deltas.
A retained
stale lock can show old coordinates while the transmitted item is
`** NO SATELLITE **`; it does not transmit those frozen display values.
Enabling DDE publishes an already-selected, valid position immediately, without
waiting for another feed update or sending a Hamlib command. If no valid position
is available yet, the next valid update for the selected aircraft supplies the
first target. The lock card's poll-based "stale" label is not the pointing cutoff:
a 7-second-old local fix is within the 120-second DDE limit, but selecting Off
or Hamlib, manual mode, or a below-horizon elevation still suppresses it.
Hot-link clients receive changed item text promptly (message pump checks every
50 ms) and a refresh at least once per second while enabled, including when
rounded angles remain unchanged. Request/poll clients read the current item
directly. A refresh never extends a position's validity or predicts a new fix.
After a synchronous request, unsolicited notifications wait for a 200 ms
quiet gap. This avoids overlapping request/advise DATA responses that produced
invalid data handles in independent 32-bit Windows DDEML client checks.
Continuous polling still gets the current position; hot-link updates resume
when polling pauses. Both 32-bit and 64-bit native test clients check exact
`SNACA314 AZ348.4 EL0.5 UP0 UMUSB DN0 DMUSB MA0.0` bytes including the terminating NUL.

The [PST Rotator manual](https://pstrotator.com/ANT/PstRotator%20User%20Manual.pdf)
(page 44) documents SatPC32 tracker selection and Tracking mode.
The [SatPC32 manual](https://www.dk1tb.de/manual_e.htm#DDEInterface) describes
its WiSP32-compatible DDE stream; an independent
[C# integration](https://github.com/M0LTE/satpc32-dde-utils/blob/master/satpc32-dde-utils/SatPC32Client.cs)
uses this service/topic/item and a space-delimited full `SNFO-29 ... MA...` record.
These sources do not establish which trailing fields PST requires, so the full
record is retained rather than guessing a shorter format.

Manual aircraft selection is separate from auto-lock ranking for all sources.
A manually selected aircraft is not released just because it falls outside
candidate filters or its scatter score drops. A temporary missing aircraft or
position stops DDE/rotator updates, but retains selection for up to 180 seconds
for local RTL-SDR (120 seconds for network sources)
without another observed aircraft record. Fresh data for the same aircraft
resumes output without selecting it again. Explicit unlock, changing active
source, an on-ground report, or that timeout releases it. Auto-lock retains
its candidate/window release behavior. A selected identity is not proof of
a fresh coordinate or an antenna alignment.

The single **Rotator output** selector chooses **Off**, **Hamlib (rotctld)**, or
on Windows **SatPC32 DDE (PST Rotator)**. These outputs are mutually exclusive
in the backend: DDE is cleared and stopped before enabling Hamlib; selecting
DDE blocks all Hamlib coordinate/park commands. The rotctld connection may stay
open for feedback, but is not required for DDE. Switching modes does not send a
Hamlib move or resend cached UI coordinates. Auto Hamlib output waits for a
current backend aircraft update and uses the same fresh, above-horizon safety
gate; direct coordinate commands require Manual mode and Hamlib output.
Output defaults to **Off on every launch**. The last explicit choice is recorded
as a preference but never automatically activated; legacy saved Rotor-control
settings are discarded without enabling either output.
Unlock, output Off, manual
mode, source stop, missing/expired positions, and below-horizon targets suppress
coordinates (`** NO SATELLITE **`). Local pointing and displayed positions expire
at 120 seconds; the selected-lock stale label uses that same limit.
network positions use their poll interval plus 5 seconds. Expiry is enforced
even if the feed stops updating. Stop selects Off; Park is available only with
Hamlib output. Select Off or exit AirWSJT-X to release the DDE service.
On a normal Windows exit, the app requests backend shutdown and waits up to
eight seconds before the helper-termination fallback. The backend disables
output, disconnects DDE conversations, unregisters the service, and releases
its DDEML instance before exiting. Abrupt crashes or forced termination cannot
guarantee that graceful sequence.
After an app restart, output still starts Off. Selecting DDE registers a new
service; a persistent client must reconnect and re-establish its advise link
on the new registration. The heartbeat refreshes an existing link, not a lost
conversation. Isolated native tests verify reconnecting clients recover across
server restart, but PST's own reconnect behavior remains an external dependency.
Clients decide whether to hold or park on loss of target: DDE does not provide
an emergency physical stop. Run both programs in the same Windows interactive
session and at the same privilege level. An existing SatPC32 service produces
a visible conflict error rather than publishing a second competing target.

Protocol references: [SatPC32 manual, general DDE interface](https://www.dk1tb.de/manual_e.htm),
[field parsing example](https://github.com/M0LTE/satpc32-dde-utils/blob/master/satpc32-dde-utils/SatPC32Client.cs).
Compatibility is checked with a native Windows DDEML test client on an isolated
test service; physical PST Rotator operation requires user-controlled validation.
The panel reports processed IQ samples, ADC-rail clipping, mean total input
power, validated-message power when available, application queue drops and
detected discontinuities. Mean input power includes noise/interference and is not
an ADS-B signal/SNR measurement. Hardware/USB overruns cannot be measured through
this API and are explicitly unavailable, not reported as zero. Queue drops count
known application losses only. Diagnostic sample counts exclude dropped buffers.
Native receiver updates push counters and measurements into the panel each second;
status polling remains a fallback.
The GPL-compatible upstream notices, license and adapted sources are bundled
alongside the helper. Older 2 MS/s IQ must be deliberately resampled before
replay; the rate-specific helper rejects it rather than silently misinterpreting it.
After starting, **Tuner gain** offers **Automatic (default)** and the exact
manual dB steps reported by the connected tuner. **Apply gain / restart**
restarts only local reception and resets its counters. Gain is not persisted;
The selector is visible before starting; discover a receiver to load its supported
manual steps. Automatic is the default, or select a manual value before Start.
Compare equal-length intervals and valid messages,
not raw frame counts alone: higher gain may overload on nearby signals, while
lower gain may lose weak aircraft. The native demodulator keeps digital AGC
enabled in both modes; bias tee remains off. The status explicitly warns when
no CRC-valid message has arrived for more than 15 seconds.
Backend RPC failures are displayed after a 10-second reply timeout rather than
leaving receiver controls stuck indefinitely. Discovery does not require MY/DX
stations; scatter scoring/list placement still uses the station context and only
positioned aircraft. A valid identification or velocity message alone does not
provide a position: complementary airborne CPR even/odd messages are needed.

> **Remote dump1090 not connecting?** The Debian/Raspberry Pi package of `dump1090-mutability` listens on localhost only by default. On the receiver, edit `/etc/default/dump1090-mutability`, set `NET_BIND_ADDRESS="0.0.0.0"` (and keep `NET_SBS_PORT=30003`), then run `sudo systemctl restart dump1090-mutability`. Verify with `ss -ltnp | grep 30003` on the receiver, and check that no firewall blocks port 30003.

> **One RTL-SDR shared between dump1090 and `rtl_tcp` on the receiver?** [docs/remote-dump1090](docs/remote-dump1090/README.md) has a small first-come-wins service that starts dump1090 when AirWSJT-X connects and releases the dongle again when it disconnects, so nothing has to be switched by hand.

## Building from Source

On Linux desktops whose WebKitGTK lacks the Web Speech API, speech callouts use
the first installed `spd-say`, `espeak-ng`, or `espeak` executable. Install
`speech-dispatcher` or `espeak-ng` to enable this fallback. Missing engines and
native launch/exit errors appear in the app's log; the Speech button indicates
platform support, not that an engine or audio output has been verified.
Stopping or replacing callouts cancels only AirWSJT-X's own Speech Dispatcher
client or terminates its owned espeak process, without interrupting other clients
such as screen readers.

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

On macOS and Linux:

```bash
npm ci
pip install pyinstaller certifi
python scripts/install_rtlsdr.py   # macOS only: builds the local RTL-SDR runtime
python -m PyInstaller --noconfirm --clean --distpath src-tauri/binaries sidecar/airwsjtx_bridge.spec
npx tauri build --config src-tauri/tauri.desktop.conf.json
```

Use the checked-in spec for desktop packaging. Windows 10+ supplies the Universal
C Runtime, so Windows bundles exclude `ucrtbase.dll` and `api-ms-win-crt-*.dll`
while retaining the Python and Visual C++ runtimes. UPX compression and stripping
are disabled to preserve the collected DLLs.

Windows uses an installed-directory runtime rather than extracting Python DLLs
to temporary `_MEI...` paths. Stage the output and include the Windows desktop
configuration when packaging (for ARM64, use the dedicated commands below):

```powershell
npm ci
python -m pip install pyinstaller certifi
python scripts\install_rtlsdr.py
python -m PyInstaller --noconfirm --clean --distpath src-tauri\binaries sidecar\airwsjtx_bridge.spec
Move-Item -Force src-tauri\binaries\airwsjtx_bridge\airwsjtx_bridge.exe src-tauri\binaries\airwsjtx_bridge-x86_64-pc-windows-msvc.exe
if (Test-Path -LiteralPath src-tauri\binaries\airwsjtx_bridge-runtime) { Remove-Item -LiteralPath src-tauri\binaries\airwsjtx_bridge-runtime -Recurse -Force }
Move-Item src-tauri\binaries\airwsjtx_bridge\airwsjtx_bridge-runtime src-tauri\binaries\airwsjtx_bridge-runtime
npx tauri build --config src-tauri\tauri.desktop.conf.json --config src-tauri\tauri.windows-desktop.conf.json
```

Keep `airwsjtx_bridge-runtime` next to the installed bridge executable. The
Windows desktop configuration includes it in the installer; do not distribute
the executable alone. Ordinary source-only development and Rust tests do not
need this frozen-runtime configuration.

### Android

```bash
npm ci
npx tauri android init
npx tauri android build --apk
```

GitHub Actions builds signed Android APKs for pushes and pull requests targeting `main`. Download them from the run's **Artifacts** section. Pull requests without access to repository signing secrets use a temporary key, so those APKs cannot update an installation signed by the release key. Version-tag builds are also attached to the GitHub release.

### Desktop (VS Code)

Open in VS Code and use **Terminal > Run Task**:
- **AirWSJT-X: Desktop Sidecar Build** — rebuild Python with PyInstaller and stage the host-target sidecar (and its runtime on Windows)
- **AirWSJT-X: Desktop Build** — rebuild Python sidecar, then frontend + release desktop binary (`Ctrl+Shift+B`)
- **AirWSJT-X: Desktop Run** — launch the built release binary
- **AirWSJT-X: Desktop Build and Run** — rebuild sidecar and release binary, then launch
- **AirWSJT-X: Desktop Dev** — launch `tauri dev` with hot-reloading
- **AirWSJT-X: Build Android APK** — build the Android APK (requires Android SDK/NDK setup) (currently untested)

The desktop build tasks require `python`, PyInstaller, and `rustc` on PATH.
Close the running desktop app before rebuilding on Windows so its executable
and Python runtime files are not locked. A failed sidecar build stops the task
chain instead of launching an app with an older backend. These tasks are for
ordinary host-native desktop builds, not macOS TestFlight/MAS packaging.

### Windows ARM64

The **Windows ARM64 setup** workflow runs on `windows-11-arm` with ARM64 Node.js, Python 3.12, and Rust. It builds both the desktop app and Python sidecar natively, checks their PE machine types, and exercises the frozen sidecar's JSON-line protocol from an empty working directory. Download `AirWSJT-X-windows-arm64` from the run's **Artifacts** section and extract `AirWSJT-X_<version>_arm64-setup.exe`. Tagged releases attach the same setup alongside the existing x64 installers.

For a local build, use Windows 11 ARM64, native ARM64 Python/Node.js, and Visual Studio's C++ ARM64 build tools:

```powershell
npm ci
python -m pip install pyinstaller certifi
python -m unittest discover -s tests -v
python -m PyInstaller --noconfirm --clean --distpath src-tauri\binaries sidecar\airwsjtx_bridge.spec
Move-Item -Force src-tauri\binaries\airwsjtx_bridge\airwsjtx_bridge.exe src-tauri\binaries\airwsjtx_bridge-aarch64-pc-windows-msvc.exe
if (Test-Path -LiteralPath src-tauri\binaries\airwsjtx_bridge-runtime) { Remove-Item -LiteralPath src-tauri\binaries\airwsjtx_bridge-runtime -Recurse -Force }
Move-Item src-tauri\binaries\airwsjtx_bridge\airwsjtx_bridge-runtime src-tauri\binaries\airwsjtx_bridge-runtime
rustup target add aarch64-pc-windows-msvc
npx tauri build --ci --target aarch64-pc-windows-msvc --config src-tauri\tauri.desktop.conf.json --config src-tauri\tauri.windows-desktop.conf.json --config src-tauri\tauri.windows-arm64.conf.json
python scripts\verify_windows_arm64.py --app src-tauri\target\aarch64-pc-windows-msvc\release\airwsjtx.exe --sidecar src-tauri\target\aarch64-pc-windows-msvc\release\airwsjtx_bridge.exe
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

### Windows "Bad Image" error with `python312.dll` / `ucrtbase.dll` and `0xc0e90002`?

This status means Windows Application Control blocked the extracted DLL, not
necessarily that the CPU architecture is wrong. Rebuild with the checked-in
sidecar spec, or install a release containing the packaging fix; do not disable
Windows security or download replacement system DLLs. Both the x64 and native
ARM64 Windows sidecars use the installed runtime layout described above, avoiding
temporary Python DLL extraction while using the OS-provided UCRT.

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
