from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
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

    @unittest.skipUnless(sys.platform == "linux", "Website deployment runs on Ubuntu")
    def test_deploy_uploads_database_and_manifest_as_uncached_json(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            shutil.copytree(root / "site", work / "site")
            az = work / "az"
            az.write_text(
                '#!/usr/bin/env bash\nprintf "%s\\0" "$@" >> "$UPLOAD_LOG"\n',
                encoding="utf-8",
            )
            az.chmod(0o755)
            log = work / "uploads"
            env = {
                **os.environ,
                "PATH": f"{work}{os.pathsep}{os.environ.get('PATH', '')}",
                "AZURE_STORAGE_KEY": "test-only",
                "UPLOAD_LOG": str(log),
            }
            subprocess.run(
                ["bash", str(root / "scripts/deploy_site.sh"), "0.3.30"],
                cwd=work, env=env, check=True, capture_output=True,
            )
            uploads = [
                [arg.decode() for arg in block.split(b"\0") if arg]
                for block in log.read_bytes().split(b"--overwrite\0") if block
            ]
        self.assertEqual(len(uploads), 8)
        by_name = {args[args.index("--name") + 1]: args for args in uploads}
        for filename in ("callsign-grid-db.json", "callsign-grid-db-version.json"):
            with self.subTest(filename=filename):
                args = by_name[f"airwsjtx/{filename}"]
                self.assertEqual(args[args.index("--file") + 1], f"site/airwsjtx/{filename}")
                self.assertEqual(args[args.index("--container-name") + 1], "$web")
                self.assertEqual(args[args.index("--content-type") + 1], "application/json; charset=utf-8")
                self.assertIn("no-store", args[args.index("--content-cache-control") + 1])
        html = by_name["AirWSJT-X/index.html"]
        self.assertEqual(html[html.index("--content-type") + 1], "text/html; charset=utf-8")


if __name__ == "__main__":
    unittest.main()
