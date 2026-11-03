import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts import build_adsb_receiver


class CompilerTargetTests(unittest.TestCase):
    def test_build_ships_complete_receiver_sources_and_licenses(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(build_adsb_receiver, "_build_posix"), \
                patch.object(build_adsb_receiver, "_build_windows"):
            destination = Path(directory)
            build_adsb_receiver.build(destination)
            root = build_adsb_receiver.ROOT
            for relative in ("adsb_receiver.c", "build_adsb_receiver.py",
                             "dump1090-fa/demod_2400.c", "dump1090-fa/icao_filter.c",
                             "dump1090-fa/dump1090.h", "dump1090-fa/COPYING",
                             "dump1090-fa/UPSTREAM.txt"):
                self.assertEqual((destination / relative).read_bytes(),
                                 (root / "scripts" / relative).read_bytes(), relative)
            self.assertEqual((destination / "COPYING-AirWSJT-X").read_bytes(),
                             (root / "LICENSE").read_bytes())

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
