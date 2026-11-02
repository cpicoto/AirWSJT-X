"""
Hamlib rotator control via rotctld TCP interface.
rotctld protocol (port 4533 by default):
  p           → get current position → "<az>\\n<el>\\n" or "Az: <f>\\nEl: <f>\\n"
  P <az> <el> → set target position   → "RPRT 0\\n"
  S           → stop rotation         → "RPRT 0\\n"
  R <az>      → reset (park)
"""
from __future__ import annotations

import math
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class RotatorPosition:
    azimuth: float
    elevation: float


class HamlibRotator:
    """
    TCP client for rotctld. Polls position every `poll_interval_s` and
    fires callbacks for position changes.
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 4533

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        poll_interval_s: float = 0.5,
        on_position: Callable[[RotatorPosition], None] | None = None,
        on_connected: Callable[[], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.poll_interval_s = poll_interval_s
        self.on_position = on_position
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_error = on_error

        self._sock: socket.socket | None = None
        self._buf: bytes = b""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current: RotatorPosition | None = None
        self._target: RotatorPosition | None = None
        self._acknowledged_target: RotatorPosition | None = None
        self._poll_responded = False
        self._last_poll_error: str | None = None
        self.connected = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._disconnect()

    def set_position(self, azimuth: float, elevation: float) -> None:
        """Command rotator to move to (azimuth, elevation).

        Azimuth within the rotator's 0..450 travel is sent as-is; anything outside
        (e.g. negative) is wrapped into [0, 360). Elevation is clamped to 0..90.
        """
        azimuth = round(azimuth, 1)
        if not 0.0 <= azimuth <= 450.0:
            azimuth = azimuth % 360.0
        elevation = min(max(elevation, 0.0), 90.0)
        self._target = RotatorPosition(azimuth, elevation)
        self._send_command(
            f"P {azimuth:.1f} {elevation:.1f}\n",
            target=RotatorPosition(azimuth, round(elevation, 1)))

    def stop_rotation(self) -> None:
        """Send stop command."""
        self._send_command("S\n")
        self._target = None

    def park(self) -> None:
        """Park rotator at 0/0."""
        self.set_position(0.0, 0.0)

    @property
    def current_position(self) -> RotatorPosition | None:
        return self._current

    # ── Internal ──────────────────────────────────────────────────────────────

    def _drain(self) -> None:
        """Flush any unread bytes from socket and internal buffer before sending."""
        self._buf = b""
        if not self._sock:
            return
        orig_timeout = self._sock.gettimeout()
        self._sock.settimeout(0.0)
        try:
            while True:
                data = self._sock.recv(1024)
                if not data:
                    break
        except (BlockingIOError, socket.error):
            pass
        finally:
            if self._sock:
                self._sock.settimeout(orig_timeout)

    def _read_line(self, timeout: float = 2.0) -> str | None:
        """Read a single newline-terminated line from the socket."""
        if not self._sock:
            return None
        self._sock.settimeout(timeout)
        while b"\n" not in self._buf:
            try:
                chunk = self._sock.recv(256)
            except (socket.timeout, TimeoutError):
                return None
            except Exception:
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("ascii", "ignore").strip()

    def _send_command(self, cmd: str, *, target: RotatorPosition | None = None) -> str | None:
        """Send a command (e.g. P or S) and read the response line (e.g. RPRT 0)."""
        with self._lock:
            self._acknowledged_target = None
            if not self._sock:
                return None
            try:
                self._drain()
                self._sock.sendall(cmd.encode("ascii"))
                deadline = time.monotonic() + 2.0
                resp = self._read_line(timeout=2.0)
                # Extended replies echo the command before the final RPRT.
                # Consume the whole reply, including when TCP splits its lines.
                if resp is not None and resp.lower().startswith(("set_pos:", "stop:")):
                    while not any(field.strip().startswith("RPRT ") for field in resp.split(";")):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            resp = None
                            break
                        resp = self._read_line(timeout=remaining)
                        if resp is None:
                            break
                while b"\n" in self._buf:
                    _, self._buf = self._buf.split(b"\n", 1)
                fields = [field.strip() for field in resp.split(";") if field.strip()] if resp else []
                if fields and fields[-1] == "RPRT 0":
                    self._acknowledged_target = target
                elif self.on_error:
                    self.on_error(f"rotctld command was not acknowledged: {resp!r}")
                return resp
            except Exception as e:
                if self.on_error:
                    self.on_error(str(e))
                return None

    def _get_position(self) -> RotatorPosition | None:
        with self._lock:
            self._poll_responded = False
            if not self._sock:
                return None

            def poll_error(message: str) -> None:
                if message != self._last_poll_error and self.on_error:
                    self.on_error(message)
                self._last_poll_error = message

            fields: list[str] = []

            def read_field() -> str:
                if not fields:
                    line = self._read_line(timeout=2.0)
                    if line is None:
                        raise TimeoutError("rotctld position reply timed out or connection closed")
                    # Extended replies may put all fields on one line.
                    fields.extend(line.split(";"))
                return fields.pop(0).strip()

            try:
                self._drain()
                self._sock.sendall(b"p\n")
                line1 = read_field()

                # Error reported by rotator daemon (e.g. "RPRT -1")
                if line1.startswith("RPRT"):
                    if line1 != "RPRT 0":
                        self._poll_responded = True
                        poll_error(f"rotctld position query failed: {line1}")
                        return None
                    # If RPRT 0 appeared first, read next line
                    line1 = read_field()

                # Skip Hamlib extended response header like "get_pos:"
                extended = line1.lower().startswith("get_pos")
                if extended:
                    line1 = read_field()

                line2 = read_field()

                # Skip any stray RPRT in line2
                if line2 == "RPRT 0":
                    line2 = read_field()

                self._poll_responded = True
                if extended:
                    status = read_field()
                    if status != "RPRT 0":
                        poll_error(f"rotctld position query failed: {status}")
                        return None
                # Drain any remaining lines in buffer (e.g. trailing blank or RPRT 0)
                while b"\n" in self._buf:
                    _, self._buf = self._buf.split(b"\n", 1)

                az_str = line1.split(":", 1)[1].strip() if ":" in line1 else line1
                el_str = line2.split(":", 1)[1].strip() if ":" in line2 else line2
                if not az_str and not el_str and self._acknowledged_target is not None:
                    self._last_poll_error = None
                    return self._acknowledged_target
                try:
                    az, el = float(az_str), float(el_str)
                    if not math.isfinite(az) or not math.isfinite(el):
                        raise ValueError
                except ValueError:
                    poll_error(f"rotctld returned invalid position: azimuth={az_str!r}, elevation={el_str!r}")
                    return None
                self._last_poll_error = None
                return RotatorPosition(az, el)
            except OSError as exc:
                self._poll_responded = False
                poll_error(f"rotctld position query failed: {exc}")
                return None

    def _connect(self) -> bool:
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            sock.connect((self.host, self.port))
            sock.settimeout(1.5)
            with self._lock:
                if self._stop.is_set():
                    sock.close()
                    return False
                self._sock = sock
                self._buf = b""
                self._acknowledged_target = None
                self._current = None
                self.connected = True
            if self.on_connected:
                self.on_connected()
            return True
        except Exception as e:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            if self._stop.is_set():
                return False
            if self.on_error:
                self.on_error(f"Cannot connect to rotctld at {self.host}:{self.port}: {e}")
            return False

    def _disconnect(self) -> None:
        was_connected = self.connected
        self.connected = False
        with self._lock:
            self._acknowledged_target = None
            self._current = None
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                self._buf = b""
        if was_connected and self.on_disconnected:
            self.on_disconnected()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._connect():
                    self._stop.wait(3.0)
                    continue

                consecutive_failures = 0
                while not self._stop.is_set():
                    pos = self._get_position()
                    if pos is not None:
                        consecutive_failures = 0
                        self._current = pos
                        if self.on_position:
                            self.on_position(pos)
                    elif self._poll_responded:
                        # A daemon reporting missing coordinates or a device error
                        # is still reachable; reconnecting cannot repair its position.
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        with self._lock:
                            sock_alive = self._sock is not None
                        if not sock_alive or consecutive_failures >= 3:
                            self._disconnect()
                            break
                    self._stop.wait(self.poll_interval_s)

                if not self._stop.is_set():
                    self._stop.wait(3.0)
        finally:
            self._disconnect()
