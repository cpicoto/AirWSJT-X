from __future__ import annotations

import socket
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from airwsjtx.wsjtx_udp import (
    MSG_DECODE, MSG_HEARTBEAT, MSG_STATUS, WSJTX_MAGIC,
    WSJTXListener, WSJTXStatus, parse_status,
)


def _enc_str(value: str | None) -> bytes:
    if value is None:
        return struct.pack(">I", 0xFFFFFFFF)
    b = value.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _build_status_packet(mode: str | None, tr_period: int) -> bytes:
    return b"".join(
        [
            struct.pack(">I", WSJTX_MAGIC),  # magic
            struct.pack(">I", 2),  # schema
            struct.pack(">I", MSG_STATUS),  # message type
            _enc_str("WSJT-X"),
            struct.pack(">Q", 18100000),  # frequency Hz
            _enc_str(mode),
            _enc_str(None),  # dx_call
            _enc_str(None),  # report
            _enc_str(None),  # tx_mode
            struct.pack(">B", 0),  # tx_enabled
            struct.pack(">B", 0),  # transmitting
            struct.pack(">B", 0),  # decoding
            struct.pack(">I", 2303),  # rx_df
            struct.pack(">I", 1600),  # tx_df
            _enc_str("K7MDL"),  # de_call
            _enc_str("CN87XS"),  # de_grid
            _enc_str(None),  # dx_grid
            struct.pack(">B", 0),  # tx_watchdog
            _enc_str(None),  # sub_mode
            struct.pack(">B", 0),  # fast_mode
            struct.pack(">B", 0),  # special_op_mode
            struct.pack(">I", 50),  # freq_tolerance
            struct.pack(">i", tr_period),  # tr_period (signed i32)
            _enc_str(None),  # config_name
        ]
    )


def _make_status(app_id: str, config_name: str | None = None) -> WSJTXStatus:
    return WSJTXStatus(
        app_id=app_id,
        freq_hz=1296150000,
        mode="Q65",
        dx_call=None,
        report=None,
        tx_mode=None,
        tx_enabled=False,
        transmitting=False,
        decoding=True,
        rx_df=0,
        tx_df=0,
        de_call=None,
        de_grid=None,
        dx_grid=None,
        tx_watchdog=False,
        sub_mode=None,
        fast_mode=False,
        special_op_mode=0,
        freq_tolerance=0,
        tr_period=15,
        config_name=config_name,
    )


class WSJTXStatusParsingTests(unittest.TestCase):
    def test_status_maps_fixed_modes_unset_period_to_default(self) -> None:
        for unset in (-1, 0):
            for mode, expected in (("FT8", 15.0), ("FT4", 7.5), ("FT2", 2.5), ("JT65", 60.0)):
                with self.subTest(mode=mode, unset=unset):
                    st = parse_status(_build_status_packet(mode, unset))
                    self.assertIsNotNone(st)
                    assert st is not None
                    self.assertEqual(st.tr_period, expected)

    def test_status_uses_wire_period_when_positive(self) -> None:
        st = parse_status(_build_status_packet("Q65", 30))
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st.tr_period, 30.0)

    def test_status_uses_none_for_unmapped_mode_with_unset_period(self) -> None:
        for unset in (-1, 0):
            with self.subTest(unset=unset):
                st = parse_status(_build_status_packet("Q65", unset))
                self.assertIsNotNone(st)
                assert st is not None
                self.assertIsNone(st.tr_period)

    def test_status_uses_none_for_missing_mode_with_negative_period(self) -> None:
        st = parse_status(_build_status_packet(None, -1))
        self.assertIsNotNone(st)
        assert st is not None
        self.assertIsNone(st.tr_period)


class WSJTXListenerFilterTests(unittest.TestCase):
    def test_without_filter_accepts_any_source(self) -> None:
        listener = WSJTXListener()
        self.assertTrue(listener._matches_source("Rig-A"))
        self.assertTrue(listener._matches_source("Rig-B"))

    def test_filter_matches_app_id_case_insensitive(self) -> None:
        listener = WSJTXListener(rig_name_filter="my-rig")
        self.assertTrue(listener._matches_source("MY-RIG"))
        self.assertFalse(listener._matches_source("other-rig"))

    def test_filter_matches_status_config_name(self) -> None:
        listener = WSJTXListener(rig_name_filter="home-shack")
        self.assertTrue(listener._matches_source("WSJT-X", _make_status("WSJT-X", config_name="Home-Shack")))
        self.assertFalse(listener._matches_source("WSJT-X", _make_status("WSJT-X", config_name="Portable")))

    def test_listener_sticks_to_active_app_id(self) -> None:
        listener = WSJTXListener()
        listener._mark_connected("WSJT-X-A")
        self.assertTrue(listener._matches_source("WSJT-X-A"))
        self.assertFalse(listener._matches_source("WSJT-X-B"))

    def test_set_filter_disconnects_and_clears_active_source(self) -> None:
        disconnected: list[bool] = []
        listener = WSJTXListener(on_disconnected=lambda: disconnected.append(True))
        listener._mark_connected("WSJT-X-A")
        listener.set_rig_name_filter("Rig-B")
        self.assertEqual(listener.get_rig_name_filter(), "Rig-B")
        self.assertEqual(disconnected, [True])
        self.assertTrue(listener._matches_source("Rig-B"))


