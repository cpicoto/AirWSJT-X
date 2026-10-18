"""Static checks for TestFlight sidecar signing, runnable on every CI platform."""

from pathlib import Path
import plistlib
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent


class MacOSTestFlightPackagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github/workflows/macos-testflight.yml").read_text(encoding="utf-8")
        self.steps = dict(re.findall(
            r"      - name: ([^\n]+)\n(.*?)(?=\n      - |\Z)",
            self.workflow,
            re.DOTALL,
        ))

    def test_certificates_are_imported_before_python_is_packaged(self) -> None:
        self.assertLess(
            self.workflow.index("- name: Import signing certificates"),
            self.workflow.index("- name: Bundle Python sidecar with PyInstaller"),
        )

    def test_embedded_libraries_use_distribution_identity_before_compression(self) -> None:
        bundle = self.steps["Bundle Python sidecar with PyInstaller"]
        self.assertIn('grep "Apple Distribution"', bundle)
        self.assertIn('if [ -z "$IDENTITY" ]; then', bundle)
        self.assertRegex(bundle, r'pyinstaller --onefile --codesign-identity "\$IDENTITY"')

    def test_real_sidecar_is_smoke_tested_before_sandbox_inheritance_is_added(self) -> None:
        self.assertLess(
            self.workflow.index("- name: Bundle Python sidecar with PyInstaller"),
            self.workflow.index("- name: Smoke test hardened Python sidecar"),
        )
        self.assertLess(
            self.workflow.index("- name: Smoke test hardened Python sidecar"),
            self.workflow.index("- name: Sign sidecar with MAS entitlements"),
        )
        smoke = self.steps["Smoke test hardened Python sidecar"]
        self.assertIn("subprocess.run(", smoke)
        self.assertIn('"method": "get_status"', smoke)
        self.assertIn("result.returncode", smoke)
        self.assertIn('reply.get("ok")', smoke)
        self.assertIn("timeout=30", smoke)

    def test_mas_signing_preserves_library_validation(self) -> None:
        for name in ["entitlements-mas.plist", "entitlements-mas-sidecar.plist"]:
            with (ROOT / "src-tauri" / name).open("rb") as stream:
                entitlements = plistlib.load(stream)
            self.assertTrue(entitlements["com.apple.security.app-sandbox"])
            self.assertNotIn("com.apple.security.cs.disable-library-validation", entitlements)
        self.assertIn("--options runtime", self.steps["Sign sidecar with MAS entitlements"])

    def test_app_allows_udp_listening_and_outbound_feed_connections(self) -> None:
        with (ROOT / "src-tauri/entitlements-mas.plist").open("rb") as stream:
            entitlements = plistlib.load(stream)
        self.assertTrue(entitlements["com.apple.security.network.client"])
        self.assertTrue(entitlements["com.apple.security.network.server"])


if __name__ == "__main__":
    unittest.main()
