from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WSJTXStatus:
    configured: bool = False
    mode: str | None = None
    period_s: int | None = None
    band_mhz: float | None = None
    frequency_hz: int | None = None
    ptt_enabled: bool = False
    psk_reporter_enabled: bool = True


class WSJTXAdapter:
    """Supervisor-side abstraction for WSJT-X control."""

    MIN_AUTOMATION_BAND_MHZ = 1240.0

    def __init__(self) -> None:
        self.status = WSJTXStatus()

    def supports_band(self, band_mhz: float) -> bool:
        return band_mhz >= self.MIN_AUTOMATION_BAND_MHZ

    def configure_q65_15s(
        self,
        *,
        band_mhz: float,
        frequency_hz: int,
        psk_reporter_enabled: bool = True,
    ) -> None:
        if not self.supports_band(band_mhz):
            raise ValueError("Automation is limited to 23cm and above.")
        self.status.configured = True
        self.status.mode = "Q65"
        self.status.period_s = 15
        self.status.band_mhz = band_mhz
        self.status.frequency_hz = frequency_hz
        self.status.psk_reporter_enabled = psk_reporter_enabled

    def is_ready_for_qso(self) -> bool:
        return (
            self.status.configured
            and self.status.mode == "Q65"
            and self.status.period_s == 15
            and self.status.band_mhz is not None
            and self.status.band_mhz >= self.MIN_AUTOMATION_BAND_MHZ
            and self.status.frequency_hz is not None
        )

    def set_ptt(self, enabled: bool) -> None:
        if not self.status.configured:
            raise ValueError("WSJT-X must be configured before PTT control.")
        self.status.ptt_enabled = enabled
