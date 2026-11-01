"""Replay deterministic, noisy, fractional-phase IQ through the shipped helper."""
import json
import hashlib
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time
import unittest

from airwsjtx.adsb import ADSBDecoder, crc_ok
from airwsjtx.rtlsdr import helper_name, platform_supported, tools_directory
from tests.test_adsb import EVEN, ODD, change_bits


def iq_fixture(rate, messages, length=262144, noise=0, amplitude=80):
    """Integrate rectangular 0.5 us pulses into unsigned complex samples."""
    rng = random.Random(1090)
    values = [0.0] * (length // 2)
    width = rate / 2000000
    for start, frame in messages:
        pulses = [0, 2, 7, 9]
        bits = len(frame) * 4
        for bit in range(bits):
            one = (int(frame, 16) >> (bits - 1 - bit)) & 1
            pulses.append(16 + 2 * bit + (0 if one else 1))
        for pulse in pulses:
            left = start + pulse * width
            right = left + width
            for index in range(int(left), min(len(values), int(right) + 1)):
                fraction = max(0, min(index + 1, right) - max(index, left))
                values[index] += amplitude * fraction
    raw = bytearray(length)
    for i, value in enumerate(values):
        for component in (0, 1):
            raw[2 * i + component] = max(0, min(255, round(127.5 + value + rng.gauss(0, noise))))
    return bytes(raw)

def short_frame(df, address, iid=0):
    value = (df << 51) | (5 << 48) | (address << 24)
    remainder = value
    for bit in range(55, 23, -1):
        if remainder & (1 << bit):
            remainder ^= 0x1FFF409 << (bit - 24)
    return f"{value | (remainder ^ iid):014X}"


class UpstreamSourceTests(unittest.TestCase):
    def test_pinned_mode_s_core_and_icao_filter_are_unchanged(self):
        folder = Path(__file__).resolve().parents[1] / "scripts" / "dump1090-fa"
        source = (folder / "demod_2400.c").read_text()
        source = source.replace(
            "#if 0 /* AirWSJT-X: Mode A/C reception is outside this ADS-B helper's scope. */\n", "", 1)
        self.assertTrue(source.endswith("#endif\n"))
        source = source[:-len("#endif\n")]
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),
                         "550d4b2dec55fb182af4572edd93ed9d6ebddc16db3ba131934e9fdeb2653303")
        icao = (folder / "icao_filter.c").read_text().encode()
        self.assertEqual(hashlib.sha256(icao).hexdigest(),
                         "15914bbc0e9bbeee77a19921206720781664894b43615e8175c312c069e1a564")


@unittest.skipUnless(platform_supported() and (tools_directory() / helper_name()).is_file(),
                     "Requires compiled ADS-B helper (Windows x64 or macOS)")
