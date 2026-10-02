import numpy as np
from django.test import SimpleTestCase

from planner.services.exceptions import NoFeasibleFuelPlan
from planner.services.optimizer import plan_fuel_stops, resample_route
from planner.services.routing import decode_polyline


def _plan(stations, total, tank_range=500):
    miles = np.array([m for m, _ in stations], dtype=float)
    prices = np.array([p for _, p in stations], dtype=float)
    return [(int(i), round(bought, 6)) for i, bought in plan_fuel_stops(miles, prices, total, tank_range)]


class PlanFuelStopsTests(SimpleTestCase):
    def test_buys_only_enough_to_reach_a_cheaper_stop(self):
        result = _plan([(0, 3.0), (100, 2.0)], total=300)
        self.assertEqual(result, [(0, 100.0), (1, 200.0)])

    def test_fills_up_when_no_cheaper_stop_is_in_range(self):
        result = _plan([(0, 3.0), (400, 4.0), (800, 3.5)], total=1200)
        # Fill at 0 (500 mi), arrive at 400 with 100 left, buy 300 to reach 800, then 400 to finish.
        self.assertEqual(result, [(0, 500.0), (1, 300.0), (2, 400.0)])

    def test_purchased_fuel_covers_the_whole_trip(self):
        stations = [(5, 3.4), (180, 3.1), (420, 3.9), (610, 2.9), (900, 3.3), (1250, 3.0)]
        result = _plan(stations, total=1500)
        self.assertAlmostEqual(sum(b for _, b in result), 1500)

    def test_skips_expensive_stops_when_possible(self):
        result = _plan([(0, 3.0), (200, 5.0), (450, 2.5)], total=700)
        self.assertNotIn(1, [i for i, _ in result])

    def test_raises_when_gap_exceeds_range(self):
        with self.assertRaises(NoFeasibleFuelPlan):
            _plan([(0, 3.0), (600, 3.0)], total=1000)

    def test_raises_without_stations(self):
        with self.assertRaises(NoFeasibleFuelPlan):
            _plan([], total=100)


class GeometryTests(SimpleTestCase):
    def test_decode_polyline(self):
        decoded = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
        np.testing.assert_allclose(decoded, [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)])

    def test_resample_route_spacing_and_endpoints(self):
        coords = np.array([[32.0, -97.0], [33.0, -97.0]])
        points, markers = resample_route(coords, total_miles=69.0)
        self.assertEqual(markers[0], 0.0)
        self.assertAlmostEqual(markers[-1], 69.0)
        np.testing.assert_allclose(points[-1], [33.0, -97.0])
        self.assertTrue(np.all(np.diff(markers) <= 1.0 + 1e-9))
