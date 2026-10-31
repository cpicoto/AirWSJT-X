import json
import math
import os
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from airwsjtx.airscout import Aircraft
from airwsjtx.geo import scatter_geometry
from airwsjtx.ranking import RANKING_METHODS, rank_geometry, ranking_key, usable_seconds
from sidecar import airwsjtx_bridge as bridge


A, B = (0.0, 0.0, 150.0), (0.0, 2.0, 500.0)
NOW = 1000.0
PLANE = Aircraft("near", "NEAR", 0.0, 0.05, 11000, 200, 90, 0, False,
                 position_updated_epoch_s=NOW)


def geometry(ac, a=A, b=B):
    return scatter_geometry(*a[:2], *b[:2], ac.lat, ac.lon, ac.altitude_m, a[2], b[2])


def ranked(ac, method="range-product", a=A, b=B, now=NOW):
    return rank_geometry(ac, a, b, method, geometry(ac, a, b)["scatter_score"], now)


def parity_cases():
    cases = []
    for ac in (
        PLANE, replace(PLANE, heading_deg=0), replace(PLANE, lon=1),
        replace(PLANE, lat=0.1, lon=0.4), replace(PLANE, lon=-0.5),
        replace(PLANE, heading_deg=None), replace(PLANE, velocity_ms=0),
        replace(PLANE, position_updated_epoch_s=None),
        replace(PLANE, position_updated_epoch_s=954),
        replace(PLANE, position_updated_epoch_s=955),
        replace(PLANE, position_updated_epoch_s=1001),
    ):
        for a, b in ((A, B), (B, A), (A, A)):
            for method in RANKING_METHODS:
                score = geometry(ac, a, b)["scatter_score"]
                cases.append({
                    "aircraft": {
                        "lat": ac.lat, "lon": ac.lon, "altitude_ft": ac.altitude_m / 0.3048,
                        "heading_deg": ac.heading_deg,
                        "velocity_kts": ac.velocity_ms * 3600 / 1852,
                        "position_updated_epoch_s": ac.position_updated_epoch_s,
                    },
                    "a": dict(lat=a[0], lon=a[1], altitudeM=a[2]),
                    "b": dict(lat=b[0], lon=b[1], altitudeM=b[2]),
                    "method": method, "score": score, "now": NOW,
                    "expected": rank_geometry(ac, a, b, method, score, NOW),
                })
    return cases


