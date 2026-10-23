# Changelog

## 0.3.30

### Added
- Add a display-only predicted usable time remaining column to Flight Data using mutual radio visibility and the inward-facing 90-degree geometry window. Keep all filters, scores, ranking and auto-lock unchanged; show unavailable data and the 30-minute cap explicitly.
- Show feed-reported aircraft ground-track heading in Flight Data as HDG°, with an explicit unavailable indicator, to help diagnose heading-filter exclusions without changing candidate rules.

### Fixed
- Make candidate position and heading filters, path-alignment scores and Off° independent of MY/DX station order by checking both ends. Preserve station-specific pointing readouts and watched-flight overrides, and honor an explicit zero heading tolerance.

### Changed
- Prefer the repository's virtual-environment Python for desktop sidecar builds when available, and ignore local virtual environments and generated unsuffixed sidecar output (#127).
- Keep predicted usable time observational only: the default heading tolerance, existing lock retention, and all duration-independent selection rules remain unchanged.

### Validation
- Automated checks do not establish installed desktop, live radio/rotator, or installed TestFlight network operation. Predicted usable time is not a reflection-quality or signal-strength guarantee.

## 0.3.29

### Fixed
- Refresh Flight Data and Top Candidates immediately after station, flight-favorite/filter, and source changes, including WSJT-X grid updates and source fallback. Settings refreshes do not initiate new auto-locks; normal feed updates retain auto-lock behavior (#125).
- Release a locked aircraft when it leaves the full filtered list or lacks usable airborne position and altitude data. Keep valid locks outside the top-five display limit and do not auto-lock a replacement in the same refresh (#125).
- Remove aircraft cache data older than 120 seconds before list refresh and tracking, so settings changes cannot revive expired candidates or retain their locks (#125).
- Accept aircraft heading along the MY-DX path in either direction within the configured tolerance. Allow clearing numeric fields while editing; blank angular filters restore their defaults on blur (#125).

### Changed
- Rebuild and stage the Python sidecar before VS Code desktop builds, including the installed runtime on Windows. Stop the build chain if the sidecar build fails. Release signing, entitlements, and macOS TestFlight packaging remain unchanged (#125).
- Update aircraft-lock, filter, and desktop-build documentation and regression coverage.

### Validation
- Automated checks do not establish installed desktop, live radio/rotator, or installed TestFlight network operation; device validation remains required.

## 0.3.28

### Fixed
- Require aircraft to be inside the inward-facing 90-degree boundaries at both MY and DX when a distinct path is set. Aircraft behind MY, beyond DX, or on either boundary now score zero in both desktop/backend and mobile scoring. Keep the existing weighted score inside these bounds and the coincident-station exception (#123).
- Preserve watched-flight filter overrides and tracking at zero score without inflating the aircraft's score (#123).
- Remove the blue mouse-focus outline from DX favorites while retaining the light border and neutral keyboard focus indicator (#123).

### Changed
- Remove leftover blue MY reciprocal dots from Sky Dome and the AZ compass while retaining green DX markers. Refresh desktop screenshots and scoring documentation (#123).

### Validation
- Automated source, browser, and packaging checks do not establish installed desktop or TestFlight network operation; device validation remains required.

## 0.3.27

### Added
- Watched-flight selection by exact ADS-B callsign, optional Only mode, and 20 locally saved flight/Only favorites. A watched flight bypasses candidate filters and remains locked below the normal 5% release threshold; manual unlock remains available (#121).
- Independent, unfiltered, score-sorted Flight Data with saved-flight highlighting, alongside the existing five-entry Top Candidates list (#121).
- Persistent vertical resizing for Flight Data and Log, collapsible numeric filters, clearer entry borders, and aligned dark favorites pickers with keyboard navigation (#121).
- Restore the last DX callsign, grid, altitude and source mode at startup; fresh WSJT-X data takes precedence in WSJT-X mode (#121).

### Fixed
- Report configured WSJT-X UDP port conflicts visibly instead of silently listening on a random port. Retry the configured port and clear the error when reception recovers, without displacing the main application layout (#118).
- Preserve original WSJT-X decode timestamps and display six-digit UTC HHMMSS independently of browser timezone. Missing or invalid timestamps display a dash (#119).
- Aircraft at least 90 degrees behind a distinct MY-to-DX path score zero, including watched flights; the no-path case retains its previous behavior (#121).
- Preserve keyboard selection, cancellation and focus navigation in DX and flight favorites (#121).

### Changed
- Keep the latest 30 log entries scrollable, newest first. Simplify MY outer-ring labels while retaining direction markers and DX labels (#121).

### Documentation
- Document Log4OM UDP forwarding, watched-flight behavior, scoring, favorites, panel resizing and DX restoration. Include updated desktop screenshots.
- Installed desktop and TestFlight network behavior still requires device validation; automated source and packaging checks do not establish live operation.

## 0.3.26

### Fixed
- Windows x64 and ARM64 installers now place the sidecar and its Python runtime together, avoiding temporary DLL extraction paths that Application Control may block during startup. Python, Visual C++, and TLS library signatures are preserved, and the OS-supplied Universal C Runtime is retained. Installed-package validation is still required on devices with affected policies (#116).

### Dependencies
- Updated `tauri-build` to 2.7.3, Tauri JavaScript API to 2.12.4, and Tauri CLI to 2.12.3 (#103, #104).
- Updated `serde_json` to 1.0.154 and Tokio to 1.53.3 (#105, #106).
- Updated React and ReactDOM to 19.3.1 (#107).
- Updated CI artifact downloads to `actions/download-artifact` v8 and Playwright to 1.64.1 (#108, #109).

### Documentation
- Corrected Windows x64 and ARM64 repeat-build staging recipes to replace both the generated sidecar executable and runtime directory, with regression coverage (#116).

## 0.3.25

### Added
- Auto mode speaks "Lost plane lock" when the locked aircraft is lost.
- Speech callouts in Auto mode now announce the locked aircraft's Az/El even when the rotator is off or not connected.
- DX station altitude in metres, used for aircraft elevation and scatter scoring.
- A persistent, 20-entry DX favorites list replaces the fixed station buttons. **Store DX** saves callsign, grid, and altitude; exact duplicates move to the top, and selecting a favorite switches DX to Local mode.
- Linux desktop speech fallback when WebKitGTK lacks the Web Speech API, using the first installed `spd-say`, `espeak-ng`, or `espeak`. Install `speech-dispatcher` or `espeak-ng` to enable it.

### Changed
- Sky Dome and the Az / El overlay open as their own floating windows that can leave the app window on desktop; browsers fall back to draggable, resizable in-page panels. Sky Dome no longer squeezes the main app layout, and its position and size are remembered.
- When the rotator is not connected, the Az and El rotor needles and hubs on the compass and elevation arc turn grey, and return to blue once the rotator is connected.
- Sky Dome marks the DX station on the outer ring with its callsign, as on the Az compass.
- The Az / El overlay is resized by dragging its frame, and its text scales in proportion; the Scale slider is removed. The overlay and Sky Dome each use a single **Open / Close** toggle button. Sky Dome and the overlay are hidden on first start, show a resize grip in the lower-right corner, and closing either window with its X switches its button back to Open.
- The docked Az / El panel is disabled (`AZEL_PANEL_ENABLED` in `App.tsx`); the code is kept so it can be re-enabled.
- On loss of plane lock in Auto mode, the last known Az/El is kept (no reset to 0) and dimmed in the display panel, overlay, and rotor panel to show it is not driven by current tracking data. Speech callouts pause and no new rotator commands are sent until the next plane is locked, avoiding rotor churn (#111).
- Tidied the Stations panel and added WSJT-X / Local source controls for MY and DX. Valid grids from WSJT-X take priority over callbook lookup in WSJT-X mode; locally entered DX values apply once complete and valid.
- Station callsigns support portable/mobile suffixes and prefixes, with home-callsign fallback for callbook lookup.
- Linux native speech uses a slower US English voice with pauses before azimuth and elevation values.

### Fixed
- Linux native speech reports missing engines, launch/exit failures, and cancellation failures in the app log instead of silently failing. Stopping or replacing callouts cancels only AirWSJT-X's own Speech Dispatcher client or owned espeak process, without interrupting screen readers or other clients.

### Documentation
- Added a user guide covering the Stations panel, DX favorites, and Auto / Manual operation.

## 0.3.24

### Fixed
- Windows sidecar packaging uses the OS-supplied Universal C Runtime instead of extracting `ucrtbase.dll` and UCRT forwarders, avoiding the reported Application Control `Bad Image` error (`0xc0e90002`). Python and Visual C++ runtimes remain bundled, with UPX compression and stripping disabled.

## 0.3.23

### Added
- Desktop Sky Dome course overlay for the locked aircraft: station-relative estimated approach and dashed future track, direction arrow, and one-minute markers for the next five minutes. Updates with aircraft reports, clips at the radio horizon, and reports missing prediction data. Desktop-only visibility defaults and mobile behavior are unchanged.

### Fixed
- Integrated the macOS TestFlight sandbox-startup fix validated in 0.3.22 build 18: signed nested Python helper packaging, preserved network entitlements and library validation, and a sandboxed startup gate before upload.
- Keep the locked aircraft in backend updates when it ranks below the first 20 candidates, preserve due-north ground tracks, and reserve space for course guidance at small desktop window sizes.

### Documentation
- Added agent/review guardrails and macOS release checks to prevent sandbox packaging regressions.

## 0.3.22

### Added
- **Desktop Sky Dome visualization**: optional polar-projection panel beside the Az/El display with labelled elevation reference rings at 22.5°, 45°, and 67.5°. Hidden by default with persisted Show/Hide preference. Desktop only — mobile unchanged.
- **Native Windows ARM64 setup builds**: ARM64 NSIS installer with native PE payloads and sidecar RPC verification. Published alongside existing x64 installers on tagged releases.
- ARM64 packaging tests, shared version synchronization script, and build documentation.

### Changed
- Rotator UI polish: rotor on/off and speech buttons, Sync, connect row, blocked clicks when rotor off.
- Derived QSO timeline: Idle/Locked/Tracking/Ready/QSO unified with header badge; Begin Session and Start QSO re-arm after abort/complete.
- WSJT-X QSO Logged parsing with auto-complete; opt-in Halt Tx on Abort/Finish via UDP.
- Dark-themed scrollbars and spin controls on Linux.

### Fixed
- **macOS TestFlight backend startup (build 18)**: restored WSJT-X UDP and aircraft-feed connectivity by replacing the sandbox-incompatible PyInstaller onefile launcher with a signed nested Python helper app. Embedded runtime libraries use the distribution signing identity, and a sandboxed backend startup check now gates uploads. Ordinary desktop and mobile packaging are unchanged.

## 0.3.21

### Added
- Mobile settings: credits/about section at the bottom with project info, contributors, and version number.

## 0.3.20

### Added
- **DX station compass indicator**: DX station direction and callsign shown on the outer compass rose ring.
- **WSJT-X QSO state tracking**: app state machine follows Idle → CQ → QSO workflow from WSJT-X UDP messages.
- `DarkSelect` component for consistent dark-themed dropdowns across the desktop UI.

### Fixed
- Linux WSJT-X rig filter box background changed from white to black for readability.
- Rotator panel UI improvements and extensive rotator testing fixes.

## 0.3.19

### Fixed
- Build: add explicit `esbuild` dependency (Vite 8 no longer bundles it).

## 0.3.18

### Fixed
- Build: add `vite-env.d.ts` for CSS module type declarations (fixes TS2882 after vite-typescript bump).

## 0.3.17

### Added
- **dump1090 network source** (desktop & mobile): connect to a remote dump1090/readsb receiver by IP or hostname on port 30003. Desktop has a new section below Aircraft accounts; mobile has a matching section in Settings. Saving an address auto-selects the "dump1090 (network)" source. iOS builds include a local-network usage description.
- dump1090 activity indicators: messages received, aircraft count, aircraft with position, and time since last message — shown in the desktop log, Scatter Aircraft panel, and mobile status badge.
- dump1090 "busy" state: when the receiver is in use by another program, a yellow status with the reason is shown and retries continue automatically, instead of falsely reporting "connected."
- Receiver host tooling (`docs/remote-dump1090`): systemd services to share one RTL-SDR between dump1090 and `rtl_tcp` on a remote host.
- **Path bearing filter**: aircraft are now filtered to within ±70° of the MY-to-DX station path. Only aircraft near the scatter corridor are shown, preventing the app from pointing at planes behind you.
- **Scatter score display**: each aircraft in the mobile list shows a color-coded scatter score (green ≥60%, yellow ≥30%, grey below). Aircraft are sorted by scatter score first, then distance.
- VS Code build tasks for Windows desktop development (build, run, build-and-run, dev).
- Auto-generated Windows sidecar wrapper in `build.rs` so desktop builds work without PyInstaller on local setups.
- Pre-build desktop process cleanup script to avoid Windows "access denied" file locks when rebuilding.

### Fixed
- Rotator: rewrote TCP reader with line-buffered parsing and consecutive-failure tolerance — handles bare float responses from Pyrodrifter ESP32 firmware alongside standard Hamlib tagged output.
- WSJT-X UDP: companion logging apps (ACLog, JTAlert, GridTracker, Log4OM, N1MM) are now filtered out so only real WSJT-X/JTDX instances drive the state machine.
- Rotator host and port are now saved to localStorage and restored on mount.

### Dependencies
- Bumped Tauri plugins, serde_json, vite/typescript tooling, and actions/upload-artifact.

## 0.3.15

### Fixed
- Android: the main page now scrolls as a whole, so the aircraft list is reachable on small screens. The list is also shown in landscape.
- iOS: builds declare no non-exempt encryption, so TestFlight no longer holds them for export-compliance review.

## 0.3.14

### Added
- Aircraft elevation now uses a K = 4/3 effective Earth radius (standard atmospheric refraction), so the radio horizon is more realistic.
- Station altitude is now part of the elevation and slant-range geometry. Desktop: new "Alt m" field next to MY callsign/grid. Mobile: the device GPS altitude is used automatically (location permission added for iOS and Android).
- Android speech callouts now use the phone's native text-to-speech engine, since the Android WebView has no Web Speech API.
- iOS app (TestFlight) and Mac App Store/TestFlight builds, both using bundle ID `us.ad7np.airwsjt-x`.

### Fixed
- Speech callouts now announce whenever azimuth or elevation changes by at least 1° (previously 3°); changes that arrived inside the minimum interval are no longer dropped, and the mobile app handles azimuth wrap-around at north.
- The mobile aircraft list now scrolls on Android.
- Mac App Store / TestFlight: nested executables are signed without the application-identifier entitlement (ITMS-90885), and the app declares a 12.0 minimum macOS (error 90869).
- Rebased the iOS/macOS TestFlight workflow on top of the current Android signing/flavor workflow, fixing "Expected multiple APK flavors, found 1" in manual Mobile Build runs.

> Note: the macOS bundle ID change moves the app's settings folder, so existing Mac installs lose saved aircraft settings once (Keychain credentials are unaffected).

## 0.3.12

### Fixed
- Android APKs are now signed with a persistent release key (repository secrets `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`) instead of a throwaway debug key regenerated on every run. A different key per release made Android reject in-place updates with a signature mismatch. The workflow now fails if the secrets are missing, if an APK is debug-signed, or if the ARM and x86_64 APKs use different certificates.
- Removed the macOS Intel build from the release workflow; the `macos-13` runner never started, which also blocked the website deploy job.

> Upgrade note: devices with an earlier debug-signed APK (v0.3.9–v0.3.11) must uninstall once before installing a release signed with the new key.

## 0.3.11

### Fixed
- Aircraft elevation and slant range now account for Earth curvature. Previously a flat-earth formula showed distant aircraft (e.g. FL369 at 8700 km) at ~0° EL instead of below the horizon, so unreachable aircraft were scored and auto-locked (#75).
- A DX grid selected in the app is no longer overwritten by WSJT-X's DX grid, so scoring, lock, and "From DX Station" AZ/EL use the chosen DX station (#76).
- Android release builds now produce separate signed ARM and x86_64 APKs, fixing the APK flavor validation/signing failure seen in v0.3.10.

### Validation
- `python -m unittest discover -s tests -v`
- `npx tsc --noEmit`

## 0.3.10

### Fixed
- Manual desktop speech callouts now follow the tracked aircraft azimuth/elevation in **Manual** mode even when the operator does not touch the pointing arcs.
- The desktop **Speech on** toggle now previews the current azimuth/elevation on hover so Windows users can confirm audio output immediately.

### Changed
- Restored the broader Android GitHub Actions APK flavor build so release automation signs and uploads the full generated APK set instead of the reduced ARM-only path.

### Validation
- `npm run build`
- `python -m unittest discover -s tests -v`
- `npx playwright test`

## 0.3.9

### Fixed
- Restored a visible, usable desktop speech-announcement control on Windows by surfacing **Speech callouts** before rotator connection instead of hiding it behind a live `rotctld` session.
- Added a prominent one-click **Speech on / Speech off** toggle in the rotator/manual status bar so azimuth/elevation announcements can be enabled or disabled quickly.
- Restored desktop azimuth/elevation speech behavior for manual pointing mode so Windows users can get spoken target updates without a connected rotator.

### Validation
- `npm run build`
- `npx playwright test e2e/speech-callouts.spec.ts`

## 0.3.5

### Fixed
- **macOS packaged app OpenSky TLS failure (root cause fix)**: The SSL context created by `_ssl_context()` was never actually passed to `urllib.request.urlopen`. All three HTTPS call sites — `OpenSkyFeed.fetch_once`, `OpenSkyFeed._get_token`, and `ADSBExchangeFeed.fetch_once` — now pass `context=` explicitly. Without this, Python's `urlopen` always used its default certificate search path which fails in a packaged macOS `.app` bundle.

### Notes for future maintainers
- The `_ssl_context()` helper returns a context built from `certifi` when available, or the system trust store. **But the context must be passed to every `urlopen` call — just creating it is not enough.**
- Any future HTTPS call added to `airscout.py` must pass `context=self._ssl_context()`.
- Test in a packaged `.app` bundle (not just `tauri dev`) before shipping macOS releases. The packaged runtime does not inherit the developer's system trust chain.

## 0.3.4

### Fixed
- Restored OpenSky connectivity in packaged macOS builds by explicitly using the local CA trust store (`certifi` when available, otherwise the system defaults) before creating outbound HTTPS requests.
- Added station locator normalization and a wider fallback bounding box so the feed remains usable even when one station is configured or a locator is partially malformed.
- Documented the packaging regression path for signed/distributed builds so future macOS releases validate runtime certificate trust before shipping.

### Why it regressed
A packaged macOS build was still using the app’s runtime trust chain instead of the system CA bundle. That caused HTTPS validation failures against `opensky-network.org` even though the API itself was healthy.

### Release validation
- Verified the OpenSky fetch path creates an explicit SSL context and uses it for `urllib.request.urlopen`.
- Confirmed the signed/distribution bundle metadata and bundle identifier were aligned for the macOS release target.
- Retained a verification note for future release candidates to check outbound HTTPS from the packaged binary before notarization/upload.
