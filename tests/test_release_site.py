import json
from pathlib import Path
import unittest


class ReleaseSiteTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.site = (root / "site" / "index.html").read_text(encoding="utf-8")

    def test_download_links_match_published_asset_names(self) -> None:
        expected = {
            "AirWSJT-X___VERSION___aarch64.dmg",
            "AirWSJT-X___VERSION___x64_en-US.msi",
            "AirWSJT-X___VERSION___x64-setup.exe",
            "AirWSJT-X___VERSION___arm64-setup.exe",
            "AirWSJT-X___VERSION___amd64.AppImage",
            "AirWSJT-X___VERSION___amd64.deb",
            "AirWSJT-X-__VERSION__-1.x86_64.rpm",
            "AirWSJT-X-android-arm.apk",
            "AirWSJT-X-android-x86_64.apk",
        }
        for asset in expected:
            with self.subTest(asset=asset):
                self.assertIn(f"/releases/download/v__VERSION__/{asset}", self.site)

        self.assertNotIn("/AirWSJT-X-android.apk", self.site)
        self.assertNotIn("/airwsjtx___VERSION___amd64.deb", self.site)
        self.assertNotIn("/airwsjtx-__VERSION__-1.x86_64.rpm", self.site)

    def test_release_and_manual_workflows_share_the_deploy_script(self) -> None:
        root = Path(__file__).resolve().parents[1]
        release = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        manual = (root / ".github" / "workflows" / "deploy-site.yml").read_text(encoding="utf-8")
        deploy = (root / "scripts" / "deploy_site.sh").read_text(encoding="utf-8")

        self.assertIn('scripts/deploy_site.sh "${GITHUB_REF_NAME#v}"', release)
        self.assertIn('scripts/deploy_site.sh "${{ inputs.version }}"', manual)
        self.assertIn('if [[ ! "$version" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+$ ]]', deploy)
        self.assertIn("upload AirWSJT-X/index.html site/index.html", deploy)
        self.assertIn(
            "upload airwsjtx/callsign-grid-db.json site/callsign-grid-db.json",
            deploy,
        )
        self.assertIn(
            "upload airwsjtx/callsign-grid-db-version.json site/callsign-grid-db-version.json",
            deploy,
        )
        self.assertIn('json_type="application/json; charset=utf-8"', deploy)

    def test_callsign_database_and_manifest_are_consistent(self) -> None:
        root = Path(__file__).resolve().parents[1]
        database = json.loads(
            (root / "site" / "callsign-grid-db.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (root / "site" / "callsign-grid-db-version.json").read_text(encoding="utf-8")
        )

        self.assertEqual(database["version"], manifest["version"])
        self.assertEqual(len(database["entries"]), manifest["entries"])
        self.assertEqual(
            set(database["entries"]),
            {"AD7NP", "K7MDL", "KD7UO", "VE7NX", "W7GLF", "W7PUA"},
        )
        for callsign, station in database["entries"].items():
            with self.subTest(callsign=callsign):
                self.assertRegex(station["grid"], r"^[A-R]{2}[0-9]{2}[A-X]{2}$")


if __name__ == "__main__":
    unittest.main()
