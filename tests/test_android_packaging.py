"""Android packaging tests.

These are static/structural tests that run without a device or emulator.
They validate the Tauri configuration and CI workflow for Android-specific
correctness that would cause the app to fail to install or build.

Why device-less:
  Full emulator tests require hardware-acceleration (KVM) that is unavailable
  on standard GitHub Actions ubuntu-latest runners.  Instead we check every
  property that `tauri android init` / Gradle uses to produce the APK, so
  packaging regressions are caught on every PR.

Run locally:
    python -m unittest tests.test_android_packaging -v
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

# Android package name rules
#   - two or more dot-separated segments
#   - each segment starts with a letter or underscore
#   - subsequent characters: letter, digit, underscore
_ANDROID_PKG_RE = re.compile(
    r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$"
)

# Java / Kotlin reserved words that must not appear as package segments
_JAVA_RESERVED = frozenset(
    [
        "abstract", "assert", "boolean", "break", "byte", "case", "catch",
        "char", "class", "const", "continue", "default", "do", "double",
        "else", "enum", "extends", "final", "finally", "float", "for",
        "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private",
        "protected", "public", "return", "short", "static", "strictfp",
        "super", "switch", "synchronized", "this", "throw", "throws",
        "transient", "try", "void", "volatile", "while",
    ]
)

# Characters that break Android XML resource names (used for app_name, etc.)
_BAD_RESOURCE_CHARS_RE = re.compile(r"[^a-zA-Z0-9 _\-\.]")


def _load_tauri_conf() -> dict:
    return json.loads(_TAURI_CONF.read_text(encoding="utf-8"))


def _load_package_json() -> dict:
    return json.loads(_PACKAGE_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TauriConfPresenceTests(unittest.TestCase):
    """Verify tauri.conf.json exists and has the fields the Android build needs."""

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


class AndroidPackageIdentifierTests(unittest.TestCase):
    """The 'identifier' in tauri.conf.json becomes the Android package name.

    An invalid identifier causes `tauri android init` or Gradle to fail,
    or produces an APK that Android refuses to install.
    """

    def setUp(self) -> None:
        self.identifier: str = _load_tauri_conf()["identifier"]

    def test_identifier_matches_android_package_format(self) -> None:
        self.assertRegex(
            self.identifier,
            _ANDROID_PKG_RE,
            f"identifier '{self.identifier}' is not a valid Android package name "
            "(must be dot-separated segments of [a-zA-Z_][a-zA-Z0-9_]*, at least two segments)",
        )

    def test_identifier_has_at_least_two_segments(self) -> None:
        segments = self.identifier.split(".")
        self.assertGreaterEqual(
            len(segments),
            2,
            f"identifier '{self.identifier}' must have at least two dot-separated segments",
        )

    def test_identifier_segments_are_not_java_reserved_words(self) -> None:
        segments = self.identifier.lower().split(".")
        reserved_used = [s for s in segments if s in _JAVA_RESERVED]
        self.assertEqual(
            reserved_used,
            [],
            f"identifier segments {reserved_used} are Java reserved keywords; "
            "this causes a Gradle compile error",
        )

    def test_identifier_has_no_uppercase_letters(self) -> None:
        self.assertEqual(
            self.identifier,
            self.identifier.lower(),
            f"identifier '{self.identifier}' must be all lowercase for Android",
        )

    def test_identifier_has_no_hyphens(self) -> None:
        self.assertNotIn(
            "-",
            self.identifier,
            f"identifier '{self.identifier}' must not contain hyphens; "
            "use underscores if word separation is needed",
        )


class AndroidVersionTests(unittest.TestCase):
    """The 'version' in tauri.conf.json becomes the Android versionName.

    Android also derives an integer versionCode from it; an invalid or
    inconsistent version can prevent installation or upgrades.
    """

    def setUp(self) -> None:
        self.conf = _load_tauri_conf()
        self.version: str = self.conf["version"]

    def test_version_is_semver_like(self) -> None:
        """Version must be MAJOR.MINOR.PATCH (all integers) for Android compatibility."""
        parts = self.version.split(".")
        self.assertEqual(
            len(parts),
            3,
            f"version '{self.version}' must have exactly 3 parts (MAJOR.MINOR.PATCH); "
            "extra labels (e.g. '-beta') are not supported in Android versionName derivation",
        )
        for part in parts:
            self.assertTrue(
                part.isdigit(),
                f"version part '{part}' in '{self.version}' must be a non-negative integer",
            )

    def test_version_parts_fit_in_android_version_code(self) -> None:
        """Android versionCode is a 32-bit int; each part must be ≤ 999 to prevent overflow."""
        parts = self.version.split(".")
        for part in parts:
            if part.isdigit():
                self.assertLessEqual(
                    int(part),
                    999,
                    f"version part '{part}' exceeds 999; this causes Android versionCode overflow",
                )

    def test_tauri_conf_version_matches_package_json_version(self) -> None:
        """tauri.conf.json and package.json versions must agree.

        A mismatch means the Android APK and the npm package report different
        versions, which breaks reproducibility and can prevent OTA upgrades.
        """
        pkg_version: str = _load_package_json()["version"]
        self.assertEqual(
            self.version,
            pkg_version,
            f"tauri.conf.json version '{self.version}' != package.json version '{pkg_version}'; "
            "keep them in sync to avoid Android install/upgrade failures",
        )


class AndroidProductNameTests(unittest.TestCase):
    """The productName is used as the Android app label in strings.xml.

    Characters outside the safe set can cause Gradle resource compilation
    failures or garbled labels on-device.
    """

    def setUp(self) -> None:
        self.product_name: str = _load_tauri_conf()["productName"]

    def test_product_name_has_no_unsafe_xml_chars(self) -> None:
        bad = _BAD_RESOURCE_CHARS_RE.findall(self.product_name)
        self.assertEqual(
            bad,
            [],
            f"productName '{self.product_name}' contains characters {bad} that are "
            "unsafe in Android XML resources",
        )

    def test_product_name_is_not_empty(self) -> None:
        self.assertTrue(self.product_name.strip(), "productName must not be empty")


class AndroidIconsTests(unittest.TestCase):
    """Tauri generates Android mipmap icons from the source PNGs at init time.

    These source icons must exist or the icon generation step silently skips
    them, resulting in a default/missing icon on-device.
    """

    def setUp(self) -> None:
        self.icons_dir = _REPO_ROOT / "src-tauri" / "icons"

    def test_32x32_icon_exists(self) -> None:
        self.assertTrue((self.icons_dir / "32x32.png").exists())

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
            "bundle.icon must include at least one .png for Android icon generation",
        )


class AndroidCIWorkflowTests(unittest.TestCase):
    """Validate that the mobile CI workflow is structurally sound.

    These checks catch copy-paste errors, missing steps, or accidental
    deletions that would silently produce a broken or unsigned APK.
    """

    def setUp(self) -> None:
        self.assertTrue(_MOBILE_WORKFLOW.exists(), f"Missing {_MOBILE_WORKFLOW}")
        self.workflow_text = _MOBILE_WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_has_android_job(self) -> None:
        self.assertIn("android:", self.workflow_text, "mobile.yml must define an 'android' job")

    def test_workflow_installs_java(self) -> None:
        self.assertIn(
            "setup-java",
            self.workflow_text,
            "mobile.yml must include actions/setup-java (required by Gradle)",
        )

    def test_workflow_installs_ndk(self) -> None:
        self.assertIn(
            "ndk",
            self.workflow_text.lower(),
            "mobile.yml must install the Android NDK (required by Tauri for native code)",
        )

    def test_workflow_runs_tauri_android_init(self) -> None:
        self.assertIn(
            "tauri android init",
            self.workflow_text,
            "mobile.yml must call 'tauri android init' before building",
        )

    def test_workflow_runs_tauri_android_build(self) -> None:
        self.assertIn(
            "tauri android build",
            self.workflow_text,
            "mobile.yml must call 'tauri android build' to produce an APK/AAB",
        )

    def test_workflow_uploads_artifact(self) -> None:
        self.assertIn(
            "upload-artifact",
            self.workflow_text,
            "mobile.yml must upload the built APK/AAB as a workflow artifact",
        )

    def test_workflow_has_rust_android_targets(self) -> None:
        self.assertIn(
            "aarch64-linux-android",
            self.workflow_text,
            "mobile.yml must include the aarch64-linux-android Rust target "
            "(required for ARM64 devices, the dominant Android architecture)",
        )

    def test_workflow_has_armv7_rust_target(self) -> None:
        self.assertIn(
            "armv7-linux-androideabi",
            self.workflow_text,
            "mobile.yml must include the armv7-linux-androideabi Rust target "
            "(required for 32-bit ARM devices)",
        )

    def test_workflow_runs_static_packaging_tests(self) -> None:
        self.assertIn(
            "test_android_packaging",
            self.workflow_text,
            "mobile.yml must run test_android_packaging before the build "
            "to catch config errors early without spending NDK/build time",
        )

    def test_workflow_validates_apk_package_name(self) -> None:
        self.assertIn(
            "EXPECTED_PKG=$(python3 -c",
            self.workflow_text,
            "mobile.yml must derive the expected APK package name from tauri.conf.json",
        )
        self.assertIn(
            "identifier",
            self.workflow_text,
            "mobile.yml package validation should read the identifier from tauri.conf.json",
        )

    def test_workflow_validates_apk_signing(self) -> None:
        self.assertIn(
            "apksigner",
            self.workflow_text,
            "mobile.yml must verify APK signing with apksigner",
        )

    def test_workflow_validates_zip_alignment(self) -> None:
        self.assertIn(
            "zipalign",
            self.workflow_text,
            "mobile.yml must check zip-alignment — some Android installers reject unaligned APKs",
        )


if __name__ == "__main__":
    unittest.main()
