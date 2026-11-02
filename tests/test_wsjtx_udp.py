from __future__ import annotations

import socket
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from airwsjtx.wsjtx_udp import (
    MSG_CONFIGURE, MSG_DECODE, MSG_HALT_TX, MSG_HEARTBEAT, MSG_QSO_LOGGED, MSG_REPLAY, MSG_REPLY,
    MSG_START_DX_TX, MSG_START_DX_TX_RESULT, MSG_STATUS, WSJTX_MAGIC, WSJTXDecode, WSJTXListener,
    WSJTXQsoLogged, WSJTXStartDxTxResult, WSJTXStatus, parse_decode, parse_qso_logged,
    parse_start_dx_tx_result, parse_status,
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


def _build_decode_packet(time_ms: int, mode: str = "FT8", message: str = "CQ K7MDL CN87") -> bytes:
    return b"".join(
        [
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_DECODE),
            _enc_str("WSJT-X"),
            struct.pack(">BIidI", 1, time_ms, -10, 0.2, 1000),
            _enc_str(mode),
            _enc_str(message),
            b"\x00\x00",
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
    def test_status_parses_optional_last_tx_message(self) -> None:
        st = parse_status(_build_status_packet("Q65", 15) + _enc_str("CQ K7MDL CN87"))
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st.tx_message, "CQ K7MDL CN87")
        older = parse_status(_build_status_packet("Q65", 15))
        self.assertIsNotNone(older)
        assert older is not None
        self.assertIsNone(older.tx_message)

    def test_status_rejects_truncated_optional_tx_message(self) -> None:
        packet = _build_status_packet("Q65", 15) + struct.pack(">I", 12) + b"CQ"
        self.assertIsNone(parse_status(packet))

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


class WSJTXDecodeTimestampTests(unittest.TestCase):
    def test_parser_and_bridge_preserve_wire_time_ms(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        for time_ms in (0, 1155000, 86399999, 0xFFFFFFFF):
            with self.subTest(time_ms=time_ms):
                decode = parse_decode(_build_decode_packet(time_ms))
                self.assertEqual(
                    decode,
                    WSJTXDecode(
                        app_id="WSJT-X",
                        is_new=True,
                        time_ms=time_ms,
                        snr=-10,
                        delta_time_s=0.2,
                        delta_freq_hz=1000,
                        mode="FT8",
                        message="CQ K7MDL CN87",
                        low_confidence=False,
                        off_air=False,
                    ),
                )
                with patch.object(bridge, "_emit") as emit:
                    bridge._on_wsjtx_decode(decode)
                self.assertEqual(emit.call_args.args[0]["data"]["time_ms"], time_ms)


class WSJTXQsoLoggedTests(unittest.TestCase):
    @staticmethod
    def _packet(app_id: str = "WSJT-X", time_spec: int = 1) -> bytes:
        timestamp = struct.pack(">QIB", 2461332, 36000000, time_spec)
        if time_spec == 2:
            timestamp += struct.pack(">i", -25200)
        return b"".join([
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_QSO_LOGGED),
            _enc_str(app_id),
            timestamp,
            _enc_str("W7PUA"),
            _enc_str("CN84"),
            struct.pack(">Q", 1296150000),
            _enc_str("Q65"),
            _enc_str("-10"),
            _enc_str("-12"),
            _enc_str(None) * 3,
            timestamp,
            _enc_str(None) * 6,
        ])

    def test_parses_utc_and_offset_timestamps(self) -> None:
        for time_spec in (1, 2):
            with self.subTest(time_spec=time_spec):
                self.assertEqual(
                    parse_qso_logged(self._packet(time_spec=time_spec)),
                    WSJTXQsoLogged("WSJT-X", "W7PUA", "CN84", 1296150000, "Q65", "-10", "-12"),
                )

    def test_rejects_truncated_or_wrong_message(self) -> None:
        self.assertIsNone(parse_qso_logged(self._packet()[:32]))
        self.assertIsNone(parse_qso_logged(_build_status_packet("Q65", 15)))

    def test_only_selected_rig_logs_a_qso(self) -> None:
        logged = Mock()
        listener = WSJTXListener(rig_name_filter="Rig-A", on_qso_logged=logged)
        self.assertFalse(listener._handle_datagram(self._packet("WSJT-X - Rig-B")))
        logged.assert_not_called()
        self.assertTrue(listener._handle_datagram(self._packet("WSJT-X - Rig-A")))
        logged.assert_called_once_with(parse_qso_logged(self._packet("WSJT-X - Rig-A")))


class WSJTXStartDxTxResultTests(unittest.TestCase):
    @staticmethod
    def _packet(
        *, app_id: str = "WSJT-X", request_id: int = 42, accepted: int = 1,
        reason: str | None = "", tx_message: str | None = "W7PUA K7MDL CN87",
    ) -> bytes:
        return b"".join([
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_START_DX_TX_RESULT),
            _enc_str(app_id),
            struct.pack(">IB", request_id, accepted),
            _enc_str(reason),
            _enc_str(tx_message),
        ])

    def test_parses_accepted_and_rejected_results(self) -> None:
        self.assertEqual(
            parse_start_dx_tx_result(self._packet()),
            WSJTXStartDxTxResult("WSJT-X", 42, True, "", "W7PUA K7MDL CN87"),
        )
        self.assertEqual(
            parse_start_dx_tx_result(self._packet(
                accepted=0, reason="dx_call_mismatch", tx_message="",
            )),
            WSJTXStartDxTxResult("WSJT-X", 42, False, "dx_call_mismatch", ""),
        )

    def test_parses_qt_null_empty_result_fields(self) -> None:
        self.assertEqual(
            parse_start_dx_tx_result(self._packet(reason=None)),
            WSJTXStartDxTxResult("WSJT-X", 42, True, "", "W7PUA K7MDL CN87"),
        )
        self.assertEqual(
            parse_start_dx_tx_result(self._packet(
                accepted=0, reason="tx1_unavailable", tx_message=None,
            )),
            WSJTXStartDxTxResult("WSJT-X", 42, False, "tx1_unavailable", ""),
        )

    def test_rejects_malformed_or_contradictory_results(self) -> None:
        packets = (
            self._packet()[:-1],
            self._packet(accepted=2),
            self._packet(accepted=1, tx_message=""),
            self._packet(accepted=1, reason="rejected"),
            self._packet(accepted=0, reason="", tx_message=""),
            self._packet(accepted=0, reason="rejected", tx_message="CQ"),
            self._packet(accepted=1, tx_message=None),
            self._packet(accepted=0, reason=None, tx_message=None),
        )
        for packet in packets:
            with self.subTest(packet=packet):
                self.assertIsNone(parse_start_dx_tx_result(packet))

    def test_dispatches_the_result_with_its_source_address(self) -> None:
        callback = Mock()
        listener = WSJTXListener(on_start_dx_tx_result=callback)
        listener._active_app_id = "WSJT-X"
        source = ("127.0.0.1", 50123)
        self.assertTrue(listener._handle_datagram(self._packet(), source))
        callback.assert_called_once_with(WSJTXStartDxTxResult(
            "WSJT-X", 42, True, "", "W7PUA K7MDL CN87", source,
        ))
        self.assertFalse(listener._handle_datagram(self._packet(app_id="WSJT-X - Other"), source))
        callback.assert_called_once()


