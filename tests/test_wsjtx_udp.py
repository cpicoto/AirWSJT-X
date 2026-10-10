import struct
import unittest

from airwsjtx.wsjtx_udp import MSG_STATUS, WSJTX_MAGIC, parse_status


def _enc_str(value: str | None) -> bytes:
    if value is None:
        return struct.pack(">I", 0xFFFFFFFF)
    b = value.encode("utf-8")
    return struct.pack(">I", len(b)) + b


def _build_status_packet(mode: str, tr_period: int) -> bytes:
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


class WSJTXStatusParsingTests(unittest.TestCase):
    def test_status_maps_ft8_negative_period_to_default(self) -> None:
        st = parse_status(_build_status_packet("FT8", -1))
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st.tr_period, 15.0)

    def test_status_uses_wire_period_when_positive(self) -> None:
        st = parse_status(_build_status_packet("Q65", 30))
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st.tr_period, 30.0)


if __name__ == "__main__":
    unittest.main()
