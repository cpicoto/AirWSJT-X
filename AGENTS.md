# Agent instructions

## macOS TestFlight / Mac App Store guardrails

Read [the TestFlight packaging guide](CONTRIBUTING.md#macos-testflight-packaging)
before changing Python packaging, sidecar startup, macOS signing or entitlements,
Tauri bundle layout, or `.github/workflows/macos-testflight.yml`.

- TestFlight is sandboxed; a working Developer ID `.dmg` or an unsandboxed
  sidecar does **not** establish that TestFlight works.
- Keep TestFlight's PyInstaller `--onedir --windowed` helper app under
  `Contents/Helpers/airwsjtx_bridge.app`, launched by
  `scripts/macos_sidecar_launcher.c`. Do not substitute `--onefile`: its launcher
  fails before Python starts because App Sandbox denies its SysV semaphore
  operation (`semctl: Operation not permitted`).
- Import the distribution signing identity before PyInstaller runs and sign
  embedded Python libraries with the same team. Signing only the outer
  executable does not fix embedded ad-hoc signatures.
- Preserve the helper's sandbox-inheritance entitlements, the main app's network
  client/server entitlements, and hardened-runtime library validation. Do not
  bypass the sandbox or disable library validation to make a build pass.
- Preserve native-launcher JSON-line stdin/stdout pipes and process lifecycle.
  Keep nested code signing inside-out: libraries, helper app, then outer app.
- Keep `tests/test_macos_packaging.py` and the post-signing sandbox smoke gate
  enforced before upload. Run `scripts/verify_macos_sidecar.py` against the final
  signed bundle, not an intermediate or unsandboxed executable.
- The automated smoke check verifies backend startup and `get_status`, not live
  network connectivity. Packaging/startup changes also require WSJT-X UDP and
  live aircraft-feed checks in the sandbox, followed by installed TestFlight
  validation. CI success or an accepted upload alone is not runtime validation.
- Keep this packaging path scoped to macOS TestFlight/MAS. Do not apply it to
  ordinary desktop releases or mobile without an explicit requirement.

If both WSJT-X and NET are disconnected, inspect backend startup and native
stderr before assuming an internet outage. Version 0.3.22 build 17 still failed
with signed libraries; build 18's nested helper was confirmed working in
TestFlight. The same semaphore failure was reproduced in pre-Sky Dome 0.3.21
build 15, so do not attribute it to Sky Dome without new evidence.
