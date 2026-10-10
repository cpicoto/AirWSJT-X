import unittest
import urllib.error

from airwsjtx.airscout import OpenSkyFeed
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
