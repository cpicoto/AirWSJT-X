import time
import unittest
from unittest.mock import patch

from airwsjtx.airscout import Aircraft
from sidecar import airwsjtx_bridge


class AirscoutBridgeCacheTests(unittest.TestCase):
    def test_poll_rate_change_emits_updated_source_status(self) -> None:
        with patch.dict(airwsjtx_bridge._source_poll_intervals):
            with patch.multiple(airwsjtx_bridge, _active_source="opensky", _opensky_feed=None):
                with patch.object(airwsjtx_bridge, "_emit") as emit:
                    airwsjtx_bridge.handle({
                        "id": "interval", "method": "source_poll_interval_set",
                        "params": {"provider": "opensky", "seconds": 60},
                    })
                    events = [call.args[0] for call in emit.call_args_list]
                    status = next(event["data"] for event in events
                                  if event.get("event") == "airscout_source_status")
                    self.assertEqual(status["poll_intervals_s"]["opensky"], 60)

    def test_source_status_reports_saved_poll_intervals_and_dump1090_cadence(self) -> None:
        with patch.dict(airwsjtx_bridge._source_poll_intervals, {"opensky": 60, "adsbexchange": 10}):
            with patch.object(airwsjtx_bridge, "_emit") as emit:
                airwsjtx_bridge._emit_source_status()
                self.assertEqual(emit.call_args.args[0]["data"]["poll_intervals_s"],
                                 {"opensky": 60, "adsbexchange": 10, "dump1090": 5})
            with patch.object(airwsjtx_bridge, "_ok") as reply:
                airwsjtx_bridge.handle({"id": "sources", "method": "airscout_get_sources"})
                self.assertEqual(reply.call_args.args[1]["poll_intervals_s"],
                                 {"opensky": 60, "adsbexchange": 10, "dump1090": 5})

    def test_tracking_emits_all_current_aircraft_fields_and_original_timestamp(self) -> None:
        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=47.5, lon=-122.4,
            altitude_m=10000, velocity_ms=200, heading_deg=90,
            vertical_rate_ms=0, on_ground=False, position_updated_epoch_s=1000,
        )
        geo = {"az_a": 123, "el_a": 5, "az_b": 234, "el_b": 6,
               "scatter_score": 0.8, "off_path_deg": 12}
        with patch.multiple(airwsjtx_bridge, _locked_aircraft_id="abc123",
                            _station_a_locator="CN87", _station_b_locator="CN89", _rotator=None):
            with patch.object(airwsjtx_bridge, "scatter_geometry", return_value=geo):
                with patch.object(airwsjtx_bridge, "_emit") as emit:
                    airwsjtx_bridge._update_rotator_for_locked([ac])
                    data = emit.call_args.args[0]["data"]
                    self.assertEqual(data["altitude_ft"], 32808)
                    for key, value in geo.items():
                        self.assertEqual(data[key], value)
                    self.assertEqual(data["callsign"], "TEST1")
                    self.assertEqual(data["position_updated_epoch_s"], 1000)
                    airwsjtx_bridge._update_rotator_for_locked([ac])
                    self.assertEqual(emit.call_args.args[0]["data"]["position_updated_epoch_s"], 1000)

    def setUp(self) -> None:
        with airwsjtx_bridge._aircraft_lock:
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
        stale_after = airwsjtx_bridge._aircraft_cache_stale_after_s
        expire_after = airwsjtx_bridge._aircraft_cache_expire_after_s
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(None), "empty")
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(max(0.0, stale_after - 1.0)), "fresh")
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(stale_after + 1.0), "stale")
        self.assertEqual(airwsjtx_bridge._aircraft_cache_freshness(expire_after + 1.0), "expired")

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

    def test_auto_lock_accepts_valid_candidate_below_20_percent_score(self) -> None:
        ac = Aircraft(
            icao24="valid123",
            callsign="VALID",
            lat=48.5,
            lon=2.0,
            altitude_m=5000.0,
            velocity_ms=200.0,
            heading_deg=90.0,
            vertical_rate_ms=0.0,
            on_ground=False,
        )
        with patch.object(airwsjtx_bridge, "_station_a_locator", "AA00"), \
             patch.object(airwsjtx_bridge, "_station_b_locator", "AA00"), \
             patch.object(airwsjtx_bridge, "locator_to_latlon",
                          side_effect=[(50.0, 0.0), (50.0, 10.0)]):
            selected = airwsjtx_bridge._select_auto_lock_candidate([ac])
            self.assertIsNotNone(selected)
            self.assertEqual(selected.icao24, "valid123")


if __name__ == "__main__":
    unittest.main()
