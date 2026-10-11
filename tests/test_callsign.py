from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import airwsjtx.callsign as callsign_module


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CallsignDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old_data_dir = os.environ.get("AIRWSJTX_DATA_DIR")
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        self.callsign = importlib.reload(callsign_module)

    def tearDown(self) -> None:
        if self._old_data_dir is None:
            os.environ.pop("AIRWSJTX_DATA_DIR", None)
        else:
            os.environ["AIRWSJTX_DATA_DIR"] = self._old_data_dir
        importlib.reload(callsign_module)

    def _write_local_db(self, *, version: str, downloaded_at: str) -> None:
        data_dir = Path(self._tmp.name)
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "callsign_grid_db.json").write_text(
            json.dumps({
                "version": version,
                "entries": {
                    "K1ABC": {
                        "name": "Test Operator",
                        "grid": "FN20",
                        "lat": 40.0,
                        "lon": -75.0,
                        "country": "USA",
                    },
                },
            }),
            encoding="utf-8",
        )
        (data_dir / "callsign_grid_db_meta.json").write_text(
            json.dumps({
                "version": version,
                "downloaded_at": downloaded_at,
                "db_url": self.callsign.DEFAULT_DB_URL,
            }),
            encoding="utf-8",
        )

    def test_lookup_uses_downloaded_database_first(self) -> None:
        now = _iso(datetime.now(timezone.utc))
        self._write_local_db(version="2026-10-10", downloaded_at=now)
        self.callsign = importlib.reload(callsign_module)

        info = self.callsign.lookup("k1abc")

        self.assertEqual(info.callsign, "K1ABC")
        self.assertEqual(info.grid, "FN20")
        self.assertEqual(info.source, "local-db")

    def test_db_status_reports_freshness(self) -> None:
        now = datetime.now(timezone.utc)
        self._write_local_db(version="2026-10-10", downloaded_at=_iso(now))
        self.callsign = importlib.reload(callsign_module)

        fresh = self.callsign.get_db_status()
        self.assertTrue(fresh["fresh"])
        self.assertEqual(fresh["entries"], 1)

        stale_time = _iso(now - timedelta(days=self.callsign.DB_FRESHNESS_DAYS + 1))
        self._write_local_db(version="2026-10-10", downloaded_at=stale_time)
        self.callsign = importlib.reload(callsign_module)
        stale = self.callsign.get_db_status()
        self.assertFalse(stale["fresh"])

    def test_check_db_update_detects_remote_version(self) -> None:
        now = _iso(datetime.now(timezone.utc))
        self._write_local_db(version="2026-10-10", downloaded_at=now)
        self.callsign = importlib.reload(callsign_module)

        with patch.object(
            self.callsign,
            "_download_manifest",
            return_value={
                "version": "2026-11-01",
                "entries": 1,
            },
        ):
            status = self.callsign.check_db_update()
        self.assertTrue(status["online"])
        self.assertTrue(status["update_available"])
        self.assertEqual(status["remote_version"], "2026-11-01")

    def test_contribution_is_queued_when_submit_fails(self) -> None:
        self.callsign = importlib.reload(callsign_module)
        with patch.object(
            self.callsign.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            result = self.callsign.submit_grid_contribution("k1abc", "fn20")

        self.assertFalse(result["accepted"])
        self.assertEqual(result["queued"], 1)
        queued = json.loads((Path(self._tmp.name) / "callsign_grid_contrib.json").read_text(encoding="utf-8"))
        self.assertEqual(queued[0]["callsign"], "K1ABC")
        self.assertEqual(queued[0]["grid"], "FN20")

    def test_successful_submit_flushes_pending_queue(self) -> None:
        q_path = Path(self._tmp.name) / "callsign_grid_contrib.json"
        q_path.write_text(json.dumps([{"callsign": "K1XYZ", "grid": "FN31"}]), encoding="utf-8")
        self.callsign = importlib.reload(callsign_module)
        ok_response = MagicMock()
        ok_response.__enter__.return_value = ok_response
        ok_response.__exit__.return_value = False
        with patch.object(self.callsign.urllib.request, "urlopen", return_value=ok_response):
            result = self.callsign.submit_grid_contribution("k1abc", "fn20")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["queued"], 0)
        self.assertFalse(q_path.exists())


if __name__ == "__main__":
    unittest.main()
