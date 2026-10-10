import struct
import time
import unittest
from datetime import datetime, timedelta, timezone

from airwsjtx.models import AircraftSample, AircraftTrack, Station
from airwsjtx.rotator import RotatorController
from airwsjtx.state_machine import SessionState
from airwsjtx.supervisor import AirWSJTXSupervisor
from airwsjtx.tracker import AircraftTracker
from airwsjtx.wsjtx_udp import WSJTXListener, parse_heartbeat


def track(
    aircraft_id: str,
    azimuths: list[float],
    elevations: list[float],
    ranges: list[float],
) -> AircraftTrack:
    now = datetime.now(timezone.utc)
    samples = tuple(
        AircraftSample(
            timestamp=now + timedelta(seconds=idx * 15),
            azimuth_deg=azimuths[idx],
            elevation_deg=elevations[idx],
            slant_range_km=ranges[idx],
        )
        for idx in range(len(azimuths))
    )
    return AircraftTrack(aircraft_id=aircraft_id, samples=samples)


class TrackerTests(unittest.TestCase):
    def test_ranking_prefers_strong_stable_track(self) -> None:
        strong = track("A1", [120, 122, 123], [18, 19, 20], [220, 230, 240])
        weak = track("B1", [60, 180, 260], [3, 10, 1], [420, 520, 590])
        ranked = AircraftTracker().rank_candidates([weak, strong])
        self.assertEqual(ranked[0].aircraft_id, "A1")


class WSJTXXUDPListenerTests(unittest.TestCase):
    def test_parse_heartbeat_is_valid(self) -> None:
        app_id = "WSJT-X"
        payload = bytearray()
        payload.extend(struct.pack(">I", 0xADBCCBDA))
        payload.extend(struct.pack(">I", 2))
        payload.extend(struct.pack(">I", 0))
        payload.extend(struct.pack(">I", len(app_id)))
        payload.extend(app_id.encode("utf-8"))
        payload.extend(struct.pack(">I", 2))
        payload.extend(struct.pack(">I", 0xFFFFFFFF))
        payload.extend(struct.pack(">I", 0xFFFFFFFF))
        self.assertIsNotNone(parse_heartbeat(bytes(payload)))

    def test_timeout_requires_explicit_quiet_period(self) -> None:
        listener = WSJTXListener(heartbeat_timeout_s=0.05, socket_timeout_s=0.01)
        disconnected = []
        listener.on_disconnected = lambda: disconnected.append("offline")
        listener._connected = True
        listener._last_packet = time.monotonic() - 0.01
        listener._check_connection_timeout(time.monotonic())
        self.assertEqual(disconnected, [])

        listener._last_packet = time.monotonic() - 0.2
        listener._check_connection_timeout(time.monotonic())
        self.assertEqual(disconnected, ["offline"])
        self.assertFalse(listener._connected)


class SupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.station_a = Station(callsign="K1AAA", locator="JN58")
        self.station_b = Station(callsign="K2BBB", locator="JN68")
        self.valid_track = track("A1", [100, 101, 102], [16, 17, 18], [250, 255, 260])
        self.supervisor = AirWSJTXSupervisor()

    def test_rejects_band_below_23cm(self) -> None:
        with self.assertRaises(ValueError):
            self.supervisor.begin_session(
                station_a=self.station_a,
                station_b=self.station_b,
                band_mhz=432.0,
                frequency_hz=432150000,
                tracks=[self.valid_track],
            )

    def test_qso_requires_rotator_lock_then_operator_start(self) -> None:
        single_target_track = track("A1", [102], [18], [250])
        hint = self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[single_target_track],
        )
        self.assertIn("WSJT-X native PSK Reporter", hint)

        self.supervisor.update_tracking(current_azimuth_deg=80, current_elevation_deg=5)
        self.assertEqual(self.supervisor.session.state, SessionState.TRACKING)

        for _ in range(self.supervisor.rotator.stable_samples_required):
            self.supervisor.update_tracking(current_azimuth_deg=102, current_elevation_deg=18)
        self.assertEqual(self.supervisor.session.state, SessionState.READY)

        self.supervisor.operator_start_qso()
        self.assertEqual(self.supervisor.session.state, SessionState.ACTIVE_QSO)
        self.assertTrue(self.supervisor.wsjtx.status.ptt_enabled)

        self.supervisor.finish_qso()
        self.assertEqual(self.supervisor.session.state, SessionState.COMPLETED)
        self.assertFalse(self.supervisor.wsjtx.status.ptt_enabled)

    def test_start_qso_is_allowed_after_plane_lock(self) -> None:
        self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[self.valid_track],
        )
        self.supervisor.operator_start_qso()
        self.assertEqual(self.supervisor.session.state, SessionState.ACTIVE_QSO)
        self.assertTrue(self.supervisor.wsjtx.status.ptt_enabled)

    def test_start_qso_rejects_invalid_session_state(self) -> None:
        with self.assertRaises(ValueError):
            self.supervisor.operator_start_qso()

        self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[self.valid_track],
        )
        self.supervisor.finish_qso(abort=True)
        with self.assertRaises(ValueError):
            self.supervisor.operator_start_qso()

    def test_start_qso_allowed_from_plane_locked(self) -> None:
        self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[self.valid_track],
        )
        self.assertEqual(self.supervisor.session.state, SessionState.PLANE_LOCKED)
        self.supervisor.operator_start_qso()
        self.assertEqual(self.supervisor.session.state, SessionState.ACTIVE_QSO)
        self.assertTrue(self.supervisor.wsjtx.status.ptt_enabled)

    def test_start_qso_rejects_idle_state(self) -> None:
        with self.assertRaises(ValueError):
            self.supervisor.operator_start_qso()

    def test_tracking_target_advances_with_new_samples(self) -> None:
        moving_supervisor = AirWSJTXSupervisor(rotator=RotatorController(stable_samples_required=1))
        moving_track = track("M1", [100, 110, 120], [10, 12, 14], [260, 255, 250])
        moving_supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[moving_track],
        )
        cmd1 = moving_supervisor.update_tracking(current_azimuth_deg=100, current_elevation_deg=10)
        cmd2 = moving_supervisor.update_tracking(current_azimuth_deg=110, current_elevation_deg=12)
        cmd3 = moving_supervisor.update_tracking(current_azimuth_deg=120, current_elevation_deg=14)
        cmd4 = moving_supervisor.update_tracking(current_azimuth_deg=120, current_elevation_deg=14)
        self.assertEqual((cmd1.target_azimuth_deg, cmd1.target_elevation_deg), (100, 10))
        self.assertEqual((cmd2.target_azimuth_deg, cmd2.target_elevation_deg), (110, 12))
        self.assertEqual((cmd3.target_azimuth_deg, cmd3.target_elevation_deg), (120, 14))
        self.assertEqual((cmd4.target_azimuth_deg, cmd4.target_elevation_deg), (120, 14))
        self.assertEqual(moving_supervisor.session.state, SessionState.READY)

    def test_finish_requires_active_qso_unless_abort(self) -> None:
        self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[self.valid_track],
        )
        with self.assertRaises(ValueError):
            self.supervisor.finish_qso(abort=False)

        self.supervisor.finish_qso(abort=True)
        self.assertEqual(self.supervisor.session.state, SessionState.ABORTED)
        with self.assertRaises(ValueError):
            self.supervisor.finish_qso(abort=True)

    def test_rotator_lock_counter_resets_per_session(self) -> None:
        self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[self.valid_track],
        )
        self.supervisor.update_tracking(current_azimuth_deg=102, current_elevation_deg=18)
        self.supervisor.update_tracking(current_azimuth_deg=102, current_elevation_deg=18)
        self.assertEqual(self.supervisor.session.state, SessionState.TRACKING)

        self.supervisor.begin_session(
            station_a=self.station_a,
            station_b=self.station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[self.valid_track],
        )
        self.supervisor.update_tracking(current_azimuth_deg=102, current_elevation_deg=18)
        self.assertEqual(self.supervisor.session.state, SessionState.TRACKING)


if __name__ == "__main__":
    unittest.main()
