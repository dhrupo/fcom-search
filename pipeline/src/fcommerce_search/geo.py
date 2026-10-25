import math
from dataclasses import dataclass
from functools import cache
from importlib import resources

BIG_CITY = 100_000
SAME_AREA_KM = 25
EARTH_KM = 6371.0


@dataclass(frozen=True)
class City:
    name: str
    country: str
    lat: float
    lon: float
    population: int


@cache
def _load() -> tuple[tuple[City, ...], dict[str, list[City]]]:
    text = resources.files("fcommerce_search").joinpath("geo", "cities15000.tsv").read_text(encoding="utf-8")
    cities, by_name = [], {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, ascii_name, country, lat, lon, pop = line.split("\t")
        city = City(name, country, float(lat), float(lon), int(pop or 0))
        cities.append(city)
        for key in {name.casefold(), ascii_name.casefold()}:
            by_name.setdefault(key, []).append(city)
    return tuple(cities), by_name


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def big_cities_near(lat: float, lon: float, radius_km: float, limit: int = 8) -> list[City]:
    """Cities of 100,000+ people within the radius, nearest first, without parts of a bigger city."""
    inside = [c for c in _load()[0] if c.population >= BIG_CITY and distance_km(lat, lon, c.lat, c.lon) <= radius_km]
    kept: list[City] = []
    # A bigger city's Marketplace search already covers places this close (Dhaka covers Gulshan).
    for city in sorted(inside, key=lambda c: -c.population):
        if all(distance_km(city.lat, city.lon, k.lat, k.lon) > SAME_AREA_KM for k in kept):
            kept.append(city)
    return sorted(kept, key=lambda c: distance_km(lat, lon, c.lat, c.lon))[:limit]


def towns_named(name: str) -> list[City]:
    return list(_load()[1].get(name.strip().casefold(), []))


def find_town(name: str, country: str | None = None) -> City | None:
    """The most populous city with this name (in this country code, when given)."""
    matches = [c for c in _load()[1].get(name.strip().casefold(), []) if not country or c.country == country]
    return max(matches, key=lambda c: c.population, default=None)


@cache
def _countries() -> dict[str, str]:
    text = resources.files("fcommerce_search").joinpath("geo", "countries.tsv").read_text(encoding="utf-8")
    return dict(line.split("\t") for line in text.splitlines() if line and not line.startswith("#"))


def country_name(code: str | None) -> str | None:
    return _countries().get(code or "")


def cities_by_prefix(prefix: str, limit: int = 4) -> list[City]:
    """Bundled cities whose name starts with the prefix, most populous first."""
    prefix = prefix.strip().casefold()
    by_name = _load()[1]
    found = {id(c): c for key, cities in by_name.items() if key.startswith(prefix) for c in cities}
    return sorted(found.values(), key=lambda c: -c.population)[:limit]
