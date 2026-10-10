import unittest
import urllib.error

from airwsjtx.airscout import Dump1090Feed, OpenSkyFeed
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
