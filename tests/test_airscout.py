import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from airwsjtx.airscout import Dump1090Feed, OpenSkyFeed, SourceRateLimit
from airwsjtx.geo import heading_delta_deg, heading_within_tolerance


class _FakeStopEvent:
    def __init__(self, max_waits: int) -> None:
        self._max_waits = max_waits
        self.waits: list[float] = []
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        if len(self.waits) >= self._max_waits:
            self._set = True
        return self._set


class Dump1090FeedParsingTests(unittest.TestCase):
    def test_default_report_interval_is_five_seconds(self) -> None:
        self.assertEqual(Dump1090Feed().report_interval_s, 5.0)

    def test_position_timestamp_does_not_advance_for_velocity_reports(self) -> None:
        feed = Dump1090Feed()
        with patch("airwsjtx.airscout.time.time", return_value=1000):
            feed._parse_sbs("MSG,3,1,1,ABC123,0,0,0,0,0,TEST,35000,0,0,47,-122,0,0,0,0,0,0")
        with patch("airwsjtx.airscout.time.time", return_value=1020):
            feed._parse_sbs("MSG,4,1,1,ABC123,0,0,0,0,0,TEST,0,420,180,0,0,0,0,0,0,0,0")
        self.assertEqual(feed._aircraft["abc123"].position_updated_epoch_s, 1000)

    def test_parse_sbs_position_and_velocity(self) -> None:
        feed = Dump1090Feed(report_interval_s=1.0)
        msg = (
            "MSG,3,1,1,7AC2A7,7AC2A7,0,0,0,0,NNN,35000,0,0,51.1234,-0.9876,0,0,0,0,0,0,0\n"
        )

        feed._parse_sbs(msg)

        ac = feed._aircraft["7ac2a7"]
        self.assertEqual(ac.callsign, "NNN")
        self.assertAlmostEqual(ac.altitude_m, 10668.0)
        self.assertAlmostEqual(ac.lat, 51.1234)
        self.assertAlmostEqual(ac.lon, -0.9876)

    def test_parse_sbs_velocity_message(self) -> None:
        feed = Dump1090Feed(report_interval_s=1.0)
        msg = (
            "MSG,4,1,1,7AC2A7,7AC2A7,0,0,0,0,GABCD,0,420,180,0,0,1500,1234,0,0,0,0\n"
        )

        feed._parse_sbs(msg)

        ac = feed._aircraft["7ac2a7"]
        self.assertEqual(ac.callsign, "GABCD")
        self.assertAlmostEqual(ac.velocity_ms, 216.06648, places=4)
        self.assertAlmostEqual(ac.heading_deg, 180.0)
        self.assertAlmostEqual(ac.vertical_rate_ms, 7.62)

    def test_parse_beast_is_rejected(self) -> None:
        feed = Dump1090Feed(report_interval_s=1.0)
        with self.assertRaises(NotImplementedError):
            feed._parse_beast(b"\x1a\x20\x00\x00")


