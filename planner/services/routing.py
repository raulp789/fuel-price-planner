"""Driving route lookup using the free public OSRM API (one HTTP call per route)."""

import logging
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from .exceptions import RouteNotFound, RoutingUnavailable
from .http import get_session

logger = logging.getLogger(__name__)

METERS_PER_MILE = 1609.344


@dataclass
class Route:
    distance_miles: float
    duration_seconds: float
    coordinates: np.ndarray  # shape (n, 2) as [lat, lon]


def get_route(start, finish):
    """Return the driving route between two `Location`s. Results are cached."""
    coords = f"{start.longitude:.5f},{start.latitude:.5f};{finish.longitude:.5f},{finish.latitude:.5f}"
    cache_key = f"osrm:{coords}"
    data = cache.get(cache_key)

    if data is None:
        data = _fetch_route(coords)
        cache.set(cache_key, data, settings.FUEL_PLANNER["CACHE_TIMEOUT_SECONDS"])

    return Route(
        distance_miles=data["distance"] / METERS_PER_MILE,
        duration_seconds=data["duration"],
        coordinates=np.array(decode_polyline(data["geometry"]), dtype=np.float64),
    )


def _fetch_route(coords):
    config = settings.FUEL_PLANNER
    url = f"{config['OSRM_BASE_URL']}/route/v1/driving/{coords}"
    try:
        response = get_session().get(
            url,
            params={"overview": "full", "geometries": "polyline", "steps": "false"},
            timeout=config["HTTP_TIMEOUT_SECONDS"],
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("OSRM request failed: %s", exc)
        raise RoutingUnavailable("Routing service is unavailable, please retry shortly.") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        if response.status_code >= 500 or response.status_code == 429:
            raise RoutingUnavailable("Routing service is unavailable, please retry shortly.")
        raise RouteNotFound(payload.get("message") or "No drivable route found between these locations.")

    route = payload["routes"][0]
    return {"distance": route["distance"], "duration": route["duration"], "geometry": route["geometry"]}


def decode_polyline(encoded, precision=5):
    """Decode a Google encoded polyline into a list of (lat, lon) tuples."""
    coordinates, index, lat, lon = [], 0, 0, 0
    factor = 10 ** precision
    length = len(encoded)
    while index < length:
        for is_lon in (False, True):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += delta
            else:
                lat += delta
        coordinates.append((lat / factor, lon / factor))
    return coordinates
