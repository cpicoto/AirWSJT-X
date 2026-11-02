from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import time
import unittest
from unittest.mock import patch

from airwsjtx.airscout import Aircraft
from airwsjtx.geo import scatter_geometry
from airwsjtx.ranking import RANKING_METHODS, usable_seconds, within_qso_window
from airwsjtx.supervisor import AirWSJTXSupervisor
from sidecar import airwsjtx_bridge as bridge


class QSOViabilityTests(unittest.TestCase):
    def setUp(self):
        self.selected = Aircraft(
            "old", "WATCH", 0, 3, 10000, 200, 90, 0, False,
            position_updated_epoch_s=time.time())
        self.best = replace(self.selected, icao24="best", callsign="BEST", lon=1)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.multiple(
            bridge, _locked_aircraft_id="old", _manual_aircraft_lock=False,
            _manual_lock_source="opensky", _manual_lock_last_seen=time.monotonic(),
            _active_source="opensky",
            _station_a_locator="A", _station_b_locator="B",
            _station_a_alt_m=0, _station_b_alt_m=0,
            _auto_lock_enabled=True, _rotator=None, _satpc32_dde=None,
            _aircraft_filters=dict(bridge._DEFAULT_FILTERS),
            supervisor=AirWSJTXSupervisor()))
        stack.enter_context(patch.object(
            bridge, "locator_to_latlon", side_effect=lambda grid: (0, 0) if grid == "A" else (0, 2)))
        self.snapshot = stack.enter_context(patch.object(
            bridge, "_current_aircraft_snapshot", return_value=[self.selected, self.best]))
        stack.enter_context(patch.object(bridge, "_aircraft_matches_filters", return_value=True))
        stack.enter_context(patch.object(bridge, "_emit"))
        self.hamlib = stack.enter_context(patch.object(bridge, "_publish_hamlib_target"))
        self.dde = stack.enter_context(patch.object(bridge, "_publish_dde_target"))
        self.lock = stack.enter_context(patch.object(bridge, "_do_lock"))

    def test_auto_and_manual_selection_handover_in_same_update_without_old_target(self):
        for manual in (False, True):
            with self.subTest(manual=manual), patch.multiple(
                bridge, _locked_aircraft_id="old", _manual_aircraft_lock=manual,
            ):
                self.lock.reset_mock()
                bridge._emit_aircraft_update()
                self.assertIsNone(bridge._locked_aircraft_id)
                self.lock.assert_called_once_with("best", manual=False)
                self.hamlib.assert_not_called()
                self.dde.assert_not_called()

    def test_no_replacement_or_auto_lock_off_releases_without_commands(self):
        for enabled, candidates in ((True, [self.selected]), (False, [self.selected, self.best])):
            with self.subTest(enabled=enabled), patch.multiple(
                bridge, _locked_aircraft_id="old", _auto_lock_enabled=enabled,
            ):
                self.snapshot.return_value = candidates
                bridge._emit_aircraft_update()
                self.assertIsNone(bridge._locked_aircraft_id)
                self.lock.assert_not_called()
                self.hamlib.assert_not_called()

    def test_priority_does_not_keep_invalid_plane_or_replace_with_unrelated_flight(self):
        for exclusive in (False, True):
            with self.subTest(exclusive=exclusive), patch.multiple(
                bridge, _locked_aircraft_id="old", _manual_aircraft_lock=True,
            ), patch.dict(bridge._aircraft_filters, {
                "priority_flight": "WATCH", "priority_flight_exclusive": exclusive,
            }):
                bridge._emit_aircraft_update()
                self.assertIsNone(bridge._locked_aircraft_id)
                self.lock.assert_not_called()
                self.hamlib.assert_not_called()
                self.assertIsNone(bridge._select_auto_lock_candidate([self.selected, self.best]))

    def test_priority_replacement_respects_matching_flight(self):
        self.best.callsign = "WATCH"
        with patch.dict(bridge._aircraft_filters, {"priority_flight": "WATCH"}):
            bridge._emit_aircraft_update()
        self.lock.assert_called_once_with("best", manual=False)
        self.hamlib.assert_not_called()

    def test_low_score_alone_does_not_end_mutual_window(self):
        self.selected.lon = 1
        geo = scatter_geometry(0, 0, 0, 2, 0, 1, 10000)
        geo["scatter_score"] = 0.049
        with patch.object(bridge, "scatter_geometry", return_value=geo):
            bridge._update_rotator_for_locked([self.selected])
        self.assertEqual(bridge._locked_aircraft_id, "old")
        self.hamlib.assert_called_once()

    def test_viable_lock_is_not_replaced_by_higher_rank(self):
        self.selected.lon = 0.1
        for manual in (False, True):
            bridge._manual_aircraft_lock = manual
            for method in RANKING_METHODS:
                with self.subTest(method=method, manual=manual), patch.dict(
                    bridge._aircraft_filters, {"ranking_method": method},
                ):
                    bridge._emit_aircraft_update()
                    self.assertEqual(bridge._locked_aircraft_id, "old")
                    self.lock.assert_not_called()
        self.assertEqual(self.hamlib.call_count, 2 * len(RANKING_METHODS))

    def test_handover_publishes_only_viable_replacement(self):
        self.lock.side_effect = self.original_lock
        with patch.object(bridge, "_aircraft_cache", [self.selected, self.best]):
            bridge._emit_aircraft_update()
        self.assertEqual(bridge._locked_aircraft_id, "best")
        self.hamlib.assert_called_once()
        self.assertEqual(self.hamlib.call_args.args[0].icao24, "best")
        self.dde.assert_called_once()
        self.assertEqual(self.dde.call_args.args[0].icao24, "best")

    def test_ranking_and_auto_selection_exclude_outside_window(self):
        for method in RANKING_METHODS:
            with self.subTest(method=method), patch.dict(
                bridge._aircraft_filters, {"ranking_method": method},
            ):
                self.assertEqual(bridge._select_auto_lock_candidate(
                    [self.selected, self.best]), self.best)
                geo = scatter_geometry(0, 0, 0, 2, 0, 3, 10000)
                self.assertFalse(within_qso_window(geo))
                self.assertEqual(usable_seconds(self.selected, (0, 0, 0), (0, 2, 0))[0], 0)

    def test_future_entry_is_not_held_under_current_window_policy(self):
        self.selected.lon, self.selected.heading_deg = -0.01, 90
        bridge._manual_aircraft_lock = True
        bridge._update_rotator_for_locked([self.selected])
        self.assertIsNone(bridge._locked_aircraft_id)
        self.hamlib.assert_not_called()
        self.dde.assert_not_called()

    def test_remaining_current_window_is_kept(self):
        self.selected.lon = 1.99
        bridge._manual_aircraft_lock = True
        remaining, _ = usable_seconds(self.selected, (0, 0, 0), (0, 2, 0))
        self.assertGreater(remaining, 0)
        bridge._update_rotator_for_locked([self.selected])
        self.assertEqual(bridge._locked_aircraft_id, "old")
        self.hamlib.assert_called_once()

    def test_local_position_gap_retains_selection_without_pointing(self):
        self.snapshot.return_value = [replace(self.selected, lat=None)]
        bridge._active_source = "rtlsdr"
        bridge._emit_aircraft_update()
        self.assertEqual(bridge._locked_aircraft_id, "old")
        self.hamlib.assert_not_called()
        self.dde.assert_not_called()
        self.lock.assert_not_called()

    def test_manual_selection_outside_window_does_not_change_existing_lock(self):
        with patch.object(bridge, "_aircraft_cache", [self.selected]):
            with self.assertRaisesRegex(ValueError, "outside the current mutual QSO window"):
                self.original_lock(self.selected.icao24)
        self.assertEqual(bridge._locked_aircraft_id, "old")

    def test_window_boundary_uses_unrounded_geometry_not_display_bearing(self):
        geo = scatter_geometry(0, 0, 0, 2, 1, 0.00001, 10000)
        self.assertEqual(geo["off_path_deg"], 90)
        self.assertTrue(within_qso_window(geo))
        geo = scatter_geometry(0, 0, 0, 2, 1, -0.00001, 10000)
        self.assertEqual(geo["off_path_deg"], 90)
        self.assertFalse(within_qso_window(geo))

    original_lock = staticmethod(bridge._do_lock)


if __name__ == "__main__":
    unittest.main()
