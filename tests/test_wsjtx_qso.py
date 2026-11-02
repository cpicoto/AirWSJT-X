from __future__ import annotations

import socket
import struct
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from airwsjtx.wsjtx_qso import WSJTXQsoAssistant
from airwsjtx.state_machine import SessionState
from airwsjtx.wsjtx_udp import (
    WSJTXDecode, WSJTXListener, WSJTXQsoLogged, WSJTXStartDxTxResult, WSJTXStatus, WSJTX_MAGIC,
)


SOURCE_ADDR = ("127.0.0.1", 50123)


def status(**changes: object) -> WSJTXStatus:
    values: dict[str, object] = {
        "app_id": "WSJT-X",
        "freq_hz": 1_296_150_000,
        "mode": "Q65",
        "dx_call": "W7PUA",
        "report": None,
        "tx_mode": None,
        "tx_enabled": False,
        "transmitting": False,
        "decoding": True,
        "rx_df": 0,
        "tx_df": 0,
        "de_call": "K7MDL",
        "de_grid": "CN87",
        "dx_grid": "CN84",
        "tx_watchdog": False,
        "sub_mode": "A",
        "fast_mode": False,
        "special_op_mode": 0,
        "freq_tolerance": 50,
        "tr_period": 15.0,
        "config_name": None,
        "source_addr": SOURCE_ADDR,
    }
    values.update(changes)
    return WSJTXStatus(**values)  # type: ignore[arg-type]


def decode(message: str, **changes: object) -> WSJTXDecode:
    values: dict[str, object] = {
        "app_id": "WSJT-X",
        "is_new": True,
        "time_ms": 1000,
        "snr": -10,
        "delta_time_s": 0.2,
        "delta_freq_hz": 1000,
        "mode": ":",
        "message": message,
        "low_confidence": False,
        "off_air": False,
        "source_addr": SOURCE_ADDR,
    }
    values.update(changes)
    return WSJTXDecode(**values)  # type: ignore[arg-type]


def start_result(**changes: object) -> WSJTXStartDxTxResult:
    values: dict[str, object] = {
        "app_id": "WSJT-X",
        "request_id": 42,
        "accepted": True,
        "reason": "",
        "tx_message": "W7PUA K7MDL CN87",
        "source_addr": SOURCE_ADDR,
    }
    values.update(changes)
    return WSJTXStartDxTxResult(**values)  # type: ignore[arg-type]


def accept_start(assistant: WSJTXQsoAssistant) -> None:
    assistant.observe_status(status())
    assistant.mark_start_requested(42)
    assistant.observe_start_result(start_result())


def encoded_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack(">I", len(data)) + data


class WSJTXQsoAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assistant = WSJTXQsoAssistant()
        self.assistant.arm(
            dx_call="W7PUA",
            dx_grid="CN84",
            my_call="K7MDL",
            app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        self.assistant.configuration_requested(0.0)

    def test_waits_for_matching_settings_before_preparing_direct_start(self) -> None:
        self.assertEqual(self.assistant.observe_status(status(mode="FT8")).stage, "preparing")
        self.assertEqual(self.assistant.observe_status(status()).stage, "verified")
        self.assertIsNotNone(self.assistant.direct_start_status())
        self.assertEqual(self.assistant.observe_status(status()).stage, "verified")

    def test_equivalent_grid_update_does_not_suspend_after_start(self) -> None:
        accept_start(self.assistant)
        progress = self.assistant.observe_status(status(dx_grid="CN84AB", tx_enabled=True))
        self.assertEqual(progress.stage, "tx_enabled")

    def test_auto_sequence_tx_off_after_exchange_does_not_suspend(self) -> None:
        accept_start(self.assistant)
        self.assistant.observe_status(status(tx_enabled=True, transmitting=True))
        self.assistant.observe_decode(decode("K7MDL W7PUA RR73"))
        self.assertEqual(self.assistant.snapshot().stage, "awaiting_log")
        progress = self.assistant.observe_status(status())
        self.assertEqual(progress.stage, "awaiting_log")
        self.assertIn("log the QSO", progress.message)

    def test_accepts_q65_wire_marker_and_legacy_name(self) -> None:
        for mode in (":", ":C", "Q65"):
            with self.subTest(mode=mode):
                self.assertTrue(self.assistant._is_q65_decode(decode("K7MDL W7PUA -10", mode=mode)))

    def test_rejects_other_mode_exchange(self) -> None:
        accept_start(self.assistant)
        self.assertEqual(
            self.assistant.observe_decode(decode("K7MDL W7PUA -10", mode="~")).stage,
            "tx_enabled",
        )

    def test_fresh_status_after_configure_is_required(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        self.assertEqual(assistant.observe_status(status()).stage, "preparing")
        assistant.configuration_requested(100.0)
        self.assertEqual(assistant.observe_status(status(), observed_at=99.0).stage, "preparing")
        self.assertEqual(assistant.observe_status(status(), observed_at=101.0).stage, "verified")

    def test_only_the_armed_address_can_confirm_settings_or_start(self) -> None:
        other = ("127.0.0.1", 50124)
        self.assertEqual(
            self.assistant.observe_status(status(source_addr=other)).stage, "preparing",
        )
        self.assertIsNone(self.assistant.direct_start_status())
        self.assertEqual(self.assistant.observe_status(status()).stage, "verified")
        self.assistant.mark_start_requested(42)
        for result in (
            start_result(source_addr=other), start_result(request_id=41),
            start_result(app_id="WSJT-X - Other"),
        ):
            self.assertEqual(self.assistant.observe_start_result(result).stage, "start_requested")
        self.assertEqual(self.assistant.observe_start_result(start_result()).stage, "tx_enabled")
        self.assertFalse(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X", "W7PUA", "CN84", 1_296_150_000, "Q65", "-10", "-12", other,
        )))

    def test_fast_mode_does_not_confirm_the_requested_configuration(self) -> None:
        self.assertEqual(self.assistant.observe_status(status(fast_mode=True)).stage, "preparing")
        self.assertIsNone(self.assistant.direct_start_status())

    def test_incompatible_tx_mode_does_not_confirm_configuration(self) -> None:
        self.assertEqual(self.assistant.observe_status(status(tx_mode="FT8")).stage, "preparing")
        self.assertIsNone(self.assistant.direct_start_status())

    def test_my_callsign_must_match_before_configuration_is_verified(self) -> None:
        self.assertEqual(self.assistant.observe_status(status(de_call="K1AAA")).stage, "preparing")
        self.assertIsNone(self.assistant.direct_start_status())

    def test_new_tx_mode_or_mismatched_dx_grid_suspends_the_qso(self) -> None:
        self.assistant.observe_status(status(tx_mode="Q65"))
        self.assertEqual(
            self.assistant.observe_status(status(tx_mode="FT8")).stage, "suspended",
        )
        self.assistant.arm(
            dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        self.assistant.configuration_requested(0.0)
        self.assistant.observe_status(status())
        self.assertEqual(
            self.assistant.observe_status(status(dx_grid="FN31")).stage, "suspended",
        )

    def test_equivalent_four_and_six_character_dx_grids_confirm_configuration(self) -> None:
        self.assertEqual(
            self.assistant.observe_status(status(dx_grid="CN84AB")).stage, "verified",
        )

    def test_tx_enabled_before_verification_suspends_without_enable_tx(self) -> None:
        progress = self.assistant.observe_status(status(tx_enabled=True))
        self.assertEqual(progress.stage, "suspended")
        self.assertIn("before settings were verified", progress.message)
        self.assertIsNone(self.assistant.direct_start_status())

    def test_transmitting_before_verification_suspends_without_enable_tx(self) -> None:
        progress = self.assistant.observe_status(status(transmitting=True))
        self.assertEqual(progress.stage, "suspended")
        self.assertIn("before settings were verified", progress.message)
        self.assertIsNone(self.assistant.direct_start_status())

    def test_transmitting_status_waits_for_matching_atomic_confirmation(self) -> None:
        self.assistant.observe_status(status())
        self.assistant.mark_start_requested(42)
        progress = self.assistant.observe_status(status(
            tx_enabled=True, transmitting=True, tx_message="CQ K7MDL CN87",
        ))
        self.assertEqual(progress.stage, "start_requested")
        self.assertIn("waiting for direct-call confirmation", progress.message)
        progress = self.assistant.observe_start_result(start_result())
        self.assertEqual(progress.stage, "transmitting")
        self.assertIn("CQ K7MDL CN87", progress.message)

    def test_qso_band_boundary(self) -> None:
        for frequency_hz, expected in (
            (143_999_999, "preparing"), (144_000_000, "verified"),
            (144_180_000, "verified"), (1_296_150_000, "verified"),
        ):
            with self.subTest(frequency_hz=frequency_hz):
                assistant = WSJTXQsoAssistant()
                assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
                assistant.configuration_requested(0.0)
                self.assertEqual(assistant.observe_status(status(freq_hz=frequency_hz)).stage, expected)

    def test_tracks_exchange_but_only_matching_dx_and_my_call(self) -> None:
        accept_start(self.assistant)
        self.assistant.observe_status(status(tx_enabled=True))
        self.assertEqual(
            self.assistant.observe_decode(decode("K1AAA K7MDL -10")).stage,
            "tx_enabled",
        )
        self.assertEqual(
            self.assistant.observe_decode(decode("W7PUA K1AAA -10")).stage,
            "tx_enabled",
        )
        self.assertEqual(
            self.assistant.observe_decode(decode("W7PUA K7MDL -10")).stage,
            "tx_enabled",
        )
        self.assertEqual(
            self.assistant.observe_decode(decode("K7MDL <W7PUA> -10")).stage,
            "exchange",
        )
        self.assertEqual(
            self.assistant.observe_decode(decode("K7MDL W7PUA RR73")).stage,
            "awaiting_log",
        )

    def test_settings_change_suspends_until_explicit_rearm(self) -> None:
        accept_start(self.assistant)
        self.assistant.observe_status(status(tx_enabled=True))
        progress = self.assistant.observe_status(status(freq_hz=1_297_000_000))
        self.assertEqual(progress.stage, "suspended")
        self.assertIsNone(self.assistant.direct_start_status())

    def test_tx_disable_before_exchange_suspends_automation(self) -> None:
        accept_start(self.assistant)
        self.assistant.observe_status(status(tx_enabled=True))
        self.assertEqual(self.assistant.observe_status(status()).stage, "suspended")

    def test_only_matching_rig_and_dx_complete_the_session(self) -> None:
        accept_start(self.assistant)
        self.assertFalse(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X - Other", "W7PUA", "CN84", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR
        )))
        self.assertFalse(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X", "K1AAA", "FN31", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR
        )))
        self.assertFalse(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X", "W7PUA", "CN84", 1_296_150_000, "FT8", "-10", "-12", SOURCE_ADDR
        )))
        self.assertFalse(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X", "W7PUA", "CN84", 1_297_000_000, "Q65", "-10", "-12", SOURCE_ADDR
        )))
        self.assertTrue(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X", "w7pua", "CN84", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR
        )))
        self.assertEqual(self.assistant.snapshot().stage, "logged")

    def test_dx_callsign_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid DX callsign"):
            self.assistant.arm(dx_call="DX", dx_grid=None, my_call="K7MDL", app_id="WSJT-X")

    def test_my_callsign_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid MY callsign"):
            self.assistant.arm(dx_call="W7PUA", dx_grid=None, my_call=None, app_id="WSJT-X")

    def test_unconfirmed_configuration_times_out_without_reply(self) -> None:
        with patch("airwsjtx.wsjtx_qso.time.monotonic", return_value=100.0):
            self.assistant.arm(
                dx_call="W7PUA",
                dx_grid="CN84",
                my_call="K7MDL",
                app_id="WSJT-X",
                source_addr=SOURCE_ADDR,
            )
        with patch("airwsjtx.wsjtx_qso.time.monotonic", return_value=131.0):
            progress = self.assistant.configuration_timeout()
        self.assertEqual(progress.stage, "suspended")
        self.assertIn("Accept UDP requests", progress.message)
        self.assertIsNone(self.assistant.direct_start_status())

    def test_start_rejection_and_wrong_tx1_suspend_without_claiming_success(self) -> None:
        self.assistant.observe_status(status())
        self.assistant.mark_start_requested(42)
        progress = self.assistant.observe_start_result(start_result(
            accepted=False, reason="dial_frequency_mismatch", tx_message="",
        ))
        self.assertEqual(progress.stage, "suspended")
        self.assertIn("dial_frequency_mismatch", progress.message)
        self.assertEqual(self.assistant.observe_start_result(start_result()).stage, "suspended")

        self.assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        self.assistant.configuration_requested(0.0)
        self.assistant.observe_status(status())
        self.assistant.mark_start_requested(43)
        progress = self.assistant.observe_start_result(start_result(
            request_id=43, tx_message="CQ K7MDL CN87",
        ))
        self.assertEqual(progress.stage, "suspended")
        self.assertIn("stop Tx", progress.message)

    def test_ignored_start_times_out_and_late_result_cannot_restart_it(self) -> None:
        self.assistant.observe_status(status())
        with patch("airwsjtx.wsjtx_qso.time.monotonic", return_value=100.0):
            self.assistant.mark_start_requested(42)
        with patch("airwsjtx.wsjtx_qso.time.monotonic", return_value=105.0):
            self.assertEqual(self.assistant.start_timeout(41).stage, "start_requested")
            progress = self.assistant.start_timeout(42)
        self.assertEqual(progress.stage, "suspended")
        self.assertIn("did not confirm", progress.message)
        self.assertEqual(self.assistant.observe_start_result(start_result()).stage, "suspended")

    def test_decodes_and_logs_do_not_confirm_a_pending_start(self) -> None:
        self.assistant.observe_status(status())
        self.assistant.mark_start_requested(42)
        self.assertEqual(
            self.assistant.observe_decode(decode("K7MDL W7PUA RR73")).stage,
            "start_requested",
        )
        self.assertFalse(self.assistant.complete_logged_qso(WSJTXQsoLogged(
            "WSJT-X", "W7PUA", "CN84", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR,
        )))
        self.assertEqual(self.assistant.observe_start_result(start_result()).stage, "tx_enabled")
        self.assertEqual(
            self.assistant.observe_decode(decode("K7MDL W7PUA RR73")).stage,
            "awaiting_log",
        )

    def test_known_and_unknown_rejection_reasons_never_claim_success(self) -> None:
        for reason in ("tx1_unavailable", "tx1_address_mismatch", "new_reason"):
            with self.subTest(reason=reason):
                self.assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL",
                                   app_id="WSJT-X", source_addr=SOURCE_ADDR)
                self.assistant.configuration_requested(0.0)
                self.assistant.observe_status(status())
                self.assistant.mark_start_requested(42)
                progress = self.assistant.observe_start_result(start_result(
                    accepted=False, reason=reason, tx_message="",
                ))
                self.assertEqual(progress.stage, "suspended")
                self.assertIn(reason, progress.message)

    def test_rearm_ignores_the_previous_start_result(self) -> None:
        self.assistant.observe_status(status())
        self.assistant.mark_start_requested(42)
        self.assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        self.assistant.configuration_requested(0.0)
        self.assistant.observe_status(status())
        self.assistant.mark_start_requested(43)
        self.assertEqual(self.assistant.observe_start_result(start_result()).stage, "start_requested")
        self.assertEqual(self.assistant.observe_start_result(start_result(request_id=43)).stage, "tx_enabled")


class WSJTXQsoBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        self.bridge = bridge
        self.real_tracking_viable = bridge._qso_tracking_viable_locked
        self.tracking_viable_patch = patch.object(bridge, "_qso_tracking_viable_locked", return_value=True)
        self.tracking_viable_patch.start()
        self.addCleanup(self.tracking_viable_patch.stop)
        station_patch = patch.multiple(bridge, _station_b_locator="CN84", _station_b_call="W7PUA")
        station_patch.start()
        self.addCleanup(station_patch.stop)
        feed_patch = patch.multiple(
            bridge, _start_configured_airscout_feed=Mock(), _start_opensky_feed=Mock(),
            _start_adsbexchange_feed=Mock(),
        )
        feed_patch.start()
        self.addCleanup(feed_patch.stop)
        self.assistant = WSJTXQsoAssistant()
        self.assistant.arm(
            dx_call="W7PUA",
            dx_grid="CN84",
            my_call="K7MDL",
            app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        self.assistant.configuration_requested(0.0)
        self.assistant.observe_status(status())

    def test_matching_cq_does_not_bypass_atomic_start(self) -> None:
        listener = Mock()
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", self.assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            item = decode("CQ W7PUA CN84")
            self.bridge._on_wsjtx_decode(item)
            self.bridge._on_wsjtx_decode(item)

        listener.send_reply.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        listener.send_start_dx_tx.assert_not_called()
        self.assertEqual(self.assistant.snapshot().stage, "verified")

    def test_start_rpc_configures_dx_and_arms_after_operator_request(self) -> None:
        assistant = WSJTXQsoAssistant()
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_configure.return_value = True
        session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        supervisor = Mock()
        supervisor.session = session
        supervisor.wsjtx.is_ready_for_qso.return_value = True

        def start_session() -> None:
            session.state = SessionState.ACTIVE_QSO

        supervisor.operator_start_qso.side_effect = start_session
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status()),
            patch.object(self.bridge, "_station_a_call", "K7MDL"),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch("sidecar.airwsjtx_bridge.threading.Timer") as timer,
        ):
            self.bridge.handle({
                "id": "qso-1",
                "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })

        listener.send_configure.assert_called_once_with(
            dx_call="W7PUA", dx_grid="CN84", app_id="WSJT-X", destination=SOURCE_ADDR,
        )
        listener.send_replay.assert_called_once_with(app_id="WSJT-X", destination=SOURCE_ADDR)
        supervisor.operator_start_qso.assert_called_once_with()
        timer.return_value.start.assert_called_once_with()
        self.assertEqual(assistant.snapshot().stage, "preparing")
        listener.send_reply.assert_not_called()
        self.assertTrue(any(event.get("ok") is True for event in emitted))

    def test_start_rpc_does_not_reply_to_cq_heard_before_start(self) -> None:
        assistant = WSJTXQsoAssistant()
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_configure.return_value = True
        listener.send_start_dx_tx.return_value = True
        session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        supervisor = Mock()
        supervisor.session = session
        supervisor.wsjtx.is_ready_for_qso.return_value = True
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status()),
            patch.object(self.bridge, "_station_a_call", "K7MDL"),
            patch.object(self.bridge, "_station_a_locator", "CN87"),
            patch.object(self.bridge, "_station_b_locator", "CN84"),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.bridge._on_wsjtx_decode(decode("CQ W7PUA CN84"))
            self.bridge.handle({
                "id": "qso-3",
                "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
            listener.send_reply.assert_not_called()
            self.assertEqual(assistant.snapshot().stage, "preparing")
            self.bridge._on_wsjtx_status(status())

        listener.send_reply.assert_not_called()
        listener.send_start_dx_tx.assert_called_once()
        self.assertEqual(assistant.snapshot().stage, "start_requested")

    def test_status_during_configure_does_not_trigger_a_premature_start(self) -> None:
        assistant = WSJTXQsoAssistant()
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_start_dx_tx.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        emitted: list[dict] = []

        def send_configure(**_kwargs: object) -> bool:
            self.bridge._on_wsjtx_status(status())
            listener.send_start_dx_tx.assert_not_called()
            return True

        listener.send_configure.side_effect = send_configure
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status()),
            patch.object(self.bridge, "_station_a_locator", "CN87"),
            patch.object(self.bridge, "_station_b_locator", "CN84"),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.bridge.handle({
                "id": "qso-order", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
            self.assertEqual(assistant.snapshot().stage, "preparing")
            self.bridge._on_wsjtx_status(status())

        listener.send_start_dx_tx.assert_called_once()
        listener.send_reply.assert_not_called()
        response_index = next(i for i, event in enumerate(emitted) if event.get("id") == "qso-order")
        start_index = next(i for i, event in enumerate(emitted)
                           if event.get("event") == "qso_progress" and event["data"]["stage"] == "start_requested")
        self.assertLess(response_index, start_index)

    def test_verified_status_sends_one_atomic_request_and_requires_result(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        assistant.configuration_requested(0.0)
        listener = Mock()
        listener.send_start_dx_tx.return_value = True
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_qso_request_id", 41),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch("sidecar.airwsjtx_bridge.threading.Timer") as timer,
        ):
            self.bridge._on_wsjtx_status(status(mode="FT8"))
            listener.send_start_dx_tx.assert_not_called()
            self.bridge._on_wsjtx_status(status())
            self.bridge._on_wsjtx_status(status())
            listener.send_start_dx_tx.assert_called_once_with(
                request_id=42, app_id="WSJT-X", destination=SOURCE_ADDR,
                de_call="K7MDL", freq_hz=1_296_150_000, dx_call="W7PUA",
                dx_grid="CN84", mode="Q65", sub_mode="A", tr_period=15,
            )
            timer.assert_called_once_with(5.0, self.bridge._check_qso_start_tx, args=(42,))
            self.bridge._on_wsjtx_status(status(tx_enabled=True))
            self.assertEqual(assistant.snapshot().stage, "start_requested")
            self.bridge._on_wsjtx_start_dx_tx_result(start_result())

        listener.send_reply.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        messages = [event["data"]["message"] for event in emitted if event.get("event") == "qso_progress"]
        self.assertTrue(any("direct-call confirmation" in message for message in messages))
        self.assertIn("accepted the direct call", messages[-1])

    def test_no_start_after_the_aircraft_was_unlocked(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        assistant.configuration_requested(0.0)
        listener = Mock()
        listener.send_start_dx_tx.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(
            state=SessionState.ACTIVE_QSO, selected_aircraft_id="abc123",
        )
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_locked_aircraft_id", None),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_qso_tracking_viable_locked", side_effect=self.real_tracking_viable),
            patch.object(self.bridge, "_emit"),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.bridge._on_wsjtx_status(status())

        listener.send_start_dx_tx.assert_not_called()
        self.assertEqual(assistant.snapshot().stage, "suspended")

    def test_unlock_serializes_against_a_waiting_status_callback(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(
            dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        assistant.configuration_requested(0.0)
        listener = Mock()
        supervisor = Mock()
        supervisor.session = SimpleNamespace(
            state=SessionState.ACTIVE_QSO, selected_aircraft_id="abc123",
        )
        entered = threading.Event()

        def receive_status() -> None:
            entered.set()
            self.bridge._on_wsjtx_status(status())

        with (
            patch.multiple(
                self.bridge, _qso_assistant=assistant, _wsjtx_listener=listener,
                supervisor=supervisor, _locked_aircraft_id="abc123", _satpc32_dde=None,
                _station_a_locator="CN87", _station_b_locator="CN84", _dx_grid_manual=True,
            ),
            patch.object(self.bridge, "_emit"),
        ):
            with self.bridge._qso_lock:
                thread = threading.Thread(target=receive_status)
                thread.start()
                self.assertTrue(entered.wait(1.0))
                self.bridge._do_unlock()
            thread.join(1.0)
            self.assertFalse(thread.is_alive())

        listener.send_reply.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        listener.send_start_dx_tx.assert_not_called()
        self.assertEqual(assistant.snapshot().stage, "aborted")
        self.assertIn("Stop WSJT-X Tx", assistant.snapshot().message)

    def test_other_instance_status_does_not_replace_the_armed_station(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(
            dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        assistant.configuration_requested(0.0)
        listener = Mock()
        original_status = status()
        with (
            patch.multiple(
                self.bridge, _qso_assistant=assistant, _wsjtx_listener=listener,
                _latest_wsjtx_status=original_status, _station_a_locator="CN87",
                _station_b_locator="CN84", _wsjtx_de_grid="CN87", _dx_grid_manual=False,
                _active_source=None,
            ),
            patch.object(self.bridge, "_emit"),
            patch.object(self.bridge, "_emit_aircraft_update"),
            patch.object(self.bridge, "_start_configured_airscout_feed"),
        ):
            self.bridge._on_wsjtx_status(status(
                source_addr=("127.0.0.1", 50124), de_grid="FN20", dx_grid="FN31",
            ))
            self.assertIs(self.bridge._latest_wsjtx_status, original_status)
            self.assertEqual(self.bridge._station_a_locator, "CN87")
            self.assertEqual(self.bridge._station_b_locator, "CN84")

        self.assertEqual(assistant.snapshot().stage, "preparing")
        listener.send_reply.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        listener.send_start_dx_tx.assert_not_called()

    def test_stale_progress_is_not_published_after_the_qso_changes(self) -> None:
        stale = self.assistant.snapshot()
        current = self.assistant.finish(aborted=True)
        with (
            patch.object(self.bridge, "_qso_assistant", self.assistant),
            patch.object(self.bridge, "_emit") as emit,
        ):
            self.bridge._emit_qso_progress(stale)
            emit.assert_not_called()
            self.bridge._emit_qso_progress(current)
            self.assertEqual(emit.call_args.args[0]["data"]["stage"], "aborted")

    def test_finish_targets_only_the_armed_wsjtx_instance(self) -> None:
        listener = Mock()
        listener.send_halt_tx.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.ACTIVE_QSO)
        other = ("127.0.0.1", 50124)
        with (
            patch.multiple(
                self.bridge, _qso_assistant=self.assistant, _wsjtx_listener=listener,
                _latest_wsjtx_status=status(source_addr=other), supervisor=supervisor,
            ),
            patch.object(self.bridge, "_emit"),
        ):
            self.bridge.handle({
                "id": "qso-abort", "method": "finish_qso",
                "params": {"abort": True, "control_wsjtx": True},
            })

        listener.send_halt_tx.assert_called_once_with(app_id="WSJT-X", destination=SOURCE_ADDR)
        supervisor.finish_qso.assert_called_once_with(abort=True)

    def test_abort_after_timeout_does_not_halt_a_replacement_instance(self) -> None:
        self.assistant.suspend("Direct-call confirmation timed out; check Tx")
        listener = Mock()
        listener.send_halt_tx.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.ACTIVE_QSO)
        with (
            patch.multiple(
                self.bridge, _qso_assistant=self.assistant, _wsjtx_listener=listener,
                _latest_wsjtx_status=status(
                    app_id="WSJT-X - Other", source_addr=("127.0.0.1", 50124),
                ),
                supervisor=supervisor,
            ),
            patch.object(self.bridge, "_emit"),
        ):
            self.bridge.handle({
                "id": "qso-abort-suspended", "method": "finish_qso",
                "params": {"abort": True, "control_wsjtx": True},
            })
        listener.send_halt_tx.assert_called_once_with(app_id="WSJT-X", destination=SOURCE_ADDR)

    def test_logged_qso_closes_its_original_aircraft_session(self) -> None:
        accept_start(self.assistant)
        original = Mock()
        original.session.state = SessionState.ACTIVE_QSO
        replacement = Mock()
        replacement.session.state = SessionState.ACTIVE_QSO

        def replace_session(_progress: object) -> None:
            with self.bridge._qso_lock:
                self.bridge.supervisor = replacement

        with (
            patch.multiple(self.bridge, _qso_assistant=self.assistant, supervisor=original),
            patch.object(self.bridge, "_emit_qso_progress", side_effect=replace_session),
            patch.object(self.bridge, "_emit"),
        ):
            self.bridge._on_wsjtx_qso_logged(WSJTXQsoLogged(
                "WSJT-X", "W7PUA", "CN84", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR,
            ))

        original.finish_qso.assert_called_once_with(abort=False)
        replacement.finish_qso.assert_not_called()

    def test_tracking_guard_requires_the_current_viable_aircraft(self) -> None:
        from airwsjtx.airscout import Aircraft

        aircraft = Aircraft(
            icao24="abc123", callsign="TEST1", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False, position_updated_epoch_s=time.time(),
        )
        supervisor = Mock()
        supervisor.session = SimpleNamespace(
            state=SessionState.ACTIVE_QSO, selected_aircraft_id=aircraft.icao24,
        )
        assistant = WSJTXQsoAssistant()
        assistant.arm(
            dx_call="W7PUA", dx_grid="CN89", my_call="K7MDL", app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        with patch.multiple(
            self.bridge, supervisor=supervisor, _locked_aircraft_id=aircraft.icao24,
            _qso_assistant=assistant,
            _station_a_locator="CN87", _station_b_locator="CN89", _aircraft_cache=[aircraft],
            _aircraft_last_update_monotonic=time.monotonic(),
        ):
            self.assertTrue(self.real_tracking_viable())
            with patch.object(aircraft, "position_updated_epoch_s", time.time() - 1000):
                self.assertFalse(self.real_tracking_viable())
            with patch.object(aircraft, "on_ground", True):
                self.assertFalse(self.real_tracking_viable())
            with patch.object(aircraft, "lat", 0.0), patch.object(aircraft, "lon", 0.0):
                self.assertFalse(self.real_tracking_viable())
            with patch.object(self.bridge, "_locked_aircraft_id", "another-aircraft"):
                self.assertFalse(self.real_tracking_viable())
            with patch.object(self.bridge, "_station_b_call", "K1AAA"):
                self.assertFalse(self.real_tracking_viable())
            with patch.object(self.bridge, "_station_a_call", "K1AAA"):
                self.assertFalse(self.real_tracking_viable())
            with patch.object(self.bridge, "_station_b_locator", "FN31"):
                self.assertFalse(self.real_tracking_viable())
            supervisor.session.state = SessionState.PLANE_LOCKED
            self.assertFalse(self.real_tracking_viable())

    def test_start_rejects_a_dx_that_does_not_match_the_tracked_station(self) -> None:
        listener = Mock()
        listener.is_connected.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", WSJTXQsoAssistant()),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status()),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            for dx_call, dx_grid in (("K1AAA", "CN84"), ("W7PUA", "FN31")):
                self.bridge.handle({
                    "id": "qso-mismatched", "method": "start_qso",
                    "params": {"dx_call": dx_call, "dx_grid": dx_grid},
                })

        listener.send_configure.assert_not_called()
        self.assertEqual(len(emitted), 2)
        self.assertTrue(all("does not match" in item["error"] or "no longer matches" in item["error"]
                            for item in emitted))

    def test_start_requires_a_known_status_source(self) -> None:
        listener = Mock()
        listener.is_connected.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", WSJTXQsoAssistant()),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status(source_addr=None)),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            self.bridge.handle({
                "id": "qso-no-source", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })

        listener.send_configure.assert_not_called()
        self.assertIn("source address", emitted[-1]["error"])

    def test_duplicate_start_does_not_rearm_an_active_qso(self) -> None:
        listener = Mock()
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.ACTIVE_QSO)
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", self.assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            self.bridge.handle({
                "id": "qso-duplicate", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })

        listener.send_configure.assert_not_called()
        self.assertIn("already armed", emitted[-1]["error"])

    def test_selecting_a_new_aircraft_cancels_the_armed_qso(self) -> None:
        from airwsjtx.airscout import Aircraft

        aircraft = Aircraft(
            icao24="new123", callsign="TEST1", lat=46.0, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False, position_updated_epoch_s=time.time(),
        )
        assistant = WSJTXQsoAssistant()
        assistant.arm(
            dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X",
            source_addr=SOURCE_ADDR,
        )
        assistant.configuration_requested(0.0)
        listener = Mock()
        supervisor = Mock()
        supervisor.session = SimpleNamespace(
            state=SessionState.ACTIVE_QSO, selected_aircraft_id="old123",
        )
        with (
            patch.multiple(
                self.bridge, _qso_assistant=assistant, _wsjtx_listener=listener,
                supervisor=supervisor, _locked_aircraft_id="old123",
                _station_a_locator="CN87", _station_b_locator="CN84",
                _aircraft_cache=[aircraft], _aircraft_last_update_monotonic=time.monotonic(),
            ),
            patch.object(self.bridge, "_publish_hamlib_target"),
            patch.object(self.bridge, "_publish_dde_target"),
            patch.object(self.bridge, "_emit"),
        ):
            self.assertIsNotNone(self.bridge._do_lock(aircraft.icao24))
            self.bridge._on_wsjtx_status(status())

        self.assertEqual(assistant.snapshot().stage, "aborted")
        listener.send_reply.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        listener.send_start_dx_tx.assert_not_called()

    def test_status_command_path_targets_only_the_verified_wsjtx_address(self) -> None:
        from airwsjtx.airscout import Aircraft
        from airwsjtx.supervisor import AirWSJTXSupervisor

        aircraft = Aircraft(
            icao24="abc123", callsign="TEST1", lat=46.0, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False, position_updated_epoch_s=time.time(),
        )
        listener = WSJTXListener(
            on_status=self.bridge._on_wsjtx_status,
            on_start_dx_tx_result=self.bridge._on_wsjtx_start_dx_tx_result,
        )
        socket = Mock()
        listener._query_sock = socket
        other = ("127.0.0.1", 50124)
        app_id = b"WSJT-X"
        packet = struct.pack(">IIII", WSJTX_MAGIC, 2, 1, len(app_id)) + app_id
        emitted: list[dict] = []
        with (
            patch.multiple(
                self.bridge, _qso_assistant=WSJTXQsoAssistant(), _wsjtx_listener=listener,
                _latest_wsjtx_status=None, supervisor=AirWSJTXSupervisor(),
                _locked_aircraft_id=None, _station_a_locator="CN87", _station_b_locator="CN84",
                _dx_grid_manual=True, _aircraft_cache=[aircraft],
                _aircraft_last_update_monotonic=time.monotonic(),
                _qso_request_id=41,
            ),
            patch.object(self.bridge, "_qso_tracking_viable_locked", side_effect=self.real_tracking_viable),
            patch.object(self.bridge, "_publish_hamlib_target"),
            patch.object(self.bridge, "_publish_dde_target"),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch("airwsjtx.wsjtx_udp.parse_status", side_effect=lambda _packet: status()),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.assertIsNotNone(self.bridge._do_lock(aircraft.icao24))
            listener._handle_datagram(packet, SOURCE_ADDR)
            self.bridge.handle({
                "id": "qso-pinned", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
            self.assertTrue(any(event.get("id") == "qso-pinned" and event.get("ok") for event in emitted))
            listener._handle_datagram(packet, other)
            self.assertEqual(socket.sendto.call_count, 2)
            listener._handle_datagram(packet, SOURCE_ADDR)
            listener._handle_datagram(packet, SOURCE_ADDR)
            result = (
                struct.pack(">III", WSJTX_MAGIC, 2, 19) + encoded_string("WSJT-X")
                + struct.pack(">IBI", 42, 1, 0xFFFFFFFF)
                + encoded_string("W7PUA K7MDL CN87")
            )
            listener._handle_datagram(result, other)
            self.assertEqual(self.bridge._qso_assistant.snapshot().stage, "start_requested")
            listener._handle_datagram(result, SOURCE_ADDR)
            self.assertEqual(self.bridge._qso_assistant.snapshot().stage, "tx_enabled")

        sent = socket.sendto.call_args_list
        self.assertEqual([struct.unpack_from(">I", call.args[0], 8)[0] for call in sent], [15, 7, 18])
        self.assertEqual([call.args[1] for call in sent], [SOURCE_ADDR, SOURCE_ADDR, SOURCE_ADDR])

    def test_noop_configure_replays_status_before_atomic_start_over_loopback(self) -> None:
        assistant = WSJTXQsoAssistant()
        listener = WSJTXListener(
            on_status=self.bridge._on_wsjtx_status,
            on_start_dx_tx_result=self.bridge._on_wsjtx_start_dx_tx_result,
        )
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        supervisor.operator_start_qso.side_effect = lambda: setattr(
            supervisor.session, "state", SessionState.ACTIVE_QSO,
        )
        app_id = b"WSJT-X"
        status_packet = struct.pack(">IIII", WSJTX_MAGIC, 2, 1, len(app_id)) + app_id
        emitted: list[dict] = []
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
        ):
            peer.bind(("127.0.0.1", 0))
            peer.settimeout(1)
            sender.bind(("127.0.0.1", 0))
            listener._query_sock = sender
            peer_addr = peer.getsockname()
            with (
                patch.multiple(
                    self.bridge, _qso_assistant=assistant, _wsjtx_listener=listener,
                    _latest_wsjtx_status=None, supervisor=supervisor, _qso_request_id=41,
                    _station_a_locator="CN87", _station_b_locator="CN84", _dx_grid_manual=True,
                ),
                patch.object(self.bridge, "_emit", side_effect=emitted.append),
                patch("airwsjtx.wsjtx_udp.parse_status", side_effect=lambda _packet: status()),
                patch("sidecar.airwsjtx_bridge.threading.Timer"),
            ):
                listener._handle_datagram(status_packet, peer_addr)
                self.bridge.handle({
                    "id": "qso-noop", "method": "start_qso",
                    "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
                })
                self.assertEqual(assistant.snapshot().stage, "preparing")
                configure, _ = peer.recvfrom(4096)
                replay, _ = peer.recvfrom(4096)
                self.assertEqual(struct.unpack_from(">I", configure, 8)[0], 15)
                self.assertEqual(struct.unpack_from(">I", replay, 8)[0], 7)
                listener._handle_datagram(status_packet, peer_addr)
                start, _ = peer.recvfrom(4096)
                self.assertEqual(struct.unpack_from(">I", start, 8)[0], 18)
                result = (
                    struct.pack(">III", WSJTX_MAGIC, 2, 19) + encoded_string("WSJT-X")
                    + struct.pack(">IBI", 42, 1, 0xFFFFFFFF)
                    + encoded_string("W7PUA K7MDL CN87")
                )
                listener._handle_datagram(result, peer_addr)
                self.assertEqual(assistant.snapshot().stage, "tx_enabled")
                self.assertTrue(any(event.get("id") == "qso-noop" and event.get("ok")
                                    for event in emitted))
                peer.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    peer.recvfrom(4096)

    def test_start_surfaces_a_failed_status_replay_without_enabling_tx(self) -> None:
        assistant = WSJTXQsoAssistant()
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_configure.return_value = True
        listener.send_replay.return_value = False
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status()),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            self.bridge.handle({
                "id": "qso-no-replay", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
        self.assertEqual(assistant.snapshot().stage, "suspended")
        self.assertIn("fresh status", emitted[-1]["error"])
        supervisor.operator_start_qso.assert_not_called()
        listener.send_start_dx_tx.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        listener.send_reply.assert_not_called()

    def test_status_request_reports_connected_and_disconnected_states(self) -> None:
        listener = Mock()
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            listener.is_connected.return_value = False
            self.bridge.handle({"id": "disconnected", "method": "wsjtx_request_status"})
            listener.send_replay.assert_not_called()
            listener.is_connected.return_value = True
            listener.send_replay.return_value = True
            self.bridge.handle({"id": "connected", "method": "wsjtx_request_status"})
            listener.send_replay.return_value = False
            self.bridge.handle({"id": "failed", "method": "wsjtx_request_status"})
        self.assertFalse(emitted[0]["result"]["requested"])
        self.assertTrue(emitted[1]["result"]["requested"])
        self.assertIn("Could not request status", emitted[2]["error"])

    def test_cq_during_atomic_request_does_not_send_reply_or_enable_tx(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        assistant.configuration_requested(0.0)
        listener = Mock()
        listener.send_start_dx_tx.return_value = True
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_emit"),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.bridge._on_wsjtx_status(status())
            self.bridge._on_wsjtx_decode(decode("CQ W7PUA CN84"))

        self.assertEqual(
            [call[0] for call in listener.method_calls if call[0].startswith("send_")],
            ["send_start_dx_tx"],
        )
        self.assertEqual(assistant.snapshot().stage, "start_requested")

    def test_unsent_atomic_request_suspends_without_retrying(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL", app_id="WSJT-X", source_addr=SOURCE_ADDR)
        assistant.configuration_requested(0.0)
        listener = Mock()
        listener.send_start_dx_tx.side_effect = [False, True]
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_emit"),
            patch("sidecar.airwsjtx_bridge.threading.Timer") as timer,
        ):
            self.bridge._on_wsjtx_status(status())
            timer.assert_not_called()
            self.bridge._on_wsjtx_status(status())

        listener.send_start_dx_tx.assert_called_once()
        listener.send_enable_tx.assert_not_called()
        listener.send_reply.assert_not_called()
        timer.assert_not_called()
        self.assertEqual(assistant.snapshot().stage, "suspended")
        self.assertIn("Could not send the direct-call request", assistant.snapshot().message)

    def test_inflight_result_after_aircraft_loss_warns_tx_may_be_on(self) -> None:
        self.assistant.mark_start_requested(42)
        listener = Mock()
        with (
            patch.object(self.bridge, "_qso_assistant", self.assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_qso_tracking_viable_locked", return_value=False),
            patch.object(self.bridge, "_emit"),
        ):
            self.bridge._on_wsjtx_start_dx_tx_result(start_result())
        self.assertEqual(self.assistant.snapshot().stage, "suspended")
        self.assertIn("may have enabled Tx", self.assistant.snapshot().message)
        listener.send_halt_tx.assert_not_called()
        listener.send_reply.assert_not_called()
        listener.send_enable_tx.assert_not_called()

    def test_result_after_abort_does_not_restart_the_qso(self) -> None:
        self.assistant.mark_start_requested(42)
        self.assistant.finish(aborted=True)
        listener = Mock()
        with (
            patch.object(self.bridge, "_qso_assistant", self.assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_emit") as emit,
        ):
            self.bridge._on_wsjtx_start_dx_tx_result(start_result())
        self.assertEqual(self.assistant.snapshot().stage, "aborted")
        emit.assert_not_called()
        listener.send_halt_tx.assert_not_called()

    def test_atomic_timeout_is_request_specific_and_never_falls_back(self) -> None:
        self.assistant.mark_start_requested(42)
        listener = Mock()
        with (
            patch.object(self.bridge, "_qso_assistant", self.assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_emit") as emit,
            patch("airwsjtx.wsjtx_qso.time.monotonic", return_value=time.monotonic() + 6),
        ):
            self.bridge._check_qso_start_tx(41)
            self.assertEqual(self.assistant.snapshot().stage, "start_requested")
            emit.assert_not_called()
            self.bridge._check_qso_start_tx(42)
        self.assertEqual(self.assistant.snapshot().stage, "suspended")
        self.assertIn("check Tx", self.assistant.snapshot().message)
        listener.send_start_dx_tx.assert_not_called()
        listener.send_enable_tx.assert_not_called()
        listener.send_reply.assert_not_called()
        listener.send_halt_tx.assert_not_called()

    def test_synchronous_start_result_cannot_arrive_before_request_registration(self) -> None:
        assistant = WSJTXQsoAssistant()
        assistant.arm(dx_call="W7PUA", dx_grid="CN84", my_call="K7MDL",
                      app_id="WSJT-X", source_addr=SOURCE_ADDR)
        assistant.configuration_requested(0.0)
        listener = Mock()

        def respond(**kwargs: object) -> bool:
            self.bridge._on_wsjtx_start_dx_tx_result(start_result(request_id=kwargs["request_id"]))
            return True

        listener.send_start_dx_tx.side_effect = respond
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_qso_request_id", 41),
            patch.object(self.bridge, "_emit"),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.bridge._on_wsjtx_status(status())
        self.assertEqual(assistant.snapshot().stage, "tx_enabled")
        listener.send_start_dx_tx.assert_called_once()

    def test_rejected_local_session_never_sends_a_start(self) -> None:
        assistant = WSJTXQsoAssistant()
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_configure.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        supervisor.operator_start_qso.side_effect = ValueError("Aircraft was unlocked")
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", assistant),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status()),
            patch.object(self.bridge, "_station_a_locator", "CN87"),
            patch.object(self.bridge, "_station_b_locator", "CN84"),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            self.bridge.handle({
                "id": "qso-race", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
            self.bridge._on_wsjtx_status(status())

        listener.send_reply.assert_not_called()
        self.assertEqual(assistant.snapshot().stage, "suspended")
        self.assertTrue(any(event.get("error") == "Aircraft was unlocked" for event in emitted))

    def test_lock_below_2m_then_start_on_2m_uses_live_frequency(self) -> None:
        from airwsjtx.airscout import Aircraft
        from airwsjtx.supervisor import AirWSJTXSupervisor

        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False,
            position_updated_epoch_s=time.time(),
        )
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_configure.return_value = True
        emitted: list[dict] = []
        with (
            patch.multiple(
                self.bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                _aircraft_cache=[ac], _locked_aircraft_id=None, _lock_score=0.0,
                _aircraft_last_update_monotonic=time.monotonic(),
                _wsjtx_band_mhz=50.1, _wsjtx_freq_hz=50_100_000,
                _latest_wsjtx_status=status(freq_hz=144_180_000, dx_grid="CN89"),
                supervisor=AirWSJTXSupervisor(),
                _wsjtx_listener=listener, _qso_assistant=WSJTXQsoAssistant(),
            ),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch.object(self.bridge, "_publish_hamlib_target"),
            patch.object(self.bridge, "_publish_dde_target"),
            patch.object(self.bridge, "_qso_tracking_viable_locked", side_effect=self.real_tracking_viable),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.assertIsNotNone(self.bridge._do_lock(ac.icao24))
            self.assertFalse(self.bridge.supervisor.wsjtx.status.configured)
            self.bridge.handle({
                "id": "qso-after-retune", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN89"},
            })
            self.assertEqual(self.bridge.supervisor.wsjtx.status.frequency_hz, 144_180_000)
            self.assertEqual(self.bridge.supervisor.wsjtx.status.band_mhz, 144.18)
            self.assertEqual(self.bridge.supervisor.session.state, SessionState.ACTIVE_QSO)

        listener.send_configure.assert_called_once_with(
            dx_call="W7PUA", dx_grid="CN89", app_id="WSJT-X", destination=SOURCE_ADDR,
        )
        listener.send_reply.assert_not_called()
        self.assertTrue(any(event.get("ok") is True for event in emitted))

    def test_start_reconfigures_actual_2m_status_without_relocking(self) -> None:
        listener = Mock()
        listener.is_connected.return_value = True
        listener.send_configure.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", WSJTXQsoAssistant()),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status(freq_hz=144_180_000)),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
            patch("sidecar.airwsjtx_bridge.threading.Timer"),
        ):
            self.bridge.handle({
                "id": "qso-2m", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
        supervisor.wsjtx.configure_q65_15s.assert_called_once_with(
            band_mhz=144.18, frequency_hz=144_180_000,
        )
        listener.send_configure.assert_called_once_with(
            dx_call="W7PUA", dx_grid="CN84", app_id="WSJT-X", destination=SOURCE_ADDR,
        )
        self.assertTrue(any(event.get("ok") is True for event in emitted))

    def test_start_below_2m_never_sends_configure(self) -> None:
        listener = Mock()
        listener.is_connected.return_value = True
        supervisor = Mock()
        supervisor.session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", WSJTXQsoAssistant()),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status(freq_hz=143_999_999)),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            self.bridge.handle({
                "id": "qso-low", "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })
        listener.send_configure.assert_not_called()
        supervisor.operator_start_qso.assert_not_called()
        self.assertIn("144 MHz", emitted[-1]["error"])

    def test_start_rpc_never_configures_while_tx_is_enabled(self) -> None:
        listener = Mock()
        listener.is_connected.return_value = True
        session = SimpleNamespace(state=SessionState.PLANE_LOCKED)
        supervisor = Mock()
        supervisor.session = session
        supervisor.wsjtx.is_ready_for_qso.return_value = True
        emitted: list[dict] = []
        with (
            patch.object(self.bridge, "_qso_assistant", WSJTXQsoAssistant()),
            patch.object(self.bridge, "_wsjtx_listener", listener),
            patch.object(self.bridge, "_latest_wsjtx_status", status(tx_enabled=True)),
            patch.object(self.bridge, "supervisor", supervisor),
            patch.object(self.bridge, "_emit", side_effect=emitted.append),
        ):
            self.bridge.handle({
                "id": "qso-2",
                "method": "start_qso",
                "params": {"dx_call": "W7PUA", "dx_grid": "CN84"},
            })

        listener.send_configure.assert_not_called()
        supervisor.operator_start_qso.assert_not_called()
        self.assertIn("Disable WSJT-X Tx", emitted[-1]["error"])

    def test_only_matching_logged_qso_completes_the_app_session(self) -> None:
        from sidecar import airwsjtx_bridge as bridge

        accept_start(self.assistant)
        supervisor = Mock()
        supervisor.session.state = SessionState.ACTIVE_QSO
        emitted: list[dict] = []
        with (
            patch.object(bridge, "_qso_assistant", self.assistant),
            patch.object(bridge, "supervisor", supervisor),
            patch.object(bridge, "_emit", side_effect=emitted.append),
        ):
            bridge._on_wsjtx_qso_logged(WSJTXQsoLogged(
                "WSJT-X", "K1AAA", "FN31", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR
            ))
            supervisor.finish_qso.assert_not_called()
            bridge._on_wsjtx_qso_logged(WSJTXQsoLogged(
                "WSJT-X", "W7PUA", "CN84", 1_296_150_000, "Q65", "-10", "-12", SOURCE_ADDR
            ))

        supervisor.finish_qso.assert_called_once_with(abort=False)
        self.assertEqual(self.assistant.snapshot().stage, "logged")


if __name__ == "__main__":
    unittest.main()
