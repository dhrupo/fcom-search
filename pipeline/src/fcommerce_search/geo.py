import math
from dataclasses import dataclass
from functools import cache
from importlib import resources

BIG_CITY = 100_000
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
    """Cities of 100,000+ people within the radius, nearest first."""
    near = [(distance_km(lat, lon, c.lat, c.lon), c) for c in _load()[0] if c.population >= BIG_CITY]
    return [c for d, c in sorted(near, key=lambda dc: dc[0]) if d <= radius_km][:limit]


def find_town(name: str, country: str | None = None) -> City | None:
    """The most populous city with this name (in this country code, when given)."""
    matches = [c for c in _load()[1].get(name.strip().casefold(), []) if not country or c.country == country]
    return max(matches, key=lambda c: c.population, default=None)
