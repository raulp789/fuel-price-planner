"""Geometry and fuel-stop optimization.

Pipeline:
  1. `resample_route`         - turn the route polyline into points every ~1 mile with a mile marker.
  2. `find_stations_on_route` - KD-tree lookup of stations within the corridor, each mapped to a mile marker,
                                keeping the cheapest station per road segment.
  3. `plan_fuel_stops`        - greedy algorithm for the "gas station problem", which is optimal for
                                minimizing total cost with a fixed tank size and known prices.
"""

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from .exceptions import NoFeasibleFuelPlan
from .stations import EARTH_RADIUS_MILES, to_unit_vectors


@dataclass
class Candidates:
    """Stations along the route, sorted by mile marker."""

    station_indices: np.ndarray
    miles: np.ndarray
    offsets: np.ndarray  # distance from route, miles
    prices: np.ndarray


def haversine_miles(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def resample_route(coordinates, total_miles, step_miles=1.0):
    """Return (points[m, 2], mile_markers[m]) evenly spaced along the polyline."""
    lat, lon = coordinates[:, 0], coordinates[:, 1]
    cumulative = np.concatenate(([0.0], np.cumsum(haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:]))))
    if cumulative[-1] > 0:
        # Align geometric length with the router's reported road distance.
        cumulative *= total_miles / cumulative[-1]

    markers = np.append(np.arange(0.0, total_miles, step_miles), total_miles)
    points = np.column_stack((np.interp(markers, cumulative, lat), np.interp(markers, cumulative, lon)))
    return points, markers


def find_stations_on_route(index, points, markers, corridor_miles, segment_miles=1.0):
    """Stations within `corridor_miles` of the route, keeping the cheapest one per `segment_miles` of road.

    A coarser segment avoids impractical micro-stops (e.g. buying 1 gallon to save 2 cents a few miles
    later) at a negligible cost difference.
    """
    if len(index) == 0 or len(points) == 0:
        return Candidates(*(np.empty(0, dtype=t) for t in (np.int64, float, float, float)))

    # Cheap bounding-box prefilter before the KD-tree query.
    lat_margin = corridor_miles / 69.0
    lon_margin = corridor_miles / (69.0 * max(np.cos(np.radians(np.abs(points[:, 0]).max())), 0.1))
    in_box = (
        (index.latitudes >= points[:, 0].min() - lat_margin)
        & (index.latitudes <= points[:, 0].max() + lat_margin)
        & (index.longitudes >= points[:, 1].min() - lon_margin)
        & (index.longitudes <= points[:, 1].max() + lon_margin)
    )
    station_ids = np.flatnonzero(in_box)

    tree = cKDTree(to_unit_vectors(points[:, 0], points[:, 1]))
    chord, nearest = tree.query(index.vectors[station_ids], distance_upper_bound=corridor_miles / EARTH_RADIUS_MILES)
    hit = np.isfinite(chord)
    station_ids, nearest, offsets = station_ids[hit], nearest[hit], chord[hit] * EARTH_RADIUS_MILES
    prices = index.prices[station_ids]
    miles = markers[nearest]

    segment = np.floor(miles / segment_miles).astype(np.int64)
    order = np.lexsort((offsets, prices, segment))
    segment_sorted = segment[order]
    first = np.concatenate(([True], segment_sorted[1:] != segment_sorted[:-1])) if len(order) else order.astype(bool)
    keep = order[first]
    keep = keep[np.argsort(miles[keep], kind="stable")]

    return Candidates(
        station_indices=station_ids[keep],
        miles=miles[keep],
        offsets=offsets[keep],
        prices=prices[keep],
    )


def _next_cheaper(prices):
    """For each position, index of the next position with a strictly lower price (or -1)."""
    result = np.full(len(prices), -1, dtype=np.int64)
    stack = []
    for i, price in enumerate(prices):
        while stack and prices[stack[-1]] > price:
            result[stack.pop()] = i
        stack.append(i)
    return result


def plan_fuel_stops(miles, prices, total_miles, tank_range_miles):
    """Choose where and how much to refuel to minimize total cost.

    Assumes the vehicle begins with an empty tank and fills up at the first stop on the route.
    At each stop:
      * if a cheaper stop is within range, buy just enough to reach it;
      * else if the destination is within range, buy just enough to finish;
      * else fill the tank and continue to the cheapest stop within range.

    Returns a list of (candidate_position, miles_of_fuel_bought), in route order.
    """
    n = len(miles)
    if n == 0:
        raise NoFeasibleFuelPlan("No truck stops were found along this route.")
    if miles[0] > tank_range_miles:
        raise NoFeasibleFuelPlan(f"The first truck stop is {miles[0]:.0f} miles into the route, beyond the vehicle range.")

    # The first stop doubles as the origin fill-up: fuel for the miles before it is bought there too,
    # so purchased fuel always covers the whole trip.
    miles = np.array(miles, dtype=np.float64)
    miles[0] = 0.0

    next_cheaper = _next_cheaper(prices)
    purchases = []
    i, fuel = 0, 0.0  # fuel is expressed in miles of range

    while True:
        reach = miles[i] + tank_range_miles
        j = next_cheaper[i]

        if j != -1 and miles[j] <= reach and miles[j] < total_miles:
            buy = max(0.0, miles[j] - miles[i] - fuel)
            leg = miles[j] - miles[i]
        elif total_miles <= reach:
            buy = max(0.0, total_miles - miles[i] - fuel)
            if buy > 1e-9:
                purchases.append((i, buy))
            return purchases
        else:
            last_reachable = np.searchsorted(miles, reach, side="right")
            if last_reachable <= i + 1:
                raise NoFeasibleFuelPlan(
                    f"No truck stop within {tank_range_miles:.0f} miles after mile {miles[i]:.0f} of the route."
                )
            window = prices[i + 1:last_reachable]
            # Cheapest in range; on ties prefer the farthest one to reduce the number of stops.
            j = i + 1 + (len(window) - 1 - int(np.argmin(window[::-1])))
            buy = tank_range_miles - fuel
            leg = miles[j] - miles[i]

        if buy > 1e-9:
            purchases.append((i, buy))
        fuel += buy - leg
        i = j
