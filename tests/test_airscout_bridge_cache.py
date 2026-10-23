import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from airwsjtx.airscout import Aircraft
from sidecar import airwsjtx_bridge


class AirscoutBridgeCacheTests(unittest.TestCase):
    def test_settings_refresh_expires_cache_without_tracking_or_relocking(self) -> None:
        from airwsjtx.supervisor import AirWSJTXSupervisor

        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False,
        )
        expiry = airwsjtx_bridge._aircraft_cache_expire_after_s
        for age, expired in ((expiry, False), (expiry + 0.1, True)):
            for locked in (False, True):
                with self.subTest(age=age, locked=locked), patch.multiple(
                    airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                    _station_b_alt_m=0.0, _aircraft_cache=[ac],
                    _aircraft_last_update_monotonic=1000.0,
                    _aircraft_last_update_epoch_s=123456,
                    _locked_aircraft_id=ac.icao24 if locked else None, _lock_score=0.5,
                    supervisor=AirWSJTXSupervisor(),
                ), patch.dict(airwsjtx_bridge._aircraft_filters,
                              airwsjtx_bridge._DEFAULT_FILTERS, clear=True), \
                        patch.object(airwsjtx_bridge.time, "monotonic", return_value=1000.0 + age), \
                        patch.object(airwsjtx_bridge, "_update_rotator_for_locked") as track, \
                        patch.object(airwsjtx_bridge, "_try_auto_lock") as auto_lock, \
                        patch.object(airwsjtx_bridge, "_emit") as emit:
                    airwsjtx_bridge.handle({
                        "id": "alt", "method": "set_dx_altitude",
                        "params": {"altitude_m": 200},
                    })
                    data = next(call.args[0]["data"] for call in emit.call_args_list
                                if call.args[0].get("event") == "airscout_update")
                    self.assertEqual(data["data_freshness"], "expired" if expired else "stale")
                    self.assertEqual(len(data["aircraft"]), 0 if expired else 1)
                    self.assertEqual(len(data["flight_data"]), 0 if expired else 1)
                    self.assertEqual(data["total"], 0 if expired else 1)
                    self.assertEqual(track.call_count, int(locked and not expired))
                    self.assertEqual(airwsjtx_bridge._locked_aircraft_id,
                                     ac.icao24 if locked and not expired else None)
                    auto_lock.assert_not_called()

    def test_feed_refresh_does_not_auto_lock_expired_cache(self) -> None:
        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False,
        )
        with patch.multiple(
            airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
            _aircraft_cache=[ac], _locked_aircraft_id=None,
            _aircraft_last_update_monotonic=1000.0,
        ), patch.object(airwsjtx_bridge.time, "monotonic", return_value=2000.0), \
                patch.object(airwsjtx_bridge, "_try_auto_lock") as auto_lock, \
                patch.object(airwsjtx_bridge, "_emit"):
            airwsjtx_bridge._emit_aircraft_update()
            auto_lock.assert_called_once_with([])

    def test_station_filter_and_source_changes_emit_aircraft_update(self) -> None:
        with patch.multiple(airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                            _station_a_alt_m=0.0, _station_b_alt_m=0.0,
                            _source_priority=["opensky"], _active_source=None,
                            _my_call_override=None, _my_grid_override=None):
            with patch.object(airwsjtx_bridge, "_emit"), \
                    patch.object(airwsjtx_bridge, "_emit_aircraft_update") as emit_update, \
                    patch.object(airwsjtx_bridge, "_start_configured_airscout_feed"), \
                    patch.object(airwsjtx_bridge, "_write_filters"), \
                    patch.object(airwsjtx_bridge, "_write_source_priority"):
                requests = [
                    {"method": "airscout_set_stations",
                     "params": {"locator_a": "CN88", "locator_b": "CN90"}},
                    {"method": "set_my_station_override",
                     "params": {"grid": "CN88", "altitude_m": 100}},
                    {"method": "set_dx_altitude", "params": {"altitude_m": 200}},
                    {"method": "airscout_apply_filters",
                     "params": {"filters": {"priority_flight": "UAL123"}}},
                    {"method": "airscout_set_sources", "params": {"sources": ["dump1090"]}},
                ]
                for index, request in enumerate(requests):
                    with self.subTest(method=request["method"]):
                        emit_update.reset_mock()
                        airwsjtx_bridge.handle({"id": str(index), **request})
                        emit_update.assert_called_once_with(try_auto_lock=False)

    def test_startup_loads_numeric_filters_but_no_active_flight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "filters.json")
            with open(path, "w", encoding="utf-8") as file:
                json.dump({
                    "altitude_min_ft": 20000,
                    "priority_flight": "UAL123",
                    "priority_flight_exclusive": True,
                }, file)
            with patch.object(airwsjtx_bridge, "_aircraft_filter_path", return_value=path):
                filters = airwsjtx_bridge._read_filters()
        self.assertEqual(filters["altitude_min_ft"], 20000)
        self.assertEqual(filters["priority_flight"], "")
        self.assertFalse(filters["priority_flight_exclusive"])

    def test_station_altitude_rpcs_reach_all_scatter_geometry_paths(self) -> None:
        from airwsjtx.supervisor import AirWSJTXSupervisor

        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False,
        )
        with patch.multiple(airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                            _my_call_override=None, _my_grid_override=None, _station_a_call=None,
                            _station_a_alt_m=0.0, _station_b_alt_m=0.0,
                            _locked_aircraft_id=None, _lock_score=0.0, _rotator=None,
                            _wsjtx_band_mhz=None, _wsjtx_freq_hz=None,
                            supervisor=AirWSJTXSupervisor()), \
                patch.object(airwsjtx_bridge, "_start_configured_airscout_feed"), \
                patch.object(airwsjtx_bridge, "_emit_aircraft_update"), \
                patch.object(airwsjtx_bridge, "_emit"), \
                patch.object(airwsjtx_bridge, "_aircraft_matches_filters", return_value=True), \
                patch.object(airwsjtx_bridge, "scatter_geometry",
                             wraps=airwsjtx_bridge.scatter_geometry) as geometry:
            airwsjtx_bridge.handle({
                "id": "my", "method": "set_my_station_override",
                "params": {"grid": "CN87", "altitude_m": 1250},
            })
            airwsjtx_bridge.handle({
                "id": "dx", "method": "set_dx_altitude", "params": {"altitude_m": 850},
            })
            airwsjtx_bridge._replace_aircraft_cache([ac])
            self.assertEqual(len(airwsjtx_bridge._get_scatter_aircraft()), 1)
            self.assertEqual(len(airwsjtx_bridge._get_scatter_aircraft_with_total()[0]), 1)
            self.assertEqual(airwsjtx_bridge._select_auto_lock_candidate([ac]), ac)
            self.assertIsNotNone(airwsjtx_bridge._do_lock(ac.icao24))
            airwsjtx_bridge._update_rotator_for_locked([ac])
            self.assertEqual(geometry.call_count, 5)
            for call in geometry.call_args_list:
                self.assertEqual(call.args[-2:], (1250.0, 850.0))

    def test_aircraft_update_retains_locked_aircraft_beyond_payload_limit(self) -> None:
        ranked = [
            {"icao24": f"ac{i}", "lat": 47.5, "lon": -122.4, "altitude_ft": 33000,
             "heading_deg": 90, "velocity_kts": 450, "scatter_score": 1 - i / 100}
            for i in range(25)
        ]
        for locked_id in [None, "ac3", "ac24", "missing"]:
            with self.subTest(locked_id=locked_id):
                with patch.object(airwsjtx_bridge, "_locked_aircraft_id", locked_id), \
                        patch.object(airwsjtx_bridge, "_get_scatter_aircraft_with_total",
                                     return_value=(ranked, 45)), \
                        patch.object(airwsjtx_bridge, "_update_rotator_for_locked"), \
                        patch.object(airwsjtx_bridge, "_do_unlock") as unlock, \
                        patch.object(airwsjtx_bridge, "_try_auto_lock"), \
                        patch.object(airwsjtx_bridge, "_emit") as emit:
                    airwsjtx_bridge._emit_aircraft_update()
                payload = emit.call_args.args[0]["data"]
                expected = ranked[:20] + ([ranked[24]] if locked_id == "ac24" else [])
                self.assertEqual(payload["aircraft"], expected)
                self.assertEqual(payload["flight_data"], ranked)
                self.assertEqual(payload["total"], 45)
                self.assertEqual(len({ac["icao24"] for ac in payload["aircraft"]}), len(expected))
                if locked_id == "missing":
                    unlock.assert_called_once_with()
                else:
                    unlock.assert_not_called()

    def test_aircraft_update_unlocks_when_locked_aircraft_has_no_current_candidate_data(self) -> None:
        from airwsjtx.supervisor import AirWSJTXSupervisor

        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False,
        )
        for reason in ("missing", "no_position", "on_ground", "filtered"):
            with self.subTest(reason=reason):
                aircraft = [] if reason == "missing" else [ac]
                with patch.multiple(
                    airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                    _aircraft_cache=aircraft, _locked_aircraft_id=ac.icao24, _lock_score=0.5,
                    supervisor=AirWSJTXSupervisor(),
                ), patch.object(ac, "lat", None if reason == "no_position" else ac.lat), \
                        patch.object(ac, "on_ground", reason == "on_ground"), \
                        patch.object(airwsjtx_bridge, "_aircraft_matches_filters",
                                     return_value=reason != "filtered"), \
                        patch.object(airwsjtx_bridge, "_update_rotator_for_locked") as track, \
                        patch.object(airwsjtx_bridge, "_try_auto_lock") as auto_lock, \
                        patch.object(airwsjtx_bridge, "_emit") as emit:
                    airwsjtx_bridge._emit_aircraft_update()
                    self.assertIsNone(airwsjtx_bridge._locked_aircraft_id)
                    self.assertEqual(airwsjtx_bridge._lock_score, 0.0)
                    self.assertEqual(emit.call_args.args[0]["event"], "plane_unlocked")
                    track.assert_not_called()
                    auto_lock.assert_not_called()

    def test_scatter_payload_preserves_due_north_heading_for_course_prediction(self) -> None:
        aircraft = Aircraft(
            icao24="abc123", callsign="NORTH", lat=47.5, lon=-122.4,
            altitude_m=10000, velocity_ms=200, heading_deg=0,
            vertical_rate_ms=0, on_ground=False,
        )
        with patch.multiple(airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89"), \
                patch.object(airwsjtx_bridge, "_aircraft_matches_filters", return_value=True):
            airwsjtx_bridge._replace_aircraft_cache([aircraft])
            ranked, _ = airwsjtx_bridge._get_scatter_aircraft_with_total()
            self.assertEqual(ranked[0]["heading_deg"], 0)
            self.assertEqual(airwsjtx_bridge._get_scatter_aircraft()[0]["heading_deg"], 0)

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

    def test_aircraft_must_be_within_position_tolerance_of_path_bearing(self) -> None:
        def plane(lon: float) -> Aircraft:
            return Aircraft(
                icao24="abc123", callsign="T", lat=40.0, lon=lon,
                altitude_m=10000, velocity_ms=200, heading_deg=90,
                vertical_rate_ms=0, on_ground=False,
            )

        # DX due east of MY: eastern plane kept, western plane rejected.
        with patch.dict(airwsjtx_bridge._aircraft_filters, {"position_tolerance_deg": 70.0}):
            self.assertTrue(airwsjtx_bridge._aircraft_matches_filters(plane(-99.0), 40.0, -100.0, 40.0, -80.0))
            self.assertFalse(airwsjtx_bridge._aircraft_matches_filters(plane(-101.0), 40.0, -100.0, 40.0, -80.0))

    def test_approaching_aircraft_remains_a_candidate_and_locked(self) -> None:
        ac = Aircraft(
            icao24="approaching", callsign="APPROACH", lat=48.5, lon=-123,
            altitude_m=10000, velocity_ms=200, heading_deg=180,
            vertical_rate_ms=0, on_ground=False,
        )
        with patch.multiple(airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                            _aircraft_cache=[ac], _locked_aircraft_id=ac.icao24), \
                patch.dict(airwsjtx_bridge._aircraft_filters, airwsjtx_bridge._DEFAULT_FILTERS, clear=True), \
                patch.object(airwsjtx_bridge, "_update_rotator_for_locked") as track, \
                patch.object(airwsjtx_bridge, "_do_unlock") as unlock, \
                patch.object(airwsjtx_bridge, "_emit") as emit:
            airwsjtx_bridge._emit_aircraft_update()
            self.assertEqual([row["icao24"] for row in emit.call_args.args[0]["data"]["aircraft"]], [ac.icao24])
            track.assert_called_once()
            unlock.assert_not_called()

    def test_priority_flight_bypasses_filters_and_can_be_exclusive(self) -> None:
        priority = Aircraft(
            icao24="priority", callsign=" UAL123 ", lat=40.0, lon=-101.0,
            altitude_m=10000, velocity_ms=200, heading_deg=270,
            vertical_rate_ms=0, on_ground=False,
        )
        normal = Aircraft(
            icao24="normal", callsign="OTHER", lat=40.0, lon=-99.0,
            altitude_m=10000, velocity_ms=200, heading_deg=90,
            vertical_rate_ms=0, on_ground=False,
        )
        filters = {
            "lat_min": 45.0,
            "lat_max": None,
            "lon_min": -100.0,
            "lon_max": None,
            "altitude_min_ft": 20000.0,
            "heading_tolerance_deg": 10.0,
            "position_tolerance_deg": 10.0,
            "priority_flight": "ual123",
            "priority_flight_exclusive": False,
        }
        with patch.dict(airwsjtx_bridge._aircraft_filters, filters, clear=True):
            self.assertTrue(airwsjtx_bridge._aircraft_matches_filters(
                priority, 40.0, -100.0, 40.0, -80.0,
            ))
            self.assertFalse(airwsjtx_bridge._aircraft_matches_filters(
                normal, 40.0, -100.0, 40.0, -80.0,
            ))

            airwsjtx_bridge._aircraft_filters.update({
                "lat_min": None,
                "lon_min": None,
                "altitude_min_ft": None,
                "heading_tolerance_deg": 180.0,
                "position_tolerance_deg": 180.0,
            })
            with patch.multiple(
                airwsjtx_bridge,
                _station_a_locator="CN87",
                _station_b_locator="CN89",
            ), patch.object(
                airwsjtx_bridge, "scatter_geometry",
                return_value={"scatter_score": 0.73},
            ):
                airwsjtx_bridge._replace_aircraft_cache([priority, normal])
                additive, total = airwsjtx_bridge._get_scatter_aircraft_with_total()
                airwsjtx_bridge._aircraft_filters["priority_flight_exclusive"] = True
                exclusive, exclusive_total = airwsjtx_bridge._get_scatter_aircraft_with_total()

        self.assertEqual(total, 2)
        self.assertEqual(exclusive_total, 2)
        self.assertEqual([ac["icao24"] for ac in additive], ["priority", "normal"])
        self.assertEqual([ac["scatter_score"] for ac in additive], [0.73, 0.73])
        self.assertEqual([ac["icao24"] for ac in exclusive], ["priority"])
        self.assertEqual(exclusive[0]["scatter_score"], 0.73)

    def test_disabling_only_restores_ranked_candidates_while_favorite_is_locked(self) -> None:
        planes = [
            Aircraft(
                icao24=icao, callsign=call, lat=lat, lon=-123,
                altitude_m=10000, velocity_ms=200, heading_deg=heading,
                vertical_rate_ms=0, on_ground=False,
            )
            for icao, call, lat, heading in [
                ("favorite", "JBU397", 46.5, 180),
                ("approaching", "APPROACH", 48.5, 180),
                ("departing", "DEPART", 48.5, 0),
            ]
        ]
        with patch.multiple(airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
                            _aircraft_cache=planes, _locked_aircraft_id="favorite"), \
                patch.dict(airwsjtx_bridge._aircraft_filters, airwsjtx_bridge._DEFAULT_FILTERS, clear=True), \
                patch.object(airwsjtx_bridge, "_write_filters"), \
                patch.object(airwsjtx_bridge, "_update_rotator_for_locked"), \
                patch.object(airwsjtx_bridge, "_do_unlock") as unlock, \
                patch.object(airwsjtx_bridge, "_emit") as emit:
            for only, expected in [
                (True, ["favorite"]),
                (False, ["approaching", "departing", "favorite"]),
            ]:
                with self.subTest(only=only):
                    emit.reset_mock()
                    airwsjtx_bridge.handle({
                        "id": "filters", "method": "airscout_apply_filters",
                        "params": {"filters": {
                            **airwsjtx_bridge._DEFAULT_FILTERS,
                            "priority_flight": "JBU397", "priority_flight_exclusive": only,
                        }},
                    })
                    update = next(call.args[0]["data"] for call in emit.call_args_list
                                  if call.args[0].get("event") == "airscout_update")
                    self.assertEqual([ac["icao24"] for ac in update["aircraft"]], expected)
                    self.assertGreater(update["flight_data"][0]["scatter_score"], 0)
                    unlock.assert_not_called()

    def test_flight_data_bypasses_all_filters_without_changing_candidates(self) -> None:
        planes = [
            Aircraft(
                icao24=f"ac{i}", callsign=f"FLIGHT{i}", lat=47.0 + i / 100, lon=-122.4,
                altitude_m=10000, velocity_ms=200, heading_deg=270,
                vertical_rate_ms=0, on_ground=False,
            )
            for i in range(25)
        ]
        planes.append(Aircraft(
            icao24="no-position", callsign="MISSING", lat=None, lon=None,
            altitude_m=10000, velocity_ms=200, heading_deg=270,
            vertical_rate_ms=0, on_ground=False,
        ))
        with patch.multiple(
            airwsjtx_bridge, _station_a_locator="CN87", _station_b_locator="CN89",
        ), patch.dict(airwsjtx_bridge._aircraft_filters, {
            "priority_flight": "FLIGHT0", "priority_flight_exclusive": True,
            "lat_min": 90.0, "altitude_min_ft": 50000.0,
        }), patch.object(
            airwsjtx_bridge, "scatter_geometry",
            side_effect=lambda *args: {"scatter_score": round(args[4] - 47.0, 2)},
        ):
            airwsjtx_bridge._replace_aircraft_cache(planes)
            candidates, _ = airwsjtx_bridge._get_scatter_aircraft_with_total()
            flight_data, total = airwsjtx_bridge._get_scatter_aircraft_with_total(apply_filters=False)
        self.assertEqual([ac["callsign"] for ac in candidates], ["FLIGHT0"])
        self.assertEqual(len(flight_data), 25)
        self.assertEqual(total, 25)
        self.assertNotIn("no-position", [ac["icao24"] for ac in flight_data])
        self.assertEqual(
            [ac["scatter_score"] for ac in flight_data],
            [i / 100 for i in reversed(range(25))],
        )

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

    def test_dump1090_host_is_validated_persisted_and_restarts_running_feed(self) -> None:
        with patch.multiple(airwsjtx_bridge, _dump1090_host="127.0.0.1", _dump1090_feed=object()):
            with patch.object(airwsjtx_bridge, "_write_dump1090_host") as write, \
                    patch.object(airwsjtx_bridge, "_start_dump1090_feed") as start, \
                    patch.object(airwsjtx_bridge, "_ok") as ok, \
                    patch.object(airwsjtx_bridge, "_err") as err:
                airwsjtx_bridge.handle({"id": "h", "method": "airscout_set_dump1090_host",
                                        "params": {"host": " 192.168.1.50 "}})
                write.assert_called_once_with("192.168.1.50")
                start.assert_called_once_with("192.168.1.50", 30003)
                self.assertEqual(ok.call_args.args[1], {"host": "192.168.1.50", "port": 30003})
                airwsjtx_bridge.handle({"id": "h", "method": "airscout_set_dump1090_host",
                                        "params": {"host": "bad host;"}})
                self.assertTrue(err.called)
                airwsjtx_bridge.handle({"id": "h", "method": "airscout_set_dump1090_host",
                                        "params": {"host": ""}})
                self.assertEqual(ok.call_args.args[1]["host"], "127.0.0.1")

    def test_dump1090_report_emits_activity_summary(self) -> None:
        ac = Aircraft(
            icao24="abc123", callsign="TEST1", lat=47.5, lon=-122.4,
            altitude_m=9000, velocity_ms=200, heading_deg=90,
            vertical_rate_ms=0, on_ground=False,
        )
        nopos = Aircraft(
            icao24="def456", callsign=None, lat=None, lon=None,
            altitude_m=None, velocity_ms=None, heading_deg=None,
            vertical_rate_ms=None, on_ground=False,
        )
        captured: dict = {}

        class FakeFeed:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)
                self.messages_received = 7
                self.last_message_epoch_s = time.time() - 2

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        with patch.object(airwsjtx_bridge, "Dump1090Feed", FakeFeed), \
                patch.object(airwsjtx_bridge, "_emit") as emit, \
                patch.object(airwsjtx_bridge, "_emit_aircraft_update"), \
                patch.object(airwsjtx_bridge, "_replace_aircraft_cache"), \
                patch.object(airwsjtx_bridge, "_set_source_status"), \
                patch.object(airwsjtx_bridge, "_active_source", None):
            airwsjtx_bridge._start_dump1090_feed("192.168.2.58", 30003)
            captured["on_update"]([ac, nopos])
        event = next(c.args[0] for c in emit.call_args_list if c.args[0].get("event") == "dump1090_activity")
        self.assertEqual(event["data"]["host"], "192.168.2.58")
        self.assertEqual(event["data"]["messages"], 7)
        self.assertEqual(event["data"]["aircraft"], 2)
        self.assertEqual(event["data"]["with_position"], 1)
        self.assertGreaterEqual(event["data"]["last_message_age_s"], 2)

    def test_dump1090_busy_sets_busy_state_and_emits_event(self) -> None:
        captured: dict = {}

        class FakeFeed:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

            def start(self) -> None:
                pass

            def stop(self) -> None:
                pass

        with patch.object(airwsjtx_bridge, "Dump1090Feed", FakeFeed), \
                patch.object(airwsjtx_bridge, "_emit") as emit, \
                patch.object(airwsjtx_bridge, "_set_source_status") as status, \
                patch.object(airwsjtx_bridge, "_active_source", None):
            airwsjtx_bridge._start_dump1090_feed("192.168.2.58", 30003)
            captured["on_busy"]("RTL-SDR in use by rtl_tcp")
        status.assert_called_with("dump1090", "busy", "RTL-SDR in use by rtl_tcp")
        event = next(c.args[0] for c in emit.call_args_list if c.args[0].get("event") == "dump1090_busy")
        self.assertEqual(event["data"], {"host": "192.168.2.58", "port": 30003,
                                         "message": "RTL-SDR in use by rtl_tcp"})

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

    def test_priority_flight_remains_locked_outside_scatter_window(self) -> None:
        ac = Aircraft(
            icao24="abc123", callsign=" UAL123 ", lat=47.5, lon=-122.4,
            altitude_m=10000, velocity_ms=200, heading_deg=270,
            vertical_rate_ms=0, on_ground=False,
        )
        geo = {"az_a": 270, "el_a": 5, "az_b": 234, "el_b": 6,
               "scatter_score": 0.0, "off_path_deg": 180, "range_a_km": 125.6}
        for flight, exclusive, retained in [
            ("ual123", False, True),
            ("UAL123", True, True),
            ("OTHER", False, False),
            ("", True, False),
        ]:
            with self.subTest(flight=flight, exclusive=exclusive):
                with patch.multiple(
                    airwsjtx_bridge, _locked_aircraft_id="abc123",
                    _station_a_locator="CN87", _station_b_locator="CN89", _rotator=None,
                ), patch.dict(
                    airwsjtx_bridge._aircraft_filters,
                    {"priority_flight": flight, "priority_flight_exclusive": exclusive},
                ), patch.object(
                    airwsjtx_bridge, "scatter_geometry", return_value=geo,
                ), patch.object(airwsjtx_bridge, "_emit") as emit, patch.object(
                    airwsjtx_bridge, "_do_unlock",
                ) as unlock:
                    airwsjtx_bridge._update_rotator_for_locked([ac])
                    if retained:
                        unlock.assert_not_called()
                        event = emit.call_args.args[0]
                        self.assertEqual(event["event"], "plane_tracking")
                        self.assertEqual(event["data"]["scatter_score"], 0.0)
                        self.assertEqual(airwsjtx_bridge._locked_aircraft_id, "abc123")
                    else:
                        unlock.assert_called_once_with()
                        self.assertEqual(emit.call_args.args[0]["event"], "plane_out_of_window")

    def setUp(self) -> None:
        filters = patch.object(airwsjtx_bridge, "_aircraft_filters", dict(airwsjtx_bridge._DEFAULT_FILTERS))
        filters.start()
        self.addCleanup(filters.stop)
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
                            with patch.object(airwsjtx_bridge, "_emit"), \
                                    patch.object(airwsjtx_bridge, "_emit_aircraft_update"):
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
