import time
import unittest

from airwsjtx.airscout import Aircraft
from sidecar import airwsjtx_bridge


class AirscoutBridgeCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_cache = list(airwsjtx_bridge._aircraft_cache)
        self._orig_last_mono = airwsjtx_bridge._aircraft_last_update_monotonic
        self._orig_last_epoch = airwsjtx_bridge._aircraft_last_update_epoch_s

    def tearDown(self) -> None:
        with airwsjtx_bridge._aircraft_lock:
            airwsjtx_bridge._aircraft_cache.clear()
            airwsjtx_bridge._aircraft_cache.extend(self._orig_cache)
            airwsjtx_bridge._aircraft_last_update_monotonic = self._orig_last_mono
            airwsjtx_bridge._aircraft_last_update_epoch_s = self._orig_last_epoch

    def test_freshness_classification_boundaries(self) -> None:
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(None), "empty")
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(10.0), "fresh")
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(60.0), "stale")
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(180.0), "expired")

    def test_replace_cache_tracks_last_update(self) -> None:
        now = time.monotonic()
        airwsjtx_bridge._replace_aircraft_cache([])
        age_s = airwsjtx_bridge._aircraft_cache_age_s(now=now)
        self.assertIsNotNone(age_s)
        self.assertLessEqual(age_s or 0.0, 1.0)
        self.assertIsNotNone(airwsjtx_bridge._aircraft_last_update_epoch_s)

    def test_expired_cache_is_cleared(self) -> None:
        ac = Aircraft(
            icao24="abc123",
            callsign="TEST1",
            lat=44.0,
            lon=-123.0,
            altitude_m=10000.0,
            velocity_ms=200.0,
            heading_deg=90.0,
            vertical_rate_ms=0.0,
            on_ground=False,
        )
        with airwsjtx_bridge._aircraft_lock:
            airwsjtx_bridge._aircraft_cache.clear()
            airwsjtx_bridge._aircraft_cache.append(ac)
            airwsjtx_bridge._aircraft_last_update_monotonic = (
                time.monotonic() - airwsjtx_bridge._aircraft_cache_expire_after_s - 5.0
            )
            airwsjtx_bridge._aircraft_last_update_epoch_s = int(time.time()) - int(
                airwsjtx_bridge._aircraft_cache_expire_after_s + 5.0
            )

        result = airwsjtx_bridge._get_scatter_aircraft()

        self.assertEqual(result, [])
        with airwsjtx_bridge._aircraft_lock:
            self.assertEqual(airwsjtx_bridge._aircraft_cache, [])


if __name__ == "__main__":
    unittest.main()