class WSJTXListenerFilterTests(unittest.TestCase):
    def test_without_filter_accepts_any_source(self) -> None:
        listener = WSJTXListener()
        self.assertTrue(listener._matches_source("WSJT-X - Rig-A"))
        self.assertTrue(listener._matches_source("JTDX - Rig-B"))
        self.assertFalse(listener._matches_source("ACLog"))
        self.assertFalse(listener._matches_source("JTAlert"))

    def test_filter_matches_app_id_case_insensitive(self) -> None:
        listener = WSJTXListener(rig_name_filter="my-rig")
        self.assertTrue(listener._matches_source("WSJT-X - MY-RIG"))
        self.assertTrue(listener._matches_source("WSJT-X-MY-RIG"))
        self.assertFalse(listener._matches_source("WSJT-X - other-rig"))
        self.assertFalse(listener._matches_source("ACLog"))

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
        self.assertTrue(listener._matches_source("WSJT-X - Rig-B"))

    def test_ignores_non_wsjtx_apps_like_aclog(self) -> None:
        on_status = Mock()
        on_decode = Mock()
        connected: list[str] = []
        listener = WSJTXListener(
            on_status=on_status,
            on_decode=on_decode,
            on_connected=lambda app: connected.append(app),
        )
        aclog_hb = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_HEARTBEAT)
            + _enc_str("ACLog") + struct.pack(">I", 3)
            + _enc_str("7.2") + _enc_str("release")
        )
        self.assertFalse(listener._handle_datagram(aclog_hb))
        self.assertEqual(listener.get_known_rigs(), [])
        self.assertFalse(listener._connected)
        self.assertEqual(connected, [])

        for companion in ("JTAlert", "GridTracker", "Log4OM", "N1MM"):
            pkt = (
                struct.pack(">III", WSJTX_MAGIC, 2, MSG_HEARTBEAT)
                + _enc_str(companion) + struct.pack(">I", 3)
                + _enc_str("1.0") + _enc_str("release")
            )
            self.assertFalse(listener._handle_datagram(pkt))
            self.assertEqual(listener.get_known_rigs(), [])

        wsjtx_hb = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_HEARTBEAT)
            + _enc_str("WSJT-X") + struct.pack(">I", 3)
            + _enc_str("2.7.0") + _enc_str("release")
        )
        self.assertTrue(listener._handle_datagram(wsjtx_hb))
        self.assertEqual(listener.get_known_rigs(), [{"app_id": "WSJT-X", "config_name": None}])
        self.assertTrue(listener._connected)
        self.assertEqual(connected, ["WSJT-X"])


