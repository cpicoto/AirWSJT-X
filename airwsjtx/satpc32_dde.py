"""Optional Windows DDEML server for SatPC32-compatible tracking clients.

Protocol: https://www.dk1tb.de/manual_e.htm (general DDE interface);
field example: M0LTE/satpc32-dde-utils, SatPC32Client.cs.
Radio fields are inert compatibility placeholders (zero Hz, USB), never CAT/PTT.
"""
from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from ctypes import wintypes

SERVICE = "SatPC32"
TOPIC = "SatPcDdeConv"
ITEM = "SatPcDdeItem"
NO_TARGET = "** NO SATELLITE **"


def format_target(name: str, azimuth: float, elevation: float) -> str:
    if not name or len(name) > 32 or not all(c.isascii() and (c.isalnum() or c == "-") for c in name):
        raise ValueError("DDE target identifier must be a single ASCII alphanumeric/hyphen token")
    if not math.isfinite(azimuth) or not math.isfinite(elevation):
        raise ValueError("DDE target angles must be finite")
    if not 0 <= azimuth <= 360 or not 0 <= elevation <= 90:
        raise ValueError("DDE target must be above the horizon with valid azimuth/elevation")
    azimuth = round(azimuth, 1) % 360
    # Legacy clients delimit EL/name using later fields rather than tokenizing
    # just SN/AZ/EL. Preserve the full conventional SatPC32 record shape.
    return f"SN{name} AZ{azimuth:.1f} EL{elevation:.1f} UP0 UMUSB DN0 DMUSB MA0.0"


