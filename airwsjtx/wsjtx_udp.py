"""
WSJT-X UDP protocol listener (schema 2).
Parses multicast datagrams from a running WSJT-X instance.
"""
from __future__ import annotations

import logging
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
MSG_HALT_TX = 8


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


def is_wsjtx_app(app_id: str | None) -> bool:
    """Return True if app_id represents a WSJT-X (or JTDX) instance.

    AirWSJT-X only processes messages from WSJT-X or JTDX. Other applications
    broadcasting on the WSJT-X UDP port or multicast group (such as ACLog, JTAlert,
    GridTracker, N1MM, Log4OM) are ignored.
    """
    if not app_id:
        return False
    normalized = app_id.strip().casefold()
    return normalized.startswith(("wsjt", "jtdx"))


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
class WSJTXQsoLogged:
    app_id: str
    dx_call: str | None
    dx_grid: str | None
    freq_hz: int
    mode: str | None
    report_sent: str | None
    report_rcvd: str | None


def _skip_qdatetime(d: bytes, o: int) -> int:
    _, o = _u64(d, o)  # julian day
    _, o = _u32(d, o)  # ms since midnight
    spec, o = _u8(d, o)
    if spec == 2:  # offset from UTC carries a seconds offset
        _, o = _i32(d, o)
    return o


