import ctypes
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from airwsjtx.rtlsdr import LocalRTLSDRFeed, _list_devices, discover_devices, reception_message, support_error, tools_directory, validate_gain
from sidecar import airwsjtx_bridge as bridge
from tests.test_adsb import EVEN, ODD, avr

DEVICE_INFO = {"index": 0, "available": True, "supported_gains_db": [0.0, 20.7, 32.8, 49.6]}


class ReceiverTests(unittest.TestCase):
    def test_gain_validation_and_stale_reception_status(self):
        self.assertIsNone(validate_gain(None, [0.0, 20.7]))
        self.assertEqual(validate_gain(20.7, [0.0, 20.7]), 20.7)
        for value in (True, "20.7", float("nan"), float("inf"), 99, 20.71):
            with self.assertRaises(ValueError):
                validate_gain(value, [0.0, 20.7])
        self.assertIn("no CRC-valid ADS-B yet", reception_message(None, now=100))
        self.assertIn("recent CRC-valid", reception_message(90, now=100))
        self.assertIn("no recent", reception_message(80, now=100))

    def test_gain_list_available_before_start_without_changing_sources(self):
        with patch.object(bridge, "_rtlsdr_feed", None), \
                patch.object(bridge, "discover_devices", return_value=[
                    {**DEVICE_INFO, "supported_gains_db": [0.0, 32.8]}
                ]), \
                patch.object(bridge, "_ok") as reply, \
                patch.object(bridge, "_set_aircraft_sources") as sources:
            bridge.handle({"id": "gains", "method": "rtlsdr_get_gains", "params": {"device_index": 0}})
            reply.assert_called_once_with("gains", {"supported_gains_db": [0.0, 32.8]})
            sources.assert_not_called()

    def test_start_manual_gain_validates_before_reconfiguring(self):
        with patch.multiple(bridge, _source_priority=["opensky"], _rtlsdr_device_index=None,
                            _rtlsdr_gain_db=None, _rtlsdr_feed=None), \
                patch.object(bridge, "discover_devices", return_value=[
                    {**DEVICE_INFO, "supported_gains_db": [32.8]}
                ]), \
                patch.object(bridge, "_set_aircraft_sources") as sources, \
                patch.object(bridge, "_emit"):
            bridge.handle({"id": "invalid", "method": "rtlsdr_start", "params": {"device_index": 0, "gain_db": 33}})
            sources.assert_not_called()
            bridge.handle({"id": "valid", "method": "rtlsdr_start", "params": {"device_index": 0, "gain_db": 32.8}})
            self.assertEqual(bridge._rtlsdr_gain_db, 32.8)
            sources.assert_called_once()

    def test_manual_gain_uses_exact_native_argument(self):
        process = Mock(stdout=io.StringIO(), stderr=io.StringIO())
        process.poll.return_value = None
        with patch("airwsjtx.rtlsdr.support_error", return_value=None), \
                patch("airwsjtx.rtlsdr.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch("airwsjtx.rtlsdr.subprocess.Popen", return_value=process) as popen, \
                patch("airwsjtx.rtlsdr._ReceiverJob"):
            feed = LocalRTLSDRFeed(0, Mock(), Mock(), gain_db=32.8, device_info=DEVICE_INFO)
            feed.start()
            try:
                self.assertEqual(popen.call_args.args[0][-2:], ["-g", "32.8"])
                self.assertEqual(feed.snapshot()["gain_db"], 32.8)
                self.assertEqual(feed.snapshot()["supported_gains_db"], [0.0, 20.7, 32.8, 49.6])
            finally:
                feed.stop()

    def test_gain_change_invalid_value_does_not_stop_receiver(self):
        receiver = Mock(supported_gains_db=[20.7, 32.8])
        with patch.object(bridge, "_rtlsdr_feed", receiver), \
                patch.object(bridge, "_stop_source") as stop, \
                patch.object(bridge, "_err") as error:
            bridge.handle({"id": "gain", "method": "rtlsdr_set_gain", "params": {"gain_db": 33}})
            error.assert_called_once()
            stop.assert_not_called()

    def test_gain_change_restarts_only_local_source_without_persisting_priority(self):
        receiver = Mock(supported_gains_db=[32.8])
        with patch.multiple(bridge, _rtlsdr_feed=receiver, _rtlsdr_gain_db=None), \
                patch.object(bridge, "_stop_source") as stop, \
                patch.object(bridge, "_start_rtlsdr_feed", return_value=True) as start, \
                patch.object(bridge, "_write_source_priority") as write, \
                patch.object(bridge, "_emit_aircraft_update"), \
                patch.object(bridge, "_ok"):
            bridge.handle({"id": "gain", "method": "rtlsdr_set_gain", "params": {"gain_db": 32.8}})
            self.assertEqual(bridge._rtlsdr_gain_db, 32.8)
            stop.assert_called_once_with("rtlsdr")
            start.assert_called_once()
            write.assert_not_called()

    def test_unsupported_platform_and_missing_tools_explicit(self):
        with patch("airwsjtx.rtlsdr.sys.platform", "linux"):
            self.assertIn("Windows x64 only", support_error())
            with self.assertRaisesRegex(RuntimeError, "Windows x64"):
                discover_devices()
        with tempfile.TemporaryDirectory() as directory, \
                patch("airwsjtx.rtlsdr.sys.platform", "win32"), \
                patch("airwsjtx.rtlsdr.platform.machine", return_value="AMD64"), \
                patch("airwsjtx.rtlsdr.tools_directory", return_value=Path(directory)):
            self.assertIn("tools are missing", support_error())

    def test_tools_resolve_inside_frozen_runtime_not_path(self):
        with patch("airwsjtx.rtlsdr.sys._MEIPASS", "installed-runtime", create=True):
            self.assertEqual(tools_directory(), Path("installed-runtime") / "rtl-sdr")

    def test_discovery_timeout_does_not_wait_for_a_blocked_usb_probe(self):
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def blocked_probe():
            started.set()
            try:
                release.wait()
                return []
            finally:
                completed.set()

        try:
            with patch("airwsjtx.rtlsdr._discover_devices", side_effect=blocked_probe):
                with self.assertRaisesRegex(RuntimeError, "timed out after 0.01 seconds"):
                    discover_devices(timeout_s=0.01)
                self.assertTrue(started.wait(1))
        finally:
            release.set()
            self.assertTrue(completed.wait(1))

    def test_device_availability_requires_open_probe_and_closes_successful_probe(self):
        library = Mock()
        library.rtlsdr_get_device_count.return_value = 2
        library.rtlsdr_get_device_name.side_effect = [b"Busy", b"Available"]

        def usb_strings(index, manufacturer, product, serial):
            manufacturer.value = b"RTL"
            product.value = b"RTL2838"
            serial.value = str(index).encode("ascii")
            return 0

        library.rtlsdr_get_device_usb_strings.side_effect = usb_strings

        def open_device(pointer, index):
            if index == 0:
                return -3
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(index + 1)
            return 0

        def gains(_device, values):
            if values is None:
                return 2
            values[0], values[1] = 0, 328
            return 2

        library.rtlsdr_open.side_effect = open_device
        library.rtlsdr_get_tuner_gains.side_effect = gains
        devices = _list_devices(library)
        self.assertFalse(devices[0]["available"])
        self.assertIn("Cannot open", devices[0]["message"])
        self.assertTrue(devices[1]["available"])
        self.assertEqual(devices[1]["supported_gains_db"], [0.0, 32.8])
        library.rtlsdr_close.assert_called_once()

    def test_no_device_does_not_spawn(self):
        with patch("airwsjtx.rtlsdr.support_error", return_value=None), \
                patch("airwsjtx.rtlsdr.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "discovered and available"):
                LocalRTLSDRFeed(0, Mock(), Mock(), device_info={
                    "index": 0, "available": False, "supported_gains_db": [],
                }).start()
            popen.assert_not_called()

    def test_owned_process_native_frames_job_and_stop(self):
        process = Mock()
        process.stdout = io.StringIO(avr(EVEN) + "\n" + avr(ODD) + "\n")
        process.stderr = io.StringIO("Sampling at 2000000 S/s.\n")
        process.poll.return_value = None
        process.wait.return_value = 0
        status = Mock()
        feed = LocalRTLSDRFeed(0, Mock(), status)
        with patch("airwsjtx.rtlsdr.support_error", return_value=None), \
                patch("airwsjtx.rtlsdr.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch("airwsjtx.rtlsdr.subprocess.Popen", return_value=process) as popen, \
                patch("airwsjtx.rtlsdr._ReceiverJob") as job:
            feed = LocalRTLSDRFeed(0, Mock(), status, device_info=DEVICE_INFO)
            feed.start()
            try:
                feed._threads[0].join(timeout=2)
                snapshot = feed.snapshot()
                self.assertEqual(snapshot["valid_messages"], 2)
                self.assertEqual(snapshot["unsupported_frames"], 0)
                self.assertEqual(snapshot["with_position"], 1)
                self.assertEqual(snapshot["positions"], 1)
                self.assertIsNone(snapshot["iq"])
                self.assertEqual(snapshot["sample_rate"], 2400000)
                args = popen.call_args
                self.assertEqual(args.args[0][1:], ["-d", "0"])
                self.assertNotIn("-T", args.args[0])
                self.assertNotIn("shell", args.kwargs)
                self.assertEqual(args.kwargs["creationflags"], 0x08000000)
            finally:
                feed.stop()
            process.terminate.assert_called_once()
            job.return_value.close.assert_called_once()
            self.assertTrue(all(not thread.is_alive() for thread in feed._threads))
            self.assertEqual(feed.snapshot()["state"], "stopped")

    def test_native_payload_evidence_is_bounded_and_independently_crc_checked(self):
        feed = LocalRTLSDRFeed(0, Mock(), Mock())
        evidence = {"frame": EVEN, "df": 17, "crc_syndrome": 0,
                    "sample_rate": 2400000, "timestamp_12mhz": 1000}
        lines = ["AIRWSJTX_FRAME " + json.dumps({**evidence, "timestamp_12mhz": n})
                 for n in range(40)]
        lines += ["AIRWSJTX_FRAME " + json.dumps({**evidence, "frame": EVEN[:-1] + "0"}),
                  "AIRWSJTX_FRAME " + json.dumps({**evidence, "df": 18}),
                  "AIRWSJTX_FRAME []"]
        metrics = {"samples": 4800000, "preamble_candidates": 20,
                   "df17_crc_rejected_phase_trials": 12, "accepted_other_modes": 3}
        lines.append("AIRWSJTX_IQ " + json.dumps(metrics))
        feed._process = Mock(stderr=io.StringIO("\n".join(lines)))
        feed._read_stderr()
        snapshot = feed.snapshot()
        self.assertEqual(len(snapshot["last_frames"]), 32)
        self.assertEqual(snapshot["last_frames"][0]["timestamp_12mhz"], 8)
        self.assertEqual(snapshot["last_frames"][-1]["timestamp_12mhz"], 39)
        self.assertEqual(len(snapshot["diagnostics"]), 3)
        self.assertEqual(snapshot["iq"], metrics)
        self.assertIn("independent CRC check", snapshot["diagnostics"][0])

    def test_process_exit_or_tuning_failure_exposes_diagnostics(self):
        for code in (1, None):
            with self.subTest(code=code):
                update, status = Mock(), Mock()
                feed = LocalRTLSDRFeed(0, update, status)
                feed._process = Mock()
                feed._process.poll.return_value = code
                feed._stderr.append("Failed to open rtlsdr device #0: LIBUSB_ERROR_BUSY")
                feed._stop.wait = Mock(return_value=False)
                feed._report()
                self.assertEqual(feed.snapshot()["state"], "error")
                self.assertIn("BUSY", feed.snapshot()["message"])
                update.assert_called_once_with([])
                if code is None:
                    feed._process.terminate.assert_called_once()

    def test_spawn_failure_never_leaves_owned_process(self):
        feed = LocalRTLSDRFeed(0, Mock(), Mock())
        process = Mock(stdout=io.StringIO(), stderr=io.StringIO())
        process.poll.return_value = None
        with patch("airwsjtx.rtlsdr.support_error", return_value=None), \
                patch("airwsjtx.rtlsdr.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch("airwsjtx.rtlsdr.subprocess.Popen", return_value=process), \
                patch("airwsjtx.rtlsdr._ReceiverJob", side_effect=OSError("job failed")):
            feed = LocalRTLSDRFeed(0, Mock(), Mock(), device_info=DEVICE_INFO)
            with self.assertRaisesRegex(OSError, "job failed"):
                feed.start()
            process.terminate.assert_called_once()
            self.assertIsNone(feed._process)

    def test_bridge_validates_device_and_preserves_network_priority(self):
        with patch.multiple(bridge, _source_priority=["dump1090", "opensky"],
                            _rtlsdr_feed=None, _rtlsdr_device_index=0), \
                patch.object(bridge, "discover_devices", return_value=[{
                    **DEVICE_INFO, "index": 2,
                }]), \
                patch.object(bridge, "_set_aircraft_sources") as sources, \
                patch.object(bridge, "_emit") as emit:
            for index in (-1, True, "2", 0):
                bridge.handle({"id": 1, "method": "rtlsdr_start", "params": {"device_index": index}})
                self.assertFalse(emit.call_args.args[0]["ok"])
            sources.assert_not_called()
            bridge.handle({"id": 1, "method": "rtlsdr_start", "params": {"device_index": 2}})
            sources.assert_called_once_with(["rtlsdr", "dump1090", "opensky"])
            self.assertEqual(bridge._rtlsdr_device_index, 2)
            bridge.handle({"id": 2, "method": "rtlsdr_stop"})
            sources.assert_called_with(["dump1090", "opensky"])

    def test_bridge_updates_same_aircraft_cache_and_releases_on_switch(self):
        with patch.multiple(bridge, _rtlsdr_feed=None, _active_source=None, _rtlsdr_device_index=0,
                            _rtlsdr_last_snapshot={}), \
                patch.object(bridge, "LocalRTLSDRFeed") as constructor, \
                patch.object(bridge, "_replace_aircraft_cache") as cache, \
                patch.object(bridge, "_emit_aircraft_update") as update, \
                patch.object(bridge, "_emit"), \
                patch.object(bridge, "_set_source_status"):
            self.assertTrue(bridge._start_source("rtlsdr"))
            constructor.return_value.snapshot.return_value = {"last_frames": [{"frame": EVEN}]}
            on_update = constructor.call_args.args[1]
            on_update(["aircraft fixture"])
            cache.assert_called_with(["aircraft fixture"], source="rtlsdr")
            update.assert_called_once()
            bridge._stop_source("rtlsdr")
            constructor.return_value.stop.assert_called_once()
            self.assertIsNone(bridge._rtlsdr_feed)
            cache.assert_called_with([])
            with patch.object(bridge, "_ok") as reply:
                bridge.handle({"id": "stopped", "method": "rtlsdr_status"})
                status = reply.call_args.args[1]
                self.assertEqual(status["last_frames"], [{"frame": EVEN}])

    def test_direct_adsbexchange_start_releases_receiver_and_retains_evidence(self):
        receiver = Mock()
        receiver.snapshot.return_value = {"last_frames": [{"frame": EVEN}]}
        old_network = Mock()
        with patch.multiple(bridge, _rtlsdr_feed=receiver, _rtlsdr_last_snapshot={},
                            _adsbexchange_feed=old_network, _adsbexchange_api_key=""), \
                patch.dict("os.environ", {"ADSBEXCHANGE_API_KEY": ""}), \
                patch.object(bridge, "_replace_aircraft_cache"), \
                patch.object(bridge, "_set_source_status"):
            bridge._start_adsbexchange_feed()
            receiver.stop.assert_called_once()
            old_network.stop.assert_called_once()
            self.assertIsNone(bridge._rtlsdr_feed)
            self.assertIsNone(bridge._adsbexchange_feed)
            self.assertEqual(bridge._rtlsdr_last_snapshot["last_frames"], [{"frame": EVEN}])
    def test_source_start_waits_for_discovery_to_select_device(self):
        with patch.object(bridge, "_rtlsdr_device_index", None), \
                patch.object(bridge, "_set_source_status") as status, \
                patch.object(bridge, "LocalRTLSDRFeed") as constructor:
            self.assertFalse(bridge._start_rtlsdr_feed())
            constructor.assert_not_called()
            self.assertIn("No receiver selected", status.call_args.args[2])

    def test_local_refresh_uses_streaming_cache_not_external_fetch(self):
        with patch.object(bridge, "_active_source", "rtlsdr"), \
                patch.object(bridge, "_aircraft_cache", []), \
                patch.object(bridge, "_emit_aircraft_update") as update, \
                patch.object(bridge, "_ok") as reply, \
                patch.object(bridge, "_err") as error:
            bridge.handle({"id": "refresh", "method": "airscout_fetch_now"})
            reply.assert_called_once_with("refresh", {"count": 0})
            error.assert_not_called()
            update.assert_called_once()

    def test_direct_external_dump1090_start_releases_local_receiver(self):
        receiver = Mock()
        with patch.multiple(bridge, _rtlsdr_feed=receiver, _dump1090_feed=None, _active_source="rtlsdr"), \
                patch.object(bridge, "Dump1090Feed") as external, \
                patch.object(bridge, "_replace_aircraft_cache"), \
                patch.object(bridge, "_set_source_status"):
            bridge._start_dump1090_feed("127.0.0.1", 30003)
            receiver.stop.assert_called_once()
            self.assertIsNone(bridge._rtlsdr_feed)
            external.return_value.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
