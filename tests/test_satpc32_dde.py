import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from ctypes import wintypes
from unittest.mock import Mock, patch

from airwsjtx.airscout import Aircraft
from airwsjtx.satpc32_dde import ITEM, NO_TARGET, TOPIC, SatPC32DDE, _api, format_target
from sidecar import airwsjtx_bridge as bridge

SUFFIX = " UP0 UMUSB DN0 DMUSB MA0.0"

class DDETests(unittest.TestCase):
    def setUp(self):
        self.output_patch = patch.multiple(bridge, _output_mode="dde", _rotator_control_enabled=True)
        self.output_patch.start()
        self.addCleanup(self.output_patch.stop)

    def test_complete_record_supports_following_field_delimiters(self):
        # Legacy delimiter-based clients need UP to terminate EL, even when
        # they do not consume radio fields. Do not equate this with testing
        # the proprietary PST executable's implementation.
        text = format_target("ACA314", 348.4, 0.5)
        name, rest = text[2:].split(" AZ", 1)
        azimuth, rest = rest.split(" EL", 1)
        elevation, rest = rest.split(" UP", 1)
        self.assertEqual((name, float(azimuth), float(elevation)), ("ACA314", 348.4, 0.5))
        self.assertEqual(rest, "0 UMUSB DN0 DMUSB MA0.0")

    def test_wire_format_and_validation(self):
        self.assertEqual(format_target("ACA314", 56.74, 6.84), "SNACA314 AZ56.7 EL6.8" + SUFFIX)
        self.assertEqual(format_target("abc123", 360, 0), "SNabc123 AZ0.0 EL0.0" + SUFFIX)
        self.assertEqual(format_target("QXE2033", 348.4, 12.4), "SNQXE2033 AZ348.4 EL12.4" + SUFFIX)
        self.assertEqual(format_target("NORTH", 359.96, 0), "SNNORTH AZ0.0 EL0.0" + SUFFIX)
        for az, el in ((float("nan"), 1), (10, float("inf")), (-1, 0), (0, -1), (0, 91)):
            with self.subTest(az=az, el=el), self.assertRaises(ValueError):
                format_target("TEST", az, el)
        for name in ("", "ACA 314", "ACA!314", "\u00c5CA314", "A" * 33):
            with self.subTest(name=name), self.assertRaises(ValueError):
                format_target(name, 10, 5)

    def test_expiry_and_clear_do_not_repeat_stale_coordinates(self):
        server = SatPC32DDE()
        with patch("airwsjtx.satpc32_dde.time.monotonic", return_value=100):
            server.update("ACA314", 20, 10, valid_for_s=5)
            self.assertIn("AZ20.0", server.status()["target"])
        with patch("airwsjtx.satpc32_dde.time.monotonic", return_value=106):
            self.assertEqual(server.status()["target"], NO_TARGET)
        server.clear()
        self.assertEqual(server.status()["target"], NO_TARGET)
        self.assertFalse(server.status()["enabled"])

    def test_non_windows_fails_explicitly(self):
        with patch("airwsjtx.satpc32_dde.sys.platform", "linux"), self.assertRaisesRegex(RuntimeError, "Windows"):
            SatPC32DDE().start()

    def test_bridge_freshness_and_control_gates(self):
        ac = Aircraft("abc123", "TEST1", 47.5, -122.4, 10000, 200, 0, 0, False,
                      position_updated_epoch_s=100)
        for source, now, enabled, manual, elevation, publish in (
            ("rtlsdr", 101, True, False, 5, True),
            ("rtlsdr", 219.9, True, False, 5, True),
            ("rtlsdr", 220, True, False, 5, False),
            ("opensky", 120, True, False, 5, True),
            ("opensky", 136, True, False, 5, False),
            ("rtlsdr", 101, False, False, 5, False),
            ("rtlsdr", 101, True, True, 5, False),
            ("rtlsdr", 101, True, False, -1, False),
        ):
            server = Mock()
            with self.subTest(source=source, now=now, enabled=enabled, manual=manual, el=elevation), \
                    patch.multiple(bridge, _satpc32_dde=server, _active_source=source,
                                   _manual_pointing=manual, _rotator_control_enabled=enabled), \
                    patch.object(bridge.time, "time", return_value=now), \
                    patch.dict(bridge._source_poll_intervals, {"opensky": 30}):
                bridge._publish_dde_target(ac, {"az_a": 20, "el_a": elevation})
                self.assertEqual(server.update.call_count, int(publish))
                self.assertEqual(server.clear.call_count, int(not publish))

    def test_local_position_visibility_and_dde_expire_at_120_seconds(self):
        from airwsjtx.adsb import ADSBDecoder
        from tests.test_adsb import EVEN, ODD

        decoder = ADSBDecoder()
        decoder.consume(f"*{EVEN};", now=0, epoch=1000)
        decoder.consume(f"*{ODD};", now=1, epoch=1001)
        server, rotor = SatPC32DDE(), Mock()
        rotor.current_position = None
        with patch.multiple(
            bridge, _satpc32_dde=server, _active_source="rtlsdr",
            _locked_aircraft_id="40621d", _manual_aircraft_lock=True,
            _station_a_locator="AA00", _station_b_locator="AA01",
            _rotator=rotor, _rotator_control_enabled=True, _manual_pointing=False,
        ), patch.object(bridge, "locator_to_latlon", return_value=(52, 3.9)), \
                patch.object(bridge.time, "time", return_value=1016) as clock, \
                patch.object(bridge.time, "monotonic", return_value=1016) as mono_clock, \
                patch.object(bridge, "_emit"):
            for age in (15, 30, 119.9, 120, 120.1):
                clock.return_value = 1001 + age
                mono_clock.return_value = 1001 + age
                snapshot = decoder.snapshot(now=1 + age)
                self.assertEqual(snapshot[0].has_position, age < 120)
                bridge._update_rotator_for_locked(snapshot)
                if age < 120:
                    self.assertNotEqual(server.status()["target"], NO_TARGET)
                    rotor.set_position.assert_not_called()
                    rotor.reset_mock()
                else:
                    self.assertEqual(server.status()["target"], NO_TARGET)
                    rotor.set_position.assert_not_called()
            decoder.consume(f"*{EVEN};", now=122, epoch=1122)
            decoder.consume(f"*{ODD};", now=123, epoch=1123)
            clock.return_value = 1123
            mono_clock.return_value = 1123
            bridge._update_rotator_for_locked(decoder.snapshot(now=123))
            self.assertNotEqual(server.status()["target"], NO_TARGET)
            rotor.set_position.assert_not_called()

    def test_selected_aircraft_geometry_to_exact_wire_uses_my_station_not_dx(self):
        # A=(0,0), B=(0,2), aircraft=(1,0): due north from A, NW from B.
        # Independent cardinal bearing is 0; repository 4/3-Earth EL rounds to 4.8.
        ac = Aircraft("abc123", " QXE2033 ", 1, 0, 10000, 200, 0, 0, False,
                      position_updated_epoch_s=100)
        server = SatPC32DDE(service="not-started-test")
        with patch.multiple(
            bridge, _satpc32_dde=server, _active_source="rtlsdr",
            _locked_aircraft_id=ac.icao24, _station_a_locator="AA00",
            _station_b_locator="AA01", _station_a_alt_m=0, _station_b_alt_m=0,
            _rotator=None, _rotator_control_enabled=True, _manual_pointing=False,
            _local_lock_waiting_for_window=True,
        ), patch.object(bridge, "locator_to_latlon", side_effect=[(0, 0), (0, 2)]), \
                patch.object(bridge.time, "time", return_value=101), \
                patch.object(bridge, "_emit") as emit:
            bridge._update_rotator_for_locked([ac])
            self.assertEqual(server.status()["target"], "SNQXE2033 AZ0.0 EL4.8" + SUFFIX)
            data = emit.call_args.args[0]["data"]
            self.assertEqual((data["az_a"], data["el_a"]), (0, 4.8))
            self.assertEqual((data["az_b"], data["el_b"]), (296.6, 1.5))
            status = bridge._dde_status()
            self.assertEqual(status["station_grid"], "AA00")
            self.assertEqual(status["reference"], "MY station (A)")
            self.assertEqual(status["name_field"], "flight identifier/callsign, or ICAO address if unavailable")

    def test_flight_identifier_updates_and_invalid_callsign_falls_back_to_icao(self):
        ac = Aircraft("abc123", "  ", 1, 0, 10000, 200, 0, 0, False,
                      position_updated_epoch_s=100)
        server = SatPC32DDE()
        with patch.multiple(bridge, _satpc32_dde=server, _active_source="rtlsdr",
                            _manual_pointing=False, _rotator_control_enabled=True), \
                patch.object(bridge.time, "time", return_value=101):
            bridge._publish_dde_target(ac, {"az_a": 348.4, "el_a": 12.4,
                                          "az_b": 293.8, "el_b": 14.7})
            self.assertEqual(server.status()["target"], "SNabc123 AZ348.4 EL12.4" + SUFFIX)
            ac.callsign = " aca 7038 "
            bridge._publish_dde_target(ac, {"az_a": 349.8, "el_a": 9.1})
            self.assertEqual(server.status()["target"], "SNACA7038 AZ349.8 EL9.1" + SUFFIX)
            self.assertEqual(ac.callsign, " aca 7038 ")
            for invalid in ("???", "\u00c5CA314", "A" * 33):
                ac.callsign = invalid
                bridge._publish_dde_target(ac, {"az_a": 349.8, "el_a": 9.1})
                self.assertEqual(server.status()["target"], "SNabc123 AZ349.8 EL9.1" + SUFFIX)

    def test_feed_update_republishes_selected_aircraft_after_it_moves(self):
        ac = Aircraft("abc123", "QXE2033", 1, 0, 10000, 200, 0, 0, False,
                      position_updated_epoch_s=100)
        server = SatPC32DDE()
        with patch.multiple(
            bridge, _satpc32_dde=server, _active_source="rtlsdr",
            _locked_aircraft_id=ac.icao24, _aircraft_cache=[ac],
            _aircraft_last_update_monotonic=None, _station_a_locator="AA00",
            _station_b_locator="AA01", _station_a_alt_m=0, _station_b_alt_m=0,
            _rotator=None, _rotator_control_enabled=True, _manual_pointing=False,
            _local_lock_waiting_for_window=True,
        ), patch.object(bridge, "locator_to_latlon",
                        side_effect=lambda grid: (0, 0) if grid == "AA00" else (0, 2)), \
                patch.object(bridge.time, "time", return_value=101), \
                patch.object(bridge, "_aircraft_matches_filters", return_value=True), \
                patch.object(bridge, "_emit"):
            bridge._emit_aircraft_update()
            self.assertEqual(server.status()["target"], "SNQXE2033 AZ0.0 EL4.8" + SUFFIX)
            ac.lat, ac.lon = 0, 1
            ac.position_updated_epoch_s = 101
            bridge._emit_aircraft_update()
            self.assertEqual(server.status()["target"], "SNQXE2033 AZ90.0 EL4.8" + SUFFIX)
            selected = Aircraft("def456", "ACA314", 0, 1, 10000, 200, 0, 0, False,
                                position_updated_epoch_s=101)
            bridge._aircraft_cache = [ac, selected]
            bridge._locked_aircraft_id = selected.icao24
            bridge._emit_aircraft_update()
            self.assertEqual(server.status()["target"], "SNACA314 AZ90.0 EL4.8" + SUFFIX)

    def test_unlock_stop_control_and_manual_mode_clear_output(self):
        for method, params in (
            ("unlock_aircraft", {}), ("rotator_stop", {}),
            ("set_pointing_mode", {"mode": "manual"}),
            ("set_rotator_control", {"enabled": False}),
        ):
            server = Mock()
            with self.subTest(method=method), patch.multiple(
                bridge, _satpc32_dde=server, _rotator=None, _manual_pointing=False,
                _rotator_control_enabled=True, _locked_aircraft_id=None,
                _local_lock_waiting_for_window=False, _lock_score=0, supervisor=Mock(),
            ), patch.object(bridge, "_emit"):
                bridge.handle({"id": "test", "method": method, "params": params})
                if method == "rotator_stop":
                    server.stop.assert_called_once()
                    self.assertIsNone(bridge._satpc32_dde)
                else:
                    server.clear.assert_called_once()

    def test_rpc_opt_in_and_disable(self):
        with patch.object(bridge, "_satpc32_dde", None), patch.object(bridge, "_output_mode", "off"), \
                patch.object(bridge, "SatPC32DDE") as factory, patch.object(bridge, "_emit"):
            bridge.handle({"id": "test", "method": "satpc32_dde_status", "params": {}})
            factory.assert_not_called()
            bridge.handle({"id": "test", "method": "satpc32_dde_set", "params": {"enabled": True}})
            factory.return_value.start.assert_called_once()
            bridge.handle({"id": "test", "method": "satpc32_dde_set", "params": {"enabled": False}})
            factory.return_value.stop.assert_called_once()
            self.assertIsNone(bridge._satpc32_dde)

    def test_enable_without_position_publishes_first_fresh_selected_update(self):
        ac = Aircraft("abc123", "N790TB", None, None, 10000, 200, 0, 0, False)
        server = SatPC32DDE()
        with patch.multiple(
            bridge, _satpc32_dde=None, _output_mode="off", _active_source="rtlsdr",
            _locked_aircraft_id=ac.icao24, _station_a_locator="AA00",
            _station_b_locator="AA01", _rotator=None, _rotator_control_enabled=True,
            _manual_pointing=False, _manual_aircraft_lock=True,
        ), patch.object(bridge, "_current_aircraft_snapshot", return_value=[ac]), \
                patch.object(bridge.time, "time", return_value=107), \
                patch.object(bridge, "scatter_geometry",
                             return_value={"az_a": 20, "el_a": 4.8, "scatter_score": 0}), \
                patch.object(bridge, "_locked_aircraft_info", return_value={}), \
                patch.object(bridge, "SatPC32DDE", return_value=server), \
                patch.object(server, "start"), patch.object(bridge, "_emit"):
            bridge.handle({"id": "test", "method": "satpc32_dde_set",
                           "params": {"enabled": True}})
            self.assertEqual(server.status()["target"], NO_TARGET)
            ac.lat, ac.lon, ac.position_updated_epoch_s = 1, 0, 107
            bridge._update_rotator_for_locked([ac])
            self.assertEqual(server.status()["target"], "SNN790TB AZ20.0 EL4.8" + SUFFIX)

    def test_enable_failure_is_reported_without_keeping_partial_server(self):
        with patch.object(bridge, "_satpc32_dde", None), \
                patch.object(bridge, "SatPC32DDE") as factory, patch.object(bridge, "_emit") as emit:
            factory.return_value.start.side_effect = RuntimeError("Service already running")
            bridge.handle({"id": "test", "method": "satpc32_dde_set", "params": {"enabled": True}})
            self.assertIsNone(bridge._satpc32_dde)
            reply = emit.call_args.args[0]
            self.assertFalse(reply["ok"])
            self.assertIn("Service already running", reply["error"])

    def test_enable_publishes_existing_position_without_waiting_for_feed_or_moving_hamlib(self):
        for age, control, manual, elevation, selected, positioned, expected in (
            (7, True, False, 4.8, True, True, True),
            (14.9, True, False, 4.8, True, True, True),
            (119.9, True, False, 4.8, True, True, True),
            (120, True, False, 4.8, True, True, False),
            (121, True, False, 4.8, True, True, False),
            (7, False, False, 4.8, True, True, True),
            (7, True, True, 4.8, True, True, False),
            (7, True, False, -1, True, True, False),
            (7, True, False, 4.8, False, True, False),
            (7, True, False, 4.8, True, False, False),
        ):
            ac = Aircraft("abc123", "N790TB", 1 if positioned else None,
                          0 if positioned else None, 10000, 200, 0, 0, False,
                          position_updated_epoch_s=100)
            server, rotor = SatPC32DDE(), Mock()
            with self.subTest(age=age, control=control, manual=manual, elevation=elevation,
                              selected=selected, positioned=positioned), patch.multiple(
                bridge, _satpc32_dde=None, _output_mode="off", _active_source="rtlsdr",
                _locked_aircraft_id=ac.icao24 if selected else None,
                _station_a_locator="AA00", _station_b_locator="AA01",
                _rotator=rotor, _rotator_control_enabled=control, _manual_pointing=manual,
            ), patch.object(bridge, "_current_aircraft_snapshot", return_value=[ac]), \
                    patch.object(bridge.time, "time", return_value=100 + age), \
                    patch.object(bridge, "scatter_geometry",
                                 return_value={"az_a": 20, "el_a": elevation}), \
                    patch.object(bridge, "SatPC32DDE", return_value=server), \
                    patch.object(server, "start"), patch.object(bridge, "_emit") as emit:
                bridge.handle({"id": "test", "method": "satpc32_dde_set",
                               "params": {"enabled": True}})
                reply = emit.call_args.args[0]
                self.assertTrue(reply["ok"])
                target = reply["result"]["target"]
                self.assertEqual(target, "SNN790TB AZ20.0 EL4.8" + SUFFIX if expected else NO_TARGET)
                rotor.set_position.assert_not_called()
                self.assertEqual(bridge._locked_aircraft_id, ac.icao24 if selected else None)
                self.assertEqual(emit.call_count, 1)

    def test_local_cpr_gap_clears_dde_even_while_selection_is_retained(self):
        ac = Aircraft("abc123", "TEST1", None, None, 10000, 200, 0, 0, False)
        server = Mock()
        with patch.multiple(
            bridge, _satpc32_dde=server, _active_source="rtlsdr",
            _locked_aircraft_id=ac.icao24, _aircraft_cache=[ac],
            _aircraft_last_update_monotonic=None, _station_a_locator="CN87",
            _station_b_locator="CN89", _local_lock_waiting_for_window=True,
        ), patch.object(bridge, "_emit"), patch.object(bridge, "_do_unlock") as unlock, \
                patch.object(bridge, "_update_rotator_for_locked") as track:
            bridge._emit_aircraft_update()
            server.clear.assert_called_once()
            server.update.assert_not_called()
            unlock.assert_not_called()
            track.assert_not_called()

    def test_manual_selection_survives_feed_gap_filters_and_recovers_dde(self):
        from airwsjtx.state_machine import SessionState

        for source in ("opensky", "rtlsdr"):
            ac = Aircraft("abc123", "ACA314", 48.5, -123, 10000, 200, 0, 0, False,
                          last_seen=1000, position_updated_epoch_s=1000)
            server = SatPC32DDE()
            supervisor = Mock()
            supervisor.session.state = SessionState.IDLE
            with self.subTest(source=source), patch.multiple(
                bridge, _satpc32_dde=server, _active_source=source,
                _locked_aircraft_id=None, _aircraft_cache=[ac], supervisor=supervisor,
                _aircraft_last_update_monotonic=1000, _station_a_locator="CN87",
                _station_b_locator="CN89", _rotator=None, _rotator_control_enabled=True,
                _manual_pointing=False, _manual_aircraft_lock=False,
                _manual_lock_source=None, _manual_lock_last_seen=0,
                _local_lock_waiting_for_window=False, _lock_score=0,
            ), patch("airwsjtx.supervisor.AirWSJTXSupervisor", return_value=supervisor), \
                    patch.object(bridge.time, "monotonic", return_value=1000) as clock, \
                    patch.object(bridge.time, "time", return_value=1000), \
                    patch.object(bridge, "_aircraft_matches_filters", return_value=False), \
                    patch.object(bridge, "_emit"):
                self.assertIsNotNone(bridge._do_lock(ac.icao24))
                initial = server.status()["target"]
                self.assertTrue(initial.startswith("SNACA314 AZ"))
                bridge._aircraft_cache = []
                clock.return_value = 1001
                bridge._emit_aircraft_update()
                self.assertEqual(bridge._locked_aircraft_id, ac.icao24)
                self.assertEqual(server.status()["target"], NO_TARGET)
                ac.lat = None
                bridge._aircraft_cache = [ac]
                bridge._emit_aircraft_update()
                self.assertEqual(bridge._locked_aircraft_id, ac.icao24)
                self.assertEqual(server.status()["target"], NO_TARGET)
                ac.lat, ac.lon, ac.last_seen = 48.4, -122.8, 1002
                clock.return_value = 1002
                bridge._emit_aircraft_update()
                self.assertEqual(bridge._locked_aircraft_id, ac.icao24)
                self.assertNotEqual(server.status()["target"], initial)
                self.assertTrue(server.status()["target"].startswith("SNACA314 AZ"))
                bridge._aircraft_cache = []
                clock.return_value = 1123
                bridge._emit_aircraft_update()
                self.assertEqual(bridge._locked_aircraft_id, ac.icao24 if source == "rtlsdr" else None)
                clock.return_value = 1182
                bridge._emit_aircraft_update()
                self.assertEqual(bridge._locked_aircraft_id, ac.icao24 if source == "rtlsdr" else None)
                clock.return_value = 1182.1
                bridge._emit_aircraft_update()
                self.assertIsNone(bridge._locked_aircraft_id)
                self.assertEqual(server.status()["target"], NO_TARGET)

    def test_manual_selection_terminal_conditions_and_stale_pointing(self):
        ac = Aircraft("abc123", "ACA314", 48.5, -123, 10000, 200, 0, 0, False,
                      last_seen=1000, position_updated_epoch_s=900)
        for source, ground, released in (("opensky", False, False),
                                          ("rtlsdr", False, True),
                                          ("opensky", True, True)):
            server, rotor = SatPC32DDE(), Mock()
            rotor.current_position = None
            with self.subTest(source=source, ground=ground), patch.multiple(
                bridge, _satpc32_dde=server, _active_source=source,
                _locked_aircraft_id=ac.icao24, _aircraft_cache=[ac], supervisor=Mock(),
                _aircraft_last_update_monotonic=1000, _station_a_locator="CN87",
                _station_b_locator="CN89", _rotator=rotor, _rotator_control_enabled=True,
                _manual_pointing=False, _manual_aircraft_lock=True,
                _manual_lock_source="opensky", _manual_lock_last_seen=1000,
                _local_lock_waiting_for_window=False, _lock_score=0,
            ), patch.object(ac, "on_ground", ground), \
                    patch.object(bridge.time, "monotonic", return_value=1000), \
                    patch.object(bridge.time, "time", return_value=1000), \
                    patch.object(bridge, "_emit"):
                bridge._emit_aircraft_update()
                self.assertEqual(bridge._locked_aircraft_id is None, released)
                self.assertEqual(server.status()["target"], NO_TARGET)
                rotor.set_position.assert_not_called()