def parse_qso_logged(d: bytes) -> WSJTXQsoLogged | None:
    try:
        magic, schema, msg_type, app_id, o = _parse_header(d)
        if magic != WSJTX_MAGIC or msg_type != MSG_QSO_LOGGED:
            return None
        o = _skip_qdatetime(d, o)
        dx_call, o = _str(d, o)
        dx_grid, o = _str(d, o)
        freq_hz, o = _u64(d, o)
        mode, o = _str(d, o)
        rst_sent, o = _str(d, o)
        rst_rcvd, o = _str(d, o)
        return WSJTXQsoLogged(app_id=app_id or "", dx_call=dx_call, dx_grid=dx_grid,
                              freq_hz=freq_hz, mode=mode, report_sent=rst_sent,
                              report_rcvd=rst_rcvd)
    except Exception:
        return None


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
        on_qso_logged: Callable[[WSJTXQsoLogged], None] | None = None,
        on_connected: Callable[[str], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        heartbeat_timeout_s: float = 30.0,
        socket_timeout_s: float = 2.0,
        rig_name_filter: str | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.port = port
        self.multicast = multicast
        self.wsjtx_host = wsjtx_host
        self.client_id = client_id
        self.on_status = on_status
        self.on_decode = on_decode
        self.on_qso_logged = on_qso_logged
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.socket_timeout_s = socket_timeout_s
        self.on_error = on_error
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._last_packet = 0.0
        self._state_lock = threading.Lock()
        self._error: str | None = None
        self._rig_name_filter = self._normalize_rig_name(rig_name_filter)
        self._active_app_id: str | None = None
        self._sock: socket.socket | None = None
        self._query_sock: socket.socket | None = None
        self._client_addrs: dict[str, dict[tuple, float]] = {}
        self.client_addr_ttl_s = 10.0
        self._known_rigs: dict[str, str | None] = {}

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

    def get_error(self) -> str | None:
        with self._state_lock:
            return self._error

    def _remember_rig(self, app_id: str, config_name: str | None = None) -> None:
        if not app_id or not is_wsjtx_app(app_id):
            return
        with self._state_lock:
            self._known_rigs[app_id] = config_name or self._known_rigs.get(app_id)

    def get_known_rigs(self) -> list[dict[str, str | None]]:
        with self._state_lock:
            return [
                {"app_id": app_id, "config_name": config_name}
                for app_id, config_name in sorted(
                    self._known_rigs.items(), key=lambda item: item[0].casefold()
                )
            ]

    def _matches_source(self, app_id: str, status: WSJTXStatus | None = None) -> bool:
        if not is_wsjtx_app(app_id):
            return False

        with self._state_lock:
            rig_name_filter = self._rig_name_filter
            active_app_id = self._active_app_id

        if active_app_id and app_id != active_app_id:
            return False
        if not rig_name_filter:
            return True

        candidates = [app_id]
        if " - " in app_id:
            candidates.append(app_id.split(" - ", 1)[1])
        elif "-" in app_id and app_id.casefold().startswith(("wsjt-x-", "jtdx-")):
            candidates.append(app_id.split("-", 2)[-1])
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
        try:
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(min(self.socket_timeout_s, 0.2))
            s.bind(("", self.port))
            try:
                mreq = socket.inet_aton(self.multicast) + socket.inet_aton("0.0.0.0")
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass
        except OSError:
            s.close()
            raise
        return s

    def _pack_str(self, value: str | None) -> bytes:
        if value is None:
            return struct.pack(">I", 0xFFFFFFFF)
        encoded = value.encode("utf-8")
        return struct.pack(">I", len(encoded)) + encoded

    def _build_command(self, msg_type: int, app_id: str, *fields: bytes) -> bytes:
        return b"".join([
            struct.pack(">III", WSJTX_MAGIC, 2, msg_type),
            self._pack_str(app_id),
            *fields,
        ])

    def _send_command(self, msg_type: int, *fields: bytes) -> bool:
        """Send a control message back to the address(es) the active WSJT-X sends from.

        WSJT-X is a UDP client bound to an ephemeral port, so commands only arrive when sent to
        the source address of its own datagrams, not to the server port it talks to.
        """
        now = time.monotonic()
        with self._state_lock:
            app_id = self._active_app_id
            tx_sock = self._query_sock
            destinations = [
                addr for addr, seen in self._client_addrs.get(app_id or "", {}).items()
                if now - seen <= self.client_addr_ttl_s
            ]
        if not app_id or tx_sock is None or not destinations:
            return False
        packet = self._build_command(msg_type, app_id, *fields)
        sent = False
        for dest in destinations:
            try:
                tx_sock.sendto(packet, dest)
                sent = True
            except OSError:
                pass
        return sent

    def send_halt_tx(self) -> bool:
        """Halt Tx (type 8): stop transmitting immediately and disable Tx.

        WSJT-X has no UDP command for Enable Tx, so Tx can only be started from WSJT-X itself.
        """
        return self._send_command(MSG_HALT_TX, struct.pack(">B", 0))

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

    def _handle_datagram(self, data: bytes, addr: tuple | None = None) -> bool:
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

        if not is_wsjtx_app(app_id):
            return False

        if addr and app_id:
            with self._state_lock:
                self._client_addrs.setdefault(app_id, {})[tuple(addr[:2])] = time.monotonic()

        if msg_type == MSG_STATUS:
            st = parse_status(data)
            if st:
                self._remember_rig(st.app_id, st.config_name)
            if st and self._matches_source(st.app_id, st):
                self._last_packet = time.monotonic()
                self._mark_connected(st.app_id)
                if self.on_status:
                    self.on_status(st)
                return True
        elif msg_type == MSG_DECODE:
            dec = parse_decode(data)
            if dec:
                self._remember_rig(dec.app_id)
            if dec and self._matches_source(dec.app_id):
                self._last_packet = time.monotonic()
                self._mark_connected(dec.app_id)
                if self.on_decode:
                    self.on_decode(dec)
                return True
        elif msg_type == MSG_QSO_LOGGED:
            qso = parse_qso_logged(data)
            if qso:
                self._remember_rig(qso.app_id)
            if qso and self._matches_source(qso.app_id):
                self._last_packet = time.monotonic()
                self._mark_connected(qso.app_id)
                if self.on_qso_logged:
                    self.on_qso_logged(qso)
                return True
        elif msg_type == MSG_CLOSE and self._matches_source(app_id or ""):
            self._mark_disconnected()
            return True
        elif msg_type == MSG_HEARTBEAT:
            hb = parse_heartbeat(data)
            if hb:
                self._remember_rig(hb.app_id)
            if self._matches_source(app_id or ""):
                self._last_packet = time.monotonic()
                self._mark_connected(app_id or "WSJT-X")
                return True
        return False

    def _make_query_socket(self) -> socket.socket | None:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.settimeout(0.05)
            sock.bind(("127.0.0.1", 0))
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            except OSError:
                pass
            return sock
        except OSError:
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sock = self._make_socket()
            except OSError as e:
                message = (
                    f"Cannot listen for WSJT-X UDP on port {self.port}: {e}. "
                    "Check whether another app owns the port. For Log4OM, send WSJT-X "
                    "to a separate UDP proxy input port and forward its data to "
                    f"port {self.port}, or use multicast with compatible receivers. "
                    "Retrying automatically."
                )
                with self._state_lock:
                    changed = self._error != message
                    self._error = message
                    self._connected = False
                    self._active_app_id = None
                if changed:
                    logging.getLogger(__name__).error(message)
                    if self.on_error:
                        self.on_error(message)
                    if self.on_disconnected:
                        self.on_disconnected()
                if self._stop.wait(1.0):
                    return
            else:
                with self._state_lock:
                    self._error = None
                break
        else:
            return

        query_sock = self._make_query_socket()
        self._sock = sock
        self._query_sock = query_sock

        heartbeat = self._build_heartbeat()
        last_heartbeat = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_heartbeat >= 2.0:
                if query_sock is not None:
                    try:
                        query_sock.sendto(heartbeat, (self.wsjtx_host, self.port))
                    except OSError:
                        pass
                last_heartbeat = now

            try:
                data, src = sock.recvfrom(4096)
                self._handle_datagram(data, src)
            except socket.timeout:
                pass
            except OSError:
                if self._stop.is_set():
                    break
            except Exception:
                pass

            if query_sock is not None:
                try:
                    q_data, q_src = query_sock.recvfrom(4096)
                    self._handle_datagram(q_data, q_src)
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