def _api():
    if sys.platform != "win32":
        raise RuntimeError("SatPC32 DDE output is available only on Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ptr = ctypes.c_void_p
    callback_type = ctypes.WINFUNCTYPE(ptr, wintypes.UINT, wintypes.UINT,
                                     ptr, ptr, ptr, ptr, ctypes.c_size_t, ctypes.c_size_t)
    signatures = {
        "DdeInitializeW": (wintypes.UINT, [ctypes.POINTER(wintypes.DWORD), callback_type, wintypes.DWORD, wintypes.DWORD]),
        "DdeCreateStringHandleW": (ptr, [wintypes.DWORD, wintypes.LPCWSTR, ctypes.c_int]),
        "DdeFreeStringHandle": (wintypes.BOOL, [wintypes.DWORD, ptr]),
        "DdeCmpStringHandles": (ctypes.c_int, [ptr, ptr]),
        "DdeNameService": (ptr, [wintypes.DWORD, ptr, ptr, wintypes.UINT]),
        "DdeCreateDataHandle": (ptr, [wintypes.DWORD, ptr, wintypes.DWORD, wintypes.DWORD, ptr, wintypes.UINT, wintypes.UINT]),
        "DdePostAdvise": (wintypes.BOOL, [wintypes.DWORD, ptr, ptr]),
        "DdeUninitialize": (wintypes.BOOL, [wintypes.DWORD]),
        "DdeConnect": (ptr, [wintypes.DWORD, ptr, ptr, ptr]),
        "DdeDisconnect": (wintypes.BOOL, [ptr]),
        "DdeGetLastError": (wintypes.UINT, [wintypes.DWORD]),
        "DdeClientTransaction": (ptr, [ptr, wintypes.DWORD, ptr, ptr, wintypes.UINT, wintypes.UINT, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]),
        "DdeGetData": (wintypes.DWORD, [ptr, ptr, wintypes.DWORD, wintypes.DWORD]),
        "DdeFreeDataHandle": (wintypes.BOOL, [ptr]),
        "PeekMessageW": (wintypes.BOOL, [ctypes.POINTER(wintypes.MSG), ptr, wintypes.UINT, wintypes.UINT, wintypes.UINT]),
        "TranslateMessage": (wintypes.BOOL, [ctypes.POINTER(wintypes.MSG)]),
        "DispatchMessageW": (ctypes.c_ssize_t, [ctypes.POINTER(wintypes.MSG)]),
    }
    for name, (result, args) in signatures.items():
        fn = getattr(user32, name)
        fn.restype, fn.argtypes = result, args
    return user32, callback_type


class SatPC32DDE:
    """Owns DDEML on a dedicated message-pump thread; never opens a rotor."""

    def __init__(self, *, service: str = SERVICE, on_error=None) -> None:
        self.service = service
        self._on_error = on_error
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        self._text = NO_TARGET
        self._expires = 0.0
        self._running = False

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("DDE output is already started")
        _api()  # Fail explicitly before starting a thread on unsupported platforms.
        self._thread = threading.Thread(target=self._run, name="SatPC32-DDE", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.stop()
            raise RuntimeError("Timed out starting SatPC32 DDE output")
        if self._error:
            self.stop()
            raise RuntimeError(self._error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(5)
            if self._thread.is_alive():
                raise RuntimeError("Timed out stopping SatPC32 DDE output")

    def update(self, name: str, azimuth: float, elevation: float, *, valid_for_s: float) -> None:
        text = format_target(name, azimuth, elevation)
        if not math.isfinite(valid_for_s) or valid_for_s <= 0:
            self.clear()
            return
        with self._lock:
            self._text = text
            self._expires = time.monotonic() + min(valid_for_s, 120)

    def clear(self) -> None:
        with self._lock:
            self._text, self._expires = NO_TARGET, 0

    def status(self) -> dict:
        with self._lock:
            return {"supported": sys.platform == "win32", "enabled": self._running,
                    "service": self.service, "topic": TOPIC, "item": ITEM,
                    "target": self._text if time.monotonic() < self._expires else NO_TARGET,
                    "error": self._error}

    def _run(self) -> None:
        api, callback_type = _api()
        instance = wintypes.DWORD()
        handles = []
        registered = False
        initialized = False
        requested_at: dict[int, float] = {}
        conversations = set()

        def callback(kind, fmt, conversation, topic, item, data, arg1, arg2):
            if kind == 0x8072:  # XTYP_CONNECT_CONFIRM
                conversations.add(conversation)
            elif kind == 0x80C2:  # XTYP_DISCONNECT
                conversations.discard(conversation)
                requested_at.pop(conversation, None)
            topic_ok = len(handles) == 3 and api.DdeCmpStringHandles(topic, handles[1]) == 0
            item_ok = len(handles) == 3 and api.DdeCmpStringHandles(item, handles[2]) == 0
            if kind == 0x1062:  # XTYP_CONNECT: hsz1 topic, hsz2 service
                return int(topic_ok and api.DdeCmpStringHandles(item, handles[0]) == 0)
            if kind & ~0x000C == 0x1030:  # XTYP_ADVSTART, optional ACKREQ/NODATA
                return int(topic_ok and item_ok and fmt == 1)
            if kind in (0x20B0, 0x2022) and topic_ok and item_ok and fmt == 1:
                if kind == 0x20B0:
                    requested_at[conversation] = time.monotonic()
                elif time.monotonic() - requested_at.get(conversation, float("-inf")) < 0.2:
                    return 0
                # XTYP_REQUEST / XTYP_ADVREQ, CF_TEXT, NUL terminated.
                value = self.status()["target"].encode("ascii") + b"\0"
                buffer = ctypes.create_string_buffer(value)
                return api.DdeCreateDataHandle(instance, buffer, len(value), 0, handles[2], 1, 0)
            return 0

        cb = callback_type(callback)
        try:
            error = api.DdeInitializeW(ctypes.byref(instance), cb, 0, 0)
            if error:
                raise RuntimeError(f"DdeInitialize failed: 0x{error:04x}")
            initialized = True
            for value in (self.service, TOPIC, ITEM):
                handle = api.DdeCreateStringHandleW(instance, value, 1200)
                if not handle:
                    raise RuntimeError("Cannot allocate DDE string handle")
                handles.append(handle)
            existing = api.DdeConnect(instance, handles[0], handles[1], None)
            if existing:
                api.DdeDisconnect(existing)
                raise RuntimeError(f"DDE service {self.service} is already running; close SatPC32 or the other publisher")
            if not api.DdeNameService(instance, handles[0], None, 1):
                raise RuntimeError(f"Cannot register DDE service: 0x{api.DdeGetLastError(instance):04x}")
            registered = True
            with self._lock:
                self._running = True
            self._ready.set()
            previous = None
            advised_at = 0.0
            message = wintypes.MSG()
            while not self._stop.is_set():
                while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    api.TranslateMessage(ctypes.byref(message))
                    api.DispatchMessageW(ctypes.byref(message))
                text = self.status()["target"]
                # Refresh existing and newly subscribed clients even when rounded
                # angles are unchanged; requests always read the current value.
                now = time.monotonic()
                if text != previous or now - advised_at >= 1.0:
                    if not api.DdePostAdvise(instance, handles[1], handles[2]):
                        raise RuntimeError(f"DDE advise failed: 0x{api.DdeGetLastError(instance):04x}")
                    previous = text
                    advised_at = time.monotonic()
                self._stop.wait(0.05)
        except (RuntimeError, OSError) as exc:
            with self._lock:
                self._error = str(exc)
            if self._on_error:
                self._on_error(str(exc))
        finally:
            self._ready.set()
            for conversation in tuple(conversations):
                if not api.DdeDisconnect(conversation):
                    print(f"Cannot disconnect DDE client: 0x{api.DdeGetLastError(instance):04x}",
                          file=sys.stderr, flush=True)
            if registered:
                if not api.DdeNameService(instance, handles[0], None, 2):
                    print(f"Cannot unregister DDE service: 0x{api.DdeGetLastError(instance):04x}",
                          file=sys.stderr, flush=True)
            for handle in reversed(handles):
                api.DdeFreeStringHandle(instance, handle)
            if initialized:
                if not api.DdeUninitialize(instance):
                    print("Cannot uninitialize DDE server", file=sys.stderr, flush=True)
            with self._lock:
                self._running = False
