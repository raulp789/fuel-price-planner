"""Resolve user supplied locations to coordinates.

Resolution order (cheapest first):
  1. "lat,lon" coordinates          -> no lookup at all
  2. "City, ST" / "City, State"    -> local Census gazetteer table (no external call)
  3. anything else (full address)  -> Nominatim (OpenStreetMap), restricted to the USA
"""

import logging
import re
from dataclasses import asdict, dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from planner.models import Place

from .exceptions import LocationNotFound
from .http import get_session, make_cache_key
from .places import normalize_place_key, normalize_state

logger = logging.getLogger(__name__)

_COORDINATES = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

# (min_lat, max_lat, min_lon, max_lon)
_US_BOUNDS = (
    (24.3, 49.5, -125.0, -66.8),   # contiguous states
    (51.0, 71.6, -180.0, -129.9),  # Alaska
    (18.8, 22.4, -160.3, -154.7),  # Hawaii
)


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    latitude: float
    longitude: float
    source: str

    def to_dict(self):
        return asdict(self)


def is_in_usa(lat, lon):
    return any(a <= lat <= b and c <= lon <= d for a, b, c, d in _US_BOUNDS)


def geocode(query):
    query = " ".join((query or "").split())
    if not query:
        raise LocationNotFound("Location must not be empty.")

    location = _from_coordinates(query) or _from_gazetteer(query) or _from_nominatim(query)
    if location is None:
        raise LocationNotFound(f"Could not find a location in the USA matching '{query}'.")
    return location


def _from_coordinates(query):
    match = _COORDINATES.match(query)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if not is_in_usa(lat, lon):
        raise LocationNotFound(f"Coordinates '{query}' are outside the USA.")
    return Location(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")


def _from_gazetteer(query):
    city, sep, state = query.rpartition(",")
    state_code = normalize_state(state)
    if not sep or not state_code:
        return None
    place = Place.objects.filter(key=normalize_place_key(city), state=state_code).first()
    if place is None:
        return None
    return Location(query, f"{place.name}, {place.state}", place.latitude, place.longitude, "gazetteer")


def _from_nominatim(query):
    cache_key = make_cache_key("geocode", query)
    cached = cache.get(cache_key)
    if cached is not None:
        return Location(**cached) if cached else None

    config = settings.FUEL_PLANNER
    try:
        response = get_session().get(
            f"{config['NOMINATIM_BASE_URL']}/search",
            params={"q": query, "format": "jsonv2", "countrycodes": "us", "limit": 1},
            timeout=config["HTTP_TIMEOUT_SECONDS"],
        )
        response.raise_for_status()
        results = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Nominatim lookup failed for %r: %s", query, exc)
        raise LocationNotFound(f"Geocoding service unavailable while resolving '{query}'.") from exc

    location = None
    if results:
        top = results[0]
        location = Location(query, top["display_name"], float(top["lat"]), float(top["lon"]), "nominatim")
    cache.set(cache_key, location.to_dict() if location else {}, config["CACHE_TIMEOUT_SECONDS"])
    return location
