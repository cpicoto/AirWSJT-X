from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import install_piper_voice


ROOT = Path(__file__).resolve().parents[1]


class PiperPackagingTests(unittest.TestCase):
    def test_downloads_are_revision_pinned_and_sha256_verified(self):
        self.assertEqual(len(install_piper_voice.VOICE_REVISION), 40)
        self.assertNotIn("/main/", install_piper_voice.VOICE_BASE)
        self.assertTrue(all(len(checksum) == 64 for _name, _url, checksum in install_piper_voice.FILES))
        self.assertTrue(any(name.endswith(".tar.gz") for name, _url, _checksum in install_piper_voice.FILES))
        self.assertTrue(any(name == "COPYING-Piper" for name, _url, _checksum in install_piper_voice.FILES))

    def test_stager_writes_model_config_license_source_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(install_piper_voice, "DESTINATION", Path(directory)), \
                patch.object(install_piper_voice, "download", side_effect=lambda url, _hash: url.encode()):
            install_piper_voice.main()
            names = {path.name for path in Path(directory).iterdir()}
        self.assertIn("en_US-ljspeech-medium.onnx", names)
        self.assertIn("en_US-ljspeech-medium.onnx.json", names)
        self.assertIn("MODEL_CARD", names)
        self.assertIn("COPYING-Piper", names)
        self.assertIn("piper_tts-1.8.0.tar.gz", names)
        self.assertIn("SOURCE.txt", names)

    def test_linux_workflows_install_and_stage_pinned_piper(self):
        for workflow in ("release.yml", "test.yml"):
            text = (ROOT / ".github" / "workflows" / workflow).read_text(encoding="utf-8")
            self.assertIn('piper-tts==1.8.0', text)
            self.assertIn("scripts/install_piper_voice.py", text)
        spec = (ROOT / "sidecar" / "airwsjtx_bridge.spec").read_text(encoding="utf-8")
        self.assertIn('hiddenimports=piper_hiddenimports', spec)
        self.assertIn('sidecar" / "hooks', spec)
        self.assertIn("piper_tts-1.8.0.tar.gz", spec)


if __name__ == "__main__":
    unittest.main()
