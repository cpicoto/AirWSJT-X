import unittest
from unittest.mock import Mock, patch

from airwsjtx.airscout import Aircraft
from sidecar import airwsjtx_bridge as bridge


class OutputModeTests(unittest.TestCase):
    def setUp(self):
        self.rotor = Mock()
        self.rotor.connected = True
        self.server = Mock()
        self.server.status.return_value = {"enabled": True}
        self.ac = Aircraft("abc123", "N790TB", 1, 0, 10000, 200, 0, 0, False,
                           position_updated_epoch_s=100)
        self.context = patch.multiple(
            bridge, _output_mode="off", _rotator_control_enabled=False,
            _satpc32_dde=None, _rotator=self.rotor, _manual_pointing=False,
            _locked_aircraft_id=None, _active_source="rtlsdr",
        )
        self.context.start()
        self.addCleanup(self.context.stop)

    def rpc(self, method, **params):
        with patch.object(bridge, "_emit") as emit:
            bridge.handle({"id": "test", "method": method, "params": params})
            return emit.call_args.args[0]

    def test_transitions_disable_old_output_before_start_and_do_not_move_on_switch(self):
        with patch.object(bridge, "SatPC32DDE", return_value=self.server), \
                patch.object(bridge.time, "time", return_value=103):
            for mode in ("off", "hamlib", "dde", "hamlib", "off"):
                reply = self.rpc("rotator_output_set", mode=mode)
                self.assertTrue(reply["ok"])
                self.assertEqual(reply["result"]["mode"], mode)
                bridge._publish_hamlib_target(self.ac, {"az_a": 20, "el_a": 11})
                bridge._publish_dde_target(self.ac, {"az_a": 20, "el_a": 11})
                self.assertEqual(self.rotor.set_position.call_count, int(mode == "hamlib"))
                self.assertEqual(self.server.update.call_count, int(mode == "dde"))
                self.rotor.reset_mock()
                self.server.reset_mock()
            self.assertIsNone(bridge._satpc32_dde)
            self.assertEqual(bridge._output_mode, "off")

    def test_dde_is_stopped_before_hamlib_mode_is_set(self):
        with patch.object(bridge, "SatPC32DDE", return_value=self.server):
            self.rpc("rotator_output_set", mode="dde")
            self.server.stop.side_effect = lambda: self.assertEqual(bridge._output_mode, "off")
            self.rpc("rotator_output_set", mode="hamlib")
            self.server.stop.assert_called_once()
            self.rotor.set_position.assert_not_called()

    def test_failed_switch_stays_off_and_reports_error(self):
        self.rpc("rotator_output_set", mode="hamlib")
        self.server.start.side_effect = RuntimeError("DDE already running")
        with patch.object(bridge, "SatPC32DDE", return_value=self.server):
            reply = self.rpc("rotator_output_set", mode="dde")
        self.assertFalse(reply["ok"])
        self.assertEqual(bridge._output_mode, "off")
        self.assertIsNone(bridge._satpc32_dde)
        self.rotor.set_position.assert_not_called()

    def test_stop_failure_cannot_enable_other_output(self):
        with patch.object(bridge, "SatPC32DDE", return_value=self.server):
            self.rpc("rotator_output_set", mode="dde")
            self.server.stop.side_effect = RuntimeError("Stop timed out")
            reply = self.rpc("rotator_output_set", mode="hamlib")
            self.assertFalse(reply["ok"])
            self.assertEqual(bridge._output_mode, "off")
            bridge._publish_hamlib_target(self.ac, {"az_a": 20, "el_a": 11})
            self.rotor.set_position.assert_not_called()
            self.server.clear.assert_called()

    def test_auto_hamlib_suppresses_stale_and_below_horizon_coordinates(self):
        self.rpc("rotator_output_set", mode="hamlib")
        for age, elevation, expected in ((3, 11, True), (14.9, 11, True),
                                          (119.9, 11, True), (120, 11, False),
                                          (121, 11, False), (3, -1, False)):
            with self.subTest(age=age, elevation=elevation), \
                    patch.object(bridge.time, "time", return_value=100 + age):
                bridge._publish_hamlib_target(self.ac, {"az_a": 20, "el_a": elevation})
                self.assertEqual(self.rotor.set_position.call_count, int(expected))
                self.rotor.reset_mock()
        self.assertFalse(self.rpc("rotator_set", azimuth=20, elevation=11)["ok"])
        self.rotor.set_position.assert_not_called()

    def test_direct_manual_commands_require_hamlib_and_off_dde_cannot_park(self):
        with patch.object(bridge, "SatPC32DDE", return_value=self.server):
            for mode in ("off", "dde", "hamlib"):
                self.rpc("rotator_output_set", mode=mode)
                bridge._manual_pointing = True
                reply = self.rpc("rotator_set", azimuth=20, elevation=11)
                self.assertEqual(reply["ok"], mode == "hamlib")
                self.assertEqual(self.rotor.set_position.call_count, int(mode == "hamlib"))
                self.rotor.reset_mock()
                self.assertEqual(self.rpc("rotator_park")["ok"], mode == "hamlib")
                self.assertEqual(self.rotor.park.call_count, int(mode == "hamlib"))
                self.rotor.reset_mock()

    def test_legacy_toggles_select_exclusive_modes_and_duplicate_start_is_ignored(self):
        with patch.object(bridge, "SatPC32DDE", return_value=self.server):
            self.assertTrue(self.rpc("satpc32_dde_set", enabled=True)["ok"])
            self.rpc("satpc32_dde_set", enabled=True)
            self.server.start.assert_called_once()
            self.rpc("set_rotator_control", enabled=True)
            self.assertEqual(bridge._output_mode, "hamlib")
            self.server.stop.assert_called_once()
            self.assertIsNone(bridge._satpc32_dde)
            self.rpc("set_rotator_control", enabled=False)
            self.assertEqual(bridge._output_mode, "off")
            self.assertFalse(self.rpc("rotator_output_set", mode="both")["ok"])

    def test_stop_in_dde_does_not_send_hamlib_stop(self):
        with patch.object(bridge, "SatPC32DDE", return_value=self.server):
            self.rpc("rotator_output_set", mode="dde")
            self.rpc("rotator_stop")
            self.rotor.stop_rotation.assert_not_called()
            self.assertEqual(bridge._output_mode, "off")

    def test_position_provenance_not_active_feed_controls_expiry_for_both_outputs(self):
        for source, active, age, valid in (
            ("rtlsdr", "opensky", 119.9, True),
            ("rtlsdr", "opensky", 120, False),
            ("opensky", "rtlsdr", 34.9, True),
            ("opensky", "rtlsdr", 35, False),
            ("adsbexchange", "rtlsdr", 35, False),
            ("dump1090", "rtlsdr", 10, False),
        ):
            self.ac.position_source = source
            with self.subTest(source=source, active=active, age=age), \
                    patch.object(bridge, "_active_source", active), \
                    patch.dict(bridge._source_poll_intervals, {"opensky": 30, "adsbexchange": 30}), \
                    patch.object(bridge.time, "time", return_value=100 + age), \
                    patch.object(bridge, "SatPC32DDE", return_value=self.server):
                for mode in ("hamlib", "dde"):
                    self.rpc("rotator_output_set", mode=mode)
                    bridge._publish_hamlib_target(self.ac, {"az_a": 20, "el_a": 11})
                    bridge._publish_dde_target(self.ac, {"az_a": 20, "el_a": 11})
                    self.assertEqual(self.rotor.set_position.call_count, int(valid and mode == "hamlib"))
                    self.assertEqual(self.server.update.call_count, int(valid and mode == "dde"))
                    self.rotor.reset_mock()
                    self.server.reset_mock()

    def test_cache_arrivals_preserve_position_stamp_and_source_when_feed_changes(self):
        with patch.multiple(bridge, _aircraft_cache=[], _aircraft_last_update_monotonic=None,
                            _aircraft_last_update_epoch_s=None):
            bridge._replace_aircraft_cache([self.ac], source="opensky")
            with patch.object(bridge, "_active_source", "rtlsdr"), \
                    patch.object(bridge.time, "time", return_value=140):
                self.assertEqual(self.ac.position_updated_epoch_s, 100)
                self.assertEqual(self.ac.position_source, "opensky")
                self.assertEqual(bridge._position_valid_for_s(self.ac), 0)
