"""
WSJT-X UDP protocol listener (schema 2).
Parses multicast datagrams from a running WSJT-X instance.
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


WSJTX_MAGIC = 0xADBCCBDA
WSJTX_PORT = 2237
WSJTX_MULTICAST = "224.0.0.1"

MSG_HEARTBEAT = 0
MSG_STATUS = 1
MSG_DECODE = 2
MSG_CLEAR = 3
MSG_QSO_LOGGED = 5
MSG_CLOSE = 6


# ─── Parsing helpers ──────────────────────────────────────────────────────────

def _u32(d: bytes, o: int) -> tuple[int, int]:
    (v,) = struct.unpack_from(">I", d, o)
    return v, o + 4


def _u64(d: bytes, o: int) -> tuple[int, int]:
    (v,) = struct.unpack_from(">Q", d, o)
    return v, o + 8


def _i32(d: bytes, o: int) -> tuple[int, int]:
    (v,) = struct.unpack_from(">i", d, o)
    return v, o + 4


def _f64(d: bytes, o: int) -> tuple[float, int]:
    (v,) = struct.unpack_from(">d", d, o)
    return v, o + 8


def _bool(d: bytes, o: int) -> tuple[bool, int]:
    return bool(d[o]), o + 1


def _u8(d: bytes, o: int) -> tuple[int, int]:
    return d[o], o + 1


def _str(d: bytes, o: int) -> tuple[str | None, int]:
    ln, o = _u32(d, o)
    if ln == 0xFFFFFFFF:
        return None, o
    return d[o : o + ln].decode("utf-8", "replace"), o + ln


# ─── Message dataclasses ──────────────────────────────────────────────────────

@dataclass
class WSJTXStatus:
    app_id: str
    freq_hz: int
    mode: str | None
    dx_call: str | None
    report: str | None
    tx_mode: str | None
    tx_enabled: bool
    transmitting: bool
    decoding: bool
    rx_df: int
    tx_df: int
    de_call: str | None
    de_grid: str | None
    dx_grid: str | None
    tx_watchdog: bool
    sub_mode: str | None
    fast_mode: bool
    special_op_mode: int
    freq_tolerance: int
    tr_period: float | None
    config_name: str | None

    @property
    def freq_mhz(self) -> float:
        return self.freq_hz / 1_000_000


@dataclass
class WSJTXDecode:
    app_id: str
    is_new: bool
    time_ms: int
    snr: int
    delta_time_s: float
    delta_freq_hz: int
    mode: str | None
    message: str | None
    low_confidence: bool
    off_air: bool


@dataclass
class WSJTXHeartbeat:
    app_id: str
    max_schema: int
    version: str | None
    revision: str | None


# ─── Parsers ──────────────────────────────────────────────────────────────────

def _parse_header(d: bytes) -> tuple[int, int, int, str | None, int]:
    """Returns (magic, schema, msg_type, app_id, offset)."""
    magic, o = _u32(d, 0)
    schema, o = _u32(d, o)
    msg_type, o = _u32(d, o)
    app_id, o = _str(d, o)
    return magic, schema, msg_type, app_id, o


def _normalize_tr_period(mode: str | None, tr_period: int) -> float | None:
    """Normalize WSJT-X T/R period.

    Positive wire values are passed through as seconds. WSJT-X may emit
    non-positive values (including 0 and -1) for unset periods where timing is
    implied by mode, so known fixed-period modes are mapped to defaults;
    unknown or missing modes return None.
    """
    if tr_period > 0:
        return float(tr_period)
    if not mode:
        return None
    return {
        "FT8": 15.0,
        "FT4": 7.5,
        "FT2": 2.5,
        "JT65": 60.0,
    }.get(mode.upper())


def parse_status(d: bytes) -> WSJTXStatus | None:
    try:
        magic, schema, msg_type, app_id, o = _parse_header(d)
        if magic != WSJTX_MAGIC or msg_type != MSG_STATUS:
            return None
        freq, o = _u64(d, o)
        mode, o = _str(d, o)
        dx_call, o = _str(d, o)
        report, o = _str(d, o)
        tx_mode, o = _str(d, o)
        tx_en, o = _bool(d, o)
        transmitting, o = _bool(d, o)
        decoding, o = _bool(d, o)
        rx_df, o = _u32(d, o)
        tx_df, o = _u32(d, o)
        de_call, o = _str(d, o)
        de_grid, o = _str(d, o)
        dx_grid, o = _str(d, o)
        watchdog, o = _bool(d, o)
        sub_mode, o = _str(d, o)
        fast, o = _bool(d, o)
        spec_op, o = _u8(d, o)
        freq_tol, o = _u32(d, o)
        tr_period, o = _i32(d, o)
        config, o = _str(d, o)
        return WSJTXStatus(
            app_id=app_id or "",
            freq_hz=freq,
            mode=mode,
            dx_call=dx_call,
            report=report,
            tx_mode=tx_mode,
            tx_enabled=tx_en,
            transmitting=transmitting,
            decoding=decoding,
            rx_df=rx_df,
            tx_df=tx_df,
            de_call=de_call,
            de_grid=de_grid,
            dx_grid=dx_grid,
            tx_watchdog=watchdog,
            sub_mode=sub_mode,
            fast_mode=fast,
            special_op_mode=spec_op,
            freq_tolerance=freq_tol,
            tr_period=_normalize_tr_period(mode, tr_period),
            config_name=config,
        )
    except Exception:
        return None


def parse_decode(d: bytes) -> WSJTXDecode | None:
    try:
        magic, schema, msg_type, app_id, o = _parse_header(d)
        if magic != WSJTX_MAGIC or msg_type != MSG_DECODE:
            return None
        is_new, o = _bool(d, o)
        time_ms, o = _u32(d, o)
        snr, o = _i32(d, o)
        dt, o = _f64(d, o)
        df, o = _u32(d, o)
        mode, o = _str(d, o)
        message, o = _str(d, o)
        low_conf, o = _bool(d, o)
        off_air, o = _bool(d, o)
        return WSJTXDecode(
            app_id=app_id or "",
            is_new=is_new,
            time_ms=time_ms,
            snr=snr,
            delta_time_s=dt,
            delta_freq_hz=df,
            mode=mode,
            message=message,
            low_confidence=low_conf,
            off_air=off_air,
        )
    except Exception:
        return None


def parse_heartbeat(d: bytes) -> WSJTXHeartbeat | None:
    try:
        magic, schema, msg_type, app_id, o = _parse_header(d)
        if magic != WSJTX_MAGIC or msg_type != MSG_HEARTBEAT:
            return None
        max_schema, o = _u32(d, o)
        version, o = _str(d, o)
        revision, o = _str(d, o)
        return WSJTXHeartbeat(app_id=app_id or "", max_schema=max_schema,
                              version=version, revision=revision)
    except Exception:
        return None


# ─── Listener thread ──────────────────────────────────────────────────────────

class WSJTXListener:
    """Background UDP listener. Calls on_status/on_decode/on_connected/on_disconnected."""

    def __init__(
        self,
        port: int = WSJTX_PORT,
        multicast: str = WSJTX_MULTICAST,
        wsjtx_host: str = "127.0.0.1",
        client_id: str = "AirWSJTX",
        on_status: Callable[[WSJTXStatus], None] | None = None,
        on_decode: Callable[[WSJTXDecode], None] | None = None,
        on_connected: Callable[[str], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        heartbeat_timeout_s: float = 30.0,
        socket_timeout_s: float = 2.0,
        rig_name_filter: str | None = None,
    ) -> None:
        self.port = port
        self.multicast = multicast
        self.wsjtx_host = wsjtx_host
        self.client_id = client_id
        self.on_status = on_status
        self.on_decode = on_decode
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.socket_timeout_s = socket_timeout_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._last_packet = 0.0
        self._state_lock = threading.Lock()
        self._rig_name_filter = self._normalize_rig_name(rig_name_filter)
        self._active_app_id: str | None = None
        self._active_query_mode = False

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _normalize_rig_name(rig_name: str | None) -> str | None:
        if rig_name is None:
            return None
        value = rig_name.strip()
        return value or None

    def set_rig_name_filter(self, rig_name: str | None) -> str | None:
        with self._state_lock:
            self._rig_name_filter = self._normalize_rig_name(rig_name)
            self._active_app_id = None
            was_connected = self._connected
            self._connected = False
            configured_filter = self._rig_name_filter
        if was_connected and self.on_disconnected:
            self.on_disconnected()
        return configured_filter

    def get_rig_name_filter(self) -> str | None:
        with self._state_lock:
            return self._rig_name_filter

    def _matches_source(self, app_id: str, status: WSJTXStatus | None = None) -> bool:
        with self._state_lock:
            rig_name_filter = self._rig_name_filter
            active_app_id = self._active_app_id

        if active_app_id and app_id != active_app_id:
            return False
        if not rig_name_filter:
            return True

        candidates = [app_id]
        if status and status.config_name:
            candidates.append(status.config_name)
        expected = rig_name_filter.casefold()
        return any(candidate.casefold() == expected for candidate in candidates if candidate)

    def _mark_connected(self, app_id: str) -> None:
        with self._state_lock:
            if self._active_app_id is None:
                self._active_app_id = app_id
            was_connected = self._connected
            self._connected = True
        if not was_connected and self.on_connected:
            self.on_connected(app_id or "WSJT-X")

    def _mark_disconnected(self) -> None:
        with self._state_lock:
            was_connected = self._connected
            self._connected = False
            self._active_app_id = None
        if was_connected and self.on_disconnected:
            self.on_disconnected()

    def _check_connection_timeout(self, now: float | None = None) -> None:
        if not self._connected or not self._last_packet:
            return
        if now is None:
            now = time.monotonic()
        if (now - self._last_packet) > self.heartbeat_timeout_s:
            self._mark_disconnected()

    def _make_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        # SO_REUSEPORT is not available/reliable on all Windows builds.
        # Best effort only; lack of support must not break WSJT-X listening.
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(min(self.socket_timeout_s, 0.2))
        try:
            s.bind(("", self.port))
            self._active_query_mode = False
        except OSError:
            # If WSJT-X already owns 2237 on loopback, continue in query mode.
            s.bind(("127.0.0.1", 0))
            self._active_query_mode = True
        # Multicast join is optional for localhost-unicast WSJT-X setups.
        # If join fails, keep socket alive for unicast traffic on the same port.
        if not self._active_query_mode:
            try:
                mreq = struct.pack("4sL", socket.inet_aton(self.multicast), socket.INADDR_ANY)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass
        return s

    def _pack_str(self, value: str | None) -> bytes:
        if value is None:
            return struct.pack(">I", 0xFFFFFFFF)
        encoded = value.encode("utf-8")
        return struct.pack(">I", len(encoded)) + encoded

    def _build_heartbeat(self) -> bytes:
        return b"".join(
            [
                struct.pack(">I", WSJTX_MAGIC),
                struct.pack(">I", 2),
                struct.pack(">I", MSG_HEARTBEAT),
                self._pack_str(self.client_id),
                struct.pack(">I", 2),
                self._pack_str("2.0"),
                self._pack_str(""),
            ]
        )

    def _handle_datagram(self, data: bytes) -> bool:
        if len(data) < 12:
            return False
        try:
            magic, _, msg_type, app_id, _ = _parse_header(data)
        except Exception:
            return False

        if magic != WSJTX_MAGIC:
            return False

        if app_id == self.client_id:
            return False

        if msg_type == MSG_STATUS:
            st = parse_status(data)
            if st and self._matches_source(st.app_id, st):
                self._last_packet = time.monotonic()
                self._mark_connected(st.app_id)
                if self.on_status:
                    self.on_status(st)
                return True
        elif msg_type == MSG_DECODE:
            dec = parse_decode(data)
            if dec and self._matches_source(dec.app_id):
                self._last_packet = time.monotonic()
                self._mark_connected(dec.app_id)
                if self.on_decode:
                    self.on_decode(dec)
                return True
        elif msg_type == MSG_CLOSE and self._matches_source(app_id or ""):
            self._mark_disconnected()
            return True
        elif msg_type == MSG_HEARTBEAT and self._matches_source(app_id or ""):
            self._last_packet = time.monotonic()
            self._mark_connected(app_id or "WSJT-X")
            return True
        return False

    def _run(self) -> None:
        try:
            sock = self._make_socket()
        except Exception:
            if self.on_disconnected:
                self.on_disconnected()
            return

        query_sock: socket.socket | None = None
        if not self._active_query_mode:
            try:
                query_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                query_sock.settimeout(0.05)
                query_sock.bind(("127.0.0.1", 0))
            except OSError:
                query_sock = None

        heartbeat = self._build_heartbeat()
        last_heartbeat = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_heartbeat >= 2.0:
                tx_sock = sock if self._active_query_mode else query_sock
                if tx_sock is not None:
                    try:
                        tx_sock.sendto(heartbeat, (self.wsjtx_host, self.port))
                    except OSError:
                        pass
                last_heartbeat = now

            try:
                data, _ = sock.recvfrom(4096)
                self._handle_datagram(data)
            except socket.timeout:
                pass
            except OSError:
                if self._stop.is_set():
                    break
            except Exception:
                pass

            if query_sock is not None:
                try:
                    q_data, _ = query_sock.recvfrom(4096)
                    self._handle_datagram(q_data)
                except socket.timeout:
                    pass
                except OSError:
                    pass
                except Exception:
                    pass

            self._check_connection_timeout(now)

        sock.close()
        if query_sock is not None:
            query_sock.close()
