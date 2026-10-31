"""Replay synthetic IQ through the actual packaged native demodulator, not USB."""
import ctypes
import json
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from airwsjtx.adsb import ADSBDecoder
from airwsjtx.rtlsdr import LocalRTLSDRFeed, tools_directory
from tests.test_adsb import EVEN, ODD


@unittest.skipUnless(sys.platform == "win32" and shutil.which("cl")
                     and (tools_directory() / "rtl_adsb.exe").is_file(),
                     "Requires staged Windows x64 RTL-SDR tools and MSVC developer environment")
class NativeDemodulatorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.target = Path(directory.name)
        source = Path(__file__).parent / "fixtures" / "rtlsdr_iq_fixture.c"
        result = subprocess.run(
            ["cl", "/nologo", "/LD", "/O2", str(source),
             f"/Fe:{self.target / 'rtlsdr.dll'}", f"/Fo:{self.target / 'fixture.obj'}"],
            cwd=self.target, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in ("airwsjtx_adsb.exe", "rtl_adsb.exe", "pthreadVC2.dll", "msvcr100.dll"):
            shutil.copyfile(tools_directory() / name, self.target / name)

    def test_native_preamble_demodulation_crc_and_cpr_with_synthetic_iq(self):
        # The fake DLL is confined to this temporary directory. No RF device is opened.
        result = subprocess.run(
            [str(self.target / "rtl_adsb.exe"), "-d", "0"], cwd=self.target,
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SYNTHETIC", result.stderr)
        frames = result.stdout.upper().splitlines()
        self.assertIn(f"*{EVEN};", frames)
        self.assertIn(f"*{ODD};", frames)
        decoder = ADSBDecoder()
        for index, frame in enumerate(frames):
            decoder.consume(frame, now=index / 10, epoch=1000 + index / 10)
        aircraft = decoder.snapshot(now=len(frames) / 10)
        self.assertGreaterEqual(decoder.valid_messages, 2)
        self.assertEqual(decoder.crc_errors, 0)
        self.assertEqual(len(aircraft), 1)
        self.assertTrue(aircraft[0].has_position)

    def test_real_windows_job_and_stop(self):
        with patch("airwsjtx.rtlsdr.tools_directory", return_value=self.target):
            feed = LocalRTLSDRFeed(
                0, lambda aircraft: None, lambda state, message: None,
                device_info={"index": 0, "available": True, "supported_gains_db": []},
            )
            feed.start()
            process = feed._process
            try:
                time.sleep(0.6)
                self.assertGreater(feed.snapshot()["valid_messages"], 0)
            finally:
                feed.stop()
            self.assertIsNotNone(process.poll())
            self.assertIsNone(feed._job)
            self.assertTrue(all(not thread.is_alive() for thread in feed._threads))

    def test_native_queue_loss_is_measured_and_resets_overlap(self):
        result = subprocess.run(
            [str(self.target / "airwsjtx_adsb.exe"), "-d", "0"],
            cwd=self.target, capture_output=True, text=True, timeout=15,
            env={**os.environ, "AIRWSJTX_FIXTURE_BURST": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metrics = [json.loads(line.split(" ", 1)[1]) for line in result.stderr.splitlines()
                   if line.startswith("AIRWSJTX_IQ ")][-1]
        self.assertGreater(metrics["dropped_buffers"], 0)
        self.assertGreater(metrics["discontinuities"], 0)
        self.assertIsNone(metrics["usb_overruns"])
        self.assertGreater(metrics["native_valid_df17"], 0)

    def test_abrupt_bridge_exit_kills_only_owned_native_receiver(self):
        script = (
            "import sys,time; from pathlib import Path; import airwsjtx.rtlsdr as r; "
            "r.tools_directory=lambda:Path(sys.argv[1]); "
            "feed=r.LocalRTLSDRFeed(0,lambda aircraft:None,lambda state,msg:None, "
            "device_info={'index':0,'available':True,'supported_gains_db':[]}); "
            "feed.start(); print(feed._process.pid,flush=True); time.sleep(30)"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", script, str(self.target)], cwd=self.target,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = None
        try:
            pid = parent.stdout.readline().strip()
            self.assertTrue(pid.isdigit(), pid)
            handle = kernel.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
            self.assertTrue(handle)
            parent.terminate()
            parent.wait(timeout=10)
            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            if parent.poll() is None:
                parent.terminate()
                parent.wait(timeout=10)
            parent.stdout.close()
            parent.stderr.close()
            if handle:
                kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