class AircraftRankingTests(unittest.TestCase):
    def test_range_value_uses_both_independent_slants(self):
        geo = geometry(PLANE)
        expected = 20 * math.log10(250 ** 2 / (geo["range_a_km"] * geo["range_b_km"]))
        self.assertAlmostEqual(ranked(PLANE)["ranking_value"], expected, delta=0.02)
        self.assertNotEqual(geo["range_a_km"] + geo["range_b_km"], geo["dist_ab_km"])

    def test_near_endpoint_is_finite_and_range_ranking_can_reverse_legacy(self):
        middle = replace(PLANE, icao24="middle", lon=1.0)
        self.assertGreater(geometry(middle)["scatter_score"], geometry(PLANE)["scatter_score"])
        self.assertGreater(ranked(PLANE)["ranking_value"], ranked(middle)["ranking_value"])
        self.assertTrue(math.isfinite(ranked(PLANE)["ranking_value"]))
        overhead = replace(PLANE, lon=0.0)
        result = rank_geometry(overhead, A, A, "range-product", 0.5, NOW)
        self.assertTrue(math.isfinite(result["ranking_value"]))

    def test_station_swap_preserves_all_ranking_outputs(self):
        for method in RANKING_METHODS:
            self.assertEqual(ranked(PLANE, method), ranked(PLANE, method, B, A))

    def test_window_weights_remaining_time_in_linear_power_not_db(self):
        result = ranked(PLANE, "window-product")
        self.assertAlmostEqual(result["ranking_value"], result["range_gain_db"]
                               + 10 * math.log10(result["ranking_window_s"] / 60))
        shorter = ranked(replace(PLANE, heading_deg=270), "window-product")
        self.assertLess(shorter["ranking_value"], result["ranking_value"])
        aged = ranked(PLANE, "window-product", now=NOW + 20)
        self.assertAlmostEqual(aged["ranking_window_s"], result["ranking_window_s"] - 20)

    def test_missing_and_stale_motion_fall_back_explicitly(self):
        for changes in (
            dict(heading_deg=None), dict(velocity_ms=None), dict(velocity_ms=0),
            dict(velocity_ms=float("nan")), dict(position_updated_epoch_s=None),
            dict(position_updated_epoch_s=954), dict(position_updated_epoch_s=1001),
        ):
            with self.subTest(changes=changes):
                result = ranked(replace(PLANE, **changes), "window-product")
                self.assertIsNone(result["ranking_value"])
                self.assertIsNotNone(result["range_gain_db"])
                self.assertIn("Range fallback", result["ranking_reason"])
        self.assertIsNotNone(ranked(replace(PLANE, position_updated_epoch_s=955), "window-product")["ranking_value"])

    def test_known_windows_rank_before_range_fallback(self):
        known, unknown = ranked(PLANE, "window-product"), ranked(
            replace(PLANE, heading_deg=None), "window-product")
        self.assertGreater(ranking_key(0.1, known, "window-product"),
                           ranking_key(0.9, unknown, "window-product"))

    def test_north_track_advances_and_long_window_is_capped(self):
        north = usable_seconds(replace(PLANE, lon=1, heading_deg=0, velocity_ms=300), A, B)
        self.assertGreater(north[0], 0)
        self.assertLess(north[0], 1800)
        slow = ranked(replace(PLANE, lon=1, velocity_ms=1), "window-product")
        self.assertEqual(slow["ranking_window_s"], 1800)
        self.assertTrue(slow["ranking_window_capped"])

    def test_invalid_or_ineligible_geometry_has_no_power_estimate(self):
        for ac in (replace(PLANE, lon=-1), replace(PLANE, altitude_m=0),
                   replace(PLANE, lat=float("nan")), replace(PLANE, lat=91)):
            result = rank_geometry(ac, A, B, "range-product", 0 if ac.lon == -1 else 0.5, NOW)
            self.assertIsNone(result["ranking_value"])
        with self.assertRaises(ValueError):
            ranked(PLANE, "unsupported")

    def test_backend_list_and_autolock_use_same_ranking(self):
        middle = replace(PLANE, icao24="middle", lon=1)
        for method, expected in (("legacy", "middle"), ("range-product", "near"), ("window-product", "near")):
            with self.subTest(method=method), patch.multiple(
                bridge, _station_a_locator="A", _station_b_locator="B",
                _station_a_alt_m=A[2], _station_b_alt_m=B[2],
            ), patch.dict(bridge._aircraft_filters, {"ranking_method": method}), \
                    patch.object(bridge, "locator_to_latlon", side_effect=lambda grid: A[:2] if grid == "A" else B[:2]), \
                    patch.object(bridge, "_current_aircraft_snapshot", return_value=[PLANE, middle]), \
                    patch.object(bridge, "_aircraft_matches_filters", return_value=True), \
                    patch.object(bridge.time, "time", return_value=NOW):
                rows, _ = bridge._get_scatter_aircraft_with_total()
                self.assertEqual(rows[0]["icao24"], expected)
                self.assertEqual(bridge._select_auto_lock_candidate([PLANE, middle]).icao24, expected)

    def test_selection_does_not_replace_active_lock_or_change_score_threshold(self):
        from airwsjtx.state_machine import SessionState
        with patch.object(bridge, "_select_auto_lock_candidate") as select, \
                patch.object(bridge, "_do_lock") as lock, \
                patch.object(bridge.supervisor.session, "state", SessionState.ACTIVE_QSO):
            bridge._try_auto_lock([PLANE])
            select.assert_not_called()
            lock.assert_not_called()
        with patch.multiple(bridge, _station_a_locator="JJ00", _station_b_locator="JJ01"), \
                patch.object(bridge, "_aircraft_matches_filters", return_value=True), \
                patch.object(bridge, "scatter_geometry", return_value={"scatter_score": 0.049}), \
                patch.dict(bridge._aircraft_filters, {"ranking_method": "range-product"}):
            self.assertIsNone(bridge._select_auto_lock_candidate([PLANE]))

    def test_update_uses_one_policy_when_filters_change_during_ranking(self):
        middle = replace(PLANE, icao24="middle", lon=1)
        methods = []

        def rank_and_change_policy(ac, a, b, method, score, now):
            methods.append(method)
            bridge._aircraft_filters = {**bridge._DEFAULT_FILTERS, "ranking_method": "legacy"}
            return rank_geometry(ac, a, b, method, score, now)

        with patch.multiple(
            bridge, _station_a_locator="A", _station_b_locator="B",
            _station_a_alt_m=A[2], _station_b_alt_m=B[2], _locked_aircraft_id=None,
            _aircraft_filters={**bridge._DEFAULT_FILTERS, "ranking_method": "range-product"},
        ), patch.object(bridge, "locator_to_latlon", side_effect=lambda grid: A[:2] if grid == "A" else B[:2]), \
                patch.object(bridge, "_current_aircraft_snapshot", return_value=[PLANE, middle]), \
                patch.object(bridge, "_aircraft_matches_filters", return_value=True), \
                patch.object(bridge.time, "time", return_value=NOW), \
                patch.object(bridge, "rank_geometry", side_effect=rank_and_change_policy), \
                patch.object(bridge, "_try_auto_lock") as auto_lock, \
                patch.object(bridge, "_emit") as emit:
            bridge._emit_aircraft_update()
            payload = emit.call_args.args[0]["data"]
            self.assertEqual(methods, ["range-product"] * 4)
            self.assertEqual(payload["ranking_method"], "range-product")
            for key in ("aircraft", "flight_data"):
                self.assertEqual([row["icao24"] for row in payload[key]], ["near", "middle"])
                self.assertGreater(payload[key][0]["ranking_value"], 1)
            self.assertEqual(auto_lock.call_args.kwargs["filters"]["ranking_method"], "range-product")

    def test_method_persistence_and_invalid_rpc(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "filters.json")
            with patch.object(bridge, "_aircraft_filter_path", return_value=path):
                bridge._write_filters({**bridge._DEFAULT_FILTERS, "ranking_method": "window-product"})
                self.assertEqual(bridge._read_filters()["ranking_method"], "window-product")
                with open(path, encoding="utf-8") as file:
                    self.assertEqual(json.load(file)["ranking_method"], "window-product")
        with patch.object(bridge, "_err") as error, patch.object(bridge, "_write_filters") as write:
            bridge.handle({"id": "bad", "method": "airscout_apply_filters",
                           "params": {"filters": {"ranking_method": "bad"}}})
            error.assert_called_once()
            write.assert_not_called()

    def test_watched_flight_unlock_exemption_is_preserved_in_every_method(self):
        behind = replace(PLANE, lon=-0.1)
        for method in RANKING_METHODS:
            for watched in (True, False):
                with self.subTest(method=method, watched=watched), patch.multiple(
                    bridge, _station_a_locator="A", _station_b_locator="B",
                    _station_a_alt_m=A[2], _station_b_alt_m=B[2],
                    _locked_aircraft_id=behind.icao24, _rotator=None,
                ), patch.dict(bridge._aircraft_filters, {"ranking_method": method}), \
                        patch.object(bridge, "locator_to_latlon", side_effect=lambda grid: A[:2] if grid == "A" else B[:2]), \
                        patch.object(bridge, "_matches_priority_flight", return_value=watched), \
                        patch.object(bridge, "_do_unlock") as unlock, \
                        patch.object(bridge, "_emit") as emit:
                    bridge._update_rotator_for_locked([behind])
                    self.assertEqual(unlock.call_count, int(not watched))
                    if watched:
                        self.assertEqual(emit.call_args.args[0]["event"], "plane_tracking")
                        self.assertEqual(emit.call_args.args[0]["data"]["scatter_score"], 0)

    def test_persistence_failure_reports_error_and_leaves_active_policy(self):
        original = dict(bridge._aircraft_filters)
        with patch.object(bridge, "_write_filters", side_effect=OSError("disk full")), \
                patch.object(bridge, "_err") as error:
            bridge.handle({"id": "save", "method": "airscout_apply_filters",
                           "params": {"filters": {"ranking_method": "window-product"}}})
            self.assertEqual(bridge._aircraft_filters, original)
            self.assertIn("disk full", error.call_args.args[1])