class PhaseDemodulatorTests(unittest.TestCase):
    def replay(self, raw, rate=2400000):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "iq.bin"
            path.write_bytes(raw)
            start = time.monotonic()
            result = subprocess.run(
                [str(tools_directory() / helper_name()), "--input", str(path),
                 "--rate", str(rate)], capture_output=True, text=True, timeout=15,
            )
            elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        measurements = [json.loads(line.split(" ", 1)[1]) for line in result.stderr.splitlines()
                        if line.startswith("AIRWSJTX_IQ ")]
        evidence = [json.loads(line.split(" ", 1)[1]) for line in result.stderr.splitlines()
                    if line.startswith("AIRWSJTX_FRAME ")]
        self.assertTrue(measurements, result.stderr)
        self.assertEqual(len(evidence), measurements[-1]["native_valid_df17"])
        self.assertEqual([f"*{item['frame']};" for item in evidence], result.stdout.splitlines())
        for item in evidence:
            self.assertTrue(crc_ok(bytes.fromhex(item["frame"])))
            self.assertEqual(item["crc_syndrome"], 0)
            self.assertEqual(item["sample_rate"], 2400000)
            self.assertGreaterEqual(item["timestamp_12mhz"], 0)
        return result.stdout.splitlines(), measurements[-1], elapsed

    def test_fractional_phase_noise_and_cpr(self):
        for phase in (0, 0.2, 0.4, 0.6, 0.8):
            with self.subTest(phase=phase):
                raw = iq_fixture(2400000, [(1024 + phase, EVEN), (4096 + phase, ODD)], noise=2)
                frames, metrics, _ = self.replay(raw)
                self.assertIn(f"*{EVEN.lower()};", frames)
                self.assertIn(f"*{ODD.lower()};", frames)
                decoder = ADSBDecoder()
                for index, frame in enumerate(frames):
                    decoder.consume(frame, now=index * 0.01)
                self.assertGreaterEqual(decoder.valid_messages, 2)
                self.assertEqual(decoder.positions, 1)
                self.assertEqual(metrics["samples"], len(raw) // 2)
                self.assertEqual(metrics["dropped_buffers"], 0)
                self.assertIsNone(metrics["usb_overruns"])

    def test_packet_across_usb_block_boundary(self):
        raw = iq_fixture(2400000, [(131072 - 100.4, EVEN), (131072 + 2000.7, ODD)],
                         length=524288, noise=1)
        frames, metrics, _ = self.replay(raw)
        self.assertEqual(frames.count(f"*{EVEN.lower()};"), 1)
        self.assertEqual(frames.count(f"*{ODD.lower()};"), 1)
        self.assertEqual(metrics["samples"], 262144)

    def test_noise_and_corruption_never_become_valid_messages(self):
        corrupt = f"{int(EVEN, 16) ^ 1:028X}"
        frames, metrics, _ = self.replay(iq_fixture(2400000, [(1024.4, corrupt)], noise=3))
        decoder = ADSBDecoder()
        for frame in frames:
            decoder.consume(frame)
        self.assertEqual(decoder.valid_messages, 0)
        self.assertEqual(metrics["native_valid_df17"], 0)

    def test_clipping_and_rate_specific_replay(self):
        _, clipped, _ = self.replay(bytes([0, 255]) * 4096)
        self.assertEqual(clipped["samples"], 4096)
        self.assertEqual(clipped["clipped_fraction"], 1)
        self.assertIsNone(clipped["signal_power_dbfs"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wrong-rate.bin"
            path.write_bytes(iq_fixture(2000000, [(1024, EVEN)]))
            result = subprocess.run(
                [str(tools_directory() / helper_name()), "--input", str(path),
                 "--rate", "2000000"], capture_output=True, text=True, timeout=5,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires 2400000", result.stderr)

    def test_invalid_crc_is_rejected_before_stdout_and_evidence_is_exact(self):
        corrupt = f"{int(EVEN, 16) ^ 1:028X}"
        frames, metrics, _ = self.replay(
            iq_fixture(2400000, [(1024.4, corrupt)], noise=1))
        self.assertEqual(frames, [])
        self.assertGreater(metrics["df17_crc_rejected_phase_trials"], 0)
        frames, metrics, _ = self.replay(iq_fixture(2400000, [(1024.4, EVEN)]))
        self.assertEqual(frames, [f"*{EVEN.lower()};"])
        self.assertEqual(metrics["native_valid_df17"], 1)

    def test_df18_and_address_parity_use_format_specific_validation(self):
        df18 = change_bits(EVEN, 1, 5, 18)
        frames, metrics, _ = self.replay(iq_fixture(2400000, [(1024.4, df18)], noise=1))
        self.assertEqual(frames, [])
        self.assertEqual(metrics["accepted_other_modes"], 1)
        # A long AP reply has nonzero syndrome: it is accepted only after the
        # uncorrected DF17/DF11 acquisition seeded the same ICAO address.
        ap = f"{int(change_bits(EVEN, 1, 5, 20), 16) ^ 0x40621D:028X}"
        frames, metrics, _ = self.replay(iq_fixture(2400000, [(4096.4, ap)], noise=1))
        self.assertEqual(frames, [])
        self.assertEqual(metrics["accepted_other_modes"], 0)
        self.assertGreater(metrics["rejected_unknown_icao"], 0)
        frames, metrics, _ = self.replay(
            iq_fixture(2400000, [(1024.4, df18), (4096.4, ap)], noise=1))
        self.assertEqual(frames, [])
        self.assertEqual(metrics["accepted_other_modes"], 1)  # DF18 must not seed the DF17 namespace.
        self.assertGreater(metrics["rejected_unknown_icao"], 0)
        for seed in (EVEN, short_frame(11, 0x40621D)):
            with self.subTest(seed=seed):
                frames, metrics, _ = self.replay(
                    iq_fixture(2400000, [(1024.4, seed), (4096.4, ap)], noise=1))
                self.assertEqual(metrics["accepted_other_modes"], 1 if seed == EVEN else 2)
                self.assertEqual(metrics["native_valid_df17"], int(seed == EVEN))

    def test_unknown_df11_interrogator_reply_does_not_seed_address_parity(self):
        iid_reply = short_frame(11, 0x40621D, iid=7)
        ap = f"{int(change_bits(EVEN, 1, 5, 20), 16) ^ 0x40621D:028X}"
        frames, metrics, _ = self.replay(
            iq_fixture(2400000, [(1024.4, iid_reply), (4096.4, ap)], noise=1))
        self.assertEqual(frames, [])
        self.assertEqual(metrics["accepted_other_modes"], 0)
        self.assertGreater(metrics["rejected_unknown_icao"], 0)

    def test_iq_phase_rotation_and_filtered_envelope(self):
        # Rotating complex samples plus a short channel filter; the oracle is
        # the externally published payload, not a local CRC encoder.
        raw = iq_fixture(2400000, [(1024.4, EVEN), (4096.2, ODD)], length=32768, noise=0)
        out = bytearray(len(raw))
        previous = 0.0
        for sample in range(len(raw) // 2):
            envelope = raw[2 * sample] - 127.5
            filtered = 0.75 * envelope + 0.25 * previous
            previous = envelope
            angle = sample * 0.23
            out[2 * sample] = round(127.5 + filtered * math.cos(angle))
            out[2 * sample + 1] = round(127.5 + filtered * math.sin(angle))
        frames, metrics, _ = self.replay(bytes(out))
        self.assertEqual(frames, [f"*{EVEN.lower()};", f"*{ODD.lower()};"])
        self.assertEqual(metrics["native_valid_df17"], 2)

    def test_final_partial_buffer_is_searched_without_accepting_truncated_frame(self):
        frames, _, _ = self.replay(
            iq_fixture(2400000, [(1024.4, EVEN)], length=3000, noise=1))
        self.assertEqual(frames, [f"*{EVEN.lower()};"])
        frames, _, _ = self.replay(
            iq_fixture(2400000, [(1024.4, EVEN)], length=2400, noise=1))
        self.assertEqual(frames, [])

    def test_replay_is_faster_than_realtime(self):
        # A generous ceiling avoids making CI depend on a benchmark score.
        raw = iq_fixture(2400000, [(1024.3, EVEN)], noise=3)
        _, _, elapsed = self.replay(raw * 8)
        self.assertLess(elapsed, 2)

    def test_lower_amplitude_messages_survive_relative_thresholds(self):
        raw = iq_fixture(2400000, [(1024.6, EVEN), (4096.2, ODD)], noise=1, amplitude=12)
        frames, metrics, _ = self.replay(raw)
        self.assertIn(f"*{EVEN.lower()};", frames)
        self.assertIn(f"*{ODD.lower()};", frames)
        self.assertGreaterEqual(metrics["native_valid_df17"], 2)

    def test_malformed_input_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "odd.bin"
            path.write_bytes(b"\x7f")
            result = subprocess.run(
                [str(tools_directory() / helper_name()), "--input", str(path)],
                capture_output=True, text=True, timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid odd IQ byte count", result.stderr)
