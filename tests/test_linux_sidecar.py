import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.verify_linux_sidecar import check_sidecar, require_header


class LinuxSidecarTests(unittest.TestCase):
    def test_requires_matching_64_bit_little_endian_elf(self):
        for machine, code in (("x86_64", 62), ("aarch64", 183)):
            header = b"\x7fELF\x02\x01" + bytes(12) + code.to_bytes(2, "little")
            require_header(header, machine, "test")
            other = "aarch64" if machine == "x86_64" else "x86_64"
            with self.assertRaisesRegex(RuntimeError, "ELF machine"):
                require_header(header, other, "test")
        for header in (b"", bytes(20), b"\x7fELF\x01\x01" + bytes(14)):
            with self.assertRaisesRegex(RuntimeError, "64-bit ELF"):
                require_header(header, "aarch64", "test")

    def result(self, *, status=True, receiver=True, code=0):
        return SimpleNamespace(
            returncode=code, stderr="diagnostic",
            stdout="\n".join(json.dumps(message) for message in (
                {"id": "linux-status", "ok": status, "result": {"state": "idle"}},
                {"id": "linux-receiver", "ok": receiver, "result": {"state": "stopped"}},
            )),
        )

    def test_frozen_backend_runs_outside_checkout_with_isolated_data(self):
        with patch("scripts.verify_linux_sidecar.subprocess.run", return_value=self.result()) as run:
            check_sidecar(Path("bridge"))
        options = run.call_args.kwargs
        self.assertNotEqual(Path(options["cwd"]), Path.cwd())
        self.assertEqual(options["env"]["AIRWSJTX_DATA_DIR"], options["cwd"])
        self.assertEqual(options["timeout"], 30)

    def test_missing_successful_reply_and_nonzero_exit_fail(self):
        for result in (self.result(status=False), self.result(receiver=False), self.result(code=1)):
            with self.subTest(result=result), \
                    patch("scripts.verify_linux_sidecar.subprocess.run", return_value=result):
                with self.assertRaises(RuntimeError):
                    check_sidecar(Path("bridge"))