@unittest.skipUnless(sys.platform == "win32", "Native Windows DDEML test")
class NativeDDETests(unittest.TestCase):
    def test_persistent_client_reconnects_and_resubscribes_after_server_restart(self):
        from concurrent.futures import ThreadPoolExecutor
        service = "AirWSJTX-Test-" + uuid.uuid4().hex
        api, callback_type = _api()
        instance = wintypes.DWORD()
        state = {"conversation": None, "reconnect": False, "disconnects": 0}
        advisories = []
        handles = []

        def callback(kind, fmt, conversation, topic, item, data, arg1, arg2):
            if kind == 0x80C2:
                state["conversation"] = None
                state["disconnects"] += 1
            elif kind == 0x80A2:  # XTYP_REGISTER; connect outside the callback
                if handles and api.DdeCmpStringHandles(topic, handles[0]) == 0:
                    state["reconnect"] = True
            elif kind & ~0x000C == 0x4010:
                size = api.DdeGetData(data, None, 0, 0)
                buffer = ctypes.create_string_buffer(size)
                api.DdeGetData(data, buffer, size, 0)
                advisories.append(buffer.value.decode("ascii"))
                return 0x8000
            return 0

        cb = callback_type(callback)
        self.assertEqual(api.DdeInitializeW(ctypes.byref(instance), cb, 0x10, 0), 0)
        servers = []
        try:
            handles = [api.DdeCreateStringHandleW(instance, value, 1200)
                       for value in (service, TOPIC, ITEM)]
            message = wintypes.MSG()
            executor = ThreadPoolExecutor(max_workers=1)

            def lifecycle(operation):
                future = executor.submit(operation)
                deadline = time.monotonic() + 10
                while not future.done() and time.monotonic() < deadline:
                    while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        api.TranslateMessage(ctypes.byref(message))
                        api.DispatchMessageW(ctypes.byref(message))
                    time.sleep(0.01)
                future.result(timeout=1)

            def pump_until(predicate):
                deadline = time.monotonic() + 5
                while not predicate() and time.monotonic() < deadline:
                    while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        api.TranslateMessage(ctypes.byref(message))
                        api.DispatchMessageW(ctypes.byref(message))
                    if state["reconnect"] and state["conversation"] is None:
                        state["reconnect"] = False
                        conversation = api.DdeConnect(instance, handles[0], handles[1], None)
                        if conversation:
                            state["conversation"] = conversation
                            result = wintypes.DWORD()
                            self.assertTrue(api.DdeClientTransaction(
                                None, 0, conversation, handles[2], 1, 0x1038,
                                2000, ctypes.byref(result)))
                    time.sleep(0.01)
                self.assertTrue(predicate())

            for index in range(2):
                server = SatPC32DDE(service=service)
                servers.append(server)
                lifecycle(server.start)
                server.update("TEST" + str(index), 20 + index, 11, valid_for_s=30)
                expected = format_target("TEST" + str(index), 20 + index, 11)
                pump_until(lambda: expected in advisories)
                lifecycle(server.stop)
                pump_until(lambda: state["disconnects"] == index + 1)
                self.assertIsNone(state["conversation"])
        finally:
            if state["conversation"]:
                api.DdeDisconnect(state["conversation"])
            for server in servers:
                lifecycle(server.stop)
            executor.shutdown()
            for handle in handles:
                api.DdeFreeStringHandle(instance, handle)
            api.DdeUninitialize(instance)

    @unittest.skipUnless(os.environ.get("VCINSTALLDIR"), "MSVC environment required for independent C clients")
    def test_native_c_x86_and_x64_request_advise_clients(self):
        setup = Path(os.environ["VCINSTALLDIR"]) / "Auxiliary" / "Build" / "vcvarsall.bat"
        source = Path(__file__).with_name("dde_test_client.c")
        for arch in ("x86", "amd64"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as directory:
                binary = Path(directory) / "dde_client.exe"
                obj = Path(directory) / "dde_client.obj"
                command = (f'call "{setup}" {arch} >nul && cl /nologo "{source}" '
                           f'/Fo:"{obj}" /Fe:"{binary}" user32.lib')
                batch = Path(directory) / "build.cmd"
                batch.write_text("@echo off\n" + command + "\n", encoding="ascii")
                compiled = subprocess.run([os.environ["ComSpec"], "/c", str(batch)],
                                          capture_output=True, text=True, timeout=30)
                self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
                server = SatPC32DDE(service="AirWSJTX-Test-" + uuid.uuid4().hex)
                try:
                    server.start()
                    server.update("ACA314", 348.4, 0.5, valid_for_s=30)
                    result = subprocess.run([str(binary), server.service], capture_output=True,
                                            text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("requests=100", result.stdout)
                    self.assertIn("failures=0", result.stdout)
                finally:
                    server.stop()

    def test_real_request_advise_and_shutdown_on_isolated_service(self):
        # Never register SatPC32: a live PST Rotator must not see test targets.
        service = "AirWSJTX-Test-" + uuid.uuid4().hex
        server = SatPC32DDE(service=service)
        server.start()
        api, callback_type = _api()
        instance = wintypes.DWORD()
        advisories = []
        advisories_by_conversation = {}
        raw_advisories = []
        callback_errors = []

        def callback(kind, fmt, conversation, topic, item, data, arg1, arg2):
            if kind & ~0x000C == 0x4010:  # XTYP_ADVDATA, optional ACKREQ/NODATA
                size = api.DdeGetData(data, None, 0, 0)
                if size == 0:
                    callback_errors.append(f"DdeGetData: 0x{api.DdeGetLastError(instance):04x}")
                    return 0
                buffer = ctypes.create_string_buffer(size)
                copied = api.DdeGetData(data, buffer, size, 0)
                if copied != size:
                    callback_errors.append(f"DdeGetData copy: 0x{api.DdeGetLastError(instance):04x}")
                raw_advisories.append(buffer.raw)
                text = buffer.value.decode("ascii")
                advisories.append(text)
                advisories_by_conversation.setdefault(conversation, []).append(text)
                return 0x8000  # DDE_FACK
            return 0

        cb = callback_type(callback)
        self.assertEqual(api.DdeInitializeW(ctypes.byref(instance), cb, 0x10, 0), 0)
        handles = []
        conversation = None
        listener_conversation = None
        try:
            handles = [api.DdeCreateStringHandleW(instance, s, 1200) for s in (service, TOPIC, ITEM)]
            conversation = api.DdeConnect(instance, handles[0], handles[1], None)
            self.assertTrue(conversation)
            listener_conversation = api.DdeConnect(instance, handles[0], handles[1], None)
            self.assertTrue(listener_conversation)
            result = wintypes.DWORD()
            for subscription in (conversation, listener_conversation):
                self.assertTrue(api.DdeClientTransaction(None, 0, subscription, handles[2], 1,
                                                        0x1038, 2000, ctypes.byref(result)))

            def request():
                result = wintypes.DWORD()
                data = api.DdeClientTransaction(None, 0, conversation, handles[2], 1, 0x20B0,
                                               2000, ctypes.byref(result))
                self.assertTrue(data)
                try:
                    size = api.DdeGetData(data, None, 0, 0)
                    self.assertGreater(size, 0, f"DdeGetData handle={data:#x} result={result.value:#x}: 0x{api.DdeGetLastError(instance):04x}")
                    buffer = ctypes.create_string_buffer(size)
                    self.assertEqual(api.DdeGetData(data, buffer, size, 0), size)
                    expected = server.status()["target"].encode("ascii") + b"\0"
                    self.assertEqual(buffer.raw, expected)
                    return buffer.value.decode("ascii")
                finally:
                    api.DdeFreeDataHandle(data)

            self.assertEqual(request(), NO_TARGET)
            result = wintypes.DWORD()
            self.assertTrue(api.DdeClientTransaction(None, 0, conversation, handles[2], 1,
                                                    0x1038, 2000, ctypes.byref(result)))
            server.update("ACA314", 348.4, 0.5, valid_for_s=30)
            self.assertEqual(request(), "SNACA314 AZ348.4 EL0.5" + SUFFIX)
            message = wintypes.MSG()

            def wait_for_advice(text, timeout=3):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and text not in advisories_by_conversation.get(listener_conversation, []):
                    while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                        api.TranslateMessage(ctypes.byref(message))
                        api.DispatchMessageW(ctypes.byref(message))
                    time.sleep(0.01)
                self.assertIn(text, advisories_by_conversation.get(listener_conversation, []))
                self.assertIn(text.encode("ascii") + b"\0", raw_advisories)
                self.assertEqual(callback_errors, [])

            wait_for_advice("SNACA314 AZ348.4 EL0.5" + SUFFIX)
            # Keep polling one subscribed conversation faster than the quiet
            # interval; an unrelated advise-only client must still get updates.
            server.update("QXE2033", 349.1, 0.5, valid_for_s=30)
            expected = "SNQXE2033 AZ349.1 EL0.5" + SUFFIX
            deadline = time.monotonic() + 0.7
            while time.monotonic() < deadline and expected not in advisories_by_conversation.get(listener_conversation, []):
                self.assertEqual(request(), expected)
                while api.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                    api.TranslateMessage(ctypes.byref(message))
                    api.DispatchMessageW(ctypes.byref(message))
                time.sleep(0.05)
            self.assertIn(expected, advisories_by_conversation.get(listener_conversation, []))
            advisories.clear()
            advisories_by_conversation[listener_conversation].clear()
            server.update("ACA314", 348.4, 0.5, valid_for_s=30)
            wait_for_advice("SNACA314 AZ348.4 EL0.5" + SUFFIX)
            advisories.clear()
            advisories_by_conversation[listener_conversation].clear()
            # A late/reset hot-link client must receive a periodic current value
            # even if rounded angles have not changed.
            wait_for_advice("SNACA314 AZ348.4 EL0.5" + SUFFIX)
            for index in range(50):
                az = 348.4 + index / 10
                el = 0.5 + index / 10
                server.update("ACA314", az, el, valid_for_s=30)
                expected = f"SNACA314 AZ{az:.1f} EL{el:.1f}" + SUFFIX
                self.assertEqual(request(), expected)
                wait_for_advice(expected)
                self.assertTrue(server.status()["enabled"])
            # Same selected name, changed position: an existing hot link must update.
            server.update("QXE2033", 350.2, 13.1, valid_for_s=0.4)
            self.assertEqual(request(), "SNQXE2033 AZ350.2 EL13.1" + SUFFIX)
            wait_for_advice("SNQXE2033 AZ350.2 EL13.1" + SUFFIX)

            advisories.clear()
            advisories_by_conversation[listener_conversation].clear()
            wait_for_advice(NO_TARGET)
            self.assertEqual(request(), NO_TARGET)
            api.DdeDisconnect(conversation)
            conversation = None
            api.DdeDisconnect(listener_conversation)
            listener_conversation = None
        finally:
            if conversation:
                api.DdeDisconnect(conversation)
            if listener_conversation:
                api.DdeDisconnect(listener_conversation)
            for handle in handles:
                api.DdeFreeStringHandle(instance, handle)
            api.DdeUninitialize(instance)
            server.stop()

    def test_isolated_service_conflict(self):
        service = "AirWSJTX-Test-" + uuid.uuid4().hex
        server = SatPC32DDE(service=service)
        try:
            server.start()
            with self.assertRaisesRegex(RuntimeError, "already running"):
                SatPC32DDE(service=service).start()
        finally:
            server.stop()
