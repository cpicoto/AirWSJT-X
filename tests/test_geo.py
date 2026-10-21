import unittest

from airwsjtx.geo import elevation_deg, locator_to_latlon, scatter_geometry, slant_range_km


class ElevationCurvatureTests(unittest.TestCase):
    def test_distant_aircraft_is_below_horizon(self) -> None:
        # FL369 aircraft ~8700 km away: far below the horizon, not ~0.1°.
        self.assertLess(elevation_deg(8716.6, 11240.0), -25.0)

    def test_horizon_distance_for_cruise_altitude(self) -> None:
        # Geometric horizon for 11 km altitude is ~375 km.
        self.assertGreater(elevation_deg(300.0, 11000.0), 0.0)
        self.assertLess(elevation_deg(450.0, 11000.0), 0.0)

    def test_short_range_matches_flat_approximation(self) -> None:
        self.assertAlmostEqual(elevation_deg(10.0, 10000.0), 45.0, delta=0.1)
        self.assertAlmostEqual(slant_range_km(10.0, 10000.0), 14.14, delta=0.05)

    def test_far_aircraft_scores_zero(self) -> None:
        lat_a, lon_a = locator_to_latlon("CN87VP")
        lat_b, lon_b = locator_to_latlon("JN34VJ")
        geo = scatter_geometry(lat_a, lon_a, lat_b, lon_b, lat_b + 0.3, lon_b, 11240.0)
        self.assertLess(geo["el_a"], 0.0)
        self.assertEqual(geo["scatter_score"], 0.0)

    def test_aircraft_behind_or_abreast_of_my_station_scores_zero(self) -> None:
        for coordinates in [
            (0.0, 0.0, 0.0, 1.0, 0.0, -1.0),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
            (40.0, -100.0, 40.0, -98.0, 39.72, -101.73),
        ]:
            with self.subTest(coordinates=coordinates):
                geo = scatter_geometry(*coordinates, 11000.0)
                self.assertGreater(geo["el_a"], 0.0)
                self.assertGreater(geo["el_b"], 0.0)
                self.assertGreaterEqual(geo["off_path_deg"], 90.0)
                self.assertEqual(geo["scatter_score"], 0.0)

    def test_forward_aircraft_retains_existing_score(self) -> None:
        geo = scatter_geometry(40.0, -100.0, 40.0, -98.0, 40.0, -99.0, 11000.0)
        self.assertEqual(geo["scatter_score"], 0.594)

    def test_unset_path_does_not_apply_rear_aircraft_gate(self) -> None:
        geo = scatter_geometry(0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 11000.0)
        self.assertGreater(geo["scatter_score"], 0.0)


class StationAltitudeTests(unittest.TestCase):
    def test_higher_station_sees_lower_elevation_and_shorter_range(self):
        self.assertLess(elevation_deg(100.0, 10000.0, 2000.0), elevation_deg(100.0, 10000.0))
        self.assertLess(slant_range_km(100.0, 10000.0, 2000.0), slant_range_km(100.0, 10000.0) + 1e-6)

    def test_default_station_altitude_is_sea_level(self):
        self.assertEqual(elevation_deg(200.0, 9000.0), elevation_deg(200.0, 9000.0, 0.0))


if __name__ == "__main__":
    unittest.main()
