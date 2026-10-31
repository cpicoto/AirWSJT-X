"""Stage pinned upstream Windows x64 receive-only tools (no driver changes)."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request
import zipfile

VERSION = "V1.4.0"
RELEASE_URL = f"https://github.com/rtlsdrblog/rtl-sdr-blog/releases/download/{VERSION}/Release.zip"
RELEASE_SHA256 = "7ef33f1304647f65e5e0fde43637a73d54f076e91e651a3cecc4f55a17fd9815"
SOURCE_URL = f"https://codeload.github.com/rtlsdrblog/rtl-sdr-blog/zip/refs/tags/{VERSION}"
SOURCE_SHA256 = "efaac50d53ea2f12d9853167ac9470bf4f04de944f17c5d52d2fd73a9ec24c18"
DESTINATION = Path(__file__).resolve().parents[1] / "sidecar" / "rtl-sdr"
RUNTIME_FILES = ("rtl_adsb.exe", "rtlsdr.dll", "pthreadVC2.dll", "msvcr100.dll")
DEPENDENCY_SOURCES = (
    ("libusb-1.0.29.tar.bz2",
     "https://github.com/libusb/libusb/releases/download/v1.0.29/libusb-1.0.29.tar.bz2",
     "5977fc950f8d1395ccea9bd48c06b3f808fd3c2c961b44b0c2e6e29fc3a70a85"),
    ("pthreads-w32-2-9-1-release.tar.gz",
     "https://sourceware.org/pub/pthreads-win32/pthreads-w32-2-9-1-release.tar.gz",
     "e6aca7aea8de33d9c8580bcb3a0ea3ec0a7ace4ba3f4e263ac7c7b66bc95fb4d"),
)


def download(url: str, expected_hash: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise RuntimeError(f"SHA256 mismatch for {url}; tools were not installed")
    return content


def main() -> None:
    release = download(RELEASE_URL, RELEASE_SHA256)
    source = download(SOURCE_URL, SOURCE_SHA256)
    with zipfile.ZipFile(io.BytesIO(release)) as archive:
        files = {name: archive.read(f"x64/{name}") for name in RUNTIME_FILES}
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        copying = next(name for name in archive.namelist() if name.endswith("/COPYING"))
        files["COPYING"] = archive.read(copying)
    files["rtl-sdr-blog-source.zip"] = source
    for name, url, checksum in DEPENDENCY_SOURCES:
        content = download(url, checksum)
        files[name] = content
        if name.startswith("libusb"):
            with tarfile.open(fileobj=io.BytesIO(content)) as archive:
                copying = next(member for member in archive.getmembers() if member.name.endswith("/COPYING"))
                license_file = archive.extractfile(copying)
                if license_file is None:
                    raise RuntimeError("Missing libusb LGPL license in source archive")
                files["COPYING-libusb"] = license_file.read()
    files["SOURCE.txt"] = (
        f"RTL-SDR Blog {VERSION}, Windows x64\n"
        "rtl_adsb / librtlsdr: GPL-2.0-or-later; see COPYING and corresponding source archive.\n"
        f"Release: {RELEASE_URL}\nSHA256: {RELEASE_SHA256}\n"
        f"Source: {SOURCE_URL}\nSHA256: {SOURCE_SHA256}\n"
        "pthreadVC2.dll: LGPL-2.1-or-later, upstream pthreads-win32.\n"
        "pthreads-win32 2.9.1 and libusb 1.0.29: corresponding sources and licenses "
        "are included in the .tar.gz / .tar.bz2 archives.\n"
        "libusb: LGPL-2.1-or-later, see COPYING-libusb.\n"
        "msvcr100.dll: Microsoft Visual C++ 2010 runtime, distributed by upstream.\n"
        "No USB drivers are included or installed. No bias tee is enabled.\n"
        + "".join(f"\nDependency source: {url}\nSHA256: {checksum}\n"
                  for _, url, checksum in DEPENDENCY_SOURCES)
    ).encode("ascii")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (DESTINATION / name).write_bytes(content)
    # Imports also work when this file is executed directly from scripts/.
    try:
        from scripts.build_adsb_receiver import build
    except ModuleNotFoundError:
        from build_adsb_receiver import build
    build(DESTINATION)
    print(f"Installed verified receive-only tools in {DESTINATION}")


if __name__ == "__main__":
    main()