class OpenSkyFeedBackoffTests(unittest.TestCase):
    def test_quota_samples_at_ten_minutes_and_immediately_on_first_429(self) -> None:
        quota = SourceRateLimit()
        with patch("airwsjtx.airscout.time.time", return_value=1000):
            with patch("airwsjtx.airscout.time.monotonic", return_value=100):
                self.assertTrue(quota.observe({"X-Rate-Limit-Remaining": "4000"}, 200))
            with patch("airwsjtx.airscout.time.monotonic", return_value=699):
                self.assertFalse(quota.observe({"X-Rate-Limit-Remaining": "3900"}, 200))
                self.assertEqual(quota.remaining, 4000)
            with patch("airwsjtx.airscout.time.monotonic", return_value=700):
                self.assertTrue(quota.observe({"x-rate-limit-remaining": "3800"}, 200))
                self.assertEqual(quota.remaining, 3800)
            with patch("airwsjtx.airscout.time.monotonic", return_value=701):
                self.assertTrue(quota.observe({"X-Rate-Limit-Remaining": "0"}, 429))
                self.assertEqual(quota.remaining, 0)
                self.assertEqual(quota.retry_wait(), 1800)
                self.assertEqual(quota.snapshot()["check_interval_s"], 1800)

    def test_server_reset_time_controls_pause_and_success_clears_it(self) -> None:
        quota = SourceRateLimit()
        with patch("airwsjtx.airscout.time.time", return_value=1000):
            with patch("airwsjtx.airscout.time.monotonic", return_value=100):
                quota.observe({"X-Rate-Limit-Retry-After-Seconds": "120"}, 429)
                self.assertEqual(quota.retry_wait(), 120)
            with patch("airwsjtx.airscout.time.monotonic", return_value=219):
                self.assertEqual(quota.retry_wait(), 1)
            with patch("airwsjtx.airscout.time.monotonic", return_value=220):
                self.assertTrue(quota.observe({"X-Rate-Limit-Remaining": "4000"}, 200))
                self.assertEqual(quota.retry_wait(), 0)
                self.assertIsNone(quota.retry_epoch_s)
                self.assertEqual(quota.remaining, 4000)
            with patch("airwsjtx.airscout.time.monotonic", return_value=2019):
                self.assertFalse(quota.observe({"X-Rate-Limit-Remaining": "3900"}, 200))
            with patch("airwsjtx.airscout.time.monotonic", return_value=2020):
                self.assertTrue(quota.observe({"X-Rate-Limit-Remaining": "3800"}, 200))

    def test_quota_retry_after_supports_longer_times_and_http_dates(self) -> None:
        with patch("airwsjtx.airscout.time.time", return_value=1000), patch("airwsjtx.airscout.time.monotonic", return_value=100):
            for header, expected in (("3600", 3600), ("Thu, 01 Jan 1970 00:20:00 GMT", 200)):
                quota = SourceRateLimit()
                quota.observe({"Retry-After": header}, 429)
                self.assertEqual(quota.retry_wait(), expected)

    def test_manual_refresh_cannot_bypass_quota_cooldown(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1)
        with patch("airwsjtx.airscout.time.monotonic", return_value=100):
            feed.rate_limit.observe({"X-Rate-Limit-Retry-After-Seconds": "120"}, 429)
            with patch("airwsjtx.airscout.urllib.request.urlopen") as request:
                with self.assertRaises(urllib.error.HTTPError):
                    feed.fetch_once()
                request.assert_not_called()

    def test_quota_headers_are_sampled_without_extra_requests(self) -> None:
        response = self.response({"states": []})
        response.__enter__.return_value.headers = {"X-Rate-Limit-Remaining": "3999"}
        updates = []
        feed = OpenSkyFeed(0, 0, 1, 1, on_quota=updates.append)
        with patch("airwsjtx.airscout.urllib.request.urlopen", return_value=response) as request:
            feed.fetch_once()
        self.assertEqual(request.call_count, 1)
        self.assertEqual(updates[0]["remaining"], 3999)

    def test_invalid_quota_header_is_reported_not_guessed(self) -> None:
        quota = SourceRateLimit()
        quota.observe({"X-Rate-Limit-Remaining": "invalid"}, 200)
        self.assertIsNone(quota.remaining)
        self.assertIn("Invalid", quota.error)

    def test_custom_poll_interval_is_applied_and_rejects_unsafe_rate(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1)
        feed.set_poll_interval(10)
        self.assertEqual(feed.interval_s, 10)
        with self.assertRaises(ValueError):
            feed.set_poll_interval(9)
        self.assertEqual(feed.interval_s, 10)
        feed._stop = _FakeStopEvent(max_waits=1)  # type: ignore[assignment]
        with patch.object(feed, "fetch_once", return_value=[]):
            with patch("airwsjtx.airscout.time.monotonic", side_effect=[100, 104]):
                feed._run()
        self.assertEqual(feed._stop.waits, [6])  # type: ignore[attr-defined]

    @staticmethod
    def response(data: dict):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(data).encode()
        return response

    def test_oauth_token_is_requested_once_and_reused(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1, client_id="test-id", client_secret="fake-secret")
        token = self.response({"access_token": "fake-token", "expires_in": 1800})
        states = self.response({"states": []})
        with patch("airwsjtx.airscout.urllib.request.urlopen", side_effect=[token, states, states]) as request:
            feed.fetch_once()
            feed.fetch_once()
        token_request = request.call_args_list[0].args[0]
        self.assertEqual(token_request.full_url, feed.TOKEN_URL)
        self.assertIn(b"grant_type=client_credentials", token_request.data)
        self.assertIn(b"client_id=test-id", token_request.data)
        self.assertIn(b"client_secret=fake-secret", token_request.data)
        self.assertEqual(request.call_args_list[1].args[0].get_header("Authorization"), "Bearer fake-token")
        self.assertEqual(request.call_count, 3)

    def test_oauth_expiry_and_401_trigger_token_renewal(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1, client_id="test-id", client_secret="fake-secret")
        token1 = self.response({"access_token": "first", "expires_in": 1800})
        token2 = self.response({"access_token": "second", "expires_in": 1800})
        states = self.response({"states": []})
        denied = urllib.error.HTTPError(feed.URL, 401, "Unauthorized", None, None)
        with patch("airwsjtx.airscout.urllib.request.urlopen", side_effect=[token1, denied, token2, states]) as request:
            feed.fetch_once()
        self.assertEqual(request.call_count, 4)
        self.assertEqual(request.call_args_list[3].args[0].get_header("Authorization"), "Bearer second")
        feed._token_expires = 0
        with patch("airwsjtx.airscout.urllib.request.urlopen", side_effect=[token1, states]) as request:
            feed.fetch_once()
        self.assertEqual(request.call_count, 2)

    def test_invalid_oauth_credentials_do_not_fall_back_to_anonymous(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1, client_id="test-id", client_secret="fake-secret")
        denied = urllib.error.HTTPError(feed.TOKEN_URL, 401, "Denied", None, None)
        with patch("airwsjtx.airscout.urllib.request.urlopen", side_effect=denied) as request:
            with self.assertRaisesRegex(ValueError, "authentication failed"):
                feed.fetch_once()
        self.assertEqual(request.call_count, 1)

    def test_anonymous_requests_do_not_request_a_token(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1)
        with patch("airwsjtx.airscout.urllib.request.urlopen", return_value=self.response({"states": []})) as request:
            feed.fetch_once()
        self.assertEqual(request.call_count, 1)
        self.assertIsNone(request.call_args.args[0].get_header("Authorization"))

    def test_oauth_rejects_invalid_responses_and_lifetimes(self) -> None:
        for payload in ({}, {"access_token": "dummy", "expires_in": 0},
                        {"access_token": "dummy", "expires_in": True},
                        {"access_token": 12, "expires_in": 1800}):
            with self.subTest(payload=payload):
                feed = OpenSkyFeed(0, 0, 1, 1, client_id="test-id", client_secret="fake-secret")
                with patch("airwsjtx.airscout.urllib.request.urlopen", return_value=self.response(payload)):
                    with self.assertRaises(ValueError) as error:
                        feed.fetch_once()
                self.assertNotIn("fake-secret", str(error.exception))
                self.assertNotIn("dummy", str(error.exception))

    def test_oauth_retries_unauthorized_only_once(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1, client_id="test-id", client_secret="fake-secret")
        token = self.response({"access_token": "dummy", "expires_in": 1800})
        denied = urllib.error.HTTPError(feed.URL, 401, "Unauthorized", None, None)
        with patch("airwsjtx.airscout.urllib.request.urlopen", side_effect=[token, denied, token, denied]) as request:
            with self.assertRaises(urllib.error.HTTPError):
                feed.fetch_once()
        self.assertEqual(request.call_count, 4)

    def test_fetch_preserves_aircraft_position_report_time(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "states": [["abc123", "TEST", "US", 1000, 1020, -122, 47, 9000,
                        False, 200, 90, 0, None, 10000]],
        }).encode()
        with patch("airwsjtx.airscout.urllib.request.urlopen", return_value=response):
            aircraft = feed.fetch_once()
        self.assertEqual(aircraft[0].position_updated_epoch_s, 1000)

    def test_successful_fetch_targets_thirty_second_start_to_start_interval(self) -> None:
        feed = OpenSkyFeed(0, 0, 1, 1)
        feed._stop = _FakeStopEvent(max_waits=1)  # type: ignore[assignment]
        with patch.object(feed, "fetch_once", return_value=[]):
            with patch("airwsjtx.airscout.time.monotonic", side_effect=[100, 104]):
                feed._run()
        self.assertEqual(feed.interval_s, 30)
        self.assertEqual(feed._stop.waits, [26])  # type: ignore[attr-defined]

    def test_429_errors_back_off_exponentially(self) -> None:
        errors: list[str] = []
        feed = OpenSkyFeed(
            lat_min=0.0,
            lon_min=0.0,
            lat_max=1.0,
            lon_max=1.0,
            interval_s=30.0,
            on_error=errors.append,
        )
        feed._stop = _FakeStopEvent(max_waits=2)  # type: ignore[assignment]
        err = urllib.error.HTTPError(feed.URL, 429, "Too Many Requests", hdrs=None, fp=None)
        attempts = iter([err, err])
        feed.fetch_once = lambda: (_ for _ in ()).throw(next(attempts))  # type: ignore[assignment]

        with patch("airwsjtx.airscout.time.monotonic", return_value=100):
            feed._run()

        self.assertEqual(feed._stop.waits, [30.0, 60.0])  # type: ignore[attr-defined]
        self.assertEqual(errors, [])

    def test_backoff_resets_after_successful_fetch(self) -> None:
        errors: list[str] = []
        feed = OpenSkyFeed(
            lat_min=0.0,
            lon_min=0.0,
            lat_max=1.0,
            lon_max=1.0,
            interval_s=5.0,
            on_error=errors.append,
        )
        feed._stop = _FakeStopEvent(max_waits=3)  # type: ignore[assignment]
        err = urllib.error.HTTPError(feed.URL, 429, "Too Many Requests", hdrs=None, fp=None)
        attempts = iter([err, [], err])

        def _fetch_once():
            step = next(attempts)
            if isinstance(step, Exception):
                raise step
            return step

        feed.fetch_once = _fetch_once  # type: ignore[assignment]

        with patch("airwsjtx.airscout.time.monotonic", return_value=100):
            feed._run()

        self.assertEqual(feed._stop.waits, [10.0, 5.0, 10.0])  # type: ignore[attr-defined]
        self.assertEqual(errors, [])


class AircraftFilterGeometryTests(unittest.TestCase):
    def test_heading_delta_is_small_for_aligned_aircraft(self) -> None:
        self.assertEqual(heading_delta_deg(0.0, 20.0), 20.0)
        self.assertLess(heading_delta_deg(180.0, 200.0), 30.0)

    def test_heading_tolerance_accepts_path_aligned_vectors(self) -> None:
        self.assertTrue(heading_within_tolerance(0.0, 20.0, 30.0))
        self.assertFalse(heading_within_tolerance(0.0, 90.0, 30.0))


if __name__ == "__main__":
    unittest.main()
