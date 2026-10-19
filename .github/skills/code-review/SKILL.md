---
name: "airwsjtx-code-review"
description: "Code review checklist for AirWSJT-X (Tauri 2 + React 19 + Python sidecar, multi-platform). Trigger on PRs, diffs, or review requests touching this project."
---

# AirWSJT-X Code Review

## Project Stack

- **Frontend:** React 19 + TypeScript, single-file components (`App.tsx`, `RotatorPanel.tsx`, `PlaneLockPanel.tsx`, `AircraftAccounts.tsx`, `MobileApp.tsx`), Vite bundler
- **Backend:** Tauri 2.x (Rust), thin shell — delegates to Python sidecar via `tauri-plugin-shell`
- **Sidecar:** Python package `airwsjtx/` — supervisor, state machine, ADS-B sources, WSJT-X UDP, rotator control, callsign lookup
- **Platforms:** macOS (aarch64 .dmg), Windows (x64 .exe/.msi), Linux (AppImage/deb/rpm), Android (APK)
- **CI:** GitHub Actions — `release.yml` (desktop), `mobile.yml` (Android APK + signing), `test.yml`, `screenshots.yml`
- **Hosting:** Azure Static Website at `ad7np.us` (blob storage, no server-side code)
- **Repo:** `cpicoto/AirWSJT-X`, branch protection on `main` (PR + 2 status checks)

## Review Procedure

### 1. Scope and intent

- Read the PR description and every commit message.
- Confirm the change matches the stated intent — no scope creep, no unrelated cleanup mixed in.
- Check that the PR targets the correct base branch.

### 2. TypeScript / React (src/)

- **State management:** All UI state lives in `App.tsx` via `useState`/`useRef`. New state must follow the existing pattern: localStorage persistence where appropriate, `useEffect` cleanup on unmount.
- **Grid lookups:** Station grid resolution chain is: user override (`stationGridOverrides`) → remote DB (`remoteStationGrids`) → WSJT-X reported grid. Use `lookupGrid()` — never access `stationGridOverrides` directly for lookups.
- **CSS variables:** All colors must use CSS custom properties from `:root` in `index.css` (`--bg`, `--surface`, `--border`, `--text`, `--text-muted`, `--green`, `--yellow`, `--blue`, `--red`). No hardcoded hex colors in inline styles except in the `STATE_COLOR` constant.
- **Platform rendering:** `<select>` and `<option>` elements need global CSS rules, not just inline styles — GTK on Linux ignores inline styles on native form controls.
- **No WhatsApp/Discord markdown in UI:** The app uses standard HTML/JSX. Format tables and lists with HTML, not chat markdown.
- **Portable view:** Three modes (`hidden`, `panel`, `overlay`). Changes must not break any mode. Default is `hidden`.
- **Type safety:** No `any` types. Sidecar message payloads must be typed with interfaces. Use `as` casts only for sidecar RPC responses where the shape is known.

### 3. Rust / Tauri (src-tauri/)

- **Thin shell rule:** The Rust layer should only do: Tauri setup, sidecar lifecycle, event relay, credential storage (keyring). Business logic belongs in the Python sidecar.
- **Windows subsystem:** `main.rs` must have `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]` — without this, a console window appears on Windows.
- **Platform-conditional deps:** Use `[target.'cfg(target_os = "...")'.dependencies]` for platform-specific crates (keyring backends). Never add a platform crate unconditionally.
- **Sidecar communication:** All sidecar messages flow through `listen("sidecar-message")`. New event types must be handled in the main `useEffect` dispatch in `App.tsx`.
- **Error handling:** Sidecar spawn failures must log and surface to the user. Don't silently swallow errors from `ShellExt::sidecar()`.

### 4. Python sidecar (airwsjtx/)

