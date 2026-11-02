from __future__ import annotations

import socket
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from airwsjtx.hamlib_rotator import HamlibRotator, RotatorPosition


class HamlibRotatorTests(unittest.TestCase):
    def _create_mock_rotator(self, response_bytes: bytes) -> HamlibRotator:
        rot = HamlibRotator()
        sock = MagicMock()
        timeout_val = [1.0]

        def settimeout(val: float) -> None:
            timeout_val[0] = val

        sock.settimeout.side_effect = settimeout
        sock.gettimeout.side_effect = lambda: timeout_val[0]

        has_sent = [False]

        def fake_recv(n: int) -> bytes:
            if timeout_val[0] == 0.0:
                raise BlockingIOError
            if not has_sent[0]:
                has_sent[0] = True
                return response_bytes
            raise BlockingIOError

        sock.recv.side_effect = fake_recv
        rot._sock = sock
        return rot

    def test_parses_bare_float_position(self) -> None:
        """Pyrodrifter and standard Hamlib rotctld return bare float lines."""
        rot = self._create_mock_rotator(b"214.500015\n10.293750\n")
        pos = rot._get_position()
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos.azimuth, 214.500015, places=4)
        self.assertAlmostEqual(pos.elevation, 10.29375, places=4)

    def test_parses_tagged_position(self) -> None:
        rot = self._create_mock_rotator(b"Az: 180.5\nEl: 45.2\n")
        pos = rot._get_position()
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos.azimuth, 180.5)
        self.assertAlmostEqual(pos.elevation, 45.2)

    def test_parses_hamlib_extended_position(self) -> None:
        rot = self._create_mock_rotator(b"get_pos:\nAzimuth: 123.400000\nElevation: 30.100000\nRPRT 0\n")
        pos = rot._get_position()
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos.azimuth, 123.4)
        self.assertAlmostEqual(pos.elevation, 30.1)

    def test_error_rprt_returns_none(self) -> None:
        rot = self._create_mock_rotator(b"RPRT -1\n")
        pos = rot._get_position()
        self.assertIsNone(pos)
        self.assertTrue(rot._poll_responded)

    def test_parses_semicolon_extended_position(self) -> None:
        rot = self._create_mock_rotator(
            b"get_pos:;Azimuth: 123.400000;Elevation: 30.100000;RPRT 0\n")
        self.assertEqual(rot._get_position(), RotatorPosition(123.4, 30.1))
        self.assertTrue(rot._poll_responded)

    def test_missing_coordinates_report_error_without_transport_failure(self) -> None:
        rot = self._create_mock_rotator(b"get_pos:;Azimuth: ;Elevation: ;RPRT 0\n")
        rot.on_error = MagicMock()
        self.assertIsNone(rot._get_position())
        self.assertTrue(rot._poll_responded)
        rot.on_error.assert_called_once_with(
            "rotctld returned invalid position: azimuth='', elevation=''")

    def test_incomplete_reply_is_transport_failure(self) -> None:
        rot = self._create_mock_rotator(b"90.0\n")
        rot.on_error = MagicMock()
        self.assertIsNone(rot._get_position())
        self.assertFalse(rot._poll_responded)
        rot.on_error.assert_called_once()

    def test_nonfinite_coordinates_are_not_published(self) -> None:
        rot = self._create_mock_rotator(b"nan\ninf\n")
        self.assertIsNone(rot._get_position())
        self.assertTrue(rot._poll_responded)

    def test_three_transport_failures_still_disconnect(self) -> None:
        rot = HamlibRotator(poll_interval_s=0)
        rot._sock = MagicMock()
        rot.connected = True
        disconnect = rot._disconnect

        def stop_after_disconnect():
            disconnect()
            rot._stop.set()

        with patch.object(rot, "_connect", return_value=True), \
                patch.object(rot, "_get_position", return_value=None) as poll, \
                patch.object(rot, "_disconnect", side_effect=stop_after_disconnect):
            rot._run()
        self.assertEqual(poll.call_count, 3)
        self.assertFalse(rot.connected)

    def test_set_position_preserves_overtravel_and_normalizes_out_of_range_values(self) -> None:
        for az, el, expected_az, expected_el in (
            (0.0, 0.0, 0.0, 0.0),
            (360.0, 90.0, 360.0, 90.0),
            (450.0, 95.0, 450.0, 90.0),
            (-10.0, -5.0, 350.0, 0.0),
            (460.0, 30.0, 100.0, 30.0),
        ):
            with self.subTest(az=az, el=el):
                rot = HamlibRotator()
                with patch.object(rot, "_send_command") as send:
                    rot.set_position(az, el)
                send.assert_called_once_with(f"P {expected_az:.1f} {expected_el:.1f}\n")
                self.assertEqual(rot._target, RotatorPosition(expected_az, expected_el))

    def test_stop_during_connect_closes_late_socket_without_publishing_connection(self) -> None:
        connect_started = threading.Event()
        release_connect = threading.Event()
        sock = MagicMock()
        sock.connect.side_effect = lambda _address: (connect_started.set(), release_connect.wait(2.0))
        connected_events: list[bool] = []
        rot = HamlibRotator(on_connected=lambda: connected_events.append(True))

        with patch("airwsjtx.hamlib_rotator.socket.socket", return_value=sock):
            rot.start()
            self.assertTrue(connect_started.wait(1.0))
            rot.stop()
            release_connect.set()
            self.assertIsNotNone(rot._thread)
            rot._thread.join(timeout=1.0)

        self.assertFalse(rot._thread.is_alive())
        self.assertFalse(rot.connected)
        self.assertIsNone(rot._sock)
        self.assertEqual(connected_events, [])
        sock.close.assert_called_once()

    def test_live_mock_server_polling_and_commands(self) -> None:
        """Run a mock TCP server speaking the rotctld protocol to test HamlibRotator."""
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.bind(("127.0.0.1", 0))
        server_sock.listen(1)
        port = server_sock.getsockname()[1]

        stop_server = threading.Event()
        commands_received: list[str] = []

        def mock_server() -> None:
            server_sock.settimeout(2.0)
            try:
                conn, _ = server_sock.accept()
                conn.settimeout(1.0)
            except Exception:
                return

            buf = b""
            while not stop_server.is_set():
                try:
                    chunk = conn.recv(128)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line_bytes, buf = buf.split(b"\n", 1)
                        cmd = line_bytes.decode("ascii", "ignore").strip()
                        if not cmd:
                            continue
                        commands_received.append(cmd)
                        if cmd == "p":
                            # Pyrodrifter format
                            conn.sendall(b"180.500000\n45.200000\n")
                        elif cmd.startswith("P"):
                            conn.sendall(b"RPRT 0\n")
                        elif cmd == "S":
                            conn.sendall(b"RPRT 0\n")
                        else:
                            conn.sendall(b"RPRT 0\n")
                except socket.timeout:
                    continue
                except Exception:
                    break
            conn.close()

        server_thread = threading.Thread(target=mock_server, daemon=True)
        server_thread.start()

        positions: list[RotatorPosition] = []
        connected_events = []
        disconnected_events = []

        rot = HamlibRotator(
            host="127.0.0.1",
            port=port,
            poll_interval_s=0.1,
            on_position=lambda p: positions.append(p),
            on_connected=lambda: connected_events.append(True),
            on_disconnected=lambda: disconnected_events.append(True),
        )

        try:
            rot.start()
            # Wait for at least 2 position updates
            t0 = time.time()
            while len(positions) < 2 and time.time() - t0 < 3.0:
                time.sleep(0.05)

            self.assertTrue(rot.connected)
            self.assertGreaterEqual(len(positions), 2)
            self.assertAlmostEqual(positions[0].azimuth, 180.5)
            self.assertAlmostEqual(positions[0].elevation, 45.2)

            # Test set position
            rot.set_position(90.0, 15.0)
            time.sleep(0.15)
            self.assertIn("P 90.0 15.0", commands_received)

            # Test stop
            rot.stop_rotation()
            time.sleep(0.15)
            self.assertIn("S", commands_received)

        finally:
            rot.stop()
            stop_server.set()
            server_sock.close()
            server_thread.join(timeout=1.0)

        self.assertFalse(rot.connected)
        self.assertGreaterEqual(len(connected_events), 1)
        self.assertGreaterEqual(len(disconnected_events), 1)


if __name__ == "__main__":
    unittest.main()
