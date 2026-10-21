# Contributing to AirWSJT-X

Thanks for your interest in contributing to AirWSJT-X! This project is built by and for the amateur radio community.

## Getting Started

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-improvement`)
3. Make your changes
4. Run the tests (`python -m unittest discover -s tests -v`)
   - Cross-platform tests live in `tests/test_cross_platform.py` and cover path handling, atomic writes, struct endianness, floating-point consistency, and callsign case normalisation. CI runs this suite on Ubuntu, Windows, and macOS.
5. Commit your changes (`git commit -m 'Add my improvement'`)
6. Push to the branch (`git push origin feature/my-improvement`)
7. Open a Pull Request

## Development Setup

See [README.md](README.md#building-from-source) for build instructions.

### Windows runtime packaging

Windows desktop builds use the shared PyInstaller spec in onedir mode. Install
`airwsjtx_bridge.exe` and its `airwsjtx_bridge-runtime` directory together; the
`tauri.windows-desktop.conf.json` Tauri configuration includes that directory in
both x64 and ARM64 installers. The bridge loads Python from the installed directory, not from a
temporary `_MEI...` extraction directory that Application Control may block.
Keep the system-provided UCRT and preserve signatures on Python/VC/TLS libraries.
Do not disable security policies to work around startup errors.
For repeat builds, replace the generated executable and remove the previous
generated runtime directory before staging its replacement, as shown in README.

After building, set `AIRWSJTX_PACKAGED_SIDECAR` to the frozen executable and run
`python -m unittest tests.test_sidecar_packaging -v`. These checks inspect the
runtime layout and launch both the original and a relocated installation from
outside the checkout. ARM64 builds additionally verify the bridge copied into
Tauri's final release directory before uploading the installer. Validate the
installed package on the affected Windows device too; a CI runner may not have
the same Application Control policies.

### macOS TestFlight packaging

Import the Apple Distribution certificate before running PyInstaller, and pass
`--codesign-identity` so the Python runtime, SSL libraries, and extension modules
are signed by the same team as the sidecar. TestFlight uses `--onedir`, not
`--onefile`: the onefile bootloader initializes a SysV semaphore that App Sandbox
denies (`semctl: Operation not permitted`), before Python starts. PyInstaller
creates a helper app under `Contents/Helpers/airwsjtx_bridge.app`, keeping code
and data in standard macOS bundle locations. A native sidecar launcher executes
that helper while preserving the JSON-RPC pipes and inherited sandbox.

The TestFlight workflow signs the helper and app for distribution, then launches
the final packaged helper from a native sandboxed parent before uploading.
A successful unsandboxed startup does not validate TestFlight behavior.
Keep library validation enabled; do not weaken sandbox entitlements to work
around packaging failures.

#### Required release checks

When changing packaging, PyInstaller, sidecar startup, Tauri bundle layout, or
macOS signing/entitlements:

1. Run the cross-platform packaging regression checks:
   `python -m unittest discover -s tests -p 'test_macos_packaging.py' -v`.
   Keep these checks and the workflow's sandbox smoke step enabled.
2. Build using the desktop and MAS Tauri configurations, as shown in
   `.github/workflows/macos-testflight.yml`. Keep the complete PyInstaller helper
   app layout; do not flatten its resources into the outer app's `Frameworks`
   directory. Sign nested libraries before the helper app, and the helper app
   before the outer app. The launcher and helper use
   `entitlements-mas-sidecar.plist` (sandbox + inherit); the outer app uses
   `entitlements-mas.plist` (including network client/server permissions).
3. After final signing, run the sandbox-aware check below. It must succeed
   **before** packaging/upload. `codesign --verify --deep --strict` is also
   required, but does not prove that the backend can start.
4. Verify WSJT-X UDP reception and a live aircraft feed from a sandboxed launch.
   A simulated WSJT-X peer must identify itself as WSJT-X or JTDX; unrelated
   application IDs are deliberately ignored. The automated smoke check only
   exercises startup and `get_status`, not these network paths.
5. After Apple processes the upload, validate the installed TestFlight build's
   WSJT-X connection and NET/aircraft-feed connectivity. A successful Developer
   ID build, CI run, or Apple upload is not a substitute for this check.

```bash
python scripts/verify_macos_sidecar.py "/path/to/AirWSJT-X.app" \
  --identity "<signing identity used for the app>"
```

This macOS-only check requires Xcode command-line tools and an available signing
identity. It copies the final app, preserves the packaged helper, and replaces
the copied main executable with a native sandboxed test parent. The parent uses
sandbox and network entitlements without application-ID/keychain entitlements.
It does not validate Apple receipts or Keychain restoration.

Keep MAS packaging changes confined to the TestFlight workflow and MAS config;
ordinary desktop releases and mobile use different packaging paths.

#### Diagnosing simultaneous WSJT-X and NET disconnection

Check native stderr and whether the sidecar starts before changing network
settings. `Failed to initialize sync semaphore!` / `semctl: Operation not
permitted` identifies the onefile/App Sandbox startup failure. Library-validation
errors about different Team IDs indicate incorrectly signed embedded libraries;
re-signing only the outer executable is insufficient.

In 0.3.22, build 17 fixed embedded-library signing but still hit the semaphore
failure. Build 18 replaced onefile packaging and was confirmed working in the
installed TestFlight app. Pre-Sky Dome 0.3.21 build 15 reproduced the same
semaphore failure under the sandbox probe; the Sky Dome integration did not
change networking or TestFlight packaging.

## Guidelines

For WSJT-X listener changes, run `python -m unittest tests.test_wsjtx_udp -v`.
The real-socket cases must cover an exclusively occupied port without forwarding,
recovery after releasing it, and status/decode delivery through a separate UDP
proxy port. Use isolated ephemeral loopback ports, not the operator's live
WSJT-X ports. Injecting a status directly into a fake socket does not validate
port sharing. Packaged startup and `get_status` checks also do not establish UDP
connectivity: verify live status and decodes in the installed app alongside the
logger before claiming that integration works. Do not create or upload fake QSOs.

- **Keep it focused.** Each PR should address one thing — a bug fix, a feature, or a refactor. Not all three.
- **Test your changes.** Add or update tests for new functionality. Run the full test suite before submitting.
- **Match the existing style.** Follow the conventions already in the codebase for naming, formatting, and structure.
- **Document user-facing changes.** Update the README if your change affects how users interact with the app.

## Reporting Bugs

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your platform (OS, WSJT-X version, rotator hardware if relevant)

## Suggesting Features

Open an issue describing the use case. For aircraft scatter and microwave operation, context about your station setup and operating scenario is helpful.

## License

By contributing, you agree that your contributions will be licensed under the [GNU General Public License v3.0](LICENSE).
