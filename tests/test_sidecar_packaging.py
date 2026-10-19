"""Sidecar spec checks, plus frozen-binary checks when AIRWSJTX_PACKAGED_SIDECAR is set."""

from __future__ import annotations

import os
import runpy
import unittest
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.verify_windows_arm64 import check_sidecar


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = _REPO_ROOT / "sidecar" / "airwsjtx_bridge.spec"
_PACKAGED_SIDECAR = os.environ.get("AIRWSJTX_PACKAGED_SIDECAR")


class SidecarSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system_runtime = [
            ("ucrtbase.dll", "system/ucrtbase.dll", "BINARY"),
            ("UCRTBASE.DLL", "sdk/UCRTBASE.DLL", "BINARY"),
            ("api-ms-win-crt-runtime-l1-1-0.dll", "sdk/runtime.dll", "BINARY"),
            ("API-MS-WIN-CRT-HEAP-L1-1-0.DLL", "sdk/heap.dll", "BINARY"),
            ("nested/ucrtbase.dll", "sdk/ucrtbase.dll", "BINARY"),
            (r"nested\api-ms-win-crt-stdio-l1-1-0.dll", "sdk/stdio.dll", "BINARY"),
        ]
        self.application_runtime = [
            ("python312.dll", "python/python312.dll", "BINARY"),
            ("vcruntime140.dll", "python/vcruntime140.dll", "BINARY"),
            ("vcruntime140_1.dll", "python/vcruntime140_1.dll", "BINARY"),
            ("_ssl.pyd", "python/_ssl.pyd", "EXTENSION"),
            ("libcrypto-3.dll", "python/libcrypto-3.dll", "BINARY"),
            ("libpython3.12.dylib", "python/libpython3.12.dylib", "BINARY"),
            ("api-ms-win-crt-not-a-dll.txt", "data/example.txt", "DATA"),
        ]

    def run_spec(self, platform: str) -> tuple[SimpleNamespace, Mock, Mock]:
        analysis = SimpleNamespace(
            binaries=self.system_runtime + self.application_runtime,
            pure=["pure-python"],
            scripts=["entrypoint"],
            datas=[("certifi/cacert.pem", "certifi/cacert.pem", "DATA")],
        )
        analyze = Mock(return_value=analysis)
        build_exe = Mock()
        with patch("sys.platform", platform):
            runpy.run_path(
                str(_SPEC),
                init_globals={
                    "SPECPATH": str(_SPEC.parent),
                    "Analysis": analyze,
                    "PYZ": Mock(return_value="python-archive"),
                    "EXE": build_exe,
                },
            )
        return analysis, analyze, build_exe

    def test_windows_uses_system_ucrt_without_removing_python_or_vc_runtime(self) -> None:
        analysis, _, build_exe = self.run_spec("win32")
        self.assertEqual(analysis.binaries, self.application_runtime)
        self.assertEqual(build_exe.call_args.args[2], self.application_runtime)
        self.assertEqual(build_exe.call_args.args[3], analysis.datas)

    def test_other_platforms_keep_all_collected_binaries(self) -> None:
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                analysis, _, _ = self.run_spec(platform)
                self.assertEqual(
                    analysis.binaries,
                    self.system_runtime + self.application_runtime,
                )

    def test_spec_resolves_project_paths_and_preserves_console_protocol(self) -> None:
        _, analyze, build_exe = self.run_spec("win32")
        self.assertEqual(
            analyze.call_args.args[0],
            [str(_REPO_ROOT / "sidecar" / "airwsjtx_bridge.py")],
        )
        self.assertEqual(analyze.call_args.kwargs["pathex"], [str(_REPO_ROOT)])
        self.assertEqual(build_exe.call_args.kwargs["name"], "airwsjtx_bridge")
        self.assertTrue(build_exe.call_args.kwargs["console"])
        self.assertFalse(build_exe.call_args.kwargs["strip"])
        self.assertFalse(build_exe.call_args.kwargs["upx"])

    def test_desktop_builds_use_the_shared_spec(self) -> None:
        for workflow in ("release.yml", "windows-arm64.yml", "test.yml"):
            with self.subTest(workflow=workflow):
                text = (_REPO_ROOT / ".github" / "workflows" / workflow).read_text(
                    encoding="utf-8"
                )
                self.assertIn("sidecar/airwsjtx_bridge.spec", text.replace("\\", "/"))
                self.assertNotIn("pyinstaller --onefile", text.lower())

    def test_windows_ci_and_releases_verify_the_frozen_binary(self) -> None:
        for workflow in ("release.yml", "windows-arm64.yml", "test.yml"):
            with self.subTest(workflow=workflow):
                text = (_REPO_ROOT / ".github" / "workflows" / workflow).read_text(
                    encoding="utf-8"
                )
                self.assertRegex(
                    text,
                    r"AIRWSJTX_PACKAGED_SIDECAR: src-tauri/binaries/"
                    r"airwsjtx_bridge(?:-aarch64-pc-windows-msvc)?\.exe",
                )
                self.assertIn("python -m unittest tests.test_sidecar_packaging -v", text)


@unittest.skipUnless(_PACKAGED_SIDECAR, "Set AIRWSJTX_PACKAGED_SIDECAR to test a frozen bridge")
class FrozenSidecarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binary = Path(_PACKAGED_SIDECAR).resolve()
        self.assertTrue(self.binary.is_file(), f"Missing frozen sidecar: {self.binary}")

    def test_archive_uses_system_ucrt_and_keeps_required_runtime(self) -> None:
        from PyInstaller.archive.readers import CArchiveReader

        archive = CArchiveReader(str(self.binary))
        names = {PureWindowsPath(name).name.lower() for name in archive.toc}
        self.assertNotIn("ucrtbase.dll", names)
        self.assertFalse(
            any(name.startswith("api-ms-win-crt-") and name.endswith(".dll") for name in names),
            "The frozen bridge must use the OS-supplied UCRT forwarders",
        )
        self.assertTrue(any(name.startswith("python3") and name.endswith(".dll") for name in names))
        self.assertIn("vcruntime140.dll", names)
        self.assertIn("_ssl.pyd", names)
        self.assertIn("cacert.pem", names)

    def test_frozen_bridge_replies_from_outside_the_checkout(self) -> None:
        check_sidecar(self.binary)


if __name__ == "__main__":
    unittest.main()