class _FakeSocket:
    def __init__(self, packets: list[bytes], fail_first_bind: bool = False) -> None:
        self._packets = packets[:]
        self._fail_first_bind = fail_first_bind
        self._bind_calls = 0
        self.sent_to: list[tuple[bytes, tuple[str, int]]] = []

    def setsockopt(self, *_args) -> None:
        return

    def settimeout(self, *_args) -> None:
        return

    def bind(self, addr: tuple[str, int]) -> None:
        self._bind_calls += 1
        if self._fail_first_bind and self._bind_calls == 1 and addr[0] == "":
            raise OSError("address already in use")

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        self.sent_to.append((data, addr))
        return len(data)

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if self._packets:
            return self._packets.pop(0), ("127.0.0.1", 2237)
        time.sleep(0.01)
        raise socket.timeout()

    def close(self) -> None:
        return


class WSJTXListenerLoopbackTests(unittest.TestCase):
    def test_listener_survives_malformed_packet_and_processes_next_status(self) -> None:
        main_socket = _FakeSocket([b"\x00\x01", _build_status_packet("Q65", 15)])
        query_socket = _FakeSocket([])
        connected_event = threading.Event()
        connected_app_ids: list[str] = []

        listener = WSJTXListener(
            on_connected=lambda app_id: (connected_app_ids.append(app_id), connected_event.set()),
            on_status=lambda _st: None,
            socket_timeout_s=0.05,
        )

        with patch("airwsjtx.wsjtx_udp.socket.socket", side_effect=[main_socket, query_socket]):
            listener.start()
            self.assertTrue(connected_event.wait(1.5))
            listener.stop()
            time.sleep(0.05)

        self.assertIn("WSJT-X", connected_app_ids)

    def test_listener_falls_back_to_active_query_mode_when_port_busy(self) -> None:
        # First packet is our own heartbeat echo and must be ignored; next is real WSJT-X status.
        heartbeat_echo = b"".join(
            [
                struct.pack(">I", WSJTX_MAGIC),
                struct.pack(">I", 2),
                struct.pack(">I", MSG_HEARTBEAT),
                _enc_str("AirWSJTX"),
                struct.pack(">I", 2),
                _enc_str("2.0"),
                _enc_str(""),
            ]
        )
        main_socket = _FakeSocket([heartbeat_echo, _build_status_packet("Q65", 15)], fail_first_bind=True)
        connected_event = threading.Event()
        connected_app_ids: list[str] = []

        listener = WSJTXListener(
            on_connected=lambda app_id: (connected_app_ids.append(app_id), connected_event.set()),
            on_status=lambda _st: None,
            socket_timeout_s=0.05,
        )

        with patch("airwsjtx.wsjtx_udp.socket.socket", return_value=main_socket):
            listener.start()
            self.assertTrue(connected_event.wait(1.5))
            listener.stop()
            time.sleep(0.05)

        self.assertTrue(listener._active_query_mode)
        self.assertIn("WSJT-X", connected_app_ids)
        self.assertTrue(main_socket.sent_to)
        self.assertTrue(all(dest == ("127.0.0.1", 2237) for _, dest in main_socket.sent_to))