- **State machine:** The supervisor runs a state machine (`state_machine.py`). State transitions must be explicit and logged. Never skip states.
- **ADS-B sources:** Multiple source backends (OpenSky, ADS-B Exchange, dump1090). New sources must implement the same interface and handle rate limiting / auth.
- **WSJT-X UDP:** The UDP listener (`wsjtx_udp.py`) handles WSJT-X protocol messages. Byte-level parsing is fragile — test with `test_wsjtx_udp.py`.
- **Geodesy:** Use `geo.py` for Maidenhead ↔ lat/lon, haversine, bearing. Don't duplicate geo math.
- **Thread safety:** The supervisor runs async (tokio on Rust side, asyncio on Python side). Shared state must be protected. The sidecar communicates via JSON-line stdout — never print debug output to stdout (it corrupts the message stream).
- **No stdout pollution:** Print statements in the sidecar break the Tauri JSON-line protocol. Use stderr or structured logging only.

### 5. Cross-platform concerns

- **Path separators:** Use `Path` / `pathlib` — never hardcode `/` or `\\`.
- **Android:** No filesystem access outside app sandbox. No desktop-only APIs (`keyring`, `rotator`). Check `#[cfg(target_os = "android")]` guards.
- **APK signing:** The `mobile.yml` workflow must include zipalign + apksigner steps. Unsigned APKs won't install.
- **CSP:** `tauri.conf.json` has `"csp": null` (permissive). If CSP is ever tightened, verify external fetches (remote station DB, ADS-B APIs) are allowed.
- **Font rendering:** The app uses the system font stack. Don't add web fonts — they increase bundle size and break offline use.

### 6. CI / workflows (.github/workflows/)

- **Release workflow:** Desktop builds produce macOS .dmg, Windows .exe/.msi, Linux AppImage/deb/rpm. Verify all targets are present.
- **macOS TestFlight/MAS:** Apply the [agent guardrails](../../../AGENTS.md#macos-testflight--mac-app-store-guardrails) and [release checks](../../../CONTRIBUTING.md#required-release-checks). Reject a return to PyInstaller `--onefile`, outer-only runtime signing, weakened sandbox/library-validation entitlements, or removal of the post-signing sandbox startup gate. Require installed TestFlight network validation for packaging/startup changes; an unsandboxed `.dmg` or successful upload is not sufficient.
- **Mobile workflow:** Must include APK signing (debug keystore generated in CI), zipalign, apksigner verify.
- **Test workflow:** Python tests (`tests/test_*.py`) and Playwright e2e tests (`e2e/*.spec.ts`). New features should have at least one test.
- **Action versions:** Use pinned major versions (`@v7`, not `@latest`). Dependabot manages bumps.

### 7. Security

- **Credentials:** API keys and keystore passwords must use GitHub Secrets or the `keyring` crate — never committed to the repo.
- **External fetch:** The app fetches from `ad7np.us` (remote DB) and ADS-B APIs. Validate response shapes — don't trust external JSON blindly.
- **No credential logging:** Sidecar must not log API keys, passwords, or tokens. Check `addLog()` calls for leaked secrets.
- **Azure deployment:** Storage account keys must never appear in code, CI logs, or PR descriptions.

### 8. Deployment (ad7np.us)

- **Azure blob structure:** Pages are deployed as both extensionless blobs and `{name}/index.html` for URL compatibility (e.g., `AirWSJT-X` and `AirWSJT-X/index.html`).
- **Relative paths:** Pages served at `/AirWSJT-X` (no trailing slash) resolve relative URLs from root, not from `AirWSJT-X/`. Use absolute paths from root (e.g., `AirWSJT-X/image.jpg`, not `image.jpg`).
- **Remote DB:** The shared station database is at `ad7np.us/RoverAIM/DB` — changes to its schema affect both RoverAIM and AirWSJT-X.

### 9. Final checks

- Run `npx tsc --noEmit` — must pass with zero errors.
- Run `python -m unittest discover tests -v` — all Python tests must pass.
- Verify the PR doesn't introduce `console.log` calls (use `addLog()` for user-visible logging).
- Check that no new dependencies were added without justification.
- Confirm commit messages follow the project convention: imperative mood, concise subject, optional body explaining why.
