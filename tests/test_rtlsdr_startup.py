import unittest
from unittest.mock import Mock, patch

from sidecar import airwsjtx_bridge as bridge


class ReceiverStartupTests(unittest.TestCase):
    def setUp(self):
        self.state = patch.multiple(
            bridge, _rtlsdr_startup_attempted=False, _rtlsdr_startup_network_priority=None,
            _rtlsdr_feed=None, _rtlsdr_device_index=None, _rtlsdr_device_info=None,
            _active_source="opensky",
            _source_priority=["opensky", "dump1090"], _rtlsdr_gain_db=None,
        )
        self.state.start()
        self.addCleanup(self.state.stop)
        self.support = patch.object(bridge, "rtlsdr_support_error", return_value=None)
        self.support.start()
        self.addCleanup(self.support.stop)

    def test_present_starts_once_preserves_network_configuration_and_stop(self):
        with patch.object(bridge, "discover_devices", return_value=[
            {"index": 0, "available": False, "supported_gains_db": []},
            {"index": 1, "available": True, "supported_gains_db": [32.8]},
        ]), patch.object(bridge, "_start_rtlsdr_feed", return_value=True) as start, \
                patch.object(bridge, "_stop_source") as stop, \
                patch.object(bridge, "_write_source_priority") as write, \
                patch.object(bridge, "_emit_source_status"):
            bridge._auto_start_rtlsdr()
            self.assertEqual(bridge._rtlsdr_device_index, 1)
            self.assertEqual(bridge._source_priority, ["rtlsdr", "opensky", "dump1090"])
            self.assertEqual(bridge._rtlsdr_startup_network_priority, ["opensky", "dump1090"])
            stop.assert_called_once_with("opensky")
            write.assert_not_called()
            bridge._auto_start_rtlsdr()
            start.assert_called_once()
            with patch.object(bridge, "_set_aircraft_sources") as sources, patch.object(bridge, "_ok"):
                bridge.handle({"id": "stop", "method": "rtlsdr_stop", "params": {}})
                sources.assert_called_once_with(["opensky", "dump1090"])
            bridge._auto_start_rtlsdr()
            start.assert_called_once()

    def test_absent_busy_or_discovery_error_keeps_network_running(self):
        for devices, error in (([], None), ([{"available": False}], None), (None, RuntimeError("driver"))):
            bridge._rtlsdr_startup_attempted = False
            with self.subTest(devices=devices), \
                    patch.object(bridge, "discover_devices", return_value=devices, side_effect=error), \
                    patch.object(bridge, "_start_rtlsdr_feed") as start, \
                    patch.object(bridge, "_set_source_status") as status, \
                    patch.object(bridge, "_start_configured_airscout_feed") as network:
                bridge._auto_start_rtlsdr()
                start.assert_not_called()
                status.assert_called_once()
                network.assert_called_once()
                self.assertEqual(bridge._source_priority, ["opensky", "dump1090"])

    def test_discovery_timeout_never_late_starts(self):
        with patch.object(bridge, "discover_devices", side_effect=RuntimeError("USB discovery timed out")), \
                patch.object(bridge, "_start_rtlsdr_feed") as start, \
                patch.object(bridge, "_set_source_status"), \
                patch.object(bridge, "_start_configured_airscout_feed"):
            bridge._auto_start_rtlsdr()
            start.assert_not_called()

    def test_start_error_restores_network_and_snapshot(self):
        aircraft = [Mock()]
        with patch.object(bridge, "discover_devices", return_value=[
            {"index": 0, "available": True, "supported_gains_db": [32.8]}
        ]), \
                patch.object(bridge, "_start_rtlsdr_feed", return_value=False), \
                patch.object(bridge, "_current_aircraft_snapshot", return_value=aircraft), \
                patch.object(bridge, "_replace_aircraft_cache") as replace, \
                patch.object(bridge, "_stop_source") as stop, \
                patch.object(bridge, "_start_configured_airscout_feed"):
            bridge._auto_start_rtlsdr()
            stop.assert_not_called()
            replace.assert_called_once_with(aircraft)
            self.assertEqual(bridge._active_source, "opensky")

    def test_native_failure_after_start_falls_back_without_retrying_usb(self):
        bridge._rtlsdr_startup_attempted = True
        bridge._rtlsdr_startup_network_priority = ["opensky", "dump1090"]
        bridge._active_source = "rtlsdr"
        bridge._source_priority = ["rtlsdr", "opensky", "dump1090"]
        bridge._rtlsdr_feed = Mock()
        bridge._rtlsdr_feed.snapshot.return_value = {"state": "error", "message": "USB removed"}
        with patch.object(bridge, "_stop_source") as stop, \
                patch.object(bridge, "_set_source_status") as status, \
                patch.object(bridge, "_start_configured_airscout_feed") as network, \
                patch.object(bridge, "discover_devices") as discover:
            bridge._recover_startup_receiver_failure()
            stop.assert_called_once_with("rtlsdr")
            status.assert_called_once_with("rtlsdr", "error", "USB removed")
            network.assert_called_once()
            bridge._auto_start_rtlsdr()
            discover.assert_not_called()

    def test_manual_start_before_automatic_attempt_is_not_restarted(self):
        bridge._rtlsdr_feed = Mock()
        with patch.object(bridge, "discover_devices") as discover:
            bridge._auto_start_rtlsdr()
            discover.assert_not_called()

    def test_startup_uses_existing_gain_without_enabling_dde_or_rotator(self):
        bridge._rtlsdr_gain_db = 32.8
        feed = Mock()
        with patch.object(bridge, "discover_devices", return_value=[
            {"index": 0, "available": True, "supported_gains_db": [32.8]}
        ]), \
                patch.object(bridge, "LocalRTLSDRFeed", return_value=feed) as constructor, \
                patch.object(bridge, "_stop_source"), \
                patch.object(bridge, "_emit_source_status"), \
                patch.object(bridge, "_replace_aircraft_cache"), \
                patch.object(bridge, "_satpc32_dde") as dde, \
                patch.object(bridge, "_rotator") as rotor:
            bridge._auto_start_rtlsdr()
            self.assertEqual(constructor.call_args.kwargs["gain_db"], 32.8)
            self.assertEqual(constructor.call_args.kwargs["device_info"]["index"], 0)
            feed.start.assert_called_once()
            dde.start.assert_not_called()
            rotor.set_position.assert_not_called()

    def test_stop_before_startup_request_suppresses_automatic_attempt(self):
        with patch.object(bridge, "_set_aircraft_sources"), patch.object(bridge, "_ok"), \
                patch.object(bridge, "discover_devices") as discover:
            bridge.handle({"id": "stop", "method": "rtlsdr_stop", "params": {}})
            bridge._auto_start_rtlsdr()
            discover.assert_not_called()
