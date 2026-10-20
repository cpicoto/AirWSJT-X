import sys
from pathlib import Path, PureWindowsPath

repo_root = Path(SPECPATH).parent
analysis = Analysis(
    [str(repo_root / "sidecar" / "airwsjtx_bridge.py")],
    pathex=[str(repo_root)],
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
