import json
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_TAURI_CONF = _ROOT / "src-tauri" / "tauri.conf.json"


class ReleasePackagingTests(unittest.TestCase):
    def test_macos_minimum_version_satisfies_arm64_only_uploads(self) -> None:
        conf = json.loads(_TAURI_CONF.read_text(encoding="utf-8"))
        minimum = conf["bundle"]["macOS"]["minimumSystemVersion"]
        major = int(minimum.split(".")[0])
        self.assertGreaterEqual(major, 12, "App Store Connect error 90869 requires >= 12.0")

    def test_release_builds_apple_silicon_and_intel(self) -> None:
        release = _RELEASE.read_text(encoding="utf-8")
        self.assertIn("aarch64-apple-darwin", release)
        self.assertIn("x86_64-apple-darwin", release)

    def test_intel_build_uses_supported_runner(self) -> None:
        release = _RELEASE.read_text(encoding="utf-8")
        self.assertIn("os: macos-15-intel", release)
        self.assertNotIn("macos-13", release)

    def test_release_verifies_macos_bundle(self) -> None:
        release = _RELEASE.read_text(encoding="utf-8")
        self.assertIn("LSMinimumSystemVersion", release)
        self.assertIn("lipo -info", release)


if __name__ == "__main__":
    unittest.main()
