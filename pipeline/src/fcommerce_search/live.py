import json
import re
import sqlite3
import threading
import time
from urllib.parse import quote, urlsplit

from crawler.facebook.marketplace import parse_search
from crawler.outcomes import Outcome, classify
from crawler.transport import DROPS, HostBlocked, Transport

from .config import Config
from .geo import City, distance_km, find_town
from .places import Places

FRESH_SECONDS = 600
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_id TEXT PRIMARY KEY, city TEXT NOT NULL, data TEXT NOT NULL, fetched_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS listings_city ON listings (city);
CREATE TABLE IF NOT EXISTS fetches (key TEXT PRIMARY KEY, at REAL NOT NULL);
"""
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
HANDLE = re.compile(r"@\w[\w.]*")
STATUS = {Outcome.OK: "ok", Outcome.NOTHING_NEW: "ok", Outcome.BLOCKED: "blocked"}


def scrub(text: str, names=()) -> str:
    """Remove phone numbers, @handles and known private names from seller text."""
    text = HANDLE.sub("•••", PHONE.sub("•••", text or ""))
    for name in names:
        text = re.sub(re.escape(name), "•••", text, flags=re.IGNORECASE)
    return " ".join(text.split())


class LiveSearch:
    """Search Marketplace live, logged out, in the big cities around a point, and keep a local pool."""

    def __init__(self, config: Config, places: Places, transport=None, now=time.time, names=()):
        self.config, self.places, self._transport, self._now, self._names = config, places, transport, now, names
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self):
        return sqlite3.connect(self.config.state_db, timeout=30)

    def _transport_for_search(self):
        # sessions=None: live search never uses an imported login.
        return self._transport or Transport(self.config.crawler, sessions=None)

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def run(self, keyword: str, lat: float, lon: float, radius_km: float, **filters):
        return self.run_cities(keyword, self.places.cities_to_search(lat, lon, radius_km), lat, lon, radius_km, **filters)

    def run_cities(self, keyword, cities: list[City], lat, lon, radius_km, min_price=None, max_price=None, days=None):
        keyword = " ".join(keyword.split())[:100]
        wanted = {"lat": lat, "lon": lon, "radius_km": radius_km, "min_price": min_price, "max_price": max_price,
                  "days": days}
        yield {"type": "start", "cities": [c.name for c in cities]}
        transport, shown, total = None, set(), 0
        for city in cities:
            slug = self.places.slug(city.name)
            # One crawl per city and keyword at a time; identical searches wait and then read the pool.
            with self._lock(f"{slug}|{keyword.casefold()}"):
                status, found = "ok", 0
                if not self._fresh(slug, keyword):
                    transport = transport or self._transport_for_search()
                    status, found = self._fetch(transport, city, slug, keyword)
            matches = [r for r in self._pool(slug, keyword, **wanted) if r["item_id"] not in shown]
            shown.update(r["item_id"] for r in matches)
            total += len(matches)
            yield {"type": "city", "city": city.name, "status": status, "found": found, "results": matches}
        yield {"type": "done", "total": total}

    def _fresh(self, slug: str, keyword: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT at FROM fetches WHERE key = ?", (f"{slug}|{keyword.casefold()}",)).fetchone()
        return bool(row) and self._now() - row[0] < FRESH_SECONDS

    def _fetch(self, transport, city: City, slug: str, keyword: str) -> tuple[str, int]:
        url = f"https://www.facebook.com/marketplace/{slug}/search?query={quote(keyword)}"
        try:
            response = transport.get(url, headers={"accept-language": "en-US,en;q=0.9"})
        except HostBlocked:
            return "blocked", 0
        except DROPS:
            return "failed", 0
        if not urlsplit(str(response.url)).path.startswith(f"/marketplace/{slug}/"):
            self.places.remember_slug(city.name, city.country, works=False)
            return "no_address", 0
        outcome = classify(response)
        if outcome is not Outcome.OK:
            return STATUS.get(outcome, "failed"), 0
        outcome, listings = parse_search(response.text)
        self.places.remember_slug(city.name, city.country, works=True)
        now = self._now()
        with self._connect() as db:
            for item in listings:
                db.execute("INSERT OR REPLACE INTO listings (item_id, city, data, fetched_at) VALUES (?, ?, ?, ?)",
                           (item["item_id"], slug, json.dumps(item, ensure_ascii=False), now))
            db.execute("INSERT OR REPLACE INTO fetches (key, at) VALUES (?, ?)", (f"{slug}|{keyword.casefold()}", now))
        return STATUS.get(outcome, "failed"), len(listings)

    def _town_point(self, town: str | None):
        if not town:
            return None
        name = town.split(",")[0].strip()
        city = find_town(name)
        if city:
            return city.lat, city.lon
        found = self.places.suggest(town)
        return (found[0]["lat"], found[0]["lon"]) if found else None

    def _pool(self, slug, keyword, lat, lon, radius_km, min_price, max_price, days):
        words = [w for w in re.findall(r"\w+", keyword.casefold())]
        with self._connect() as db:
            rows = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM listings WHERE city = ? ORDER BY fetched_at DESC", (slug,))]
        out = []
        for item in rows:
            title = (item.get("title") or "").casefold()
            if words and not all(w in title for w in words):
                continue
            price = item["price"].get("amount") if isinstance(item.get("price"), dict) else None
            if (min_price is not None or max_price is not None) and price is None:
                continue
            if price is not None and ((min_price is not None and price < min_price) or
                                      (max_price is not None and price > max_price)):
                continue
            if days and (item.get("created_at") or 0) < self._now() - days * 86400:
                continue
            point = self._town_point(item.get("city"))
            distance = round(distance_km(lat, lon, *point), 1) if point else None
            if distance is not None and distance > radius_km:
                continue
            out.append({
                "item_id": item["item_id"], "title": scrub(item.get("title"), self._names),
                "price": price, "price_text": item["price"].get("formatted") if isinstance(item.get("price"), dict) else None,
                "town": item.get("city"), "created_at": item.get("created_at"),
                "photo": (item.get("images") or [None])[0], "permalink": item.get("permalink"),
                "sold": item.get("sold"), "distance_km": distance,
            })
        return out
