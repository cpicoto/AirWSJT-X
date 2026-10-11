from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .models import Station


class SessionState(str, Enum):
    IDLE = "idle"
    STATIONS_READY = "stations_ready"
    CANDIDATES_AVAILABLE = "candidates_available"
    PLANE_LOCKED = "plane_locked"
    TRACKING = "tracking"
    READY = "ready"
    ACTIVE_QSO = "active_qso"
    COMPLETED = "completed"
    ABORTED = "aborted"


@dataclass
class ContactSession:
    state: SessionState = SessionState.IDLE
    station_a: Station | None = None
    station_b: Station | None = None
    selected_aircraft_id: str | None = None
    candidate_count: int = 0

    def set_stations(self, station_a: Station, station_b: Station) -> None:
        self.station_a = station_a
        self.station_b = station_b
        self.state = SessionState.STATIONS_READY

    def set_candidates(self, count: int) -> None:
        if self.state != SessionState.STATIONS_READY:
            raise ValueError("Stations must be set before candidates.")
        self.candidate_count = count
        self.state = SessionState.CANDIDATES_AVAILABLE

    def lock_aircraft(self, aircraft_id: str) -> None:
        if self.state != SessionState.CANDIDATES_AVAILABLE:
            raise ValueError("Candidates must exist before aircraft lock.")
        self.selected_aircraft_id = aircraft_id
        self.state = SessionState.PLANE_LOCKED

    def update_tracking(self, rotator_locked: bool) -> None:
        if self.state not in (SessionState.PLANE_LOCKED, SessionState.TRACKING, SessionState.READY):
            raise ValueError("Aircraft must be locked before tracking.")
        if rotator_locked:
            self.state = SessionState.READY
        else:
            self.state = SessionState.TRACKING

    def start_qso(self) -> None:
        if self.state == SessionState.ACTIVE_QSO:
            raise ValueError("QSO is already active.")
        if self.state not in (SessionState.PLANE_LOCKED, SessionState.TRACKING, SessionState.READY):
            raise ValueError("Cannot start QSO until an aircraft is locked and ready to transmit.")
        self.state = SessionState.ACTIVE_QSO

    def complete(self) -> None:
        if self.state != SessionState.ACTIVE_QSO:
            raise ValueError("QSO must be active to complete.")
        self.state = SessionState.COMPLETED

    def abort(self) -> None:
        self.state = SessionState.ABORTED
