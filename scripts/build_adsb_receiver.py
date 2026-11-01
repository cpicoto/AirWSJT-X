"""Build the app-owned ADS-B helper (MSVC on Windows x64, clang on macOS)."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _current_compiler_targets_x64() -> bool:
    target_arch = os.environ.get("VSCMD_ARG_TGT_ARCH", os.environ.get("Platform", "")).lower()
    return bool(shutil.which("cl")) and target_arch in ("x64", "amd64")


MACOS_DEPLOYMENT_TARGET = "12.0"


def helper_name(platform: str | None = None) -> str:
    return "airwsjtx_adsb.exe" if (platform or sys.platform) == "win32" else "airwsjtx_adsb"


def _build_posix(sources: list[Path], target: Path) -> None:
    env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": MACOS_DEPLOYMENT_TARGET}
    subprocess.run([os.environ.get("CC", "clang"), "-O2", "-Wall", "-Wextra", "-o", str(target),
                    *map(str, sources), "-lm", *(["-ldl"] if sys.platform.startswith("linux") else [])],
                   check=True, env=env)


def build(destination: Path | None = None) -> Path:
    source = Path(__file__).with_name("adsb_receiver.c")
    destination = destination or (ROOT / "sidecar" / "rtl-sdr" if (ROOT / "LICENSE").is_file()
                                  else Path(__file__).parent)
    destination.mkdir(parents=True, exist_ok=True)
    upstream = source.parent / "dump1090-fa"
    target = destination / helper_name()
    if sys.platform != "win32":
        _build_posix([source, upstream / "demod_2400.c", upstream / "icao_filter.c"], target)
    else:
        _build_windows(source, upstream, target)
    for origin in (source, Path(__file__)):
        if origin.resolve() != (destination / origin.name).resolve():
            shutil.copyfile(origin, destination / origin.name)
    if upstream.resolve() != (destination / "dump1090-fa").resolve():
        shutil.copytree(upstream, destination / "dump1090-fa", dirs_exist_ok=True)
    if (ROOT / "LICENSE").is_file():
        shutil.copyfile(ROOT / "LICENSE", destination / "COPYING-AirWSJT-X")
    return target


def _build_windows(source: Path, upstream: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="airwsjtx-adsb-build-") as directory:
        command = ["cl", "/nologo", "/O2", "/W4", "/MT", str(source),
                   str(upstream / "demod_2400.c"), str(upstream / "icao_filter.c"),
                   f"/Fe:{target}", f"/Fo:{directory}\\"]
        if _current_compiler_targets_x64():
            subprocess.run(command, cwd=directory, check=True)
        else:
            installer = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            vswhere = installer / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
            if not vswhere.is_file():
                raise RuntimeError("Building the ADS-B helper requires Visual Studio C++ x64 build tools.")
            installation = subprocess.check_output(
                [str(vswhere), "-latest", "-products", "*", "-requires",
                 "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                text=True,
            ).strip()
            setup = Path(installation) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
            if not installation or not setup.is_file():
                raise RuntimeError("Visual Studio C++ x64 build tools were not found.")
            subprocess.run(
                f'"{os.environ["COMSPEC"]}" /d /s /c '
                f'"call "{setup}" >nul && {subprocess.list2cmdline(command)}"',
                cwd=directory, check=True,
            )


if __name__ == "__main__":
    print(build())
