"""
Cross-platform regression tests.

These tests validate behaviour that is sensitive to operating-system or runtime
differences: file-path handling, atomic writes, struct endianness, floating-point
consistency, and environment-variable expansion.  They are fully self-contained
and require no network access or external processes.

Run locally:
    python -m unittest tests.test_cross_platform -v

In CI these same tests execute on ubuntu-latest, windows-latest, and
macos-latest (see .github/workflows/test.yml).
"""
from __future__ import annotations

import importlib
import json
import os
import struct
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import airwsjtx.callsign as callsign_module
from airwsjtx.geo import bearing_deg, haversine_km, locator_to_latlon
from airwsjtx.wsjtx_udp import (
    MSG_STATUS,
    WSJTX_MAGIC,
    parse_heartbeat,
    parse_status,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_db(data_dir: str, *, entries: dict | None = None) -> None:
    """Write a minimal callsign DB + meta to *data_dir*."""
    p = Path(data_dir)
    if entries is None:
        entries = {
            "K1ABC": {"name": "Test Op", "grid": "FN20", "lat": 40.0, "lon": -74.0, "country": "USA"},
        }
    (p / "callsign_grid_db.json").write_text(
        json.dumps({"version": "2026-01-01", "entries": entries}),
        encoding="utf-8",
    )
    (p / "callsign_grid_db_meta.json").write_text(
        json.dumps({
            "version": "2026-01-01",
            "downloaded_at": _now_iso(),
            "db_url": callsign_module.DEFAULT_DB_URL,
        }),
        encoding="utf-8",
    )


# ─── Test suites ──────────────────────────────────────────────────────────────

class DataDirResolutionTests(unittest.TestCase):
    """_data_dir() must resolve to a correct absolute path on every platform."""

    def setUp(self) -> None:
        self._orig = os.environ.get("AIRWSJTX_DATA_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._orig is None:
            os.environ.pop("AIRWSJTX_DATA_DIR", None)
        else:
            os.environ["AIRWSJTX_DATA_DIR"] = self._orig
        importlib.reload(callsign_module)

    def _reload(self) -> None:
        importlib.reload(callsign_module)

    def test_env_var_overrides_default_data_dir(self) -> None:
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        self._reload()
        self.assertEqual(callsign_module._data_dir(), Path(self._tmp.name))

    def test_default_data_dir_ends_with_dotairwsjtx(self) -> None:
        os.environ.pop("AIRWSJTX_DATA_DIR", None)
        self._reload()
        self.assertEqual(callsign_module._data_dir().name, ".airwsjtx")

    def test_data_dir_is_always_absolute(self) -> None:
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        self._reload()
        self.assertTrue(callsign_module._data_dir().is_absolute())

    def test_env_var_with_tilde_expands_to_absolute(self) -> None:
        os.environ["AIRWSJTX_DATA_DIR"] = "~/airwsjtx_xplat_test"
        self._reload()
        resolved = callsign_module._data_dir()
        self.assertTrue(resolved.is_absolute(), "Tilde must expand to an absolute path")
        self.assertNotIn("~", str(resolved))

    def test_db_file_path_is_child_of_data_dir(self) -> None:
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        self._reload()
        self.assertEqual(callsign_module._db_file().parent, Path(self._tmp.name))

    def test_meta_file_path_is_child_of_data_dir(self) -> None:
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        self._reload()
        self.assertEqual(callsign_module._meta_file().parent, Path(self._tmp.name))


class AtomicJsonWriteTests(unittest.TestCase):
    """_write_json must complete an atomic create-or-replace on every platform.

    On Windows, Path.replace() uses MoveFileExW which replaces an existing file
    atomically. On POSIX it uses rename(2). Both must leave a valid UTF-8 JSON
    file in place.
    """

    def setUp(self) -> None:
        self._orig = os.environ.get("AIRWSJTX_DATA_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        importlib.reload(callsign_module)

    def tearDown(self) -> None:
        if self._orig is None:
            os.environ.pop("AIRWSJTX_DATA_DIR", None)
        else:
            os.environ["AIRWSJTX_DATA_DIR"] = self._orig
        importlib.reload(callsign_module)

    def test_write_dict_round_trips(self) -> None:
        target = Path(self._tmp.name) / "test.json"
        payload: dict = {"key": "value", "num": 42}
        callsign_module._write_json(target, payload)
        result = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(result, payload)

    def test_write_list_round_trips(self) -> None:
        target = Path(self._tmp.name) / "list.json"
        payload = [{"callsign": "K1XYZ", "grid": "FN31"}]
        callsign_module._write_json(target, payload)
        result = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(result, payload)

    def test_write_replaces_existing_file_atomically(self) -> None:
        """Overwriting an existing file must succeed (the Windows rename edge-case)."""
        target = Path(self._tmp.name) / "replace.json"
        target.write_text(json.dumps({"generation": 1}), encoding="utf-8")
        callsign_module._write_json(target, {"generation": 2})
        result = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(result["generation"], 2)

    def test_non_ascii_characters_survive_roundtrip(self) -> None:
        """Non-ASCII content must not be corrupted by default system encoding."""
        target = Path(self._tmp.name) / "unicode.json"
        payload = {"name": "Ärger mit Ü und ö", "kanji": "日本語テスト", "arabic": "مرحبا"}
        callsign_module._write_json(target, payload)
        result = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(result["name"], payload["name"])
        self.assertEqual(result["kanji"], payload["kanji"])
        self.assertEqual(result["arabic"], payload["arabic"])

    def test_stored_url_contains_no_backslash_separators(self) -> None:
        """URLs written to disk must not contain OS-specific path separators."""
        target = Path(self._tmp.name) / "meta.json"
        callsign_module._write_json(target, {"db_url": callsign_module.DEFAULT_DB_URL})
        raw = target.read_text(encoding="utf-8")
        # A backslash in the URL field (not inside a JSON escape) would indicate
        # Windows path separators leaked into the value.
        parsed = json.loads(raw)
        self.assertNotIn("\\", parsed["db_url"])


class StructEndiannessTests(unittest.TestCase):
    """Binary packet parsing must be host-endian-neutral.

    All WSJT-X packets use explicit big-endian (">") format codes so results
    must be identical on little-endian (x86/ARM) and big-endian hosts.
    """

    def test_wsjtx_magic_bytes_are_always_big_endian(self) -> None:
        packed = struct.pack(">I", WSJTX_MAGIC)
        self.assertEqual(packed, b"\xad\xbc\xcb\xda")

    def test_explicit_big_endian_u32_is_platform_neutral(self) -> None:
        for n in (0, 1, 127, 255, 65535, 0xADBCCBDA):
            with self.subTest(n=n):
                packed = struct.pack(">I", n)
                (unpacked,) = struct.unpack_from(">I", packed, 0)
                self.assertEqual(unpacked, n)

    def test_big_endian_u32_value_42(self) -> None:
        # 42 == 0x0000002a — in big-endian the high byte comes first
        packed = struct.pack(">I", 42)
        self.assertEqual(packed, b"\x00\x00\x00\x2a")

    def test_heartbeat_packet_parses_on_this_platform(self) -> None:
        app_id = "WSJT-X"
        payload = bytearray()
        payload += struct.pack(">I", WSJTX_MAGIC)
        payload += struct.pack(">I", 2)           # schema
        payload += struct.pack(">I", 0)           # MSG_HEARTBEAT
        payload += struct.pack(">I", len(app_id))
        payload += app_id.encode("utf-8")
        payload += struct.pack(">I", 2)           # max_schema
        payload += struct.pack(">I", 0xFFFFFFFF)  # version (None)
        payload += struct.pack(">I", 0xFFFFFFFF)  # revision (None)
        hb = parse_heartbeat(bytes(payload))
        self.assertIsNotNone(hb)
        assert hb is not None
        self.assertEqual(hb.app_id, "WSJT-X")


class PlatformMathTests(unittest.TestCase):
    """Floating-point geometry results must be deterministic across platforms.

    Python uses IEEE 754 double precision on every supported platform, but this
    suite confirms the numeric results we depend on are stable.
    """

    def test_locator_jn58_centre(self) -> None:
        # J=9 → lon_base=0; 5 → +10; centre +1 → 11.0
        # N=13 → lat_base=40; 8 → +8; centre +0.5 → 48.5
        lat, lon = locator_to_latlon("JN58")
        self.assertAlmostEqual(lat, 48.5, places=1)
        self.assertAlmostEqual(lon, 11.0, places=1)

    def test_locator_fn20_centre(self) -> None:
        # F=5 → lon_base=-80; 2 → +4; centre +1 → -75.0
        # N=13 → lat_base=40; 0 → +0; centre +0.5 → 40.5
        lat, lon = locator_to_latlon("FN20")
        self.assertAlmostEqual(lat, 40.5, places=1)
        self.assertAlmostEqual(lon, -75.0, places=1)

    def test_locator_io91_centre(self) -> None:
        # London area: IO91
        lat, lon = locator_to_latlon("IO91")
        self.assertAlmostEqual(lat, 51.5, places=1)
        self.assertAlmostEqual(lon, -1.0, places=1)

    def test_haversine_london_to_paris_approx_340km(self) -> None:
        km = haversine_km(51.5, -0.1, 48.9, 2.3)
        self.assertGreater(km, 330.0)
        self.assertLess(km, 360.0)

    def test_haversine_same_point_is_zero(self) -> None:
        self.assertAlmostEqual(haversine_km(48.0, 11.0, 48.0, 11.0), 0.0, places=6)

    def test_bearing_due_east(self) -> None:
        b = bearing_deg(0.0, 0.0, 0.0, 1.0)
        self.assertAlmostEqual(b, 90.0, places=0)

    def test_bearing_due_north(self) -> None:
        b = bearing_deg(0.0, 0.0, 1.0, 0.0)
        self.assertAlmostEqual(b, 0.0, places=0)

    def test_bearing_due_south(self) -> None:
        b = bearing_deg(0.0, 0.0, -1.0, 0.0)
        self.assertAlmostEqual(b, 180.0, places=0)

    def test_locator_short_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            locator_to_latlon("AB")


class CallsignCaseNormalisationTests(unittest.TestCase):
    """Callsign lookup must normalise case regardless of filesystem case-sensitivity.

    Linux uses a case-sensitive filesystem. The callsign database keys are
    stored in upper-case, so lookups must up-case the input rather than relying
    on the OS to do case-folding.
    """

    def setUp(self) -> None:
        self._orig = os.environ.get("AIRWSJTX_DATA_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore)
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        _write_db(self._tmp.name)
        importlib.reload(callsign_module)

    def _restore(self) -> None:
        if self._orig is None:
            os.environ.pop("AIRWSJTX_DATA_DIR", None)
        else:
            os.environ["AIRWSJTX_DATA_DIR"] = self._orig
        importlib.reload(callsign_module)

    def test_lookup_lowercase_input_finds_uppercase_key(self) -> None:
        info = callsign_module.lookup("k1abc")
        self.assertEqual(info.callsign, "K1ABC")

    def test_lookup_uppercase_input_finds_key(self) -> None:
        info = callsign_module.lookup("K1ABC")
        self.assertEqual(info.callsign, "K1ABC")

    def test_lookup_mixed_case_input_finds_key(self) -> None:
        info = callsign_module.lookup("k1Abc")
        self.assertEqual(info.callsign, "K1ABC")

    def test_lookup_unknown_callsign_returns_inferred_result(self) -> None:
        """An unknown callsign must not raise; it falls back gracefully."""
        info = callsign_module.lookup("W9ZZZ")
        self.assertEqual(info.callsign, "W9ZZZ")


class ContribQueuePathTests(unittest.TestCase):
    """Contribution queue file must use platform-appropriate path handling."""

    def setUp(self) -> None:
        self._orig = os.environ.get("AIRWSJTX_DATA_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore)
        os.environ["AIRWSJTX_DATA_DIR"] = self._tmp.name
        importlib.reload(callsign_module)

    def _restore(self) -> None:
        if self._orig is None:
            os.environ.pop("AIRWSJTX_DATA_DIR", None)
        else:
            os.environ["AIRWSJTX_DATA_DIR"] = self._orig
        importlib.reload(callsign_module)

    def test_contrib_file_is_inside_data_dir(self) -> None:
        cf = callsign_module._contrib_file()
        self.assertEqual(cf.parent, Path(self._tmp.name))

    def test_queued_contribution_json_uses_no_os_path_separators(self) -> None:
        """Serialised contribution records must not embed OS path separators."""
        from unittest.mock import patch
        import urllib.error

        with patch.object(
            callsign_module.urllib.request,
            "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            callsign_module.submit_grid_contribution("k1abc", "fn20")

        queue_path = Path(self._tmp.name) / "callsign_grid_contrib.json"
        raw = queue_path.read_text(encoding="utf-8")
        records = json.loads(raw)
        for record in records:
            for field_name, value in record.items():
                if isinstance(value, str):
                    self.assertNotIn("\\", value, f"OS path sep in field {field_name!r}: {value!r}")


if __name__ == "__main__":
    unittest.main()