def _read_str(data: bytes, offset: int) -> tuple[str | None, int]:
    length = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    if length == 0xFFFFFFFF:
        return None, offset
    return data[offset:offset + length].decode(), offset + length


class WSJTXControlMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.listener = WSJTXListener()
        self.socket = Mock()
        self.listener._query_sock = self.socket
        self.listener._active_app_id = "WSJT-X"
        destination = ("127.0.0.1", 2240)
        self.listener._last_active_addr = destination
        self.listener._client_addrs = {"WSJT-X": {destination: time.monotonic()}}

    def test_configure_sends_q65_dx_and_standard_message_generation(self) -> None:
        self.assertTrue(self.listener.send_configure(dx_call="W7PUA", dx_grid="CN84"))
        packet = self.socket.sendto.call_args.args[0]
        self.assertEqual(struct.unpack_from(">III", packet), (WSJTX_MAGIC, 2, MSG_CONFIGURE))
        offset = 12
        app_id, offset = _read_str(packet, offset)
        mode, offset = _read_str(packet, offset)
        self.assertEqual((app_id, mode), ("WSJT-X", "Q65"))
        self.assertEqual(struct.unpack_from(">I", packet, offset)[0], 0xFFFFFFFF)
        offset += 4
        submode, offset = _read_str(packet, offset)
        self.assertEqual(submode, "")
        self.assertEqual(packet[offset], 0)
        offset += 1
        self.assertEqual(struct.unpack_from(">I", packet, offset)[0], 15)
        offset += 4 + 4
        dx_call, offset = _read_str(packet, offset)
        dx_grid, offset = _read_str(packet, offset)
        self.assertEqual((dx_call, dx_grid, packet[offset]), ("W7PUA", "CN84", 1))

    def test_reply_uses_the_matching_decode_fields(self) -> None:
        decode = WSJTXDecode(
            app_id="WSJT-X",
            is_new=True,
            time_ms=1234,
            snr=-12,
            delta_time_s=0.25,
            delta_freq_hz=1500,
            mode=":",
            message="CQ W7PUA CN84",
            low_confidence=False,
            off_air=False,
            source_addr=("127.0.0.1", 2240),
        )
        self.assertTrue(self.listener.send_reply(decode))
        packet = self.socket.sendto.call_args.args[0]
        self.assertEqual(struct.unpack_from(">III", packet), (WSJTX_MAGIC, 2, MSG_REPLY))
        offset = 12
        _, offset = _read_str(packet, offset)
        self.assertEqual(struct.unpack_from(">IidI", packet, offset), (1234, -12, 0.25, 1500))
        offset += 20
        mode, offset = _read_str(packet, offset)
        message, offset = _read_str(packet, offset)
        self.assertEqual((mode, message, packet[offset], packet[offset + 1]), (":", "CQ W7PUA CN84", 0, 0))

    def test_wire_q65_decode_reaches_bridge_without_sending_reply(self) -> None:
        from dataclasses import replace

        from airwsjtx.wsjtx_qso import WSJTXQsoAssistant
        from sidecar import airwsjtx_bridge as bridge

        assistant = WSJTXQsoAssistant()
        assistant.arm(
            dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X",
            source_addr=("127.0.0.1", 2240),
        )
        assistant.configuration_requested(0.0)
        assistant.observe_status(replace(
            _make_status("WSJT-X"), dx_call="W7PUA", dx_grid="CN84", de_call="K7MDL",
            source_addr=("127.0.0.1", 2240),
        ))
        assistant.mark_start_requested(42)
        assistant.observe_start_result(WSJTXStartDxTxResult(
            "WSJT-X", 42, True, "", "W7PUA K7MDL CN87", ("127.0.0.1", 2240),
        ))
        packet = _build_decode_packet(1234, ":", "K7MDL W7PUA -10")
        self.listener.on_decode = bridge._on_wsjtx_decode
        with (
            patch.object(bridge, "_qso_assistant", assistant),
            patch.object(bridge, "_wsjtx_listener", self.listener),
            patch.object(bridge, "_qso_tracking_viable_locked", return_value=True),
            patch.object(bridge, "_emit"),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.assertTrue(self.listener._handle_datagram(packet, ("127.0.0.1", 2240)))

        self.socket.sendto.assert_not_called()
        self.assertEqual(assistant.snapshot().stage, "exchange")



class _FakeSocket:
    def __init__(self, packets: list[bytes]) -> None:
        self._packets = packets[:]
        self.sent_to: list[tuple[bytes, tuple[str, int]]] = []

    def setsockopt(self, *_args) -> None:
        return

    def settimeout(self, *_args) -> None:
        return

    def bind(self, _addr: tuple[str, int]) -> None:
        return

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

class WSJTXListenerSocketTests(unittest.TestCase):
    def _udp_receiver(self, host: str = "127.0.0.1") -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(sock.close)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((host, 0))
        sock.settimeout(2)
        return sock

    def _start_listener(self, listener: WSJTXListener) -> None:
        def cleanup() -> None:
            listener.stop()
            if listener._thread:
                listener._thread.join(2)
                self.assertFalse(listener._thread.is_alive(), "UDP listener did not stop")

        self.addCleanup(cleanup)
        listener.start()

    def test_exclusive_port_reports_error_without_receiving_another_apps_traffic(self) -> None:
        owner = self._udp_receiver("0.0.0.0")
        port = owner.getsockname()[1]
        sender = self._udp_receiver()
        error = threading.Event()
        connected = Mock()
        status = Mock()
        listener = WSJTXListener(
            port=port, socket_timeout_s=0.05, on_error=lambda _: error.set(),
            on_connected=connected, on_status=status,
        )
        with self.assertLogs("airwsjtx.wsjtx_udp", level="ERROR"):
            self._start_listener(listener)
            self.assertTrue(error.wait(2), "Exclusive UDP port conflict was silently ignored")
        packet = _build_status_packet("FT8", 15)
        sender.sendto(packet, ("127.0.0.1", port))
        self.assertEqual(owner.recvfrom(4096)[0], packet)
        self.assertIn(str(port), listener.get_error() or "")
        self.assertIn("Log4OM", listener.get_error() or "")
        self.assertIsNone(listener._sock)
        self.assertFalse(listener._connected)
        connected.assert_not_called()
        status.assert_not_called()

    def test_manual_station_override_survives_wsjtx_status_and_can_be_cleared(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        with patch.multiple(bridge, _my_call_override=None, _my_grid_override=None,
                            _station_a_call=None, _station_a_locator=None,
                            _wsjtx_de_call=None, _wsjtx_de_grid=None,
                            _wsjtx_freq_hz=None, _wsjtx_band_mhz=None):
            with patch.object(bridge, "_start_configured_airscout_feed"), \
                    patch.object(bridge, "_emit_aircraft_update"), patch.object(bridge, "_emit"):
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

    def test_my_station_altitude_is_stored_and_validated(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        with patch.multiple(bridge, _my_call_override=None, _my_grid_override=None,
                            _station_a_call=None, _station_a_locator=None,
                            _wsjtx_de_call=None, _wsjtx_de_grid=None, _station_a_alt_m=0.0):
            with patch.object(bridge, "_start_configured_airscout_feed"), \
                    patch.object(bridge, "_emit_aircraft_update"), patch.object(bridge, "_emit"):
                bridge.handle({"id": "a", "method": "set_my_station_override",
                               "params": {"grid": "CN87VP", "altitude_m": 1250}})
                self.assertEqual(bridge._station_a_alt_m, 1250.0)
                bridge.handle({"id": "b", "method": "set_my_station_override", "params": {}})
                self.assertEqual(bridge._station_a_alt_m, 0.0)

    def test_manual_dx_grid_survives_wsjtx_status(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        with patch.multiple(bridge, _my_call_override=None, _my_grid_override="CN87VP",
                            _station_a_locator="CN87VP", _station_b_locator=None,
                            _dx_grid_manual=False, _wsjtx_freq_hz=None, _wsjtx_band_mhz=None):
            with patch.object(bridge, "_start_configured_airscout_feed"), \
                    patch.object(bridge, "_start_opensky_feed"), \
                    patch.object(bridge, "_emit_aircraft_update") as refresh, patch.object(bridge, "_emit"):
                status = _make_status("WSJT-X")
                status.dx_grid = "JN34VJ"
                bridge._on_wsjtx_status(status)
                refresh.assert_called_once_with(try_auto_lock=False)
                self.assertEqual(bridge._station_b_locator, "JN34VJ")
                bridge.handle({"id": "dx", "method": "airscout_set_stations",
                               "params": {"locator_a": "CN87VP", "locator_b": "CN89OH"}})
                self.assertEqual(refresh.call_count, 2)
                bridge._on_wsjtx_status(status)
                self.assertEqual(refresh.call_count, 2)
                self.assertEqual(bridge._station_b_locator, "CN89OH")

    def test_dx_source_can_follow_wsjtx_again_without_changing_grid(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        with patch.multiple(bridge, _my_call_override=None, _my_grid_override="CN87VP",
                            _station_a_locator="CN87VP", _station_b_locator=None,
                            _station_a_call=None, _wsjtx_de_call=None, _wsjtx_de_grid=None,
                            _dx_grid_manual=False, _wsjtx_freq_hz=None, _wsjtx_band_mhz=None,
                            _active_source=None):
            with patch.object(bridge, "_start_configured_airscout_feed"), \
                    patch.object(bridge, "_emit_aircraft_update"), patch.object(bridge, "_emit"):
                status = _make_status("WSJT-X")
                params = {"locator_a": "CN87VP", "locator_b": "CN89OH", "dx_local": True}
                bridge.handle({"id": "local", "method": "airscout_set_stations", "params": params})
                self.assertTrue(bridge._dx_grid_manual)
                status.dx_grid = "CN90AA"
                bridge._on_wsjtx_status(status)
                self.assertEqual(bridge._station_b_locator, "CN89OH")

                params["dx_local"] = False
                bridge.handle({"id": "wsjtx", "method": "airscout_set_stations", "params": params})
                self.assertFalse(bridge._dx_grid_manual)
                bridge._on_wsjtx_status(status)
                self.assertEqual(bridge._station_b_locator, "CN90AA")
                status.dx_grid = "CN91BB"
                bridge._on_wsjtx_status(status)
                self.assertEqual(bridge._station_b_locator, "CN91BB")

                params["locator_b"] = "FN32AA"
                bridge.handle({"id": "fallback", "method": "airscout_set_stations", "params": params})
                for grid in [None, "", "INVALID", "ZZ99", "CN91B"]:
                    status.de_grid, status.dx_grid = grid, grid
                    bridge._on_wsjtx_status(status)
                    self.assertEqual(bridge._station_a_locator, "CN87VP")
                    self.assertEqual(bridge._station_b_locator, "FN32AA")

                params["locator_b"] = None
                bridge.handle({"id": "clear", "method": "airscout_set_stations", "params": params})
                self.assertIsNone(bridge._station_b_locator)
                params["dx_local"] = True
                bridge.handle({"id": "empty-local", "method": "airscout_set_stations", "params": params})
                bridge._on_wsjtx_status(status)
                self.assertTrue(bridge._dx_grid_manual)
                self.assertIsNone(bridge._station_b_locator)

    def test_discovers_filtered_out_rigs_from_udp_messages(self) -> None:
        on_status = Mock()
        listener = WSJTXListener(rig_name_filter="Selected-Rig", on_status=on_status)
        heartbeat = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_HEARTBEAT)
            + _enc_str("WSJT-X - Heartbeat-Rig") + struct.pack(">I", 3)
            + _enc_str("2.7") + _enc_str("release")
        )
        decode = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_DECODE)
            + _enc_str("WSJT-X - Decode-Rig") + struct.pack(">BIidI", 1, 0, -10, 0.2, 1000)
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
        with patch.object(listener, "_make_socket", return_value=sock), \
             patch.object(listener, "_make_query_socket", return_value=None):
            listener._run()
        self.assertEqual(listener.get_known_rigs(), [
            {"app_id": "WSJT-X", "config_name": None},
            {"app_id": "WSJT-X - Decode-Rig", "config_name": None},
            {"app_id": "WSJT-X - Heartbeat-Rig", "config_name": None},
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
        self.assertIsNone(reply.call_args.args[1]["wsjtx_error"])

    def test_bridge_status_preserves_listener_error_for_late_subscribers(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        listener = WSJTXListener(on_error=lambda _: listener.stop())
        with patch.object(listener, "_make_socket", side_effect=OSError("port in use")), \
                self.assertLogs("airwsjtx.wsjtx_udp", level="ERROR"):
            listener._run()
        with patch.object(bridge, "_wsjtx_listener", listener), patch.object(bridge, "_ok") as reply:
            bridge.handle({"id": "status", "method": "get_status", "params": {}})
        self.assertEqual(reply.call_args.args[1]["wsjtx_error"], listener.get_error())
        self.assertIn("port in use", reply.call_args.args[1]["wsjtx_error"])

    def test_known_rigs_preserve_names_and_return_independent_snapshots(self) -> None:
        listener = WSJTXListener()
        listener._remember_rig("WSJT-X - Rig-A", "Home")
        listener._remember_rig("WSJT-X - Rig-A")
        listener._remember_rig("")
        listener._remember_rig("ACLog")  # Should be ignored
        rigs = listener.get_known_rigs()
        self.assertEqual(rigs, [{"app_id": "WSJT-X - Rig-A", "config_name": "Home"}])
        rigs[0]["config_name"] = "Modified"
        listener.set_rig_name_filter("Rig-B")
        self.assertEqual(listener.get_known_rigs(), [
            {"app_id": "WSJT-X - Rig-A", "config_name": "Home"},
        ])
        listener._remember_rig("WSJT-X - Rig-A", "Portable")
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
        def on_error(message: str) -> None:
            errors.append(message)
            listener.stop()

        listener = WSJTXListener(on_error=on_error, on_disconnected=disconnected)
        with patch.object(listener, "_make_socket", side_effect=OSError("port in use")):
            with self.assertLogs("airwsjtx.wsjtx_udp", level="ERROR"):
                listener._run()
        self.assertEqual(len(errors), 1)
        self.assertIn("2237", errors[0])
        self.assertIn("port in use", errors[0])
        disconnected.assert_called_once()

    def test_repeated_bind_failure_does_not_flood_error_events(self) -> None:
        errors = Mock()
        listener = WSJTXListener(on_error=errors)
        with patch.object(listener, "_make_socket", side_effect=OSError("port in use")) as bind, \
                patch.object(listener._stop, "wait", side_effect=[False, False, True]), \
                self.assertLogs("airwsjtx.wsjtx_udp", level="ERROR") as logs:
            listener._run()
        self.assertEqual(bind.call_count, 3)
        errors.assert_called_once()
        self.assertEqual(len(logs.output), 1)

    def test_recovers_when_exclusive_port_is_released_and_receives_real_status(self) -> None:
        owner = self._udp_receiver("0.0.0.0")
        port = owner.getsockname()[1]
        sender = self._udp_receiver()
        error = threading.Event()
        received = threading.Event()
        statuses: list[WSJTXStatus] = []
        listener = WSJTXListener(
            port=port, socket_timeout_s=0.05, on_error=lambda _: error.set(),
            on_status=lambda status: (statuses.append(status), received.set()),
        )
        with self.assertLogs("airwsjtx.wsjtx_udp", level="ERROR"):
            self._start_listener(listener)
            self.assertTrue(error.wait(2))
        self.assertFalse(listener._connected)
        owner.close()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            sender.sendto(_build_status_packet("FT8", 15), ("127.0.0.1", port))
            if received.wait(0.05):
                break
        self.assertTrue(received.is_set(), "Listener did not recover after the port was released")
        self.assertIsNone(listener.get_error())
        self.assertTrue(listener._connected)
        self.assertEqual(statuses[0].mode, "FT8")

    def test_proxy_forwards_real_status_and_decode_to_listener_on_separate_port(self) -> None:
        proxy = self._udp_receiver()
        sender = self._udp_receiver()
        status_received = threading.Event()
        decode_received = threading.Event()
        statuses: list[WSJTXStatus] = []
        decodes: list[str | None] = []
        listener = WSJTXListener(
            port=0, socket_timeout_s=0.05,
            on_status=lambda status: (statuses.append(status), status_received.set()),
            on_decode=lambda decode: (decodes.append(decode.message), decode_received.set()),
        )
        self._start_listener(listener)
        deadline = time.monotonic() + 2
        while listener._sock is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(listener._sock, "Listener did not bind a UDP port")
        assert listener._sock is not None
        destination = ("127.0.0.1", listener._sock.getsockname()[1])
        status_packet = _build_status_packet("FT8", 15)
        decode_packet = (
            struct.pack(">III", WSJTX_MAGIC, 2, MSG_DECODE)
            + _enc_str("WSJT-X") + struct.pack(">BIidI", 1, 0, -10, 0.2, 1000)
            + _enc_str("FT8") + _enc_str("CQ K7MDL CN87") + b"\x00\x00"
        )
        for packet in (status_packet, decode_packet):
            sender.sendto(packet, proxy.getsockname())
            forwarded, _ = proxy.recvfrom(4096)
            self.assertEqual(forwarded, packet)
            if packet == status_packet:
                local_status = parse_status(forwarded)
                self.assertIsNotNone(local_status)
                assert local_status is not None
                self.assertEqual(local_status.de_call, "K7MDL")
            proxy.sendto(forwarded, destination)
        self.assertTrue(status_received.wait(2), "Forwarded status was not received")
        self.assertTrue(decode_received.wait(2), "Forwarded decode was not received")
        self.assertEqual(statuses[0].de_call, "K7MDL")
        self.assertEqual(decodes, ["CQ K7MDL CN87"])
        self.assertTrue(listener._connected)
        self.assertIsNone(listener.get_error())

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
                self.assertTrue(listener.send_halt_tx())
                sender.settimeout(2)
                command, _ = sender.recvfrom(4096)
                self.assertEqual(
                    command,
                    struct.pack(">III", WSJTX_MAGIC, 2, MSG_HALT_TX) + _enc_str("WSJT-X") + b"\x00",
                )
        finally:
            listener.stop()
            if listener._thread:
                listener._thread.join(2)
            sender.close()
            sock.close()


class WSJTXListenerCommandTests(unittest.TestCase):
    def _listener_with_socket(self, app_id: str | None):
        from airwsjtx.wsjtx_udp import WSJTXListener
        listener = WSJTXListener(port=2237)
        sock = Mock()
        listener._query_sock = sock
        listener._active_app_id = app_id
        return listener, sock

    @staticmethod
    def _header(msg_type: int, app_id: str) -> bytes:
        raw = app_id.encode()
        return struct.pack(">IIII", 0xADBCCBDA, 2, msg_type, len(raw)) + raw

    def test_halt_tx_goes_to_the_address_wsjtx_sends_from(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        listener._handle_datagram(self._header(0, "WSJT-X") + struct.pack(">I", 2) + struct.pack(">I", 0xFFFFFFFF) * 2, ("127.0.0.1", 50123))
        self.assertTrue(listener.send_halt_tx())
        expected = self._header(8, "WSJT-X") + b"\x00"
        sock.sendto.assert_called_once_with(expected, ("127.0.0.1", 50123))

    def test_halt_tx_can_target_one_specific_wsjtx_instance(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        target = ("127.0.0.1", 50123)
        other = ("127.0.0.1", 50124)
        listener._client_addrs["WSJT-X"] = {target: time.monotonic(), other: time.monotonic()}
        self.assertTrue(listener.send_halt_tx(app_id="WSJT-X", destination=target))
        sock.sendto.assert_called_once_with(self._header(8, "WSJT-X") + b"\x00", target)

    def test_replay_requests_status_only_from_the_selected_address(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        target = ("127.0.0.1", 50123)
        other = ("127.0.0.1", 50124)
        listener._client_addrs["WSJT-X"] = {target: time.monotonic(), other: time.monotonic()}
        listener._last_active_addr = target
        self.assertTrue(listener.send_replay(app_id="WSJT-X"))
        self.assertFalse(listener.send_replay(app_id="WSJT-X - Other", destination=target))
        self.assertFalse(listener.send_replay(app_id="WSJT-X", destination=("127.0.0.1", 50125)))
        sock.sendto.assert_called_once_with(self._header(MSG_REPLAY, "WSJT-X"), target)

    def test_connected_callback_can_request_replay_immediately(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        listener.on_connected = lambda app_id: listener.send_replay(app_id=app_id)
        heartbeat = struct.pack(">I", 2) + struct.pack(">I", 0xFFFFFFFF) * 2
        target = ("127.0.0.1", 50123)
        self.assertTrue(listener._handle_datagram(self._header(0, "WSJT-X") + heartbeat, target))
        sock.sendto.assert_called_once_with(self._header(MSG_REPLAY, "WSJT-X"), target)

    def test_enable_tx_is_type_17_with_only_the_id_and_targets_the_active_address(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        heartbeat = struct.pack(">I", 2) + struct.pack(">I", 0xFFFFFFFF) * 2
        listener._handle_datagram(self._header(0, "WSJT-X") + heartbeat, ("127.0.0.1", 50123))
        listener._client_addrs["WSJT-X"][("127.0.0.1", 50124)] = time.monotonic() - 1
        self.assertTrue(listener.send_enable_tx())
        sock.sendto.assert_called_once_with(self._header(17, "WSJT-X"), ("127.0.0.1", 50123))

    def test_enable_tx_needs_a_connected_instance(self) -> None:
        listener, sock = self._listener_with_socket(None)
        self.assertFalse(listener.send_enable_tx())
        sock.sendto.assert_not_called()

    def test_enable_tx_never_broadcasts_when_active_address_is_missing(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        listener._client_addrs["WSJT-X"] = {
            ("127.0.0.1", 50123): time.monotonic(),
            ("127.0.0.1", 50124): time.monotonic(),
        }
        self.assertFalse(listener.send_enable_tx())
        sock.sendto.assert_not_called()

    def test_enable_tx_requires_the_expected_instance_and_exact_address(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        target = ("127.0.0.1", 50123)
        other = ("127.0.0.1", 50124)
        listener._client_addrs["WSJT-X"] = {target: time.monotonic(), other: time.monotonic()}
        listener._last_active_addr = other
        self.assertFalse(listener.send_enable_tx(app_id="WSJT-X - Other", destination=target))
        self.assertFalse(listener.send_enable_tx(app_id="WSJT-X", destination=("127.0.0.1", 50125)))
        sock.sendto.assert_not_called()
        self.assertTrue(listener.send_enable_tx(app_id="WSJT-X", destination=target))
        sock.sendto.assert_called_once_with(self._header(17, "WSJT-X"), target)

    def test_start_dx_tx_uses_the_exact_contract_and_single_destination(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        target = ("127.0.0.1", 50123)
        other = ("127.0.0.1", 50124)
        listener._client_addrs["WSJT-X"] = {target: time.monotonic(), other: time.monotonic()}
        fields = {
            "request_id": 42, "app_id": "WSJT-X", "destination": target,
            "de_call": "K7MDL", "freq_hz": 1_296_150_000, "dx_call": "W7PUA",
            "dx_grid": "CN84AB", "mode": "Q65", "sub_mode": "C", "tr_period": 15,
        }
        self.assertTrue(listener.send_start_dx_tx(**fields))
        packet, destination = sock.sendto.call_args.args
        self.assertEqual(destination, target)
        expected = b"".join([
            self._header(MSG_START_DX_TX, "WSJT-X"),
            struct.pack(">I", 42),
            _enc_str("K7MDL"),
            struct.pack(">Q", 1_296_150_000),
            _enc_str("W7PUA"), _enc_str("CN84AB"), _enc_str("Q65"), _enc_str("C"),
            struct.pack(">I", 15),
        ])
        self.assertEqual(packet, expected)
        self.assertFalse(listener.send_start_dx_tx(**{**fields, "app_id": "WSJT-X - Other"}))
        self.assertFalse(listener.send_start_dx_tx(**{**fields, "destination": ("127.0.0.1", 50125)}))
        self.assertFalse(listener.send_start_dx_tx(**{**fields, "request_id": 0}))
        sock.sendto.assert_called_once()

    def test_command_address_survives_the_normal_wsjtx_heartbeat_interval(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        target = ("127.0.0.1", 50123)
        listener._client_addrs["WSJT-X"] = {target: 88.0}
        listener._last_active_addr = target
        with patch("airwsjtx.wsjtx_udp.time.monotonic", return_value=100.0):
            self.assertTrue(listener.send_configure(dx_call="W7PUA", dx_grid="CN84"))
        self.assertEqual(sock.sendto.call_args.args[1], target)

    def test_reply_targets_only_the_source_of_the_matching_decode(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        on_decode = Mock()
        listener.on_decode = on_decode
        listener._handle_datagram(_build_decode_packet(1234), ("127.0.0.1", 50123))
        decode = on_decode.call_args.args[0]
        listener._client_addrs["WSJT-X"][("127.0.0.1", 50124)] = time.monotonic()

        self.assertTrue(listener.send_reply(decode))
        self.assertEqual(sock.sendto.call_count, 1)
        self.assertEqual(sock.sendto.call_args.args[1], ("127.0.0.1", 50123))

    def test_halt_tx_needs_a_known_wsjtx_address(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        self.assertFalse(listener.send_halt_tx())
        sock.sendto.assert_not_called()

    def test_stale_wsjtx_address_is_not_used(self) -> None:
        listener, sock = self._listener_with_socket("WSJT-X")
        listener._client_addrs["WSJT-X"] = {("127.0.0.1", 50123): time.monotonic() - 60}
        self.assertFalse(listener.send_halt_tx())
        sock.sendto.assert_not_called()

    def test_commands_need_a_connected_instance(self) -> None:
        listener, sock = self._listener_with_socket(None)
        self.assertFalse(listener.send_halt_tx())
        sock.sendto.assert_not_called()


if __name__ == "__main__":
    unittest.main()