import unittest
from dataclasses import replace
from unittest.mock import patch

from airwsjtx.aircraft_metadata import normalize_category
from airwsjtx.ranking import apply_scatter_preferences, ranking_key, scatter_eligible
from airwsjtx.tracking import select_best_aircraft
from tests.test_aircraft_ranking import A, B, NOW, PLANE, geometry, ranked
from sidecar import airwsjtx_bridge as bridge


def preference_parity_cases():
    cases = []
    for category in (None, "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "B1", "B6", "C1"):
        for rate, timestamp in ((None, None), (0, NOW), (2, NOW - 45), (-3, NOW - 46), (3, NOW + 1)):
            ac = replace(PLANE, category=category, vertical_rate_ms=rate, position_updated_epoch_s=timestamp)
            for method in ("legacy", "range-product", "window-product"):
                for enabled in (False, True):
                    rank = ranked(ac, method)
                    cases.append({
                        "metadata": dict(category=category, vertical_rate_ms=rate,
                                         position_updated_epoch_s=timestamp, on_ground=False),
                        "rank": rank, "now": NOW, "method": method, "enabled": enabled,
                        "expected": apply_scatter_preferences(rank, ac, NOW, method, enabled=enabled),
                    })
    return cases


class AircraftMetadataTests(unittest.TestCase):
    def test_unknown_categories_are_neutral_and_old_construction_still_works(self):
        for category in (None, "", "A0", "B0", "C0", "D1", "A8", 5):
            self.assertIsNone(normalize_category(category))
            ac = replace(PLANE, category=category, vertical_rate_ms=None, position_updated_epoch_s=None)
            for method in ("legacy", "range-product", "window-product"):
                rank = ranked(ac, method)
                actual = apply_scatter_preferences(rank, ac, NOW, method)
                self.assertEqual(actual["scatter_adjustment_db"], 0)
                for key in rank:
                    self.assertEqual(actual[key], rank[key])

    def test_size_status_and_age_preferences_are_modest(self):
        for method in ("legacy", "range-product", "window-product"):
            values = []
            for category in ("A1", "A2", "A3", "A4", "A5"):
                ac = replace(PLANE, category=category)
                values.append(apply_scatter_preferences(ranked(ac, method), ac, NOW, method)["ranking_value"])
            self.assertEqual(values, sorted(values))
            self.assertGreater(values[-1], values[0])
        for category in ("A7", "B1", "B2", "B3", "B4", "B5", "B6", "B7"):
            ac = replace(PLANE, category=category)
            self.assertEqual(apply_scatter_preferences(ranked(ac), ac, NOW, "range-product")["scatter_adjustment_db"], -3)
        ac = replace(PLANE, category="A5", vertical_rate_ms=-3, position_updated_epoch_s=NOW - 46)
        result = apply_scatter_preferences(ranked(ac), ac, NOW, "range-product")
        self.assertAlmostEqual(result["scatter_adjustment_db"], -0.2)
        self.assertEqual(result["range_gain_db"], ranked(ac)["range_gain_db"])
        self.assertEqual(apply_scatter_preferences(ranked(ac), ac, NOW, "range-product", enabled=False)["ranking_value"],
                         ranked(ac)["ranking_value"])

    def test_geometry_still_dominates_and_flags_do_not_rank(self):
        heavy = replace(PLANE, category="A5", lon=1)
        light = replace(PLANE, category="A1")
        self.assertGreater(
            apply_scatter_preferences(ranked(light), light, NOW, "range-product")["ranking_value"],
            apply_scatter_preferences(ranked(heavy), heavy, NOW, "range-product")["ranking_value"])
        flagged = replace(PLANE, db_flags=15, squawk="7700", emergency="general", type_code="B77W")
        self.assertEqual(apply_scatter_preferences(ranked(flagged), flagged, NOW, "range-product"),
                         apply_scatter_preferences(ranked(PLANE), PLANE, NOW, "range-product"))

    def test_both_ends_and_fallback_order_use_identical_policy(self):
        for category in (None, "A5", "B6"):
            ac = replace(PLANE, category=category, heading_deg=None, vertical_rate_ms=3)
            for method in ("legacy", "range-product", "window-product"):
                a = apply_scatter_preferences(ranked(ac, method, A, B), ac, NOW, method)
                b = apply_scatter_preferences(ranked(ac, method, B, A), ac, NOW, method)
                self.assertEqual(a, b)
                self.assertEqual(ranking_key(geometry(ac)["scatter_score"], a, method),
                                 ranking_key(geometry(ac)["scatter_score"], b, method))

    def test_list_auto_lock_and_tracking_choose_heavy_with_equal_geometry(self):
        light = replace(PLANE, icao24="a-light", category="A1")
        heavy = replace(PLANE, icao24="z-heavy", category="A5")
        ground = replace(PLANE, icao24="ground", on_ground=True)
        surface = replace(PLANE, icao24="surface", category="C1")
        planes = [light, heavy, ground, surface]
        for method in ("legacy", "range-product", "window-product"):
            for enabled in (True, False):
                with self.subTest(method=method, enabled=enabled), patch.multiple(
                    bridge, _station_a_locator="A", _station_b_locator="B",
                    _station_a_alt_m=A[2], _station_b_alt_m=B[2],
                ), patch.object(bridge, "locator_to_latlon", side_effect=lambda grid: A[:2] if grid == "A" else B[:2]), \
                        patch.object(bridge, "_current_aircraft_snapshot", return_value=planes), \
                        patch.object(bridge, "_aircraft_matches_filters", return_value=True), \
                        patch.object(bridge.time, "time", return_value=NOW):
                    filters = {**bridge._DEFAULT_FILTERS, "ranking_method": method, "scatter_metadata": enabled}
                    rows, _ = bridge._get_scatter_aircraft_with_total(filters=filters, now=NOW)
                    expected = "z-heavy" if enabled else "a-light"
                    self.assertEqual(rows[0]["icao24"], expected)
                    self.assertEqual(bridge._select_auto_lock_candidate(list(reversed(planes)), filters=filters).icao24, expected)
                    self.assertEqual(set(row["icao24"] for row in rows), {"a-light", "z-heavy"})
                    self.assertEqual(rows[0]["category"], "A5" if enabled else "A1")
        with patch("airwsjtx.tracking.time.time", return_value=NOW):
            self.assertEqual(select_best_aircraft(planes, *A[:2], *B[:2], min_score=0)[0], heavy)
        self.assertFalse(scatter_eligible(ground))
        self.assertFalse(scatter_eligible(surface))

    def test_ground_or_surface_status_releases_existing_selection(self):
        for changes in (dict(on_ground=True), dict(category="C2")):
            ac = replace(PLANE, **changes)
            with patch.multiple(bridge, _locked_aircraft_id=ac.icao24, _station_a_locator="A"), \
                    patch.object(bridge, "_do_unlock") as unlock, patch.object(bridge, "_emit") as emit:
                bridge._update_rotator_for_locked([ac])
                unlock.assert_called_once()
                self.assertEqual(emit.call_args.args[0]["event"], "plane_out_of_window")

    def test_invalid_preference_configuration_is_not_persisted(self):
        with patch.object(bridge, "_err") as error, patch.object(bridge, "_write_filters") as write:
            bridge.handle({"id": "bad", "method": "airscout_apply_filters",
                           "params": {"filters": {"scatter_metadata": "yes"}}})
            error.assert_called_once()
            write.assert_not_called()
