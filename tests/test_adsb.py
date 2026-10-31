import math
import unittest

from airwsjtx.adsb import ADSBDecoder, crc_ok, global_cpr, plausible_position

# Public ADS-B worked examples: https://mode-s.org/1090mhz/content/ads-b/
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
IDENTITY = "8D4840D6202CC371C32CE0576098"
VELOCITY = "8D485020994409940838175B284F"


def avr(frame):
    return f"*{frame};"


def change_bits(frame, start, length, value):
    number = int(frame, 16)
    shift = 112 - start - length + 1
    number = (number & ~(((1 << length) - 1) << shift)) | (value << shift)
    number &= ~0xFFFFFF
    remainder = number
    for bit in range(111, 23, -1):
        if remainder & (1 << bit):
            remainder ^= 0x1FFF409 << (bit - 24)
    return f"{number | remainder:028X}"


class ADSBTests(unittest.TestCase):
    def test_published_crc_and_single_bit_corruptions(self):
        frame = bytes.fromhex(EVEN)
        self.assertTrue(crc_ok(frame))
        for bit in range(112):
            damaged = (int.from_bytes(frame, "big") ^ (1 << bit)).to_bytes(14, "big")
            self.assertFalse(crc_ok(damaged), bit)
        self.assertFalse(crc_ok(b""))

    def test_identification(self):
        decoder = ADSBDecoder()
        self.assertTrue(decoder.consume(avr(IDENTITY), now=1))
        aircraft = decoder.snapshot(now=1)[0]
        self.assertEqual(aircraft.icao24, "4840d6")
        self.assertEqual(aircraft.callsign, "KLM1023")
        self.assertFalse(aircraft.has_position)

    def test_published_airborne_pair_newest_even(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(ODD), now=0, epoch=1000)
        self.assertFalse(decoder.snapshot(now=0)[0].has_position)
        decoder.consume(avr(EVEN), now=1, epoch=1001)
        aircraft = decoder.snapshot(now=1)[0]
        self.assertAlmostEqual(aircraft.lat, 52.257202, places=5)
        self.assertAlmostEqual(aircraft.lon, 3.919373, places=5)
        self.assertAlmostEqual(aircraft.altitude_m, 38000 * 0.3048)
        self.assertEqual(aircraft.position_updated_epoch_s, 1001)
        self.assertEqual(decoder.positions, 1)
        self.assertTrue(aircraft.has_position)

    def test_published_pair_newest_odd(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(EVEN), now=0)
        decoder.consume(avr(ODD), now=1)
        aircraft = decoder.snapshot(now=1)[0]
        self.assertAlmostEqual(aircraft.lat, 52.265780, places=5)
        self.assertAlmostEqual(aircraft.lon, 3.938913, places=5)

    def test_pair_age_and_track_position_expiry(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(EVEN), now=0)
        decoder.consume(avr(ODD), now=11)
        self.assertFalse(decoder.snapshot(now=11)[0].has_position)
        decoder.consume(avr(EVEN), now=12)
        self.assertTrue(decoder.snapshot(now=131.9)[0].has_position)
        decoder.consume(avr(change_bits(IDENTITY, 9, 24, 0x40621D)), now=28)
        self.assertFalse(decoder.snapshot(now=132)[0].has_position)
        self.assertIsNone(decoder.snapshot(now=132)[0].position_updated_epoch_s)
        self.assertEqual(len(decoder.snapshot(now=208)), 1)
        self.assertEqual(decoder.snapshot(now=208.1), [])

    def test_ground_transition_and_unsupported_altitude(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(EVEN), now=0)
        decoder.consume(avr(ODD), now=1)
        decoder.consume(avr(change_bits(EVEN, 48, 1, 0)), now=2)  # Q=0
        self.assertIsNone(decoder.snapshot(now=2)[0].altitude_m)
        decoder.consume(avr(change_bits(EVEN, 33, 5, 5)), now=3)
        aircraft = decoder.snapshot(now=3)[0]
        self.assertTrue(aircraft.on_ground)
        self.assertIsNone(aircraft.lat)
        self.assertFalse(aircraft.has_position)
        decoder.consume(avr(EVEN), now=4)
        self.assertFalse(decoder.snapshot(now=4)[0].has_position)  # ground cleared old pair

    def test_published_ground_speed_and_vertical_rate(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(VELOCITY), now=0)
        aircraft = decoder.snapshot(now=0)[0]
        self.assertAlmostEqual(aircraft.velocity_ms / 0.514444, 159.201, places=3)
        self.assertAlmostEqual(aircraft.heading_deg, 182.88, places=2)
        self.assertAlmostEqual(aircraft.vertical_rate_ms, -832 * 0.00508)
        self.assertIsNone(decoder.snapshot(now=16)[0].velocity_ms)

    def test_invalid_missing_velocity_and_df18(self):
        decoder = ADSBDecoder()
        for line in ("bad", "*00;", "*not hex;", avr(EVEN[:-1] + "0"), "8D40621D"):
            self.assertFalse(decoder.consume(line, now=0))
        self.assertFalse(decoder.consume(avr(change_bits(EVEN, 1, 5, 18)), now=0))
        decoder.consume(avr(change_bits(VELOCITY, 47, 10, 0)), now=1)
        self.assertIsNone(decoder.snapshot(now=1)[0].velocity_ms)
        self.assertEqual(decoder.crc_errors, 1)
        self.assertEqual(decoder.valid_messages, 1)
        self.assertEqual(decoder.unsupported_frames, 1)

    def test_address_parity_and_unknown_formats_are_not_adsb_crc_errors(self):
        decoder = ADSBDecoder()
        for df in (16, 18, 19, 20, 21, 24, 31):
            candidate = bytearray.fromhex(EVEN)
            candidate[0] = (df << 3) | 5
            self.assertFalse(decoder.consume(avr(candidate.hex()), now=0))
        self.assertEqual(decoder.frames, 7)
        self.assertEqual(decoder.unsupported_frames, 7)
        self.assertEqual(decoder.crc_errors, 0)
        self.assertEqual(decoder.valid_messages, 0)

    def test_global_cpr_southern_hemisphere_dateline_and_poles(self):
        def encode(lat, lon, parity):
            # Independent CPR encoding using the reference NL zone search.
            nl = 1
            for n in range(59, 1, -1):
                boundary = math.degrees(math.acos(math.sqrt(
                    (1 - math.cos(math.pi / 30)) / (1 - math.cos(2 * math.pi / n)))))
                if abs(lat) < boundary:
                    nl = n
                    break
            dlat = 360 / (60 - parity)
            dlon = 360 / max(nl - parity, 1)
            return (parity, round(131072 * ((lat % dlat) / dlat)) % 131072,
                    round(131072 * ((lon % dlon) / dlon)) % 131072)

        for lat, lon in ((-33.8, 151.2), (45.1, -179.99), (-88.1, -40.5), (0.1, 0.1)):
            with self.subTest(lat=lat, lon=lon):
                position = global_cpr(encode(lat, lon, 0), encode(lat, lon, 1))
                self.assertIsNotNone(position)
                self.assertAlmostEqual(position[0], lat, places=3)
                self.assertAlmostEqual(position[1], lon, places=2)

    def test_recent_position_jump_rejected_without_using_station_reference(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(EVEN), now=0)
        decoder.consume(avr(ODD), now=1)
        old = decoder.snapshot(now=1)[0]
        # A CRC-valid airborne pair at (0,0) cannot replace a fresh European fix.
        zero_even = change_bits(change_bits(EVEN, 55, 17, 0), 72, 17, 0)
        zero_odd = change_bits(change_bits(ODD, 55, 17, 0), 72, 17, 0)
        decoder.consume(avr(zero_even), now=2)
        decoder.consume(avr(zero_odd), now=3)
        self.assertGreater(decoder.position_rejections, 0)
        self.assertEqual(decoder.positions, 1)
        self.assertEqual(decoder.snapshot(now=3)[0].lat, old.lat)
        # No stale-reference lockout: a new pair after expiry can establish a fix.
        decoder.consume(avr(zero_even), now=30)
        decoder.consume(avr(zero_odd), now=31)
        self.assertEqual(decoder.snapshot(now=31)[0].lat, 0)

    def test_position_motion_margin_dateline_and_stale_reference(self):
        decoder = ADSBDecoder()
        decoder.consume(avr(EVEN), now=0)
        decoder.consume(avr(ODD), now=1)
        track = decoder.tracks["40621d"]
        self.assertTrue(plausible_position(track, (track.aircraft.lat + .001, track.aircraft.lon), 2))
        self.assertFalse(plausible_position(track, (0, 0), 2))
        self.assertFalse(plausible_position(track, (0, 0), 0))
        self.assertTrue(plausible_position(track, (0, 0), 17))
        track.aircraft.lat, track.aircraft.lon = 0, 179.999
        self.assertTrue(plausible_position(track, (0, -179.999), 2))


if __name__ == "__main__":
    unittest.main()
