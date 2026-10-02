"""In-memory index of fuel stations.

The full station table (~7k rows) is loaded once per process into numpy arrays so a
route request never has to query the database row-by-row.
"""

import threading
from dataclasses import dataclass

import numpy as np

from planner.models import FuelStation

EARTH_RADIUS_MILES = 3958.8

_index = None
_lock = threading.Lock()


def to_unit_vectors(lat_deg, lon_deg):
    """Convert lat/lon (degrees) to 3D unit vectors so a KD-tree can do spherical nearest-neighbour."""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


@dataclass(frozen=True)
class StationIndex:
    ids: np.ndarray
    names: list
    addresses: list
    cities: list
    states: list
    prices: np.ndarray
    latitudes: np.ndarray
    longitudes: np.ndarray
    vectors: np.ndarray

    def __len__(self):
        return len(self.ids)

    def station(self, i):
        return {
            "opis_id": int(self.ids[i]),
            "name": self.names[i],
            "address": self.addresses[i],
            "city": self.cities[i],
            "state": self.states[i],
            "latitude": round(float(self.latitudes[i]), 5),
            "longitude": round(float(self.longitudes[i]), 5),
        }


def get_station_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = _build_index()
    return _index


def reset_station_index():
    global _index
    with _lock:
        _index = None


def _build_index():
    rows = list(
        FuelStation.objects.values_list(
            "opis_id", "name", "address", "city", "state", "retail_price", "latitude", "longitude"
        )
    )
    ids, names, addresses, cities, states, prices, lats, lons = zip(*rows) if rows else ([],) * 8
    lats = np.array(lats, dtype=np.float64)
    lons = np.array(lons, dtype=np.float64)
    return StationIndex(
        ids=np.array(ids, dtype=np.int64),
        names=list(names),
        addresses=list(addresses),
        cities=list(cities),
        states=list(states),
        prices=np.array(prices, dtype=np.float64),
        latitudes=lats,
        longitudes=lons,
        vectors=to_unit_vectors(lats, lons) if rows else np.empty((0, 3)),
    )
