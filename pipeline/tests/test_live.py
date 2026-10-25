import json
import threading

import pytest
from crawler.config import Config as CrawlerConfig
from replay import ReplayTransport

from fcommerce_search import live as live_module
from fcommerce_search.config import Config
from fcommerce_search.geo import City
from fcommerce_search.live import LiveSearch
from fcommerce_search.places import Places

NOW = 1792924206 + 3600
SYLHET = (24.8949, 91.8687)
TOWNS = {"maulvi bazar": [91.7774, 24.4829], "chhatak": [91.6694, 25.0381], "lalabazar sylhet bangladesh": [91.83, 24.86]}


def photon(url):
    q = url.split("q=")[1].split("&")[0].replace("+", " ").replace("%2C", "").lower() if "q=" in url else ""
    hit = next((v for k, v in TOWNS.items() if q.startswith(k)), None)
    return json.dumps({"features": [{"properties": {"name": q.title(), "countrycode": "BD"},
                                     "geometry": {"coordinates": hit}}] if hit else []})


def search(tmp_path, transport="replay", names=(), point=False):
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=tmp_path, state_db=tmp_path / "state.sqlite3")
    places = Places(config.state_db, fetch=photon, clock=lambda: NOW, sleep=lambda s: None)
    transport = ReplayTransport() if transport == "replay" else transport
    return LiveSearch(config, places, transport=transport, now=lambda: NOW, names=names, point_search=point)


def results(events):
    return [r for e in events if e["type"] in ("city", "done") for r in e.get("results", [])]


def test_events_stream_per_city(tmp_path):
    events = list(search(tmp_path).run("iphone", *SYLHET, radius_km=10))
    assert [e["type"] for e in events] == ["start", "city", "done"]
    assert events[0]["cities"] == ["Sylhet"]
    city = events[1]
    # found counts the listings shown: 24 fetched, 22 of them inside 10 km.
    assert (city["city"], city["status"], city["found"]) == ("Sylhet", "ok", 22)
    first = city["results"][0]
    assert set(first) >= {"item_id", "title", "price", "price_text", "town", "created_at", "photo", "permalink", "distance_km"}
    assert first["permalink"].startswith("https://www.facebook.com/marketplace/item/")


@pytest.mark.parametrize("radius, expected", [(20, 9), (100, 10)])
def test_filters_price_days_radius(tmp_path, radius, expected):
    events = list(search(tmp_path).run("iphone", *SYLHET, radius_km=radius, min_price=10000, max_price=30000, days=7))
    found = results(events)
    assert len(found) == expected
    assert all(10000 <= r["price"] <= 30000 and r["created_at"] >= NOW - 7 * 86400 for r in found)
    assert ("Maulvi Bazar" in [r["town"] for r in found]) == (radius == 100)


def test_pool_grows_and_repeat_search_reuses_it(tmp_path):
    transport = ReplayTransport()
    s = search(tmp_path, transport)
    first = results(list(s.run("iphone", *SYLHET, radius_km=10)))
    again = results(list(s.run("iphone", *SYLHET, radius_km=10)))
    assert len(transport.urls) == 1
    assert sorted(r["item_id"] for r in again) == sorted(r["item_id"] for r in first)


def test_blocked_city_reports_and_others_continue(tmp_path):
    cities = [City("Sylhet", "BD", *SYLHET, 237_000), City("Dhaka", "BD", 23.7104, 90.4074, 10_000_000)]
    events = list(search(tmp_path, ReplayTransport(status={"sylhet": 429})).run_cities("iphone", cities, *SYLHET, radius_km=250))
    statuses = {e["city"]: e["status"] for e in events if e["type"] == "city"}
    assert statuses["Sylhet"] == "blocked"
    assert "Dhaka" in statuses and statuses["Dhaka"] == "ok"
    assert events[-1]["type"] == "done"


def test_city_without_an_address_is_remembered(tmp_path):
    s = search(tmp_path)
    town = City("Moulvibazar", "BD", 24.4829, 91.7774, 100_000)
    events = list(s.run_cities("iphone", [town], *SYLHET, radius_km=100))
    assert [e["status"] for e in events if e["type"] == "city"] == ["no_address"]
    assert s.places.slug_status("Moulvibazar", "BD") is False


