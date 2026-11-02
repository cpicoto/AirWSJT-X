from __future__ import annotations

from dataclasses import dataclass

MIN_QSO_FREQUENCY_HZ = 144_000_000


@dataclass
class WSJTXStatus:
    configured: bool = False
    mode: str | None = None
    period_s: int | None = None
    band_mhz: float | None = None
    frequency_hz: int | None = None
    psk_reporter_enabled: bool = True


class WSJTXAdapter:
    """Supervisor-side abstraction for WSJT-X control."""

    MIN_AUTOMATION_BAND_MHZ = MIN_QSO_FREQUENCY_HZ / 1_000_000

    def __init__(self) -> None:
        self.status = WSJTXStatus()

    def clear_configuration(self) -> None:
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
            raise ValueError("QSO control requires 2 m (144 MHz) or higher.")
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
