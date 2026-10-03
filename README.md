# Fuel Route Planner API

A Django REST API that takes a start and finish location in the USA and returns:

- the driving route (GeoJSON + an interactive map page),
- the most cost-effective fuel stops along the way, for a vehicle with a **500 mile range**,
- the **total fuel cost**, at **10 miles per gallon**, using the prices in `fuel-prices-for-be-assessment.csv`.

**Stack:** Django 6.1 · Django REST Framework · NumPy/SciPy · [OSRM](https://project-osrm.org/) (free routing API) · OpenStreetMap/Leaflet (map).

---

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_data          # one-time: downloads the Census gazetteer (~14 MB) and loads stations
python manage.py runserver
```

Then open http://127.0.0.1:8000/api/route/map/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA

Run the tests with `python manage.py test planner`.

---

## API

### `GET /api/route/?start=<location>&finish=<location>`
### `POST /api/route/` with body `{"start": "<location>", "finish": "<location>"}`

A location can be any of:

| Format | Example | How it is resolved |
|---|---|---|
| `City, ST` or `City, State` | `Dallas, TX`, `saint louis, missouri` | Local Census gazetteer table, no external call |
| `lat,lon` | `36.1699,-115.1398` | Used as-is (must be inside the USA) |
| Free-text address | `1600 Pennsylvania Ave NW, Washington, DC` | Nominatim (OpenStreetMap), restricted to the US |

**Response (abridged, real output):**

```json
{
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=Dallas%2C+TX&finish=Chicago%2C+IL",
  "start":  {"query": "Dallas, TX", "label": "Dallas, TX", "latitude": 32.793333, "longitude": -96.766513, "source": "gazetteer"},
  "finish": {"query": "Chicago, IL", "label": "Chicago, IL", "latitude": 41.837045, "longitude": -87.684939, "source": "gazetteer"},
  "summary": {
    "total_distance_miles": 961.0,
    "estimated_drive_time_hours": 17.04,
    "number_of_fuel_stops": 3,
    "total_gallons": 96.1,
    "total_fuel_cost": 273.27,
    "average_price_per_gallon": 2.844,
    "stations_considered": 20,
    "vehicle_range_miles": 500,
    "miles_per_gallon": 10
  },
  "fuel_stops": [
    {
      "stop_number": 1, "opis_id": 68213, "name": "CADOO MILLS",
      "address": "I-30/US-67, EXIT 87 & FM-1903", "city": "Caddo Mills", "state": "TX",
      "latitude": 33.07454, "longitude": -96.22692,
      "price_per_gallon": 2.801, "route_mile": 40.0, "miles_off_route": 3.5,
      "gallons": 50.0, "cost": 140.03
    }
  ],
  "route_geojson": {"type": "FeatureCollection", "features": ["route LineString, start/finish/fuel stop Points"]}
}
```

- `map_url` opens an interactive Leaflet map of the route with every fuel stop marked.
- `route_geojson` is standard GeoJSON, so you can also paste it into [geojson.io](https://geojson.io).

**Errors** return `{"error": "<code>", "detail": "<message>"}`:

| Status | When |
|---|---|
| 400 | A parameter is missing or invalid, or the location was not found / is outside the USA (`location_not_found`) |
| 422 | No drivable route exists (`route_not_found`), or a gap between truck stops is longer than the range (`no_feasible_fuel_plan`) |
| 502 | The routing service is unavailable (`routing_unavailable`) |

### `GET /api/route/map/?start=...&finish=...`
Returns the HTML map page. Results are cached, so opening `map_url` right after an API call does not call the routing API again.

### Postman
Import `postman_collection.json`. It includes GET and POST examples, a long trip, the map page and the error cases. `base_url` defaults to `http://127.0.0.1:8000`.

---

## How it works

```
request ─► geocode start/finish ─► OSRM route (1 call) ─► resample route every 1 mi
        ─► KD-tree: stations within 10 mi of the route ─► greedy optimal refuelling ─► response (cached)
```

1. **Stations are geocoded offline, once.** The CSV has no coordinates. `load_data` matches each truck stop's city/state against the public-domain [US Census Gazetteer](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html) (97% match). The remaining small towns are stored in `data/city_coordinates_extra.csv`, which was generated once with `load_data --geocode-missing`. Canadian stops are skipped, and duplicate OPIS ids keep their lowest price. Result: **6,626 US stations**.
2. **External API usage:** a typical request makes **exactly one** external call, to OSRM for the route. `City, ST` and coordinate inputs are resolved locally. Only free-text addresses use Nominatim, which allows at most 3 calls. Routes, geocodes and full plans are cached, so repeated requests make **zero** calls.
3. **Finding stations on the route:** the station table is loaded into NumPy arrays once per process. For each request, the route polyline is resampled every mile and put into a SciPy `cKDTree`, using 3D unit vectors so distances are spherical. All stations are then queried at once. Each station within 10 miles of the route gets a mile marker. Only the cheapest station per 50 miles of road is kept, which avoids impractical micro-stops. In testing this cost under 0.1% versus considering every station.
4. **Choosing the stops:** this uses the classic greedy algorithm for the "gas station problem", which is optimal for minimizing cost with a fixed tank size. At each stop:
   - If a cheaper stop is within range, buy just enough fuel to reach it.
   - Otherwise, if the destination is within range, buy just enough to finish.
   - Otherwise, fill the tank and drive to the cheapest stop within range.

**Performance** (local machine, real OSRM):

| Trip | Distance | Stops | Fuel cost | First call | Cached |
|---|---|---|---|---|---|
| Dallas, TX → Chicago, IL | 961 mi | 3 | $273.27 | ~0.5 s | ~7 ms |
| New York, NY → Los Angeles, CA | 2,810 mi | 14 | $852.97 | ~1.9 s | ~14 ms |
| Seattle, WA → Miami, FL | 3,303 mi | 16 | $1,027.08 | ~0.9 s | ~18 ms |

Nearly all of the first-call time is the OSRM network request. The planning itself takes a few milliseconds.

## Assumptions

- The vehicle starts with an empty tank. The first stop (the cheapest one in the first 50 miles) is the origin fill-up. Fuel for the miles before it is billed at that stop's price, so `total_gallons` always equals `distance / 10` and the cost covers the whole trip.
- Stations are located at their city's coordinates, since the CSV only has addresses like "I-44, EXIT 283". The 10-mile corridor absorbs this imprecision. Detours to stations are not added to the fuel used.
- The vehicle arrives at the destination with an empty tank, so no fuel is bought that isn't needed.

## Configuration

All settings live in `FUEL_PLANNER` in `config/settings.py`: vehicle range, MPG, corridor width, segment size, API URLs, timeouts and cache TTL. `OSRM_BASE_URL` and `NOMINATIM_BASE_URL` can also be overridden with environment variables, for example to use a self-hosted OSRM. The default cache is in-process (LocMem). Point `CACHES` at Redis for multi-process deployments.

## Project layout

```
config/                     Django settings & root urls
planner/
  models.py                 Place (gazetteer), FuelStation
  views.py / serializers.py RoutePlanView (GET/POST), route_map_view
  services/
    geocoding.py            coordinates -> gazetteer -> Nominatim
    routing.py              OSRM client + polyline decoder
    stations.py             in-memory station index (NumPy)
    optimizer.py            route resampling, KD-tree corridor search, greedy optimizer
    planner.py              orchestration, response building, caching
  management/commands/load_data.py
  templates/planner/map.html  Leaflet map
  tests/                    optimizer + API tests (routing mocked)
data/city_coordinates_extra.csv
postman_collection.json
```
