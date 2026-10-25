import json

from fcommerce_search.places import Places

PHOTON = {"features": [
    {"properties": {"name": "London", "country": "United Kingdom", "countrycode": "GB", "osm_value": "city"},
     "geometry": {"coordinates": [-0.1276, 51.5073]}},
    {"properties": {"name": "London", "country": "Canada", "countrycode": "CA", "osm_value": "city"},
     "geometry": {"coordinates": [-81.2453, 42.9849]}},
]}


class FakeFetch:
    def __init__(self, body=PHOTON):
        self.urls, self.body = [], body

    def __call__(self, url):
        self.urls.append(url)
        return json.dumps(self.body)


class FakeClock:
    def __init__(self):
        self.now, self.slept = 1000.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def places(tmp_path, fetch=None, clock=None):
    clock = clock or FakeClock()
    return Places(tmp_path / "state.sqlite3", fetch=fetch or FakeFetch(), clock=clock.time, sleep=clock.sleep)


def test_suggestions_are_cached_and_rate_limited(tmp_path):
    fetch, clock = FakeFetch(), FakeClock()
    p = places(tmp_path, fetch, clock)
    first = p.suggest("londo")
    named = [(s["name"], s["country"], s["countrycode"]) for s in first]
    assert named[0] == ("London", "United Kingdom", "GB") and ("London", "Canada", "CA") in named
    assert p.suggest("LONDO ") == first and len(fetch.urls) == 1
    p.suggest("paris")
    assert len(fetch.urls) == 2 and clock.slept and clock.slept[-1] >= 0.99
    assert "q=paris" in fetch.urls[-1] and fetch.urls[-1].startswith("https://photon.komoot.io/api/")


def test_long_or_empty_query_is_not_sent(tmp_path):
    fetch = FakeFetch()
    p = places(tmp_path, fetch)
    assert p.suggest("") == [] and p.suggest("x") == []
    p.suggest("a" * 500)
    assert all(len(u) < 400 for u in fetch.urls)


def test_bad_photon_answer_is_an_empty_list(tmp_path):
    bundled_only = places(tmp_path / "ok", FakeFetch(body={"features": []})).suggest("london")
    for n, body in enumerate(({"unexpected": True}, ["a", "list"], {"features": "nope"})):
        assert places(tmp_path / str(n), FakeFetch(body=body)).suggest("london") == bundled_only


def test_slug_that_redirects_is_remembered_as_bad(tmp_path):
    p = places(tmp_path)
    assert p.slug_status("Maulvi Bazar", "BD") is None
    assert p.slug("Kuala Lumpur") == "kualalumpur" and p.slug("Brāhmanbāria") == "brahmanbaria"
    p.remember_slug("Maulvi Bazar", "BD", works=False)
    p.remember_slug("Sylhet", "BD", works=True)
    again = places(tmp_path)
    assert (again.slug_status("Maulvi Bazar", "BD"), again.slug_status("Sylhet", "BD")) == (False, True)


def test_small_town_uses_nearest_big_city(tmp_path):
    p = places(tmp_path)
    moulvibazar = (24.4829, 91.7774)
    assert [c.name for c in p.cities_to_search(*moulvibazar, radius_km=10)] == ["Sylhet"]
    near_sylhet = [c.name for c in p.cities_to_search(24.8949, 91.8687, radius_km=100)]
    assert near_sylhet[0] == "Sylhet" and len(near_sylhet) <= 8


def test_cities_with_a_bad_slug_are_skipped(tmp_path):
    p = places(tmp_path)
    p.remember_slug("Sylhet", "BD", works=False)
    assert "Sylhet" not in [c.name for c in p.cities_to_search(24.8949, 91.8687, radius_km=100)]


def test_suggestions_rank_big_cities_first_and_add_bundled_matches(tmp_path):
    photon_answer = {"features": [
        {"properties": {"name": "Chittaguppa", "country": "India", "countrycode": "IN"}, "geometry": {"coordinates": [77.1, 17.8]}},
        {"properties": {"name": "Chattogram", "country": "Bangladesh", "countrycode": "BD"}, "geometry": {"coordinates": [91.8, 22.3]}},
    ]}
    fetch = FakeFetch(photon_answer)
    p = places(tmp_path, fetch)
    assert [s["name"] for s in p.suggest("chittag")][:2] == ["Chattogram", "Chittaguppa"]
    assert "osm_tag=place%3Acity" in fetch.urls[0] and "osm_tag=place%3Atown" in fetch.urls[0]

    kuala = places(tmp_path / "k", FakeFetch({"features": []})).suggest("kuala")
    assert (kuala[0]["name"], kuala[0]["country"], kuala[0]["countrycode"]) == ("Kuala Lumpur", "Malaysia", "MY")
