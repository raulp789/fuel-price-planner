from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from planner.models import FuelStation, Place
from planner.services.geocoding import geocode
from planner.services.exceptions import LocationNotFound
from planner.services.stations import reset_station_index

DALLAS = (32.7767, -96.7970)
WACO = (31.5493, -97.1467)
HOUSTON = (29.7604, -95.3698)


def encode_polyline(points):
    def encode_value(value):
        value = ~(value << 1) if value < 0 else value << 1
        chunks = ""
        while value >= 0x20:
            chunks += chr((0x20 | (value & 0x1F)) + 63)
            value >>= 5
        return chunks + chr(value + 63)

    result, prev_lat, prev_lon = "", 0, 0
    for lat, lon in points:
        lat_i, lon_i = round(lat * 1e5), round(lon * 1e5)
        result += encode_value(lat_i - prev_lat) + encode_value(lon_i - prev_lon)
        prev_lat, prev_lon = lat_i, lon_i
    return result


FAKE_ROUTE = {"distance": 385_000.0, "duration": 13_000.0, "geometry": encode_polyline([DALLAS, WACO, HOUSTON])}


class GeocodingTests(TestCase):
    def setUp(self):
        cache.clear()
        Place.objects.create(name="St. Louis", key="stlouis", state="MO", latitude=38.63, longitude=-90.2)

    def test_coordinates(self):
        location = geocode("32.7767, -96.797")
        self.assertEqual((location.latitude, location.source), (32.7767, "coordinates"))

    def test_gazetteer_lookup_accepts_name_variants(self):
        for query in ("St. Louis, MO", "saint louis, missouri", "ST LOUIS,MO"):
            self.assertEqual(geocode(query).source, "gazetteer")

    def test_rejects_locations_outside_usa(self):
        with self.assertRaises(LocationNotFound):
            geocode("51.5074,-0.1278")


@patch("planner.services.routing._fetch_route", return_value=FAKE_ROUTE)
class RoutePlanApiTests(TestCase):
    url = reverse("route-plan")

    def setUp(self):
        cache.clear()
        reset_station_index()
        Place.objects.create(name="Dallas", key="dallas", state="TX", latitude=DALLAS[0], longitude=DALLAS[1])
        Place.objects.create(name="Houston", key="houston", state="TX", latitude=HOUSTON[0], longitude=HOUSTON[1])
        FuelStation.objects.create(
            opis_id=1, name="DALLAS STOP", address="I-45", city="Dallas", state="TX",
            retail_price=Decimal("3.50"), latitude=DALLAS[0], longitude=DALLAS[1],
        )
        FuelStation.objects.create(
            opis_id=2, name="WACO STOP", address="I-35", city="Waco", state="TX",
            retail_price=Decimal("3.00"), latitude=WACO[0], longitude=WACO[1],
        )
        FuelStation.objects.create(
            opis_id=3, name="FAR AWAY STOP", address="I-10", city="El Paso", state="TX",
            retail_price=Decimal("1.00"), latitude=31.76, longitude=-106.48,
        )

    def tearDown(self):
        reset_station_index()

    def test_get_returns_optimized_plan(self, mock_route):
        response = self.client.get(self.url, {"start": "Dallas, TX", "finish": "Houston, TX"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([s["opis_id"] for s in body["fuel_stops"]], [1, 2])
        self.assertEqual(body["summary"]["number_of_fuel_stops"], 2)

        miles = body["summary"]["total_distance_miles"]
        self.assertAlmostEqual(body["summary"]["total_gallons"], miles / 10, delta=0.01)
        expected_cost = sum(s["cost"] for s in body["fuel_stops"])
        self.assertAlmostEqual(body["summary"]["total_fuel_cost"], expected_cost, delta=0.02)

        self.assertIn("/api/route/map/?", body["map_url"])
        self.assertEqual(body["route_geojson"]["type"], "FeatureCollection")
        mock_route.assert_called_once()

    def test_post_json_and_caching_avoids_extra_routing_calls(self, mock_route):
        payload = {"start": "Dallas, TX", "finish": "Houston, TX"}
        first = self.client.post(self.url, payload, content_type="application/json")
        second = self.client.post(self.url, payload, content_type="application/json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        mock_route.assert_called_once()

    def test_missing_parameters(self, mock_route):
        response = self.client.get(self.url, {"start": "Dallas, TX"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("finish", response.json())

    def test_location_outside_usa(self, mock_route):
        response = self.client.get(self.url, {"start": "48.8566,2.3522", "finish": "Houston, TX"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "location_not_found")
        mock_route.assert_not_called()

    def test_map_page_renders(self, mock_route):
        response = self.client.get(reverse("route-map"), {"start": "Dallas, TX", "finish": "Houston, TX"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WACO STOP")
        self.assertContains(response, "leaflet")
