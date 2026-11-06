from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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

    def test_published_database_and_manifest_are_compatible(self) -> None:
        root = Path(__file__).resolve().parents[1]
        payload = json.loads((root / "site/airwsjtx/callsign-grid-db.json").read_text(encoding="utf-8"))
        manifest = json.loads((root / "site/airwsjtx/callsign-grid-db-version.json").read_text(encoding="utf-8"))

        self.assertIsInstance(payload["version"], str)
        self.assertTrue(payload["version"])
        self.assertEqual(manifest["version"], payload["version"])
        self.assertEqual(manifest["entries"], len(payload["entries"]))
        self.assertGreater(manifest["entries"], 0)
        parsed = self.callsign._parse_db_entries(payload["entries"])
        self.assertEqual(len(parsed), manifest["entries"])
        for call, raw in payload["entries"].items():
            with self.subTest(callsign=call):
                self.assertEqual(call, call.upper())
                self.assertEqual(set(raw), {"name", "grid", "lat", "lon", "country"})
                self.assertIsInstance(raw["grid"], str)
                self.assertIsInstance(raw["lat"], (int, float))
                self.assertIsInstance(raw["lon"], (int, float))
                self.assertGreaterEqual(raw["lat"], -90)
                self.assertLessEqual(raw["lat"], 90)
                self.assertGreaterEqual(raw["lon"], -180)
                self.assertLessEqual(raw["lon"], 180)

    def test_download_and_check_served_site_database(self) -> None:
        root = Path(__file__).resolve().parents[1]
        handler = partial(SimpleHTTPRequestHandler, directory=str(root / "site"))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        base_url = f"http://127.0.0.1:{server.server_port}/airwsjtx"
        db_url = f"{base_url}/callsign-grid-db.json"
        manifest_url = f"{base_url}/callsign-grid-db-version.json"
        thread.start()
        try:
            with patch.object(SimpleHTTPRequestHandler, "log_message"):
                before = self.callsign.check_db_update(db_url, manifest_url=manifest_url)
                self.assertTrue(before["online"])
                self.assertTrue(before["update_available"])
                status = self.callsign.download_db(db_url)
                self.assertTrue(status["fresh"])
                self.assertEqual(status["entries"], before["remote_entries"])
                self.assertEqual(status["version"], before["remote_version"])
                self.assertEqual(status["db_url"], db_url)
                self.callsign = importlib.reload(callsign_module)
                after = self.callsign.check_db_update(db_url, manifest_url=manifest_url)
                self.assertTrue(after["online"])
                self.assertFalse(after["update_available"])
                info = self.callsign.lookup("ve7nx")
                self.assertEqual(info.source, "local-db")
                self.assertEqual(info.grid, "CN89oh")
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_download_http_error_preserves_existing_database(self) -> None:
        self._write_local_db(version="old", downloaded_at=_iso(datetime.now(timezone.utc)))
        db_path = Path(self._tmp.name) / "callsign_grid_db.json"
        meta_path = Path(self._tmp.name) / "callsign_grid_db_meta.json"
        original_db = db_path.read_bytes()
        original_meta = meta_path.read_bytes()
        with patch.object(
            self.callsign.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(self.callsign.DEFAULT_DB_URL, 404, "Not Found", {}, None),
        ):
            with self.assertRaisesRegex(ValueError, "Callsign database request failed: HTTP 404"):
                self.callsign.download_db()
        self.assertEqual(db_path.read_bytes(), original_db)
        self.assertEqual(meta_path.read_bytes(), original_meta)

    def test_check_http_error_reports_status_and_url(self) -> None:
        with patch.object(
            self.callsign.urllib.request,
            "urlopen",
            side_effect=urllib.error.HTTPError(self.callsign.DEFAULT_DB_MANIFEST_URL, 404, "Not Found", {}, None),
        ):
            status = self.callsign.check_db_update()
        self.assertFalse(status["online"])
        self.assertFalse(status["update_available"])
        self.assertIn("HTTP 404", status["error"])
        self.assertIn(self.callsign.DEFAULT_DB_MANIFEST_URL, status["error"])

    def test_download_rejects_html_response_without_saving_it(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"<html>Not a database</html>"
        with patch.object(self.callsign.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "did not return valid JSON"):
                self.callsign.download_db()
        self.assertFalse((Path(self._tmp.name) / "callsign_grid_db.json").exists())

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
