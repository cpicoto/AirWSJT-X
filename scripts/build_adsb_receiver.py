"""Build the app-owned Windows x64 helper with the installed MSVC toolchain."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _current_compiler_targets_x64() -> bool:
    target_arch = os.environ.get("VSCMD_ARG_TGT_ARCH", os.environ.get("Platform", "")).lower()
    return bool(shutil.which("cl")) and target_arch in ("x64", "amd64")


def build(destination: Path | None = None) -> Path:
    source = Path(__file__).with_name("adsb_receiver.c")
    destination = destination or (ROOT / "sidecar" / "rtl-sdr" if (ROOT / "LICENSE").is_file()
                                  else Path(__file__).parent)
    destination.mkdir(parents=True, exist_ok=True)
    upstream = source.parent / "dump1090-fa"
    target = destination / "airwsjtx_adsb.exe"
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
    for origin in (source, Path(__file__)):
        if origin.resolve() != (destination / origin.name).resolve():
            shutil.copyfile(origin, destination / origin.name)
    if upstream.resolve() != (destination / "dump1090-fa").resolve():
        shutil.copytree(upstream, destination / "dump1090-fa", dirs_exist_ok=True)
    if (ROOT / "LICENSE").is_file():
        shutil.copyfile(ROOT / "LICENSE", destination / "COPYING-AirWSJT-X")
    return target


if __name__ == "__main__":
    print(build())
