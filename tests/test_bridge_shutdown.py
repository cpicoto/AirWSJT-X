import io
import unittest
from unittest.mock import Mock, patch

from sidecar import airwsjtx_bridge as bridge


class ShutdownTests(unittest.TestCase):
    def test_eof_explicit_shutdown_and_read_failure_cleanup(self):
        class BrokenInput:
            def __iter__(self):
                raise OSError("stdin failed")

        for stream in (io.StringIO(""), io.StringIO('{"method":"shutdown"}\n'), BrokenInput()):
            listener, dde, receiver, rotor = Mock(), Mock(), Mock(), Mock()
            with self.subTest(stream=type(stream).__name__), patch.multiple(
                bridge, _satpc32_dde=dde, _rtlsdr_feed=receiver, _rotator=rotor,
                _opensky_feed=None, _dump1090_feed=None, _adsbexchange_feed=None,
                _output_mode="dde", _rotator_control_enabled=True, _wsjtx_listener=None,
            ), patch.object(bridge, "WSJTXListener", return_value=listener), \
                    patch.object(bridge.sys, "stdin", stream), patch.object(bridge, "_emit_source_status"):
                if isinstance(stream, BrokenInput):
                    with self.assertRaises(OSError):
                        bridge.main()
                else:
                    bridge.main()
                dde.clear.assert_called_once()
                for resource in (listener, dde, receiver, rotor):
                    resource.stop.assert_called_once()
                rotor.stop_rotation.assert_not_called()
                self.assertEqual(bridge._output_mode, "off")

    def test_cleanup_error_does_not_skip_other_helpers(self):
        dde, receiver = Mock(), Mock()
        dde.stop.side_effect = RuntimeError("DDE timeout")
        with patch.multiple(
            bridge, _satpc32_dde=dde, _rtlsdr_feed=receiver, _rotator=None,
            _opensky_feed=None, _dump1090_feed=None, _adsbexchange_feed=None,
            _wsjtx_listener=None, _output_mode="dde", _rotator_control_enabled=True,
        ), patch.object(bridge.sys, "stderr", io.StringIO()) as errors:
            bridge._shutdown_backend()
            receiver.stop.assert_called_once()
            self.assertIn("Cannot stop DDE", errors.getvalue())
