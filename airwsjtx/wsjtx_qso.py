from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, replace

from .callsign import base_callsign
from .wsjtx import MIN_QSO_FREQUENCY_HZ
from .wsjtx_udp import WSJTXDecode, WSJTXQsoLogged, WSJTXStartDxTxResult, WSJTXStatus


_CALL_RE = re.compile(r"^[A-Z0-9/]{3,}$")
_CALL_PART_RE = re.compile(r"(?=.*[A-Z])(?=.*\d)^[A-Z0-9/]+$")
START_DX_TX_CONFIRM_S = 5.0
CONFIGURATION_CONFIRM_S = 30.0
_ENDED_STAGES = {"idle", "logged", "aborted", "finished", "suspended"}
_EXCHANGE_STAGES = {"exchange", "awaiting_log"}


@dataclass(frozen=True)
class QsoProgress:
    stage: str
    message: str
    dx_call: str


class WSJTXQsoAssistant:
    """Track one operator-armed QSO through atomic DX-start confirmation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dx_call: str | None = None
        self._dx_grid: str | None = None
        self._my_call: str | None = None
        self._app_id: str | None = None
        self._source_addr: tuple[str, int] | None = None
        self._verified_signature: tuple[object, ...] | None = None
        self._verified_status: WSJTXStatus | None = None
        self._start_request_id: int | None = None
        self._start_requested_at = 0.0
        self._start_accepted = False
        self._start_message = ""
        self._tx_was_enabled = False
        self._tx_enabled = False
        self._transmitting = False
        self._observed_tx_message: str | None = None
        self._stage = "idle"
        self._message = "No QSO armed"
        self._armed_at = 0.0
        self._configuration_requested_at: float | None = None

    @staticmethod
    def _normalize_call(value: str | None) -> str:
        return (value or "").strip().upper()

    @staticmethod
    def grids_match(first: str | None, second: str | None) -> bool:
        first_grid = (first or "").strip().upper()
        second_grid = (second or "").strip().upper()
        return bool(
            len(first_grid) >= 4
            and len(second_grid) >= 4
            and (first_grid.startswith(second_grid) or second_grid.startswith(first_grid))
        )

    def arm(
        self,
        *,
        dx_call: str,
        dx_grid: str | None,
        my_call: str | None,
        app_id: str,
        source_addr: tuple[str, int] | None = None,
    ) -> QsoProgress:
        call = self._normalize_call(dx_call)
        if not _CALL_RE.fullmatch(call) or not _CALL_PART_RE.fullmatch(call):
            raise ValueError("A valid DX callsign is required before starting a QSO.")
        own_call = self._normalize_call(my_call)
        if not _CALL_RE.fullmatch(own_call) or not _CALL_PART_RE.fullmatch(own_call):
            raise ValueError("A valid MY callsign is required before starting a QSO.")
        grid = (dx_grid or "").strip().upper() or None
        with self._lock:
            self._dx_call = call
            self._dx_grid = grid
            self._my_call = own_call
            self._app_id = app_id
            self._source_addr = source_addr
            self._verified_signature = None
            self._verified_status = None
            self._start_request_id = None
            self._start_requested_at = 0.0
            self._start_accepted = False
            self._start_message = ""
            self._tx_was_enabled = False
            self._tx_enabled = False
            self._transmitting = False
            self._observed_tx_message = None
            self._stage = "preparing"
            self._message = f"Waiting for WSJT-X to confirm Q65-15 and {call}"
            self._armed_at = time.monotonic()
            self._configuration_requested_at = None
            return self.snapshot()

    def configuration_requested(self, requested_at: float) -> None:
        with self._lock:
            self._configuration_requested_at = requested_at

    def snapshot(self) -> QsoProgress:
        with self._lock:
            return QsoProgress(
                self._stage,
                self._message,
                self._dx_call or "",
            )

    def active_source(self) -> tuple[str, tuple[str, int]] | None:
        with self._lock:
            if self._stage in _ENDED_STAGES or self._app_id is None or self._source_addr is None:
                return None
            return self._app_id, self._source_addr

    def halt_target(self) -> tuple[str, tuple[str, int]] | None:
        with self._lock:
            if (
                self._stage in {"idle", "logged", "aborted", "finished"}
                or self._app_id is None or self._source_addr is None
            ):
                return None
            return self._app_id, self._source_addr

    @staticmethod
    def _signature(status: WSJTXStatus) -> tuple[object, ...]:
        return (
            status.freq_hz,
            (status.mode or "").upper(),
            (status.tx_mode or "").upper(),
            (status.sub_mode or "").upper(),
            status.fast_mode,
            status.special_op_mode,
            status.tr_period,
            status.config_name,
            WSJTXQsoAssistant._normalize_call(status.dx_call),
            WSJTXQsoAssistant._normalize_call(status.de_call),
        )

    def observe_status(self, status: WSJTXStatus, *, observed_at: float | None = None) -> QsoProgress:
        with self._lock:
            if self._stage in _ENDED_STAGES:
                return self.snapshot()
            if self._verified_signature is None and (
                self._configuration_requested_at is None
                or (observed_at is not None and observed_at < self._configuration_requested_at)
            ):
                return self.snapshot()
            if status.app_id != self._app_id:
                return self._suspend("WSJT-X instance changed; re-arm the QSO to continue")
            if self._source_addr is not None and status.source_addr != self._source_addr:
                return self.snapshot()

            mode_ok = (
                (status.mode or "").upper() == "Q65"
                and (not status.tx_mode or status.tx_mode.upper() == "Q65")
                and not status.fast_mode
            )
            period_ok = status.tr_period == 15
            band_ok = status.freq_hz >= MIN_QSO_FREQUENCY_HZ
            dx_ok = self._normalize_call(status.dx_call) == self._dx_call
            my_call_ok = self._normalize_call(status.de_call) == self._my_call
            grid_ok = self._dx_grid is None or self.grids_match(self._dx_grid, status.dx_grid)

            if self._verified_signature is None:
                if status.transmitting:
                    return self._suspend(
                        "WSJT-X began transmitting before settings were verified; stop Tx and re-arm"
                    )
                if status.tx_enabled:
                    return self._suspend(
                        "WSJT-X Tx was enabled before settings were verified; disable Tx and re-arm"
                    )
                if mode_ok and period_ok and band_ok and dx_ok and my_call_ok and grid_ok:
                    self._verified_signature = self._signature(status)
                    self._verified_status = replace(status)
                    self._stage = "verified"
                    self._message = f"Settings verified for {self._dx_call}; preparing the direct call"
                else:
                    self._stage = "preparing"
                    self._message = "Waiting for WSJT-X to confirm Q65-15, 2 m+, and the selected callsigns"
                return self.snapshot()

            if self._signature(status) != self._verified_signature or not grid_ok:
                return self._suspend("WSJT-X settings changed; re-arm before continuing")
            self._tx_enabled = status.tx_enabled
            self._transmitting = status.transmitting
            self._observed_tx_message = status.tx_message
            if not status.tx_enabled:
                if self._stage in _EXCHANGE_STAGES:
                    # WSJT-X auto-sequencing turns Tx off after sending its final message.
                    if self._stage == "awaiting_log":
                        self._message = (
                            f"WSJT-X Tx is off; log the QSO with {self._dx_call} in WSJT-X"
                        )
                    return self.snapshot()
                if self._tx_was_enabled:
                    return self._suspend("WSJT-X Tx was disabled; re-arm before continuing")
                return self.snapshot()
            self._tx_was_enabled = True
            if not self._start_accepted:
                self._message = "WSJT-X Tx is enabled; waiting for direct-call confirmation"
                return self.snapshot()
            if self._stage in _EXCHANGE_STAGES:
                return self.snapshot()
            self._update_tx_progress()
            return self.snapshot()

    @staticmethod
    def _is_q65_decode(decode: WSJTXDecode) -> bool:
        mode = (decode.mode or "").strip().upper()
        return mode == "Q65" or mode.startswith(":")

    @staticmethod
    def _call_matches(token: str, expected: str | None) -> bool:
        if not expected:
            return False
        call = token.strip().upper()
        if call.startswith("<") and call.endswith(">"):
            call = call[1:-1]
        return call in {expected, base_callsign(expected)}

    def direct_start_status(self) -> WSJTXStatus | None:
        with self._lock:
            if self._stage != "verified" or self._verified_status is None:
                return None
            return replace(self._verified_status)

    def mark_start_requested(self, request_id: int) -> QsoProgress:
        with self._lock:
            if self._stage != "verified" or request_id <= 0:
                return self.snapshot()
            self._start_request_id = request_id
            self._start_requested_at = time.monotonic()
            self._stage = "start_requested"
            self._message = f"Waiting for WSJT-X to confirm the direct call to {self._dx_call}"
            return self.snapshot()

    def observe_start_result(self, result: WSJTXStartDxTxResult) -> QsoProgress:
        with self._lock:
            if (
                self._stage != "start_requested"
                or result.request_id != self._start_request_id
                or result.app_id != self._app_id
                or result.source_addr != self._source_addr
            ):
                return self.snapshot()
            if not result.accepted:
                return self._suspend(
                    f"WSJT-X rejected the direct call ({result.reason}); check Tx before re-arming"
                )
            tokens = result.tx_message.split()
            if (
                len(tokens) < 2
                or not self._call_matches(tokens[0], self._dx_call)
                or not self._call_matches(tokens[1], self._my_call)
            ):
                return self._suspend(
                    "WSJT-X reported an unexpected Tx1 message; stop Tx and check the rig"
                )
            self._start_accepted = True
            self._start_message = result.tx_message
            self._tx_was_enabled = True
            self._tx_enabled = True
            self._update_tx_progress()
            return self.snapshot()

    def start_timeout(
        self, request_id: int, timeout_s: float = START_DX_TX_CONFIRM_S,
    ) -> QsoProgress:
        with self._lock:
            if (
                self._stage == "start_requested"
                and self._start_request_id == request_id
                and time.monotonic() - self._start_requested_at >= timeout_s
            ):
                return self._suspend(
                    "WSJT-X did not confirm the direct call. Check Accept UDP requests and "
                    "atomic DX-start support; check Tx before re-arming"
                )
            return self.snapshot()

    def _update_tx_progress(self) -> None:
        if self._transmitting:
            self._stage = "transmitting"
            self._message = (
                f"WSJT-X is transmitting: {self._observed_tx_message}"
                if self._observed_tx_message else "WSJT-X is transmitting"
            )
        else:
            self._stage = "tx_enabled"
            self._message = f"WSJT-X accepted the direct call: {self._start_message}"

    def matches_station(
        self, dx_call: str | None, dx_grid: str | None, my_call: str | None = None,
    ) -> bool:
        with self._lock:
            return bool(
                self._dx_call
                and (not dx_call or self._normalize_call(dx_call) == self._dx_call)
                and (not my_call or self._normalize_call(my_call) == self._my_call)
                and (
                    not self._dx_grid
                    or self.grids_match(self._dx_grid, dx_grid)
                )
            )

    def observe_decode(self, decode: WSJTXDecode) -> QsoProgress:
        with self._lock:
            if (
                not self._start_accepted
                or self._stage in _ENDED_STAGES
                or decode.app_id != self._app_id
                or decode.source_addr != self._source_addr
                or not decode.is_new
                or decode.low_confidence
                or decode.off_air
                or not self._is_q65_decode(decode)
                or not decode.message
            ):
                return self.snapshot()
            tokens = decode.message.upper().split()
            if len(tokens) < 2:
                return self.snapshot()
            if not self._call_matches(tokens[0], self._my_call) or not self._call_matches(tokens[1], self._dx_call):
                return self.snapshot()
            if any(token in {"RRR", "RR73", "73"} for token in tokens):
                self._stage = "awaiting_log"
                self._message = f"Final exchange heard from {self._dx_call}; confirm the QSO is logged"
            elif self._stage != "awaiting_log":
                self._stage = "exchange"
                self._message = f"Exchange received from {self._dx_call}"
            return self.snapshot()

    def complete_logged_qso(self, qso: WSJTXQsoLogged) -> bool:
        with self._lock:
            if (
                self._stage in {"idle", "logged", "aborted", "finished"}
                or self._verified_signature is None
                or not self._start_accepted
                or qso.app_id != self._app_id
                or qso.source_addr != self._source_addr
                or self._normalize_call(qso.dx_call) != self._dx_call
                or (qso.mode or "").upper() != "Q65"
                or qso.freq_hz != self._verified_signature[0]
            ):
                return False
            self._stage = "logged"
            self._message = f"QSO with {self._dx_call} logged by WSJT-X"
            return True

    def suspend(self, message: str) -> QsoProgress:
        with self._lock:
            if self._stage not in {"idle", "logged", "aborted", "finished"}:
                self._suspend(message)
            return self.snapshot()

    def configuration_timeout(self, timeout_s: float = CONFIGURATION_CONFIRM_S) -> QsoProgress:
        with self._lock:
            if (
                self._stage == "preparing"
                and time.monotonic() - self._armed_at >= timeout_s
            ):
                self._suspend(
                    "WSJT-X did not confirm the requested settings; check 'Accept UDP requests' "
                    "in Settings > Reporting and re-arm"
                )
            return self.snapshot()

    def finish(self, *, aborted: bool, message: str | None = None) -> QsoProgress:
        with self._lock:
            if self._stage not in {"idle", "logged"}:
                self._stage = "aborted" if aborted else "finished"
                self._message = message or ("QSO aborted" if aborted else "QSO marked finished")
            return self.snapshot()

    def _suspend(self, message: str) -> QsoProgress:
        self._stage = "suspended"
        self._message = message
        return self.snapshot()
