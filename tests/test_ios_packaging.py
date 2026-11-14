"""iOS packaging tests.

These are static/structural tests that run without a device, simulator, or
macOS code signing credentials. They validate the Tauri configuration and CI
workflow for iOS-specific correctness that would cause `tauri ios init` /
`tauri ios build` to fail or produce a broken app bundle.

Why device-less:
  Full simulator/device builds require Xcode, the iOS Rust targets, and (for
  on-device/TestFlight builds) Apple Developer signing credentials. Instead
  we check every property that `tauri ios init` / xcodebuild uses to produce
  the .app bundle, so packaging regressions are caught on every PR without
  needing those resources.

Run locally:
    python -m unittest tests.test_ios_packaging -v
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent
_TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
_PACKAGE_JSON = _REPO_ROOT / "package.json"
_MOBILE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "mobile.yml"
_WEB_SOURCE_DIR = _REPO_ROOT / "src"

# iOS / Apple bundle identifier rules (reverse-DNS):
#   - two or more dot-separated segments
#   - each segment starts with a letter
#   - subsequent characters: letter, digit, hyphen (no underscores)
_IOS_BUNDLE_ID_RE = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9-]*(\.[a-zA-Z][a-zA-Z0-9-]*)+$"
)

# Characters that break Info.plist string values (used for CFBundleName, etc.)
_BAD_PLIST_CHARS_RE = re.compile(r"[<>&]")


def _load_tauri_conf() -> dict:
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


def _load_package_json() -> dict:
    return json.loads(_PACKAGE_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TauriConfPresenceTests(unittest.TestCase):
    """Verify tauri.conf.json exists and has the fields the iOS build needs."""

    def setUp(self) -> None:
        self.conf = _load_tauri_conf()

    def test_config_file_is_readable(self) -> None:
        self.assertTrue(_TAURI_CONF.exists(), f"Missing {_TAURI_CONF}")

    def test_identifier_field_is_present(self) -> None:
        self.assertIn("identifier", self.conf, "tauri.conf.json must have an 'identifier' field")

    def test_version_field_is_present(self) -> None:
        self.assertIn("version", self.conf, "tauri.conf.json must have a 'version' field")

    def test_product_name_field_is_present(self) -> None:
        self.assertIn("productName", self.conf, "tauri.conf.json must have a 'productName' field")


class IOSBundleIdentifierTests(unittest.TestCase):
    """The 'identifier' in tauri.conf.json becomes the iOS CFBundleIdentifier.

    An invalid identifier causes `tauri ios init` to generate a project that
    Xcode refuses to build, or App Store Connect refuses to accept.
    """

    def setUp(self) -> None:
        self.identifier: str = _load_tauri_conf()["identifier"]

    def test_identifier_matches_ios_bundle_id_format(self) -> None:
        self.assertRegex(
            self.identifier,
            _IOS_BUNDLE_ID_RE,
            f"identifier '{self.identifier}' is not a valid iOS bundle identifier "
            "(must be dot-separated segments of [a-zA-Z][a-zA-Z0-9-]*, at least two segments)",
        )

    def test_identifier_has_at_least_two_segments(self) -> None:
        segments = self.identifier.split(".")
        self.assertGreaterEqual(
            len(segments),
            2,
            f"identifier '{self.identifier}' must have at least two dot-separated segments",
        )

    def test_identifier_has_no_underscores(self) -> None:
        self.assertNotIn(
            "_",
            self.identifier,
            f"identifier '{self.identifier}' must not contain underscores; "
            "Apple's bundle ID format only permits alphanumerics, hyphens, and dots",
        )


class IOSVersionTests(unittest.TestCase):
    """The 'version' in tauri.conf.json becomes CFBundleShortVersionString.

    An invalid version can prevent Xcode from archiving or App Store Connect
    from accepting the build.
    """

    def setUp(self) -> None:
        self.conf = _load_tauri_conf()
        self.version: str = self.conf["version"]

    def test_version_is_semver_like(self) -> None:
        """Version must be MAJOR.MINOR.PATCH (all integers) for iOS compatibility."""
        parts = self.version.split(".")
        self.assertEqual(
            len(parts),
            3,
            f"version '{self.version}' must have exactly 3 parts (MAJOR.MINOR.PATCH); "
            "extra labels (e.g. '-beta') are not valid in CFBundleShortVersionString",
        )
        for part in parts:
            self.assertTrue(
                part.isdigit(),
                f"version part '{part}' in '{self.version}' must be a non-negative integer",
            )

    def test_tauri_conf_version_matches_package_json_version(self) -> None:
        """tauri.conf.json and package.json versions must agree.

        A mismatch means the iOS build and the npm package report different
        versions, which breaks reproducibility.
        """
        pkg_version: str = _load_package_json()["version"]
        self.assertEqual(
            self.version,
            pkg_version,
            f"tauri.conf.json version '{self.version}' != package.json version '{pkg_version}'; "
            "keep them in sync to avoid iOS build/App Store inconsistencies",
        )


class IOSProductNameTests(unittest.TestCase):
    """The productName is used as CFBundleName / CFBundleDisplayName.

    Characters outside the safe set can cause Info.plist generation failures
    or a garbled app name on the home screen.
    """

    def setUp(self) -> None:
        self.product_name: str = _load_tauri_conf()["productName"]

    def test_product_name_has_no_unsafe_plist_chars(self) -> None:
        bad = _BAD_PLIST_CHARS_RE.findall(self.product_name)
        self.assertEqual(
            bad,
            [],
            f"productName '{self.product_name}' contains characters {bad} that are "
            "unsafe in an Info.plist string value",
        )

    def test_product_name_is_not_empty(self) -> None:
        self.assertTrue(self.product_name.strip(), "productName must not be empty")


class IOSWebRequestTests(unittest.TestCase):
    """WebKit must be able to issue cross-origin API requests without preflight.

    WKWebView honors an explicitly supplied User-Agent header by sending an
    OPTIONS preflight. Our static Azure website only supports GET/HEAD, so the
    preflight fails before the callsign database can be downloaded.
    """

    def test_browser_code_does_not_override_user_agent(self) -> None:
        offenders: list[str] = []
        for pattern in ("*.ts", "*.tsx"):
            for path in _WEB_SOURCE_DIR.rglob(pattern):
                if '"User-Agent"' in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(_REPO_ROOT)))
        self.assertEqual(
            offenders,
            [],
            "browser fetch code must not set User-Agent because it triggers "
            f"CORS preflight in iOS WKWebView: {offenders}",
        )


class IOSIconsTests(unittest.TestCase):
    """Tauri generates the iOS AppIcon asset catalog from the source PNGs at
    init time. These source icons must exist or icon generation silently
    skips them, resulting in a missing/default icon on-device.
    """

    def setUp(self) -> None:
        self.icons_dir = _REPO_ROOT / "src-tauri" / "icons"

    def test_128x128_icon_exists(self) -> None:
        self.assertTrue((self.icons_dir / "128x128.png").exists())

    def test_128x128_2x_icon_exists(self) -> None:
        self.assertTrue((self.icons_dir / "128x128@2x.png").exists())

    def test_icon_listed_in_bundle_config(self) -> None:
        conf = _load_tauri_conf()
        icons: list[str] = conf.get("bundle", {}).get("icon", [])
        self.assertTrue(icons, "bundle.icon list must not be empty")
        png_icons = [i for i in icons if i.endswith(".png")]
        self.assertTrue(
            png_icons,
            "bundle.icon must include at least one .png for iOS icon generation",
        )


class IOSCIWorkflowTests(unittest.TestCase):
    """Validate that the mobile CI workflow's iOS job is structurally sound.

    These checks catch copy-paste errors, missing steps, or accidental
    deletions that would silently produce a broken or empty iOS artifact.
    """

    def setUp(self) -> None:
        self.assertTrue(_MOBILE_WORKFLOW.exists(), f"Missing {_MOBILE_WORKFLOW}")
        self.workflow_text = _MOBILE_WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_has_ios_job(self) -> None:
        self.assertIn("ios:", self.workflow_text, "mobile.yml must define an 'ios' job")

    def test_ios_job_runs_on_macos(self) -> None:
        ios_section = self.workflow_text.split("ios:", 1)[1].split("android:", 1)[0]
        self.assertIn(
            "runs-on: macos-latest",
            ios_section,
            "the iOS job must run on a macOS runner (Xcode is required)",
        )

    def test_workflow_installs_ios_rust_targets(self) -> None:
        self.assertIn(
            "aarch64-apple-ios",
            self.workflow_text,
            "mobile.yml must install the aarch64-apple-ios Rust target (required for device builds)",
        )
        self.assertIn(
            "aarch64-apple-ios-sim",
            self.workflow_text,
            "mobile.yml must install the aarch64-apple-ios-sim Rust target (required for simulator builds)",
        )

    def test_workflow_runs_tauri_ios_init(self) -> None:
        self.assertIn(
            "tauri ios init",
            self.workflow_text,
            "mobile.yml must initialize the Tauri iOS project before building",
        )

    def test_workflow_runs_tauri_ios_build(self) -> None:
        self.assertIn(
            "tauri ios build",
            self.workflow_text,
            "mobile.yml must build the iOS app in CI",
        )

    def test_workflow_uploads_ios_artifact(self) -> None:
        self.assertIn(
            "ios-simulator-build",
            self.workflow_text,
            "mobile.yml must upload the built iOS Simulator app as a workflow artifact",
        )

    def test_workflow_adhoc_signs_simulator_app(self) -> None:
        self.assertIn(
            'codesign --force --sign - "$APP"',
            self.workflow_text,
            "mobile.yml must ad-hoc sign the simulator app; unsigned apps cannot "
            "access the Keychain, which breaks credential storage",
        )

    def test_workflow_applies_repo_icons_after_init(self) -> None:
        self.assertIn(
            "src-tauri/icons/ios/*.png",
            self.workflow_text,
            "mobile.yml must copy the repo icons over the template icons that "
            "`tauri ios init` generates",
        )

    def test_apple_platforms_use_app_store_connect_identifier(self) -> None:
        # Android package names cannot contain hyphens, so the base identifier
        # stays hyphen-free and Apple platforms override it.
        src_tauri = Path(__file__).resolve().parent.parent / "src-tauri"
        for name in ("tauri.ios.conf.json", "tauri.macos.conf.json"):
            override = json.loads((src_tauri / name).read_text(encoding="utf-8"))
            self.assertEqual(
                override["identifier"],
                "us.ad7np.airwsjt-x",
                f"{name} must match the App Store Connect app record",
            )

    def test_workflow_runs_static_packaging_tests(self) -> None:
        self.assertIn(
            "test_ios_packaging",
            self.workflow_text,
            "mobile.yml must run test_ios_packaging before the build "
            "to catch config errors early without spending build time",
        )

    def test_testflight_job_syncs_marketing_version_before_init(self) -> None:
        testflight_section = self.workflow_text.split("ios-testflight:", 1)[1].split(
            "macos-testflight:", 1
        )[0]
        sync_index = testflight_section.index("scripts/sync_release_version.py")
        init_index = testflight_section.index("npx tauri ios init")
        self.assertLess(
            sync_index,
            init_index,
            "the TestFlight marketing version must be synced before Tauri generates the Xcode project",
        )
        self.assertIn(
            "RELEASE_VERSION: ${{ inputs.release_version || github.ref_name }}",
            testflight_section,
        )

    def test_manual_testflight_can_build_an_exact_release_ref(self) -> None:
        testflight_section = self.workflow_text.split("ios-testflight:", 1)[1].split(
            "macos-testflight:", 1
        )[0]
        self.assertIn("ref: ${{ inputs.source_ref || github.ref }}", testflight_section)


if __name__ == "__main__":
    unittest.main()
