"""Windows ARM64 packaging checks that also run on non-Windows hosts."""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.sync_release_version import sync_version
from scripts.verify_windows_arm64 import check_sidecar, require_arm64


ROOT = Path(__file__).resolve().parent.parent


class Arm64BinaryTests(unittest.TestCase):
    def test_accepts_arm64_and_rejects_emulated_architectures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "sidecar.exe"
            header = b"MZ" + bytes(58) + struct.pack("<I", 64) + b"PE\0\0"
            for machine in (0xAA64, 0x8664, 0x14C, 0xA641):
                with self.subTest(machine=machine):
                    executable.write_bytes(header + struct.pack("<H", machine))
                    if machine == 0xAA64:
                        require_arm64(executable)
                    else:
                        with self.assertRaisesRegex(ValueError, "expected native ARM64"):
                            require_arm64(executable)

    def test_rejects_invalid_or_truncated_pe_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "invalid.exe"
            for content in (b"", b"MZ", bytes(64), b"MZ" + bytes(58) + struct.pack("<I", 4096)):
                with self.subTest(content=content):
                    executable.write_bytes(content)
                    with self.assertRaises(ValueError):
                        require_arm64(executable)

    def test_sidecar_runs_outside_checkout_and_accepts_events_before_reply(self) -> None:
        reply = {"id": "arm64-smoke", "ok": True, "result": {"state": "idle"}}
        output = json.dumps({"event": "airscout_source_status"}) + "\n" + json.dumps(reply)
        with patch("scripts.verify_windows_arm64.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, output, "")
            check_sidecar(Path("sidecar.exe"))
        kwargs = run.call_args.kwargs
        self.assertEqual(json.loads(kwargs["input"])["method"], "get_status")
        self.assertEqual(kwargs["cwd"], kwargs["env"]["AIRWSJTX_DATA_DIR"])
        self.assertNotEqual(Path(kwargs["cwd"]), ROOT)
        self.assertEqual(kwargs["timeout"], 30)

    def test_sidecar_rejects_missing_failed_duplicate_or_non_idle_reply(self) -> None:
        replies = (
            [],
            [{"id": "arm64-smoke", "ok": False, "error": "failed"}],
            [{"id": "arm64-smoke", "ok": True, "result": {"state": "active_qso"}}],
            [{"id": "arm64-smoke", "ok": True, "result": {"state": "idle"}}] * 2,
        )
        for messages in replies:
            with self.subTest(messages=messages):
                with patch("scripts.verify_windows_arm64.subprocess.run") as run:
                    run.return_value = subprocess.CompletedProcess(
                        [], 0, "\n".join(json.dumps(message) for message in messages), "",
                    )
                    with self.assertRaises(RuntimeError):
                        check_sidecar(Path("sidecar.exe"))

    def test_sidecar_reports_process_failure(self) -> None:
        with patch("scripts.verify_windows_arm64.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "Missing Python DLL")
            with self.assertRaisesRegex(RuntimeError, "Missing Python DLL"):
                check_sidecar(Path("sidecar.exe"))


class ReleaseVersionTests(unittest.TestCase):
    def test_updates_all_release_metadata_without_changing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src-tauri").mkdir()
            for path in (root / "package.json", root / "src-tauri" / "tauri.conf.json"):
                path.write_text('{"version": "0.0.0", "name": "unchanged"}', encoding="utf-8")
            cargo = root / "src-tauri" / "Cargo.toml"
            cargo.write_text('[package]\nversion = "0.0.0"\n[dependencies.foo]\nversion = "2"\n', encoding="utf-8")
            sync_version(root, "v1.2.3-beta.1")
            for path in (root / "package.json", root / "src-tauri" / "tauri.conf.json"):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {
                    "version": "1.2.3-beta.1", "name": "unchanged",
                })
            self.assertIn('version = "1.2.3-beta.1"', cargo.read_text(encoding="utf-8"))
            self.assertIn('[dependencies.foo]\nversion = "2"', cargo.read_text(encoding="utf-8"))

    def test_invalid_version_is_rejected_before_any_file_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid release version"):
            sync_version(Path("not-a-repository"), "main")


class WindowsWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows build commands require PowerShell")
    def test_documented_rebuilds_replace_generated_bridge_and_runtime(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        blocks = [
            block for block in re.findall(r"```powershell\n(.*?)```", readme, re.DOTALL)
            if "sidecar\\airwsjtx_bridge.spec" in block
        ]
        self.assertEqual(len(blocks), 2)
        for block in blocks:
            target = (
                "aarch64-pc-windows-msvc"
                if "airwsjtx_bridge-aarch64" in block
                else "x86_64-pc-windows-msvc"
            )
            commands = "\n".join(
                line for line in block.splitlines()
                if line.startswith(("Move-Item", "if (Test-Path"))
            )
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                binaries = Path(directory) / "src-tauri" / "binaries"
                binaries.mkdir(parents=True)
                executable = binaries / f"airwsjtx_bridge-{target}.exe"
                executable.write_text("old bridge", encoding="utf-8")
                runtime = binaries / "airwsjtx_bridge-runtime"
                runtime.mkdir()
                (runtime / "stale.dll").write_text("old runtime", encoding="utf-8")
                unrelated = binaries / "keep.txt"
                unrelated.write_text("unrelated output", encoding="utf-8")
                for version in ("first rebuild", "second rebuild"):
                    source = binaries / "airwsjtx_bridge"
                    source_runtime = source / "airwsjtx_bridge-runtime"
                    source_runtime.mkdir(parents=True)
                    (source / "airwsjtx_bridge.exe").write_text(version, encoding="utf-8")
                    (source_runtime / "python312.dll").write_text(version, encoding="utf-8")
                    result = subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         "$ErrorActionPreference = 'Stop';\n" + commands],
                        cwd=directory,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(executable.read_text(encoding="utf-8"), version)
                    self.assertEqual((runtime / "python312.dll").read_text(encoding="utf-8"), version)
                    self.assertEqual(sorted(path.name for path in runtime.iterdir()), ["python312.dll"])
                    self.assertEqual(unrelated.read_text(encoding="utf-8"), "unrelated output")

    def test_windows_installs_runtime_next_to_the_sidecar(self) -> None:
        config = json.loads((ROOT / "src-tauri" / "tauri.windows-desktop.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(config["bundle"]["resources"], {
            "binaries/airwsjtx_bridge-runtime/": "airwsjtx_bridge-runtime/",
        })
        for name in ("windows-arm64.yml", "release.yml", "test.yml"):
            workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn(
                    r"Move-Item src-tauri\binaries\airwsjtx_bridge\airwsjtx_bridge-runtime "
                    r"src-tauri\binaries\airwsjtx_bridge-runtime",
                    workflow,
                )
        workflow = (ROOT / ".github" / "workflows" / "windows-arm64.yml").read_text(encoding="utf-8")
        self.assertIn(r"--config src-tauri\tauri.windows-desktop.conf.json", workflow)
        self.assertIn(
            r"--sidecar src-tauri\target\aarch64-pc-windows-msvc\release\airwsjtx_bridge.exe",
            workflow,
        )
        release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn(
            "runner.os == 'Windows' && ' --config src-tauri/tauri.windows-desktop.conf.json'",
            release,
        )

    def test_native_toolchain_and_payload_checks_are_required(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "windows-arm64.yml").read_text(encoding="utf-8")
        for required in (
            "runs-on: windows-11-arm",
            "architecture: arm64",
            "stable-aarch64-pc-windows-msvc",
            "python -m PyInstaller",
            "airwsjtx_bridge-aarch64-pc-windows-msvc.exe",
            "scripts\\verify_windows_arm64.py --app",
            "*_arm64-setup.exe",
            "if-no-files-found: error",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)
        self.assertNotIn("continue-on-error", workflow)
        self.assertLess(
            workflow.index("- name: Run Python tests on ARM64"),
            workflow.index("- name: Sync release version"),
            "Platform metadata tests require the checked-in stable version, not a prerelease tag.",
        )
        config = json.loads((ROOT / "src-tauri" / "tauri.windows-arm64.conf.json").read_text(encoding="utf-8"))
        self.assertEqual(config["bundle"]["targets"], ["nsis"])
        self.assertEqual(config["build"]["beforeBuildCommand"], "npm run build")

    def test_release_reuses_native_build_and_keeps_x64(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("target: x86_64-pc-windows-msvc", workflow)
        self.assertIn("uses: ./.github/workflows/windows-arm64.yml", workflow)
        self.assertIn("needs: [build, windows-arm64]", workflow)
        self.assertIn("needs: [build, publish-windows-arm64]", workflow)
        self.assertIn('gh release upload "$RELEASE_TAG" arm64-setup/*_arm64-setup.exe', workflow)
        self.assertIn("python scripts/sync_release_version.py", workflow)


if __name__ == "__main__":
    unittest.main()