def test_logged_out_only(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(live_module, "Transport", lambda config, **kwargs: made.append(kwargs) or ReplayTransport())
    s = search(tmp_path, transport=None)
    list(s.run("iphone", *SYLHET, radius_km=10))
    assert made and all(k.get("sessions") is None for k in made)


def test_identical_concurrent_searches_share_one_crawl(tmp_path):
    transport = ReplayTransport()
    s = search(tmp_path, transport)
    out = []
    threads = [threading.Thread(target=lambda: out.append(results(list(s.run("iphone", *SYLHET, radius_km=10)))))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 21 listings in Sylhet + 1 in Lalabazar are inside 10 km; Maulvi Bazar and Chhatak are not.
    assert len(transport.urls) == 1 and [len(o) for o in out] == [22, 22, 22]


def test_client_disconnect_stops_queued_cities(tmp_path):
    transport = ReplayTransport()
    stream = search(tmp_path, transport).run("iphone", *SYLHET, radius_km=250)
    assert next(stream)["type"] == "start"
    next(stream)
    stream.close()
    assert len(transport.urls) == 1


def test_listing_has_no_seller_name_and_scrubs_title(tmp_path):
    events = list(search(tmp_path, names=("Rahim Uddin",)).run("iphone", *SYLHET, radius_km=10))
    text = json.dumps(events, ensure_ascii=False)
    assert "marketplace_listing_seller" not in text and "seller" not in json.dumps(results(events)[0])
    assert live_module.scrub("iPhone 13 call 01711-223344 @rahim_shop Rahim Uddin", ("Rahim Uddin",)) == "iPhone 13 call ••• ••• •••"


def test_network_drop_and_cooldown_are_reported(tmp_path):
    from crawler.transport import DROPS, HostBlocked

    class Failing(ReplayTransport):
        def __init__(self, error):
            super().__init__()
            self.error = error

        def get(self, url, **kwargs):
            raise self.error

    for error, status in ((HostBlocked("facebook.com", 0), "blocked"), (DROPS[0]("reset"), "failed")):
        events = list(search(tmp_path / status, Failing(error)).run("iphone", *SYLHET, radius_km=10))
        assert [e["status"] for e in events if e["type"] == "city"] == [status]


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def test_point_search_is_one_request(tmp_path):
    transport = ReplayTransport()
    events = list(search(tmp_path, transport, point=True).run("iphone", *SYLHET, radius_km=40))
    assert [e["type"] for e in events] == ["start", "city", "done"]
    assert (len(transport.urls), len(transport.posts)) == (1, 1)
    assert events[0]["mode"] == "point" and events[-1]["search_id"] and events[-1]["can_load_more"] is True
    assert events[1]["found"] == len(events[1]["results"]) > 0


def test_falls_back_to_city_addresses(tmp_path):
    events = list(search(tmp_path, ReplayTransport(graphql_status=500), point=True).run("iphone", *SYLHET, radius_km=10))
    assert events[0] == {"type": "mode", "mode": "basic"}
    assert [(e["city"], e["status"]) for e in events if e["type"] == "city"] == [("Sylhet", "ok")]
    assert events[-1]["type"] == "done" and events[-1].get("can_load_more") is False


def test_load_more_fetches_new_band_without_repeats(tmp_path):
    transport = ReplayTransport()
    s = search(tmp_path, transport, point=True)
    first = list(s.run("iphone", *SYLHET, radius_km=40))
    shown = {r["item_id"] for r in results(first)}
    more = list(s.more(first[-1]["search_id"]))
    new = results(more)
    band = json.loads(transport.posts[-1]["form"]["variables"])["params"]["browse_request_params"]
    assert band["filter_price_upper_bound"] < 214748364700
    assert new and not {r["item_id"] for r in new} & shown
    assert more[-1]["type"] == "done" and more[-1]["total"] == len(new)


def test_load_more_keeps_the_users_price_range(tmp_path):
    s = search(tmp_path, point=True)
    first = list(s.run("iphone", *SYLHET, radius_km=40, min_price=50_000, max_price=80_000))
    more = list(s.more(first[-1]["search_id"]))
    assert all(50_000 <= r["price"] <= 80_000 for r in results(first) + results(more))


def test_load_more_stops_after_five(tmp_path):
    transport = ReplayTransport()
    s = search(tmp_path, transport, point=True)
    search_id = list(s.run("iphone", *SYLHET, radius_km=40))[-1]["search_id"]
    dones = [list(s.more(search_id))[-1] for _ in range(6)]
    assert [d["can_load_more"] for d in dones] == [True, True, True, True, False, False]
    assert len(transport.posts) == 1 + 5


def test_unknown_or_expired_search_id(tmp_path):
    clock = Clock()
    s = search(tmp_path, point=True)
    s._now = clock
    search_id = list(s.run("iphone", *SYLHET, radius_km=40))[-1]["search_id"]
    assert list(s.more("not-a-search")) == [{"type": "error", "error": "this search has expired; search again"}]
    clock.now += 31 * 60
    assert next(iter(s.more(search_id)))["type"] == "error"


def test_searches_table_is_bounded(tmp_path):
    s = search(tmp_path, point=True)
    for i in range(130):
        list(s.run(f"phone {i}", *SYLHET, radius_km=40))
    assert len(s._searches) <= 100


def test_new_price_range_fetches_again(tmp_path):
    transport = ReplayTransport()
    s = search(tmp_path, transport, point=True)
    list(s.run("iphone", *SYLHET, radius_km=40))
    list(s.run("iphone", *SYLHET, radius_km=40))
    list(s.run("iphone", *SYLHET, radius_km=40, min_price=50_000, max_price=80_000))
    assert len(transport.posts) == 2
