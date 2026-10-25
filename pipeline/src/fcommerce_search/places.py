import json
import re
import sqlite3
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from .geo import City, big_cities_near, cities_by_prefix, country_name, find_town

PHOTON = "https://photon.komoot.io"
USER_AGENT = "fcommerce-search (local, personal use)"
MIN_GAP_SECONDS = 1.0
FALLBACK_KM = 150
SCHEMA = """
CREATE TABLE IF NOT EXISTS photon_cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS slugs (key TEXT PRIMARY KEY, works INTEGER NOT NULL, checked_at REAL NOT NULL);
"""


def _http_get(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read(1_000_000).decode("utf-8", "replace")


class Places:
    """Place suggestions (OpenStreetMap via Photon) and which Marketplace city addresses work."""

    def __init__(self, db_path: Path, fetch=_http_get, clock=time.time, sleep=time.sleep):
        self._db_path, self._fetch, self._clock, self._sleep = Path(db_path), fetch, clock, sleep
        self._last_call = 0.0
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self):
        return sqlite3.connect(self._db_path, timeout=30)

    def _photon(self, path: str, params: dict) -> dict:
        key = path + "?" + urllib.parse.urlencode(sorted(params.items()), doseq=True)
        with self._connect() as db:
            row = db.execute("SELECT body FROM photon_cache WHERE key = ?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        # Photon's fair-use limit: at most one request a second from us.
        wait = self._last_call + MIN_GAP_SECONDS - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last_call = self._clock()
        try:
            body = json.loads(self._fetch(f"{PHOTON}{key}"))
        except (OSError, ValueError):
            return {}
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO photon_cache (key, body, at) VALUES (?, ?, ?)",
                       (key, json.dumps(body), self._clock()))
        return body

    @staticmethod
    def _features(body: dict) -> list[dict]:
        out = []
        features = body.get("features") if isinstance(body, dict) else None
        for f in features if isinstance(features, list) else []:
            props, coords = f.get("properties") or {}, (f.get("geometry") or {}).get("coordinates") or []
            if props.get("name") and len(coords) == 2:
                out.append({"name": props["name"], "country": props.get("country"), "countrycode": props.get("countrycode"),
                            "state": props.get("state"), "lon": coords[0], "lat": coords[1]})
        return out

    def suggest(self, query: str) -> list[dict]:
        """Cities and towns for a typed name, the most populous first (villages are left out)."""
        q = " ".join(query.split()).casefold()[:100]
        if len(q) < 2:
            return []
        params = {"q": q, "limit": 6, "lang": "en", "osm_tag": ["place:city", "place:town"]}
        bundled = [{"name": c.name, "country": country_name(c.country), "countrycode": c.country, "state": None,
                    "lat": c.lat, "lon": c.lon, "population": c.population} for c in cities_by_prefix(q)]
        found = self._features(self._photon("/api/", params))
        for f in found:
            town = find_town(f["name"], (f.get("countrycode") or "").upper() or None)
            f["population"] = town.population if town else 0
        merged = {}
        for s in sorted(bundled + found, key=lambda s: -s["population"]):
            merged.setdefault((self.slug(s["name"]), (s.get("countrycode") or "").upper()), s)
        return [{k: v for k, v in s.items() if k != "population"} for s in merged.values()][:6]

    def reverse(self, lat: float, lon: float) -> dict | None:
        found = self._features(self._photon("/reverse", {"lat": round(lat, 3), "lon": round(lon, 3), "limit": 1}))
        return found[0] if found else None

    @staticmethod
    def slug(name: str) -> str:
        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z0-9]", "", ascii_name.lower())

    def slug_status(self, name: str, country: str) -> bool | None:
        with self._connect() as db:
            row = db.execute("SELECT works FROM slugs WHERE key = ?", (f"{name}|{country}",)).fetchone()
        return None if row is None else bool(row[0])

    def remember_slug(self, name: str, country: str, works: bool) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO slugs (key, works, checked_at) VALUES (?, ?, ?)",
                       (f"{name}|{country}", int(works), self._clock()))

    def cities_to_search(self, lat: float, lon: float, radius_km: float) -> list[City]:
        """Big cities inside the radius whose address is not known to fail; else the nearest big city."""
        usable = lambda cities: [c for c in cities if self.slug_status(c.name, c.country) is not False]
        return usable(big_cities_near(lat, lon, radius_km)) or usable(big_cities_near(lat, lon, FALLBACK_KM))[:1]
