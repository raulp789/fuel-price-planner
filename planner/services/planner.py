"""Orchestrates geocoding, routing and fuel optimization into a single trip plan."""

import logging
import time

from django.conf import settings
from django.core.cache import cache

from .geocoding import geocode
from .optimizer import find_stations_on_route, plan_fuel_stops, resample_route
from .http import make_cache_key
from .routing import get_route
from .stations import get_station_index

logger = logging.getLogger(__name__)


def plan_trip(start_query, finish_query):
    config = settings.FUEL_PLANNER
    cache_key = make_cache_key("plan", start_query, finish_query)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    started = time.perf_counter()
    start, finish = geocode(start_query), geocode(finish_query)
    route = get_route(start, finish)

    points, markers = resample_route(route.coordinates, route.distance_miles)
    index = get_station_index()
    candidates = find_stations_on_route(
        index, points, markers, config["ROUTE_CORRIDOR_MILES"], config["STOP_SEGMENT_MILES"]
    )
    purchases = plan_fuel_stops(
        candidates.miles, candidates.prices, route.distance_miles, config["VEHICLE_RANGE_MILES"]
    )

    mpg = config["MILES_PER_GALLON"]
    fuel_stops = []
    for number, (pos, fuel_miles) in enumerate(purchases, start=1):
        gallons = fuel_miles / mpg
        price = float(candidates.prices[pos])
        fuel_stops.append({
            "stop_number": number,
            **index.station(candidates.station_indices[pos]),
            "price_per_gallon": round(price, 3),
            "route_mile": round(float(candidates.miles[pos]), 1),
            "miles_off_route": round(float(candidates.offsets[pos]), 1),
            "gallons": round(gallons, 2),
            "cost": round(gallons * price, 2),
        })

    total_gallons = sum(fuel_miles for _, fuel_miles in purchases) / mpg
    total_cost = sum(fuel_miles / mpg * float(candidates.prices[pos]) for pos, fuel_miles in purchases)

    plan = {
        "start": start.to_dict(),
        "finish": finish.to_dict(),
        "summary": {
            "total_distance_miles": round(route.distance_miles, 1),
            "estimated_drive_time_hours": round(route.duration_seconds / 3600, 2),
            "number_of_fuel_stops": len(fuel_stops),
            "total_gallons": round(total_gallons, 2),
            "total_fuel_cost": round(total_cost, 2),
            "average_price_per_gallon": round(total_cost / total_gallons, 3) if total_gallons else None,
            "stations_considered": int(len(candidates.miles)),
            "vehicle_range_miles": config["VEHICLE_RANGE_MILES"],
            "miles_per_gallon": mpg,
        },
        "fuel_stops": fuel_stops,
        "route_geojson": _to_geojson(points, start, finish, fuel_stops),
    }

    logger.info(
        "Planned %s -> %s: %.0f mi, %d stops, $%.2f in %.0f ms",
        start.label, finish.label, route.distance_miles, len(fuel_stops), total_cost,
        (time.perf_counter() - started) * 1000,
    )
    cache.set(cache_key, plan, config["CACHE_TIMEOUT_SECONDS"])
    return plan


def _to_geojson(points, start, finish, fuel_stops):
    """GeoJSON FeatureCollection (route line + markers); paste into geojson.io to view."""
    line = [[round(float(lon), 5), round(float(lat), 5)] for lat, lon in points]

    def point(lat, lon, properties):
        return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": properties}

    features = [
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": line}, "properties": {"kind": "route"}},
        point(start.latitude, start.longitude, {"kind": "start", "label": start.label, "marker-color": "#2e7d32"}),
        point(finish.latitude, finish.longitude, {"kind": "finish", "label": finish.label, "marker-color": "#c62828"}),
    ]
    for stop in fuel_stops:
        features.append(point(stop["latitude"], stop["longitude"], {
            "kind": "fuel_stop",
            "stop_number": stop["stop_number"],
            "label": f"#{stop['stop_number']} {stop['name']} - ${stop['price_per_gallon']}/gal",
            "marker-color": "#1565c0",
        }))
    return {"type": "FeatureCollection", "features": features}
