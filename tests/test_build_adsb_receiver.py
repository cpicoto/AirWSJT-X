import unittest
from unittest.mock import patch

from scripts import build_adsb_receiver


class CompilerTargetTests(unittest.TestCase):
    def test_only_reuses_cl_from_an_x64_target_environment(self):
        with patch.object(build_adsb_receiver.shutil, "which", return_value="cl.exe"), \
                patch.dict("os.environ", {"VSCMD_ARG_TGT_ARCH": "x64"}, clear=True):
            self.assertTrue(build_adsb_receiver._current_compiler_targets_x64())
        with patch.object(build_adsb_receiver.shutil, "which", return_value="cl.exe"), \
                patch.dict("os.environ", {"VSCMD_ARG_TGT_ARCH": "x86"}, clear=True):
            self.assertFalse(build_adsb_receiver._current_compiler_targets_x64())
        with patch.object(build_adsb_receiver.shutil, "which", return_value="cl.exe"), \
                patch.dict("os.environ", {}, clear=True):
            self.assertFalse(build_adsb_receiver._current_compiler_targets_x64())
        with patch.object(build_adsb_receiver.shutil, "which", return_value=None), \
                patch.dict("os.environ", {"Platform": "x64"}, clear=True):
            self.assertFalse(build_adsb_receiver._current_compiler_targets_x64())


if __name__ == "__main__":
    unittest.main()
