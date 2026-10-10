from __future__ import annotations

import struct
import socket
import threading
import time
import unittest
from unittest.mock import patch

from airwsjtx.wsjtx_udp import MSG_HEARTBEAT, MSG_STATUS, WSJTX_MAGIC, WSJTXListener, WSJTXStatus, parse_status


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


if __name__ == "__main__":
    unittest.main()
