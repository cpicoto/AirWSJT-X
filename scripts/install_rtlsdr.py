"""Stage pinned upstream receive-only RTL-SDR tools (no driver changes).

Windows x64 uses the upstream prebuilt release. macOS builds librtlsdr from the same
pinned source with a statically linked, pinned libusb; no Homebrew libraries are used.
``--android`` stages the same pinned sources, with librtlsdr patched to open an Android
USB file descriptor, for src-tauri/build.rs to compile with the NDK.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
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


LIBUSB_ARCHIVE = DEPENDENCY_SOURCES[0]
MACOS_DEPLOYMENT_TARGET = "12.0"
MACOS_RUNTIME_FILES = ("librtlsdr.dylib", "COPYING", "COPYING-libusb", "SOURCE.txt",
                       "rtl-sdr-blog-source.zip", LIBUSB_ARCHIVE[0])
LIBRTLSDR_SOURCES = ("librtlsdr.c", "tuner_e4k.c", "tuner_fc0012.c", "tuner_fc0013.c",
                     "tuner_fc2580.c", "tuner_r82xx.c")


def _build_macos_librtlsdr(source_zip: bytes, libusb_tar: bytes, output: Path) -> None:
    """Build a self-contained librtlsdr.dylib that exports only the rtlsdr_* API."""
    env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": MACOS_DEPLOYMENT_TARGET}
    cc = os.environ.get("CC", "clang")
    with tempfile.TemporaryDirectory(prefix="airwsjtx-librtlsdr-") as work:
        root = Path(work)
        with tarfile.open(fileobj=io.BytesIO(libusb_tar)) as archive:
            archive.extractall(root, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        libusb = next(root.glob("libusb-*"))
        prefix = root / "prefix"
        subprocess.run(["./configure", f"--prefix={prefix}", "--disable-shared", "--enable-static",
                        "--disable-dependency-tracking", "--disable-udev", f"CC={cc}", "CFLAGS=-O2"],
                       cwd=libusb, env=env, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["make", f"-j{os.cpu_count() or 2}", "install"],
                       cwd=libusb, env=env, check=True, stdout=subprocess.DEVNULL)
        with zipfile.ZipFile(io.BytesIO(source_zip)) as archive:
            archive.extractall(root)
        rtl = next(path for path in root.iterdir() if path.name.startswith("rtl-sdr-blog-"))
        subprocess.run([
            cc, "-O2", "-dynamiclib", "-fvisibility=hidden", "-Drtlsdr_EXPORTS",
            "-install_name", "@loader_path/librtlsdr.dylib",
            "-I", str(rtl / "include"), "-I", str(prefix / "include" / "libusb-1.0"),
            *(str(rtl / "src" / name) for name in LIBRTLSDR_SOURCES),
            str(prefix / "lib" / "libusb-1.0.a"),
            "-Wl,-exported_symbol,_rtlsdr_*",
            "-framework", "CoreFoundation", "-framework", "IOKit", "-framework", "Security",
            "-lobjc", "-o", str(output),
        ], env=env, check=True)


def _license_files(source: bytes, libusb_tar: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        copying = next(name for name in archive.namelist() if name.endswith("/COPYING"))
        files["COPYING"] = archive.read(copying)
    with tarfile.open(fileobj=io.BytesIO(libusb_tar)) as archive:
        member = next(member for member in archive.getmembers() if member.name.endswith("/COPYING"))
        license_file = archive.extractfile(member)
        if license_file is None:
            raise RuntimeError("Missing libusb LGPL license in source archive")
        files["COPYING-libusb"] = license_file.read()
    return files


def main_macos() -> None:
    source = download(SOURCE_URL, SOURCE_SHA256)
    name, url, checksum = LIBUSB_ARCHIVE
    libusb_tar = download(url, checksum)
    DESTINATION.mkdir(parents=True, exist_ok=True)
    _build_macos_librtlsdr(source, libusb_tar, DESTINATION / "librtlsdr.dylib")
    files = _license_files(source, libusb_tar)
    files["rtl-sdr-blog-source.zip"] = source
    files[name] = libusb_tar
    files["SOURCE.txt"] = (
        f"RTL-SDR Blog {VERSION} librtlsdr, built from source for macOS "
        f"{platform.machine()} (minimum macOS {MACOS_DEPLOYMENT_TARGET}).\n"
        "librtlsdr: GPL-2.0-or-later; see COPYING and the corresponding source archive.\n"
        f"Source: {SOURCE_URL}\nSHA256: {SOURCE_SHA256}\n"
        "libusb 1.0.29: LGPL-2.1-or-later, statically linked into librtlsdr.dylib; see COPYING-libusb.\n"
        f"Dependency source: {url}\nSHA256: {checksum}\n"
        "No USB drivers or kernel extensions are included or installed. No bias tee is enabled.\n"
    ).encode("ascii")
    for file_name, content in files.items():
        (DESTINATION / file_name).write_bytes(content)
    _build_helper()
    print(f"Built verified receive-only tools in {DESTINATION}")


ANDROID_DESTINATION = DESTINATION / "android"
_OPEN_SIGNATURE = "int rtlsdr_open(rtlsdr_dev_t **out_dev, uint32_t index)\n{"
_DEVICE_LIST = "\tcnt = libusb_get_device_list(dev->ctx, &list);\n"
_OPENED = "\tlibusb_free_device_list(list, 1);\n\n\tif (libusb_kernel_driver_active(dev->devh, 0) == 1) {"


def patch_librtlsdr_for_android(text: str) -> str:
    """Add rtlsdr_open_sys_dev(), which wraps an Android UsbDeviceConnection descriptor.

    Android apps cannot enumerate /dev/bus/usb; libusb must wrap the descriptor the
    platform grants after the user's USB permission. Everything after the device is
    opened (EEPROM, tuner probe, reset) stays upstream code.
    """
    for anchor in (_OPEN_SIGNATURE, _DEVICE_LIST, _OPENED):
        if text.count(anchor) != 1:
            raise RuntimeError(f"librtlsdr source no longer matches the Android patch: {anchor!r}")
    text = text.replace(_OPEN_SIGNATURE,
                        "static int rtlsdr_open_common(rtlsdr_dev_t **out_dev, uint32_t index, intptr_t sys_dev)\n{")
    text = text.replace(_DEVICE_LIST,
                        "\tif (sys_dev) {\n"
                        "\t\tr = libusb_wrap_sys_device(dev->ctx, sys_dev, &dev->devh);\n"
                        "\t\tif (r < 0) {\n"
                        "\t\t\tfprintf(stderr, \"usb_wrap_sys_device error %d\\n\", r);\n"
                        "\t\t\tgoto err;\n"
                        "\t\t}\n"
                        "\t\tgoto opened;\n"
                        "\t}\n\n" + _DEVICE_LIST)
    text = text.replace(_OPENED, "\tlibusb_free_device_list(list, 1);\n\nopened:\n"
                        "\tif (libusb_kernel_driver_active(dev->devh, 0) == 1) {")
    return text + (
        "\n/* AirWSJT-X Android patch. */\n"
        "int rtlsdr_open(rtlsdr_dev_t **out_dev, uint32_t index)\n"
        "{\n\treturn rtlsdr_open_common(out_dev, index, 0);\n}\n\n"
        "int rtlsdr_open_sys_dev(rtlsdr_dev_t **out_dev, intptr_t sys_dev)\n"
        "{\n\tif (!sys_dev)\n\t\treturn -1;\n"
        "\tlibusb_set_option(NULL, LIBUSB_OPTION_NO_DEVICE_DISCOVERY);\n"
        "\treturn rtlsdr_open_common(out_dev, 0, sys_dev);\n}\n"
    )


def main_android() -> None:
    """Stage verified, patched librtlsdr and libusb sources for the Android NDK build."""
    source = download(SOURCE_URL, SOURCE_SHA256)
    name, url, checksum = LIBUSB_ARCHIVE
    libusb_tar = download(url, checksum)
    if ANDROID_DESTINATION.exists():
        shutil.rmtree(ANDROID_DESTINATION)
    ANDROID_DESTINATION.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="airwsjtx-android-rtlsdr-") as work:
        root = Path(work)
        with tarfile.open(fileobj=io.BytesIO(libusb_tar)) as archive:
            archive.extractall(root, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            archive.extractall(root)
        shutil.move(str(next(root.glob("libusb-*"))), ANDROID_DESTINATION / "libusb")
        rtl = next(path for path in root.iterdir() if path.name.startswith("rtl-sdr-blog-"))
        shutil.move(str(rtl), ANDROID_DESTINATION / "rtl-sdr")
    librtlsdr = ANDROID_DESTINATION / "rtl-sdr" / "src" / "librtlsdr.c"
    librtlsdr.write_text(patch_librtlsdr_for_android(librtlsdr.read_text(encoding="utf-8")),
                         encoding="utf-8")
    files = _license_files(source, libusb_tar)
    files["rtl-sdr-blog-source.zip"] = source
    files[name] = libusb_tar
    files["SOURCE.txt"] = (
        f"RTL-SDR Blog {VERSION} librtlsdr and libusb 1.0.29, compiled into the Android app.\n"
        "librtlsdr: GPL-2.0-or-later; see COPYING and the corresponding source archive.\n"
        "Modified: rtlsdr_open_sys_dev() added to open an Android USB file descriptor "
        "(scripts/install_rtlsdr.py, patch_librtlsdr_for_android).\n"
        f"Source: {SOURCE_URL}\nSHA256: {SOURCE_SHA256}\n"
        "libusb 1.0.29: LGPL-2.1-or-later; see COPYING-libusb.\n"
        f"Dependency source: {url}\nSHA256: {checksum}\n"
        "No bias tee is enabled.\n"
    ).encode("ascii")
    for file_name, content in files.items():
        (ANDROID_DESTINATION / file_name).write_bytes(content)
    print(f"Staged verified Android RTL-SDR sources in {ANDROID_DESTINATION}")


def _build_helper() -> None:
    # Imports also work when this file is executed directly from scripts/.
    try:
        from scripts.build_adsb_receiver import build
    except ModuleNotFoundError:
        from build_adsb_receiver import build
    build(DESTINATION)


def main() -> None:
    if "--android" in sys.argv[1:]:
        main_android()
        return
    if sys.platform == "darwin":
        main_macos()
        return
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
    _build_helper()
    print(f"Installed verified receive-only tools in {DESTINATION}")


if __name__ == "__main__":
    main()
