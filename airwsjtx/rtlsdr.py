"""Local RTL-SDR receiver (Windows x64, macOS); owns a native receive-only demodulator."""
from __future__ import annotations

import ctypes
from collections import deque
import os
import math
import json
import queue
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from typing import Callable

from airwsjtx.adsb import ADSBDecoder, crc_ok
from airwsjtx.airscout import Aircraft


def tools_directory() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1] / "sidecar"))
    return root / "rtl-sdr"


def platform_supported() -> bool:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return machine in ("amd64", "x86_64")
    return sys.platform == "darwin" and machine in ("arm64", "x86_64")


def helper_name() -> str:
    return "airwsjtx_adsb.exe" if sys.platform == "win32" else "airwsjtx_adsb"


def library_name() -> str:
    return "rtlsdr.dll" if sys.platform == "win32" else "librtlsdr.dylib"


def support_error() -> str | None:
    if not platform_supported():
        return ("Integrated RTL-SDR reception currently supports Windows x64 and macOS only. "
                "Use a network source on this platform.")
    if sys.platform == "darwin" and os.environ.get("APP_SANDBOX_CONTAINER_ID"):
        return ("Integrated RTL-SDR reception is unavailable in the sandboxed Mac App Store/TestFlight build. "
                "Use the direct-download macOS app or a network source.")
    if not (tools_directory() / helper_name()).is_file() or not (tools_directory() / library_name()).is_file():
        return ("RTL-SDR tools are missing. Reinstall the app; source builds must run "
                "python scripts/install_rtlsdr.py.")
    return None


def discover_devices(timeout_s: float = 3.0) -> list[dict[str, object]]:
    result: queue.Queue[tuple[list[dict[str, object]] | None, Exception | None]] = queue.Queue(maxsize=1)

    def probe() -> None:
        try:
            result.put((_discover_devices(), None))
        except Exception as error:
            result.put((None, error))

    threading.Thread(target=probe, daemon=True, name="rtl-usb-discovery").start()
    try:
        devices, error = result.get(timeout=timeout_s)
    except queue.Empty as error:
        raise RuntimeError(f"RTL-SDR USB discovery timed out after {timeout_s:g} seconds.") from error
    if error:
        raise error
    assert devices is not None
    return devices


