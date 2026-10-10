from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from airwsjtx.callsign import StationInfo, lookup
from airwsjtx.geo import locator_to_latlon
from airwsjtx.models import AircraftSample, AircraftTrack, Station
from airwsjtx.supervisor import AirWSJTXSupervisor


class ContactValidationTests(unittest.TestCase):
    def test_ad7np_to_k7mdl_contact_validation(self) -> None:
        with patch(
            "airwsjtx.callsign._callook",
            return_value=StationInfo(
                callsign="K7MDL",
                name="Mike",
                grid="CN87xs",
                lat=47.6,
                lon=-122.3,
                country="USA",
                source="callook.info",
            ),
        ):
            k7mdl = lookup("K7MDL")

        self.assertEqual(k7mdl.callsign, "K7MDL")
        self.assertEqual((k7mdl.grid or "").upper(), "CN87XS")

        station_a = Station(callsign="AD7NP", locator="CN87VP")
        station_b = Station(callsign="K7MDL", locator=(k7mdl.grid or "CN87XS").upper())

        lat_a, lon_a = locator_to_latlon(station_a.locator)
        lat_b, lon_b = locator_to_latlon(station_b.locator)
        self.assertNotEqual((lat_a, lon_a), (lat_b, lon_b))

        track = AircraftTrack(
            aircraft_id="A1",
            samples=(
                AircraftSample(
                    timestamp=datetime.now(timezone.utc),
                    azimuth_deg=102.0,
                    elevation_deg=18.0,
                    slant_range_km=250.0,
                ),
            ),
        )

        supervisor = AirWSJTXSupervisor()
        hint = supervisor.begin_session(
            station_a=station_a,
            station_b=station_b,
            band_mhz=1296.0,
            frequency_hz=1296150000,
            tracks=[track],
        )

        self.assertIn("WSJT-X native PSK Reporter", hint)
        self.assertEqual(supervisor.session.station_a.callsign, "AD7NP")
        self.assertEqual(supervisor.session.station_a.locator, "CN87VP")
        self.assertEqual(supervisor.session.station_b.callsign, "K7MDL")
        self.assertEqual(supervisor.session.station_b.locator, "CN87XS")


if __name__ == "__main__":
    unittest.main()
