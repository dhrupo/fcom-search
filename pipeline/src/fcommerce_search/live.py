import json
import re
import sqlite3
import statistics
import threading
import time
import uuid
from collections import OrderedDict
from urllib.parse import quote, urlsplit

from crawler.facebook.marketplace import parse_search
from crawler.outcomes import Outcome, classify
from crawler.transport import DROPS, HostBlocked, Transport

from .config import Config
from .geo import City, big_cities_near, distance_km, towns_named
from .marketplace import PAGE_SIZE, PointSearch
from .places import Places

FRESH_SECONDS = 600
SEARCH_SECONDS = 30 * 60
MAX_SEARCHES = 100
MAX_MORE = 5
LOCK_STRIPES = 64
SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    item_id TEXT NOT NULL, city TEXT NOT NULL, data TEXT NOT NULL, fetched_at REAL NOT NULL,
    PRIMARY KEY (item_id, city));
CREATE INDEX IF NOT EXISTS listings_city ON listings (city);
CREATE TABLE IF NOT EXISTS fetches (key TEXT PRIMARY KEY, at REAL NOT NULL);
"""
# Phone numbers: 10+ digits joined by '-' or '.', or an international '+' number that may use spaces.
PHONE = re.compile(r"(?<![\w+])(?:\+\d[\d\s-]{8,}\d|\d(?:[-.]?\d){9,})(?!\w)")
HANDLE = re.compile(r"@\w[\w.]*")
STATUS = {Outcome.OK: "ok", Outcome.NOTHING_NEW: "ok", Outcome.BLOCKED: "blocked"}


def scrub(text: str, names=()) -> str:
    """Remove phone numbers, @handles and known private names from seller text."""
    text = HANDLE.sub("•••", PHONE.sub("•••", text or ""))
    for name in names:
        text = re.sub(rf"(?<![\w.]){re.escape(name)}(?!\w)", "•••", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _price(item: dict) -> dict:
    return item["price"] if isinstance(item.get("price"), dict) else {}


class LiveSearch:
    """Search Marketplace live, logged out, in the big cities around a point, and keep a local pool."""

    def __init__(self, config: Config, places: Places, transport=None, now=time.time, names=(), point_search=True):
        self.config, self.places, self._transport, self._now, self._names = config, places, transport, now, names
        self._point_search, self._point = point_search, None
        self._searches: OrderedDict[str, dict] = OrderedDict()
        self._locks = [threading.Lock() for _ in range(LOCK_STRIPES)]
        self._search_lock = threading.Lock()
        with self._connect() as db:
            db.executescript(SCHEMA)

    def _connect(self):
        return sqlite3.connect(self.config.state_db, timeout=30)

    def _transport_for_search(self):
        # sessions=None: live search never uses an imported login.
        return self._transport or Transport(self.config.crawler, sessions=None)

    def _point_session(self) -> PointSearch:
        self._point = self._point or PointSearch(self._transport_for_search(), clock=self._now)
        return self._point

    def _lock(self, key: str) -> threading.Lock:
        # A fixed set of locks: the same key always gets the same lock, and the set never grows.
        return self._locks[hash(key) % LOCK_STRIPES]

    def run(self, keyword: str, lat: float, lon: float, radius_km: float, min_price=None, max_price=None, days=None):
        """Search Facebook at the point; when that fails, search the nearby big cities by address."""
        keyword = " ".join(keyword.split())[:100]
        wanted = {"lat": lat, "lon": lon, "radius_km": radius_km, "min_price": min_price, "max_price": max_price,
                  "days": days}
        if self._point_search:
            key = f"pt:{lat:.3f},{lon:.3f},{radius_km},{min_price},{max_price},{keyword.casefold()}"
            with self._lock(key):
                status, full = "ok", self._pool_size(key) >= PAGE_SIZE
                if not self._fresh(key, keyword):
                    status, listings, full = self._point_session().search(keyword, lat, lon, radius_km, band=(min_price, max_price))
                    if status == "ok":
                        self._save(key, keyword, listings)
            if status == "ok":
                yield from self._point_events(key, keyword, wanted, (min_price, max_price), full)
                return
            yield {"type": "mode", "mode": "basic"}
        cities = self.places.cities_to_search(lat, lon, radius_km)
        yield from self.run_cities(keyword, cities, lat, lon, radius_km, min_price, max_price, days)

    def _point_events(self, key, keyword, wanted, band, full):
        near = big_cities_near(wanted["lat"], wanted["lon"], wanted["radius_km"], limit=1)
        area = near[0].name if near else "this area"
        matches = self._pool(key, keyword, match_words=False, **wanted)
        search_id = uuid.uuid4().hex
        state = {"keyword": keyword, "key": key, "wanted": wanted, "area": area, "bands": [], "more_left": MAX_MORE,
                 "shown": {r["item_id"] for r in matches}, "at": self._now()}
        if full:
            state["bands"] = self._split(band, [r["price"] for r in matches])
        self._searches[search_id] = state
        while len(self._searches) > MAX_SEARCHES:
            self._searches.popitem(last=False)
        yield {"type": "start", "cities": [area], "mode": "point", "search_id": search_id}
        yield {"type": "city", "city": area, "status": "ok", "found": len(matches), "results": matches}
        yield {"type": "done", "total": len(matches), "search_id": search_id, "can_load_more": self._can_more(state)}

    @staticmethod
    def _can_more(state) -> bool:
        return bool(state["bands"]) and state["more_left"] > 0

    @staticmethod
    def _split(band, prices) -> list:
        """A band that came back full hides more listings: split it at its middle price."""
        low, high = band
        inside = [p for p in prices if p is not None and (low is None or p >= low) and (high is None or p <= high)]
        if not inside:
            return []
        middle = statistics.median(inside)
        if (low is not None and middle <= low) or (high is not None and middle >= high):
            return []
        return [(low or 0, middle), (middle, high)]

    def more(self, search_id: str):
        """The next price band of an earlier point search: up to MAX_MORE times, never repeating a listing."""
        with self._search_lock:
            yield from self._more(search_id)

    def _more(self, search_id: str):
        state = self._searches.get(search_id)
        if not state or self._now() - state["at"] > SEARCH_SECONDS:
            self._searches.pop(search_id, None)
            yield {"type": "error", "error": "this search has expired; search again"}
            return
        if not self._can_more(state):
            yield {"type": "done", "total": 0, "search_id": search_id, "can_load_more": False}
            return
        band = state["bands"].pop(0)
        state["more_left"] -= 1
        wanted = state["wanted"]
        status, listings, full = self._point_session().search(state["keyword"], wanted["lat"], wanted["lon"],
                                                              wanted["radius_km"], band=band)
        if status != "ok":
            state["bands"].insert(0, band)
            state["more_left"] += 1
        else:
            self._save(state["key"], state["keyword"], listings, mark_fresh=False)
        fresh = {item["item_id"] for item in listings}
        matches = [r for r in self._pool(state["key"], state["keyword"], match_words=False, **wanted)
                   if r["item_id"] in fresh and r["item_id"] not in state["shown"]]
        state["shown"].update(r["item_id"] for r in matches)
        if full:
            state["bands"] += self._split(band, [_price(item).get("amount") for item in listings])
        yield {"type": "city", "city": state["area"], "status": status, "found": len(matches), "results": matches}
        yield {"type": "done", "total": len(matches), "search_id": search_id, "can_load_more": self._can_more(state)}

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
                status = "ok"
                if not self._fresh(slug, keyword):
                    transport = transport or self._transport_for_search()
                    status = self._fetch(transport, city, slug, keyword)
            matches = [r for r in self._pool(slug, keyword, **wanted) if r["item_id"] not in shown]
            shown.update(r["item_id"] for r in matches)
            total += len(matches)
            yield {"type": "city", "city": city.name, "status": status, "found": len(matches), "results": matches}
        yield {"type": "done", "total": total, "can_load_more": False}

    def _fresh(self, slug: str, keyword: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT at FROM fetches WHERE key = ?", (f"{slug}|{keyword.casefold()}",)).fetchone()
        return bool(row) and self._now() - row[0] < FRESH_SECONDS

    def _fetch(self, transport, city: City, slug: str, keyword: str) -> str:
        url = f"https://www.facebook.com/marketplace/{slug}/search?query={quote(keyword)}"
        try:
            response = transport.get(url, headers={"accept-language": "en-US,en;q=0.9"})
        except HostBlocked:
            return "blocked"
        except DROPS:
            return "failed"
        if not urlsplit(str(response.url)).path.startswith(f"/marketplace/{slug}/"):
            self.places.remember_slug(city.name, city.country, works=False)
            return "no_address"
        outcome = classify(response)
        if outcome is not Outcome.OK:
            return STATUS.get(outcome, "failed")
        outcome, listings = parse_search(response.text)
        self.places.remember_slug(city.name, city.country, works=True)
        self._save(slug, keyword, listings)
        return STATUS.get(outcome, "failed")

    def _save(self, key: str, keyword: str, listings: list[dict], mark_fresh: bool = True) -> None:
        now = self._now()
        with self._connect() as db:
            for item in listings:
                db.execute("INSERT OR REPLACE INTO listings (item_id, city, data, fetched_at) VALUES (?, ?, ?, ?)",
                           (item["item_id"], key, json.dumps(item, ensure_ascii=False), now))
            if mark_fresh:
                db.execute("INSERT OR REPLACE INTO fetches (key, at) VALUES (?, ?)", (f"{key}|{keyword.casefold()}", now))

    def _town_point(self, town: str | None, near: tuple[float, float]):
        """The listing's town on the map: the same-named place nearest the search (bundled list, then OpenStreetMap)."""
        if not town:
            return None
        points = [(c.lat, c.lon) for c in towns_named(town.split(",")[0].strip())]
        if not points:
            points = [(p["lat"], p["lon"]) for p in self.places.suggest(town)]
        return min(points, key=lambda p: distance_km(*near, *p)) if points else None

    def _pool_size(self, key: str) -> int:
        with self._connect() as db:
            return db.execute("SELECT count(*) FROM listings WHERE city = ?", (key,)).fetchone()[0]

    def _pool(self, slug, keyword, lat, lon, radius_km, min_price, max_price, days, match_words=True):
        towns: dict[str, tuple | None] = {}
        # Facebook already matched point-search results to the keyword, sometimes without the exact words.
        words = re.findall(r"\w+", keyword.casefold()) if match_words else []
        with self._connect() as db:
            rows = [json.loads(r[0]) for r in db.execute(
                "SELECT data FROM listings WHERE city = ? ORDER BY fetched_at DESC", (slug,))]
        out = []
        for item in rows:
            title = (item.get("title") or "").casefold()
            if words and not all(w in title for w in words):
                continue
            price = _price(item).get("amount")
            if (min_price is not None or max_price is not None) and price is None:
                continue
            if price is not None and ((min_price is not None and price < min_price) or
                                      (max_price is not None and price > max_price)):
                continue
            if days and (item.get("created_at") or 0) < self._now() - days * 86400:
                continue
            # Facebook's radius is loose (a 40 km search returned a town 80 km away): check it here, once per town.
            town = item.get("city")
            if town not in towns:
                towns[town] = self._town_point(town, (lat, lon))
            point = towns[town]
            distance = round(distance_km(lat, lon, *point), 1) if point else None
            if distance is not None and distance > radius_km:
                continue
            out.append({
                "item_id": item["item_id"], "title": scrub(item.get("title"), self._names),
                "price": price, "price_text": _price(item).get("formatted"),
                "town": item.get("city"), "created_at": item.get("created_at"),
                "photo": (item.get("images") or [None])[0], "permalink": item.get("permalink"),
                "sold": item.get("sold"), "distance_km": distance,
            })
        return out
