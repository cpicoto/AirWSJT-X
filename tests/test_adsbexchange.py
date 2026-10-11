import gzip
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from airwsjtx.airscout import ADSBExchangeFeed
from sidecar import airwsjtx_bridge as bridge


class ADSBExchangeFeedTests(unittest.TestCase):
    def fetch(self, data: object, compressed: bool = False):
        response = MagicMock()
        body = json.dumps(data).encode()
        response.__enter__.return_value.read.return_value = gzip.compress(body) if compressed else body
        response.__enter__.return_value.headers = {"Content-Encoding": "gzip"} if compressed else {}
        feed = ADSBExchangeFeed("test-key", 47, -122, 250)
        with patch("airwsjtx.airscout.urllib.request.urlopen", return_value=response) as request:
            aircraft = feed.fetch_once()
        return feed, aircraft, request.call_args.args[0]

    def test_gzip_authentication_query_and_unit_conversion(self) -> None:
        feed, aircraft, request = self.fetch({
            "now": 1000000,
            "ac": [{"hex": "ABC123", "flight": " TEST1 ", "lat": 47, "lon": -122,
                    "alt_geom": 35000, "alt_baro": 34000, "gs": 420, "track": 180,
                    "geom_rate": 1500, "seen_pos": 4, "future_field": True}],
        }, compressed=True)
        self.assertEqual(feed.interval_s, 30)
        self.assertEqual(request.full_url, "https://gateway.adsbexchange.com/api/aircraft/v2/lat/47/lon/-122/dist/250")
        self.assertEqual(request.get_header("X-api-key"), "test-key")
        self.assertEqual(request.get_header("Accept-encoding"), "gzip")
        ac = aircraft[0]
        self.assertEqual(ac.icao24, "abc123")
        self.assertEqual(ac.callsign, "TEST1")
        self.assertAlmostEqual(ac.altitude_m, 10668)
        self.assertAlmostEqual(ac.velocity_ms, 216.06648)
        self.assertAlmostEqual(ac.vertical_rate_ms, 7.62)
        self.assertEqual(ac.position_updated_epoch_s, 996)
        self.assertTrue(ac.has_position)

    def test_ground_missing_values_and_barometric_fallback(self) -> None:
        _, aircraft, _ = self.fetch({"ac": [
            {"hex": "a", "alt_baro": "ground"},
            {"hex": "b"},
            {"hex": "c", "alt_baro": 10000, "baro_rate": -1000},
        ]})
        self.assertTrue(aircraft[0].on_ground)
        self.assertFalse(aircraft[0].has_position)
        self.assertIsNone(aircraft[1].altitude_m)
        self.assertIsNone(aircraft[1].position_updated_epoch_s)
        self.assertEqual(aircraft[2].altitude_m, 3048)
        self.assertAlmostEqual(aircraft[2].vertical_rate_ms, -5.08)

    def test_rejects_bad_payloads_instead_of_returning_empty_success(self) -> None:
        for data in ({}, {"ac": {}}, {"ac": [None]}, {"ac": [{"hex": "a", "lat": "NaN"}]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.fetch(data)
        self.assertEqual(self.fetch({"ac": []})[1], [])

    def test_missing_key_is_explicit(self) -> None:
        with self.assertRaisesRegex(ValueError, "ADSBEXCHANGE_API_KEY"):
            ADSBExchangeFeed("", 47, -122, 250)

    def test_manual_request_throttling(self) -> None:
        feed, _, _ = self.fetch({"ac": []})
        with self.assertRaisesRegex(ValueError, "five seconds"):
            feed.fetch_once()

    def test_authentication_errors_are_reported_and_rate_limits_back_off(self) -> None:
        for code in (401, 403, 429):
            feed = ADSBExchangeFeed("test-key", 47, -122, 250)
            error = urllib.error.HTTPError(feed.URL, code, "Denied", None, None)
            messages, waits = [], []
            feed.on_error = messages.append
            feed.on_rate_limited = waits.append
            with patch.object(feed, "fetch_once", side_effect=error):
                with patch.object(feed._stop, "is_set", side_effect=[False, True]):
                    with patch.object(feed._stop, "wait"):
                        feed._run()
            if code == 429:
                self.assertEqual(waits, [30])
            else:
                self.assertEqual(messages, [f"HTTP Error {code}: Denied"])
            self.assertNotIn("test-key", str(messages))


class ADSBExchangeBridgeTests(unittest.TestCase):
    def test_poll_rate_changes_active_feed_without_restarting_or_clearing_credentials(self) -> None:
        with patch.dict(bridge._source_poll_intervals, {"opensky": 30, "adsbexchange": 30}):
            feed = MagicMock()
            with patch.multiple(bridge, _active_source="opensky", _opensky_feed=feed, _opensky_client_secret="saved-test-secret"):
                with patch.object(bridge, "_ok") as reply:
                    bridge.handle({"id": "poll", "method": "source_poll_interval_set",
                                   "params": {"provider": "opensky", "seconds": 10}})
                    feed.set_poll_interval.assert_called_once_with(10.0)
                    self.assertEqual(bridge._source_poll_intervals["opensky"], 10)
                    self.assertEqual(bridge._opensky_client_secret, "saved-test-secret")
                    reply.assert_called_with("poll", {})

    def test_poll_rate_limits_are_validated_before_mutation(self) -> None:
        with patch.dict(bridge._source_poll_intervals, {"opensky": 30, "adsbexchange": 30}):
            for provider, seconds in (("opensky", 9), ("adsbexchange", 4), ("opensky", True), ("opensky", float("nan"))):
                with self.subTest(provider=provider, seconds=seconds):
                    with patch.object(bridge, "_err") as error:
                        bridge.handle({"id": "poll", "method": "source_poll_interval_set",
                                       "params": {"provider": provider, "seconds": seconds}})
                        error.assert_called_once()
                        self.assertEqual(bridge._source_poll_intervals[provider], 30)

    def test_custom_poll_rate_is_used_when_adsb_feed_starts(self) -> None:
        with patch.dict(bridge._source_poll_intervals, {"adsbexchange": 5}):
            with patch.multiple(bridge, _adsbexchange_feed=None, _active_source=None,
                                _adsbexchange_api_key="dummy-key", _station_a_locator="CN87VP", _station_b_locator=""):
                with patch.object(bridge, "ADSBExchangeFeed") as constructor, patch.object(bridge, "_emit"):
                    bridge._start_adsbexchange_feed()
                    self.assertEqual(constructor.call_args.kwargs["interval_s"], 5)

    def setUp(self) -> None:
        self.status_patch = patch.dict(bridge._source_status, {
            key: dict(value) for key, value in bridge._source_status.items()
        })
        self.status_patch.start()
        self.addCleanup(self.status_patch.stop)

    def test_failure_falls_back_and_late_updates_do_not_replace_cache(self) -> None:
        with patch.dict("os.environ", {"ADSBEXCHANGE_API_KEY": "test-key"}):
            with patch.multiple(bridge, _adsbexchange_feed=None, _active_source=None,
                                _source_priority=["adsbexchange", "opensky"],
                                _station_a_locator="CN87VP", _station_b_locator=None):
                with patch.object(bridge, "ADSBExchangeFeed") as constructor:
                    with patch.object(bridge, "_emit"), patch.object(bridge, "_replace_aircraft_cache") as cache:
                        with patch.object(bridge, "_start_source", return_value=True) as start:
                            bridge._start_adsbexchange_feed()
                            on_update, on_error = constructor.call_args.args[4:6]
                            on_error("HTTP Error 401: Unauthorized")
                            start.assert_called_with("opensky")
                            self.assertEqual(bridge._active_source, "opensky")
                            on_update([])
                            cache.assert_not_called()

    def test_missing_key_and_fallback_skip(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with patch.multiple(bridge, _adsbexchange_feed=None,
                                _source_priority=["opensky", "adsbexchange", "dump1090"]):
                with patch.object(bridge, "_emit"):
                    self.assertFalse(bridge._start_source("adsbexchange"))
                    self.assertEqual(bridge._next_fallback_source("opensky"), "dump1090")
                    self.assertIn("API key", bridge._source_status["adsbexchange"]["message"])

    def test_runtime_credentials_are_applied_without_echoing_secrets(self) -> None:
        with patch.multiple(bridge, _opensky_client_id="", _opensky_client_secret="",
                            _adsbexchange_api_key="", _active_source="opensky"):
            with patch.object(bridge, "_start_configured_airscout_feed") as restart:
                with patch.object(bridge, "_ok") as reply:
                    bridge.handle({"method": "source_credentials_set", "params": {
                        "provider": "opensky", "client_id": "test-id", "secret": "fake-secret",
                    }})
                    self.assertEqual(bridge._opensky_client_id, "test-id")
                    self.assertEqual(bridge._opensky_client_secret, "fake-secret")
                    restart.assert_called_with(force_restart=True)
                    reply.assert_called_with(None, {})
                    bridge.handle({"method": "source_credentials_set", "params": {
                        "provider": "opensky", "client_id": "", "secret": "",
                    }})
                    self.assertEqual(bridge._opensky_client_secret, "")

    def test_invalid_credentials_do_not_replace_existing_account(self) -> None:
        with patch.multiple(bridge, _opensky_client_id="saved-id", _opensky_client_secret="saved-secret"):
            with patch.object(bridge, "_emit"), patch.object(bridge, "_err") as error:
                bridge.handle({"id": "bad", "method": "source_credentials_set", "params": {
                    "provider": "opensky", "client_id": "new-id", "secret": None,
                }})
                error.assert_called_with("bad", "Unable to apply aircraft credentials")
                self.assertEqual(bridge._opensky_client_id, "saved-id")
                self.assertEqual(bridge._opensky_client_secret, "saved-secret")

    def test_saved_adsb_key_overrides_environment_and_clear_restores_fallback(self) -> None:
        with patch.dict("os.environ", {"ADSBEXCHANGE_API_KEY": "environment-test-key"}):
            with patch.multiple(bridge, _adsbexchange_api_key="", _active_source="adsbexchange",
                                _adsbexchange_feed=None, _source_priority=["adsbexchange"],
                                _station_a_locator="CN87VP", _station_b_locator=""):
                with patch.object(bridge, "ADSBExchangeFeed") as constructor:
                    with patch.object(bridge, "_emit"), patch.object(bridge, "_ok"):
                        bridge.handle({"method": "source_credentials_set", "params": {
                            "provider": "adsbexchange", "secret": "saved-test-key",
                        }})
                        self.assertEqual(constructor.call_args.args[0], "saved-test-key")
                        bridge.handle({"method": "source_credentials_set", "params": {
                            "provider": "adsbexchange", "secret": "",
                        }})
                        self.assertEqual(constructor.call_args.args[0], "environment-test-key")

    def test_superseded_opensky_feed_cannot_update_after_credentials_restart(self) -> None:
        with patch.multiple(bridge, _opensky_feed=None, _active_source="opensky",
                            _station_a_locator="CN87VP", _station_b_locator=""):
            with patch.object(bridge, "OpenSkyFeed") as constructor:
                with patch.object(bridge, "_emit"), patch.object(bridge, "_replace_aircraft_cache") as cache:
                    bridge._start_opensky_feed()
                    callback = constructor.call_args.kwargs["on_update"]
                    bridge._opensky_feed = None
                    callback([])
                    cache.assert_not_called()

    def test_start_update_stop_and_refresh(self) -> None:
        with patch.dict("os.environ", {"ADSBEXCHANGE_API_KEY": "test-key"}):
            with patch.multiple(bridge, _adsbexchange_feed=None, _active_source=None,
                                _station_a_locator="CN87VP", _station_b_locator="CN89OH"):
                with patch.object(bridge, "ADSBExchangeFeed") as constructor:
                    with patch.object(bridge, "_emit"), patch.object(bridge, "_replace_aircraft_cache") as cache:
                        with patch.object(bridge, "_emit_aircraft_update") as update:
                            self.assertTrue(bridge._start_source("adsbexchange"))
                            feed = constructor.return_value
                            feed.start.assert_called_once()
                            self.assertGreater(constructor.call_args.args[3], 400 / 1.852)
                            constructor.call_args.args[4]([])
                            cache.assert_called_with([])
                            update.assert_called()
                            feed.fetch_once.return_value = []
                            with patch.object(bridge, "_ok") as reply:
                                bridge.handle({"id": "refresh", "method": "airscout_fetch_now", "params": {}})
                                reply.assert_called_with("refresh", {"count": 0})
                            bridge._stop_source("adsbexchange")
                            feed.stop.assert_called_once()
                            self.assertIsNone(bridge._adsbexchange_feed)


if __name__ == "__main__":
    unittest.main()
