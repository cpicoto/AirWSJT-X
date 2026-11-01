import sys
from pathlib import Path, PureWindowsPath

repo_root = Path(SPECPATH).parent
rtl_sdr_datas = []
rtl_sdr_binaries = []
machine = __import__("platform").machine().lower()
if sys.platform == "darwin" and machine in ("arm64", "x86_64"):
    rtl_sdr_dir = repo_root / "sidecar" / "rtl-sdr"
    executables = ("airwsjtx_adsb", "librtlsdr.dylib")
    required = ("adsb_receiver.c", "build_adsb_receiver.py", "COPYING-AirWSJT-X", "COPYING",
                "SOURCE.txt", "rtl-sdr-blog-source.zip", "COPYING-libusb", "libusb-1.0.29.tar.bz2")
    required += tuple(f"dump1090-fa/{name}" for name in
                      ("demod_2400.c", "icao_filter.c", "dump1090.h", "COPYING", "UPSTREAM.txt"))
    if any(not (rtl_sdr_dir / name).is_file() for name in executables + required):
        raise SystemExit("Run python scripts/install_rtlsdr.py before packaging macOS.")
    # Binaries keep their executable mode and are signed by PyInstaller with the bridge.
    rtl_sdr_binaries = [(str(rtl_sdr_dir / name), "rtl-sdr") for name in executables]
    rtl_sdr_datas = [(str(rtl_sdr_dir / name), str(Path("rtl-sdr") / Path(name).parent))
                    for name in required]
elif sys.platform == "win32" and machine in ("amd64", "x86_64"):
    rtl_sdr_dir = repo_root / "sidecar" / "rtl-sdr"
    required = ("airwsjtx_adsb.exe", "adsb_receiver.c", "build_adsb_receiver.py",
                "COPYING-AirWSJT-X", "rtl_adsb.exe", "rtlsdr.dll", "pthreadVC2.dll", "msvcr100.dll",
                "COPYING", "SOURCE.txt", "rtl-sdr-blog-source.zip", "COPYING-libusb",
                "libusb-1.0.29.tar.bz2", "pthreads-w32-2-9-1-release.tar.gz")
    required += tuple(f"dump1090-fa/{name}" for name in
                      ("demod_2400.c", "icao_filter.c", "dump1090.h", "COPYING", "UPSTREAM.txt"))
    if any(not (rtl_sdr_dir / name).is_file() for name in required):
        raise SystemExit("Run python scripts/install_rtlsdr.py before packaging Windows x64.")
    rtl_sdr_datas = [(str(rtl_sdr_dir / name), str(Path("rtl-sdr") / Path(name).parent))
                    for name in required]
analysis = Analysis(
    [str(repo_root / "sidecar" / "airwsjtx_bridge.py")],
    pathex=[str(repo_root)],
    binaries=rtl_sdr_binaries,
    datas=rtl_sdr_datas,
)

if sys.platform == "win32":
    # Windows 10+ supplies UCRT. App-local copies can be blocked by Application Control.
    analysis.binaries = [
        entry
        for entry in analysis.binaries
        if not (
            PureWindowsPath(entry[0]).match("ucrtbase.dll")
            or PureWindowsPath(entry[0]).match("api-ms-win-crt-*.dll")
        )
    ]

pyz = PYZ(analysis.pure)
if sys.platform == "win32":
    # Install the runtime beside the bridge instead of executing DLLs from _MEI temp paths.
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        contents_directory="airwsjtx_bridge-runtime",
        name="airwsjtx_bridge",
        console=True,
        strip=False,
        upx=False,
    )
    collect = COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        name="airwsjtx_bridge",
        strip=False,
        upx=False,
    )
else:
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name="airwsjtx_bridge",
        console=True,
        strip=False,
        upx=False,
    )
