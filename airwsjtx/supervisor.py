from __future__ import annotations

from dataclasses import dataclass

from .models import AircraftTrack, Station
from .psk import AirscatterOperatingProfile
from .rotator import RotatorCommand, RotatorController
from .state_machine import ContactSession, SessionState
from .tracker import AircraftTracker
from .wsjtx import WSJTXAdapter


@dataclass(frozen=True)
class SupervisorConfig:
    minimum_score_to_lock: float = 0.30


class AirWSJTXSupervisor:
    def __init__(
        self,
        *,
        config: SupervisorConfig | None = None,
        tracker: AircraftTracker | None = None,
        rotator: RotatorController | None = None,
        wsjtx: WSJTXAdapter | None = None,
        profile: AirscatterOperatingProfile | None = None,
    ) -> None:
        self.config = config or SupervisorConfig()
        self.tracker = tracker or AircraftTracker()
        self.rotator = rotator or RotatorController()
        self.wsjtx = wsjtx or WSJTXAdapter()
        self.profile = profile or AirscatterOperatingProfile()
        self.session = ContactSession()
        self._selected_track: AircraftTrack | None = None
        self._track_cursor: int = 0

    def begin_session(
        self,
        *,
        station_a: Station,
        station_b: Station,
        band_mhz: float,
        frequency_hz: int,
        tracks: list[AircraftTrack],
    ) -> str:
        if not self.wsjtx.supports_band(band_mhz):
            raise ValueError("Band below 23cm automation threshold.")

        self.session.set_stations(station_a, station_b)
        self.rotator.reset()
        ranked = self.tracker.rank_candidates(tracks)
        if not ranked:
            raise ValueError("No viable aircraft candidates.")
        self.session.set_candidates(len(ranked))
        best = ranked[0]
        if best.total_score < self.config.minimum_score_to_lock:
            raise ValueError("No aircraft passed lock threshold.")
        selected = next((track for track in tracks if track.aircraft_id == best.aircraft_id), None)
        if selected is None:
            raise ValueError("Selected aircraft track could not be resolved.")
        self._selected_track = selected
        self._track_cursor = 0
        self.session.lock_aircraft(best.aircraft_id)
        self.wsjtx.configure_q65_15s(
            band_mhz=band_mhz,
            frequency_hz=frequency_hz,
            psk_reporter_enabled=True,
        )
        return self.profile.discoverability_hint(band_mhz)

    def update_tracking(self, current_azimuth_deg: float, current_elevation_deg: float) -> RotatorCommand:
        if self._selected_track is None:
            raise ValueError("No aircraft selected.")
        target = self._selected_track.samples[self._track_cursor]
        command = self.rotator.create_command(target.azimuth_deg, target.elevation_deg)
        locked = self.rotator.update_lock(
            current_azimuth_deg=current_azimuth_deg,
            current_elevation_deg=current_elevation_deg,
            target_azimuth_deg=target.azimuth_deg,
            target_elevation_deg=target.elevation_deg,
        )
        if locked and self._track_cursor < len(self._selected_track.samples) - 1:
            self._track_cursor += 1
            self.rotator.reset()
        self.session.update_tracking(rotator_locked=locked)
        return command

    def operator_start_qso(self) -> None:
        if not self.wsjtx.is_ready_for_qso():
            raise ValueError("WSJT-X must be configured for Q65-15s on a supported 23cm+ band before starting a QSO.")
        self.session.start_qso()
        self.wsjtx.set_ptt(True)

    def finish_qso(self, abort: bool = False) -> None:
        if abort:
            if self.session.state in (SessionState.COMPLETED, SessionState.ABORTED):
                raise ValueError("Cannot abort a terminal session.")
            if self.session.state == SessionState.ACTIVE_QSO:
                self.wsjtx.set_ptt(False)
            self.session.abort()
        else:
            if self.session.state != SessionState.ACTIVE_QSO:
                raise ValueError("Cannot complete QSO before it starts.")
            self.wsjtx.set_ptt(False)
            self.session.complete()
