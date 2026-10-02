"""Load the offline geocoding data and the fuel price CSV into the database.

The fuel CSV has no coordinates, so each truck stop is geocoded to its city using the
public-domain US Census gazetteer (places + county subdivisions). This happens once,
offline, so API requests never need to geocode stations.
"""

import csv
import io
import time
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import FuelStation, Place
from planner.services.http import get_session
from planner.services.places import US_STATES, gazetteer_base_names, normalize_place_key
from planner.services.stations import reset_station_index

GAZETTEER_BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer"
GAZETTEER_FILES = ("2024_Gaz_place_national", "2024_Gaz_cousubs_national")
OVERRIDES_FILE = "city_coordinates_extra.csv"


class Command(BaseCommand):
    help = "Load the Census gazetteer and the fuel prices CSV (with coordinates) into the database."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.BASE_DIR / "fuel-prices-for-be-assessment.csv"))
        parser.add_argument("--data-dir", default=str(settings.BASE_DIR / "data"))
        parser.add_argument(
            "--geocode-missing",
            action="store_true",
            help="Resolve cities missing from the gazetteer via Nominatim (1 req/sec) and save them for reuse.",
        )

    def handle(self, *args, **options):
        csv_path, data_dir = Path(options["csv"]), Path(options["data_dir"])
        if not csv_path.exists():
            raise CommandError(f"Fuel prices CSV not found: {csv_path}")
        data_dir.mkdir(parents=True, exist_ok=True)

        places = self._read_gazetteer(data_dir)
        coordinates = {key: (p.latitude, p.longitude) for key, p in places.items()}
        coordinates.update(self._read_overrides(data_dir / OVERRIDES_FILE))

        stations, missing = self._read_stations(csv_path, coordinates)
        if missing and options["geocode_missing"]:
            resolved = self._geocode_missing(missing, data_dir / OVERRIDES_FILE)
            coordinates.update(resolved)
            stations, missing = self._read_stations(csv_path, coordinates)

        with transaction.atomic():
            Place.objects.all().delete()
            Place.objects.bulk_create(places.values(), batch_size=2000)
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=2000)
        reset_station_index()

        self.stdout.write(self.style.SUCCESS(f"Loaded {len(places)} places and {len(stations)} fuel stations."))
        if missing:
            sample = ", ".join(f"{c}, {s}" for c, s in sorted(missing)[:10])
            self.stdout.write(self.style.WARNING(
                f"{len(missing)} US cities could not be geocoded and were skipped (e.g. {sample}). "
                "Re-run with --geocode-missing to resolve them."
            ))

    def _read_gazetteer(self, data_dir):
        entries = []
        for priority, name in enumerate(GAZETTEER_FILES):
            path = data_dir / f"{name}.txt"
            if not path.exists():
                self._download_gazetteer(name, data_dir)
            with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.reader(fh, delimiter="\t")
                header = [h.strip() for h in next(reader)]
                for row in reader:
                    rec = dict(zip(header, (v.strip() for v in row)))
                    if rec["USPS"] not in US_STATES:
                        continue
                    # Prefer incorporated places, then larger land area, for duplicate names.
                    unincorporated = rec["NAME"].endswith((" CDP", " CCD"))
                    rank = (priority, unincorporated, -int(rec["ALAND"] or 0))
                    entries.append((rank, rec))

        entries.sort(key=lambda e: e[0])
        places = {}
        for _, rec in entries:
            for base_name in gazetteer_base_names(rec["NAME"]):
                key = (normalize_place_key(base_name), rec["USPS"])
                if key[0] and key not in places:
                    places[key] = Place(
                        name=base_name, key=key[0], state=key[1],
                        latitude=float(rec["INTPTLAT"]), longitude=float(rec["INTPTLONG"]),
                    )
        return places

    def _download_gazetteer(self, name, data_dir):
        url = f"{GAZETTEER_BASE}/{name}.zip"
        self.stdout.write(f"Downloading {url} ...")
        response = get_session().get(url, timeout=120)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            archive.extractall(data_dir)

    def _read_overrides(self, path):
        if not path.exists():
            return {}
        with path.open(encoding="utf-8", newline="") as fh:
            return {
                (normalize_place_key(r["city"]), r["state"]): (float(r["latitude"]), float(r["longitude"]))
                for r in csv.DictReader(fh)
            }

    def _read_stations(self, csv_path, coordinates):
        """Parse the CSV, keeping US stations only and the lowest price per OPIS id."""
        stations, missing = {}, set()
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                state, city = row["State"].strip().upper(), row["City"].strip()
                if state not in US_STATES:
                    continue
                try:
                    opis_id = int(row["OPIS Truckstop ID"])
                    price = Decimal(row["Retail Price"].strip())
                except (ValueError, InvalidOperation):
                    continue

                coords = coordinates.get((normalize_place_key(city), state))
                if coords is None:
                    missing.add((city, state))
                    continue

                existing = stations.get(opis_id)
                if existing is not None and existing.retail_price <= price:
                    continue
                stations[opis_id] = FuelStation(
                    opis_id=opis_id,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=city,
                    state=state,
                    rack_id=int(row["Rack ID"]) if row["Rack ID"].strip().isdigit() else None,
                    retail_price=price,
                    latitude=coords[0],
                    longitude=coords[1],
                )
        return list(stations.values()), missing

    def _geocode_missing(self, missing, overrides_path):
        config = settings.FUEL_PLANNER
        self.stdout.write(f"Geocoding {len(missing)} cities via Nominatim (about {len(missing)} seconds)...")
        resolved, new_rows = {}, []
        for city, state in sorted(missing):
            try:
                response = get_session().get(
                    f"{config['NOMINATIM_BASE_URL']}/search",
                    params={"city": city, "state": state, "countrycodes": "us", "format": "jsonv2", "limit": 1},
                    timeout=config["HTTP_TIMEOUT_SECONDS"],
                )
                response.raise_for_status()
                results = response.json()
            except Exception as exc:  # noqa: BLE001 - best effort, keep going
                self.stdout.write(self.style.WARNING(f"  {city}, {state}: {exc}"))
                results = []
            if results:
                lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
                resolved[(normalize_place_key(city), state)] = (lat, lon)
                new_rows.append({"city": city, "state": state, "latitude": lat, "longitude": lon})
            time.sleep(1.1)  # Nominatim usage policy: max 1 request per second

        write_header = not overrides_path.exists()
        with overrides_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["city", "state", "latitude", "longitude"])
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
        self.stdout.write(f"Resolved {len(resolved)}/{len(missing)} cities; saved to {overrides_path.name}.")
        return resolved
