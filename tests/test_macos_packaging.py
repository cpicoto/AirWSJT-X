"""Static checks for TestFlight sidecar signing, runnable on every CI platform."""

from pathlib import Path
import json
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

    def test_onedir_libraries_use_distribution_identity(self) -> None:
        bundle = self.steps["Bundle Python sidecar with PyInstaller"]
        self.assertIn('grep "Apple Distribution"', bundle)
        self.assertIn('if [ -z "$IDENTITY" ]; then', bundle)
        self.assertRegex(bundle, r'pyinstaller --onedir --windowed --codesign-identity "\$IDENTITY"')
        self.assertNotIn("--onefile", bundle)
        self.assertIn("--osx-entitlements-file src-tauri/entitlements-mas-sidecar.plist", bundle)
        self.assertIn("scripts/macos_sidecar_launcher.c", bundle)

    def test_runtime_layout_is_bundled_for_the_sidecar(self) -> None:
        config = json.loads((ROOT / "src-tauri/tauri.mas.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(
            config["bundle"]["macOS"]["files"]["Helpers/airwsjtx_bridge.app"],
            "binaries/airwsjtx_bridge.app",
        )
        self.assertIn(
            '"$APP_PATH/Contents/Helpers/airwsjtx_bridge.app"',
            self.steps["Re-sign .app bundle for MAS"],
        )

    def test_sandboxed_backend_is_checked_before_upload(self) -> None:
        self.assertLess(
            self.workflow.index("- name: Re-sign .app bundle for MAS"),
            self.workflow.index("- name: Smoke test sandboxed Python sidecar"),
        )
        self.assertLess(
            self.workflow.index("- name: Smoke test sandboxed Python sidecar"),
            self.workflow.index("- name: Upload to TestFlight"),
        )
        self.assertIn(
            'python scripts/verify_macos_sidecar.py "$APP_PATH" --identity "$IDENTITY"',
            self.steps["Smoke test sandboxed Python sidecar"],
        )

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

    def test_app_review_notes_explain_the_inbound_udp_listener(self) -> None:
        notes = (ROOT / "appstore-assets/review-notes.txt").read_text(encoding="utf-8")
        self.assertIn("com.apple.security.network.server", notes)
        self.assertIn("binds UDP port 2237", notes)
        self.assertIn("heartbeat, status, decode, and QSO Logged datagrams", notes)
        self.assertIn("It is not a general-purpose web, FTP, or file server", notes)

    def test_metadata_uploader_publishes_review_notes_for_macos(self) -> None:
        uploader = (ROOT / "appstore-assets/upload_to_asc.py").read_text(encoding="utf-8")
        self.assertIn('REVIEW_NOTES_PATH = Path(__file__).parent / "review-notes.txt"', uploader)
        self.assertIn('/appStoreVersions/{version_id}/appStoreReviewDetail', uploader)
        self.assertIn('/appStoreReviewDetails/{detail_id}', uploader)
        self.assertIn('if platform is PLATFORMS["mac"]:', uploader)


if __name__ == "__main__":
    unittest.main()