class WSJTXListenerSocketTests(unittest.TestCase):
    def test_manual_station_override_survives_wsjtx_status_and_can_be_cleared(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        with patch.multiple(bridge, _my_call_override=None, _my_grid_override=None,
                            _station_a_call=None, _station_a_locator=None,
                            _wsjtx_de_call=None, _wsjtx_de_grid=None,
                            _wsjtx_freq_hz=None, _wsjtx_band_mhz=None):
            with patch.object(bridge, "_start_configured_airscout_feed"), patch.object(bridge, "_emit"):
                bridge.handle({"id": "my", "method": "set_my_station_override",
                               "params": {"callsign": "K7MDL", "grid": "CN87VP"}})
                status = _make_status("WSJT-X")
                status.de_call, status.de_grid = "W1AW", "FN31PR"
                bridge._on_wsjtx_status(status)
                self.assertEqual(bridge._station_a_call, "K7MDL")
                self.assertEqual(bridge._station_a_locator, "CN87VP")
                bridge.handle({"id": "reset", "method": "set_my_station_override", "params": {}})
                self.assertEqual(bridge._station_a_call, "W1AW")
                self.assertEqual(bridge._station_a_locator, "FN31PR")

    def test_discovers_filtered_out_rigs_from_udp_messages(self) -> None:
        on_status = Mock()
        listener = WSJTXListener(rig_name_filter="Selected-Rig", on_status=on_status)
        heartbeat = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_HEARTBEAT)
            + _enc_str("Heartbeat-Rig") + struct.pack(">I", 3)
            + _enc_str("2.7") + _enc_str("release")
        )
        decode = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_DECODE)
            + _enc_str("Decode-Rig") + struct.pack(">BIidI", 1, 0, -10, 0.2, 1000)
            + _enc_str("FT8") + _enc_str("CQ K7MDL CN87") + b"\x00\x00"
        )
        status = _build_status_packet("Q65", 15)
        packets = iter([heartbeat, decode, status, heartbeat])

        def receive(_: int) -> tuple[bytes, tuple[str, int]]:
            packet = next(packets, None)
            if packet is None:
                listener.stop()
                raise socket.timeout()
            return packet, ("127.0.0.1", 12345)

        sock = Mock()
        sock.recvfrom.side_effect = receive
        with patch.object(listener, "_make_socket", return_value=sock):
            listener._run()
        self.assertEqual(listener.get_known_rigs(), [
            {"app_id": "Decode-Rig", "config_name": None},
            {"app_id": "Heartbeat-Rig", "config_name": None},
            {"app_id": "WSJT-X", "config_name": None},
        ])
        self.assertFalse(listener._connected)
        on_status.assert_not_called()

    def test_bridge_status_includes_known_rigs(self) -> None:
        from sidecar import airwsjtx_bridge

        listener = WSJTXListener()
        listener._remember_rig("WSJT-X-A", "Home")
        with patch.object(airwsjtx_bridge, "_wsjtx_listener", listener):
            with patch.object(airwsjtx_bridge, "_ok") as reply:
                airwsjtx_bridge.handle({"id": "rigs", "method": "get_status", "params": {}})
        self.assertEqual(reply.call_args.args[1]["wsjtx_known_rigs"], [
            {"app_id": "WSJT-X-A", "config_name": "Home"},
        ])

    def test_known_rigs_preserve_names_and_return_independent_snapshots(self) -> None:
        listener = WSJTXListener()
        listener._remember_rig("Rig-A", "Home")
        listener._remember_rig("Rig-A")
        listener._remember_rig("")
        rigs = listener.get_known_rigs()
        self.assertEqual(rigs, [{"app_id": "Rig-A", "config_name": "Home"}])
        rigs[0]["config_name"] = "Modified"
        listener.set_rig_name_filter("Rig-B")
        self.assertEqual(listener.get_known_rigs(), [
            {"app_id": "Rig-A", "config_name": "Home"},
        ])
        listener._remember_rig("Rig-A", "Portable")
        self.assertEqual(listener.get_known_rigs()[0]["config_name"], "Portable")

    def test_socket_setup_without_reuseport(self) -> None:
        sock = Mock()
        windows_socket = SimpleNamespace(
            **{name: getattr(socket, name) for name in (
                "AF_INET", "SOCK_DGRAM", "IPPROTO_UDP", "SOL_SOCKET",
                "SO_REUSEADDR", "IPPROTO_IP", "IP_ADD_MEMBERSHIP", "inet_aton",
            )},
            socket=Mock(return_value=sock),
        )
        with patch("airwsjtx.wsjtx_udp.socket", windows_socket):
            self.assertIs(WSJTXListener()._make_socket(), sock)
        sock.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt.assert_any_call(
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
            b"\xe0\x00\x00\x01\x00\x00\x00\x00",
        )
        sock.bind.assert_called_once_with(("", 2237))
        sock.close.assert_not_called()

    def test_socket_setup_failure_closes_socket(self) -> None:
        sock = Mock()
        sock.bind.side_effect = OSError("port in use")
        with patch("airwsjtx.wsjtx_udp.socket.socket", return_value=sock):
            with self.assertRaisesRegex(OSError, "port in use"):
                WSJTXListener()._make_socket()
        sock.close.assert_called_once()

    def test_startup_failure_reports_error(self) -> None:
        errors: list[str] = []
        disconnected = Mock()
        listener = WSJTXListener(on_error=errors.append, on_disconnected=disconnected)
        with patch.object(listener, "_make_socket", side_effect=OSError("port in use")):
            with self.assertLogs("airwsjtx.wsjtx_udp", level="ERROR"):
                listener._run()
        self.assertEqual(len(errors), 1)
        self.assertIn("2237", errors[0])
        self.assertIn("port in use", errors[0])
        disconnected.assert_called_once()

    def test_receives_status_over_real_udp_socket(self) -> None:
        connected = threading.Event()
        received = threading.Event()
        statuses: list[WSJTXStatus] = []

        def on_status(status: WSJTXStatus) -> None:
            statuses.append(status)
            received.set()

        listener = WSJTXListener(
            port=0, socket_timeout_s=0.05,
            on_connected=lambda _: connected.set(),
            on_status=on_status,
        )
        sock = listener._make_socket()
        port = sock.getsockname()[1]
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with patch.object(listener, "_make_socket", return_value=sock):
                listener.start()
                sender.sendto(_build_status_packet("Q65", 15), ("127.0.0.1", port))
                self.assertTrue(received.wait(2), "Listener did not receive WSJT-X status")
                self.assertTrue(connected.is_set())
                self.assertEqual(statuses[0].de_call, "K7MDL")
                self.assertEqual(statuses[0].tr_period, 15.0)
        finally:
            listener.stop()
            if listener._thread:
                listener._thread.join(2)
            sender.close()
            sock.close()


if __name__ == "__main__":
    unittest.main()