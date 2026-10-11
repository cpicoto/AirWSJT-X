# Changelog

## Unreleased

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
