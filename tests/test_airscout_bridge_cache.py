import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from airwsjtx.airscout import Aircraft
from sidecar import airwsjtx_bridge


class AirscoutBridgeCacheTests(unittest.TestCase):
    def test_locked_distance_uses_full_eight_character_my_grid(self) -> None:
        lat = 47 + 15 / 24 + 0.5 / 240
        lon = -124 + 21 * 2 / 24 + 0.5 * 2 / 240
        self.assertEqual(airwsjtx_bridge.locator_to_latlon("CN87"), (47.5, -123))
        six_lat, six_lon = airwsjtx_bridge.locator_to_latlon("CN87VP")
        self.assertAlmostEqual(six_lat, 47 + 15.5 / 24)
        self.assertAlmostEqual(six_lon, -124 + 21.5 * 2 / 24)
        eight_lat, eight_lon = airwsjtx_bridge.locator_to_latlon("CN87VP00")
        self.assertAlmostEqual(eight_lat, lat)
        self.assertAlmostEqual(eight_lon, lon)
        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=lat, lon=lon,
            altitude_m=10000, velocity_ms=200, heading_deg=90,
            vertical_rate_ms=0, on_ground=False,
        )
        with patch.multiple(airwsjtx_bridge, _locked_aircraft_id="abc123",
                            _station_a_locator="CN87VP00", _station_b_locator="CN89", _rotator=None):
            with patch.object(airwsjtx_bridge, "_emit") as emit:
                airwsjtx_bridge._update_rotator_for_locked([ac])
                self.assertEqual(emit.call_args.args[0]["data"]["range_a_km"], 10.0)

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
               "scatter_score": 0.8, "off_path_deg": 12, "range_a_km": 125.6}
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
            altitude_m=10000.0,
            velocity_ms=200.0,
            heading_deg=90.0,
            vertical_rate_ms=0.0,
            on_ground=False,
        )
        with patch.object(airwsjtx_bridge, "_station_a_locator", "AA00"), \
             patch.object(airwsjtx_bridge, "_station_b_locator", "AA00"), \
             patch.object(airwsjtx_bridge, "locator_to_latlon",
                          side_effect=[(50.0, 0.0), (50.0, 4.0)]):
            selected = airwsjtx_bridge._select_auto_lock_candidate([ac])
            self.assertIsNotNone(selected)
            self.assertEqual(selected.icao24, "valid123")


class SourcePriorityDefaultAndPersistenceTests(unittest.TestCase):
    """dump1090 must be OFF by default; user opt-in must survive restarts."""

    def test_default_priority_excludes_dump1090(self) -> None:
        self.assertNotIn("dump1090", airwsjtx_bridge._DEFAULT_SOURCE_PRIORITY)
        self.assertIn("opensky", airwsjtx_bridge._DEFAULT_SOURCE_PRIORITY)

    def test_read_source_priority_returns_default_when_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            priority_file = os.path.join(tmpdir, "airwsjtx", "source_priority.json")
            with patch.object(airwsjtx_bridge, "_source_priority_path", return_value=priority_file):
                result = airwsjtx_bridge._read_source_priority()
        self.assertEqual(result, list(airwsjtx_bridge._DEFAULT_SOURCE_PRIORITY))
        self.assertNotIn("dump1090", result)

    def test_write_and_read_source_priority_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            priority_file = os.path.join(tmpdir, "source_priority.json")
            with patch.object(airwsjtx_bridge, "_source_priority_path", return_value=priority_file):
                airwsjtx_bridge._write_source_priority(["opensky", "dump1090"])
                result = airwsjtx_bridge._read_source_priority()
        self.assertEqual(result, ["opensky", "dump1090"])

    def test_read_source_priority_ignores_invalid_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            priority_file = os.path.join(tmpdir, "source_priority.json")
            with open(priority_file, "w") as fh:
                json.dump(["opensky", "invalid_source", "dump1090"], fh)
            with patch.object(airwsjtx_bridge, "_source_priority_path", return_value=priority_file):
                result = airwsjtx_bridge._read_source_priority()
        self.assertEqual(result, ["opensky", "dump1090"])

    def test_read_source_priority_falls_back_on_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            priority_file = os.path.join(tmpdir, "source_priority.json")
            with open(priority_file, "w") as fh:
                fh.write("not valid json{{")
            with patch.object(airwsjtx_bridge, "_source_priority_path", return_value=priority_file):
                result = airwsjtx_bridge._read_source_priority()
        self.assertEqual(result, list(airwsjtx_bridge._DEFAULT_SOURCE_PRIORITY))

    def test_airscout_set_sources_persists_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            priority_file = os.path.join(tmpdir, "source_priority.json")
            with patch.object(airwsjtx_bridge, "_source_priority_path", return_value=priority_file):
                with patch.multiple(airwsjtx_bridge, _source_priority=["opensky"],
                                    _active_source=None):
                    with patch.object(airwsjtx_bridge, "_start_configured_airscout_feed"):
                        with patch.object(airwsjtx_bridge, "_ok"):
                            with patch.object(airwsjtx_bridge, "_emit"):
                                airwsjtx_bridge.handle({
                                    "id": "set_src",
                                    "method": "airscout_set_sources",
                                    "params": {"sources": ["opensky", "dump1090"]},
                                })
                # Read back from file without mocking the module variable
                result = airwsjtx_bridge._read_source_priority()
        self.assertIn("dump1090", result)


if __name__ == "__main__":
    unittest.main()