def _discover_devices() -> list[dict[str, object]]:
    error = support_error()
    if error:
        raise RuntimeError(error)
    directory = tools_directory()
    if sys.platform != "win32":
        # Absolute path to the bundled, self-contained library; no search paths are consulted.
        # It stays loaded: libusb's Darwin event thread makes unloading unsafe.
        return _list_devices(ctypes.CDLL(str(directory / library_name()), mode=os.RTLD_LOCAL))
    from ctypes import wintypes
    # Scope DLL lookup; never search the current working directory for dependencies.
    with os.add_dll_directory(str(directory)):
        library = ctypes.CDLL(str(directory / library_name()))
    try:
        return _list_devices(library)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
        kernel.FreeLibrary.restype = wintypes.BOOL
        if not kernel.FreeLibrary(library._handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _list_devices(library: ctypes.CDLL) -> list[dict[str, object]]:
    library.rtlsdr_get_device_count.restype = ctypes.c_uint32
    library.rtlsdr_get_device_name.argtypes = [ctypes.c_uint32]
    library.rtlsdr_get_device_name.restype = ctypes.c_char_p
    library.rtlsdr_get_device_usb_strings.argtypes = [
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
    ]
    library.rtlsdr_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
    library.rtlsdr_close.argtypes = [ctypes.c_void_p]
    library.rtlsdr_get_tuner_gains.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    devices = []
    for index in range(library.rtlsdr_get_device_count()):
        manufacturer, product, serial = (ctypes.create_string_buffer(256) for _ in range(3))
        descriptor_code = library.rtlsdr_get_device_usb_strings(index, manufacturer, product, serial)
        device = ctypes.c_void_p()
        gains: list[float] = []
        open_code = library.rtlsdr_open(ctypes.byref(device), index) if descriptor_code == 0 else -1
        message = None
        if descriptor_code != 0:
            message = (
                "Cannot access USB descriptors. The receiver may be busy or its WinUSB driver missing. "
                "Close the app using this dongle; do not replace drivers without checking the device."
                if sys.platform == "win32" else
                "Cannot access USB descriptors. The receiver may be busy; close other apps using this dongle."
            )
        elif open_code < 0:
            message = (
                f"Cannot open RTL-SDR receiver ({open_code}). Close other SDR apps and check USB"
                + ("/WinUSB." if sys.platform == "win32" else " (another SDR app may own it).")
            )
        else:
            try:
                count = library.rtlsdr_get_tuner_gains(device, None)
                if 0 < count <= 256:
                    values = (ctypes.c_int * count)()
                    actual = library.rtlsdr_get_tuner_gains(device, values)
                    if actual == count:
                        gains = sorted(set(value / 10 for value in values))
            finally:
                library.rtlsdr_close(device)
        devices.append({
            "index": index,
            "name": library.rtlsdr_get_device_name(index).decode("utf-8", errors="replace"),
            "manufacturer": manufacturer.value.decode("utf-8", errors="replace"),
            "product": product.value.decode("utf-8", errors="replace"),
            "serial": serial.value.decode("utf-8", errors="replace"),
            "available": descriptor_code == 0 and open_code == 0,
            "supported_gains_db": gains,
            "message": message,
        })
    return devices


def validate_gain(value: object, supported: list[float]) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Gain must be automatic (null) or a supported tuner gain in dB")
    if value not in supported:
        raise ValueError("Select an exact supported tuner gain; arbitrary gains are not accepted")
    return float(value)


class _ReceiverJob:
    """Kill only our receiver if the bridge is terminated without stdin cleanup."""
    def __init__(self, process: subprocess.Popen[str]) -> None:
        from ctypes import wintypes
        from ctypes.wintypes import BOOL, DWORD, HANDLE, LARGE_INTEGER, LPVOID
        SIZE_T = ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", LARGE_INTEGER), ("PerJobUserTimeLimit", LARGE_INTEGER),
                        ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T),
                        ("MaximumWorkingSetSize", SIZE_T), ("ActiveProcessLimit", DWORD),
                        ("Affinity", SIZE_T), ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                        ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                        ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [LPVOID, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = HANDLE
        self.kernel.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, LPVOID, DWORD]
        self.kernel.SetInformationJobObject.restype = BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
        self.kernel.AssignProcessToJobObject.restype = BOOL
        self.kernel.CloseHandle.argtypes = [HANDLE]
        self.kernel.CloseHandle.restype = BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error
        if not self.kernel.AssignProcessToJobObject(self.handle, HANDLE(int(process._handle))):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class LocalRTLSDRFeed:
    def __init__(self, device_index: int, on_update: Callable[[list[Aircraft]], None],
                 on_status: Callable[[str, str | None], None], gain_db: float | None = None,
                 device_info: dict[str, object] | None = None) -> None:
        self.device_index = device_index
        self.on_update = on_update
        self.on_status = on_status
        self.gain_db = gain_db
        self.device_info = device_info
        gains = device_info.get("supported_gains_db", []) if device_info else []
        self.supported_gains_db = list(gains) if isinstance(gains, list) else []
        self.decoder = ADSBDecoder()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._process: subprocess.Popen[str] | None = None
        self._job: _ReceiverJob | None = None
        self._threads: list[threading.Thread] = []
        self._stderr: deque[str] = deque(maxlen=12)
        self._state = "stopped"
        self._message: str | None = None
        self._iq: dict[str, object] | None = None
        self._last_frames: deque[dict[str, object]] = deque(maxlen=32)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            aircraft = self.decoder.snapshot()
            last = self.decoder.last_message_at
            return {
                "state": self._state, "message": self._message, "device_index": self.device_index,
                "frequency_hz": 1090000000, "sample_rate": 2400000,
                "gain_db": self.gain_db, "supported_gains_db": list(self.supported_gains_db),
                "frames": self.decoder.frames, "crc_errors": self.decoder.crc_errors,
                "unsupported_frames": self.decoder.unsupported_frames,
                "valid_messages": self.decoder.valid_messages, "positions": self.decoder.positions,
                "position_rejections": self.decoder.position_rejections,
                "aircraft": len(aircraft), "with_position": sum(ac.has_position for ac in aircraft),
                "last_message_age_s": round(time.monotonic() - last, 1) if last is not None else None,
                "diagnostics": list(self._stderr),
                "iq": dict(self._iq) if self._iq is not None else None,
                "last_frames": [dict(frame) for frame in self._last_frames],
            }

    def _status(self, state: str, message: str | None = None) -> None:
        with self._lock:
            self._state, self._message = state, message
        self.on_status(state, message)

    def start(self) -> None:
        error = support_error()
        if error:
            raise RuntimeError(error)
        if self._process is not None:
            raise RuntimeError("Local receiver is already started")
        if (self.device_info is None or self.device_info.get("index") != self.device_index
                or self.device_info.get("available") is not True):
            raise RuntimeError("RTL-SDR receiver must be discovered and available before starting.")
        self.gain_db = validate_gain(self.gain_db, self.supported_gains_db)
        self._status("connecting", "Opening RTL-SDR at 1090 MHz (receive only, bias tee off).")
        try:
            # Never enable -T (bias tee), shell execution, a TCP server, or transmission.
            args = [str(tools_directory() / helper_name()), "-d", str(self.device_index)]
            if self.gain_db is not None:
                args.extend(["-g", str(self.gain_db)])
            windows = sys.platform == "win32"
            # On POSIX the helper stops itself if this bridge exits and it is reparented.
            platform_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if windows else {}
            self._process = subprocess.Popen(
                args,
                cwd=tools_directory(), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="ascii", errors="replace", bufsize=1,
                **platform_options,
            )
            if windows:
                self._job = _ReceiverJob(self._process)
        except (OSError, RuntimeError):
            self.stop()
            raise
        for target in (self._read_frames, self._read_stderr, self._report):
            thread = threading.Thread(target=target, daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None:
            if process.poll() is None:
                # Windows closes this process's USB handles on termination; on POSIX SIGTERM lets
                # the helper cancel and close the device itself. No other PID is touched.
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError("RTL-SDR receiver worker did not stop")
        if process is not None:
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        if self._job is not None:
            self._job.close()
            self._job = None
        self._process = None
        self._status("stopped", "Receiver stopped; USB device released.")

    def _read_frames(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        for line in self._process.stdout:
            if self._stop.is_set():
                break
            with self._lock:
                self.decoder.consume(line)

    def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for line in self._process.stderr:
            with self._lock:
                if line.startswith("AIRWSJTX_FRAME "):
                    try:
                        data = json.loads(line[len("AIRWSJTX_FRAME "):])
                        if not isinstance(data, dict):
                            raise ValueError("Invalid native frame diagnostic shape")
                        frame = data.get("frame")
                        if not isinstance(frame, str) or len(frame) != 28:
                            raise ValueError("Invalid native frame length")
                        raw = bytes.fromhex(frame)
                        if raw[0] >> 3 != 17 or not crc_ok(raw):
                            raise ValueError("Native frame diagnostics failed independent CRC check")
                        if (data.get("df") != 17 or data.get("crc_syndrome") != 0
                                or type(data.get("timestamp_12mhz")) is not int
                                or data.get("sample_rate") != 2400000):
                            raise ValueError("Invalid native frame diagnostic metadata")
                        self._last_frames.append(data)
                    except (ValueError, TypeError) as error:
                        self._stderr.append(f"Invalid native frame diagnostics: {error}")
                elif line.startswith("AIRWSJTX_IQ "):
                    try:
                        data = json.loads(line[len("AIRWSJTX_IQ "):])
                        if not isinstance(data, dict) or not isinstance(data.get("samples"), int):
                            raise ValueError("Invalid native IQ diagnostic shape")
                        self._iq = data
                    except ValueError as error:
                        self._stderr.append(f"Invalid native IQ diagnostics: {error}")
                else:
                    self._stderr.append(line.strip())

    def _report(self) -> None:
        assert self._process is not None
        while not self._stop.wait(1):
            code = self._process.poll()
            with self._lock:
                diagnostics = " ".join(self._stderr)
            failed = any(word in diagnostics.lower() for word in ("failed", "error", "no supported devices"))
            if code is not None or failed:
                if code is None:
                    self._process.terminate()
                    code = self._process.wait(timeout=10)
                # Readers finish before exposing the final native diagnostic.
                for thread in self._threads[:2]:
                    thread.join(timeout=2)
                with self._lock:
                    diagnostics = " ".join(self._stderr)
                hint = ("Check WinUSB for Bulk-In Interface 0, unplugged hardware, or another app using the dongle."
                        if sys.platform == "win32" else
                        "Check for unplugged hardware or another app using the dongle.")
                self._status("error", f"RTL-SDR exited ({code}). {diagnostics} {hint}")
                self.on_update([])
                return
            with self._lock:
                aircraft = self.decoder.snapshot()
                last = self.decoder.last_message_at
            self._status("connected", reception_message(last))
            self.on_update(aircraft)


def reception_message(last_message_at: float | None, now: float | None = None) -> str:
    now = time.monotonic() if now is None else now
    if last_message_at is None:
        return "Receiver running; no CRC-valid ADS-B yet. Check a 1090 MHz antenna and coverage."
    age = max(0, now - last_message_at)
    if age > 15:
        return f"Receiver running; no recent CRC-valid ADS-B (last {age:.0f}s ago). Check antenna, coverage, or gain."
    return "Receiving recent CRC-valid ADS-B."
