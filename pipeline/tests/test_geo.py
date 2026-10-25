import pytest

from fcommerce_search.geo import big_cities_near, distance_km, find_town

SYLHET = (24.8949, 91.8687)


def test_distance_km():
    assert distance_km(51.5072, -0.1276, 48.8566, 2.3522) == pytest.approx(344, abs=5)
    assert distance_km(*SYLHET, *SYLHET) == 0


def test_big_cities_within_radius_nearest_first():
    names = [c.name for c in big_cities_near(*SYLHET, radius_km=100)]
    assert names[0] == "Sylhet"
    assert "Dhaka" not in names
    assert all(c.population >= 100_000 for c in big_cities_near(*SYLHET, radius_km=100))
    assert "Dhaka" in [c.name for c in big_cities_near(*SYLHET, radius_km=250, limit=50)]
    assert len(big_cities_near(*SYLHET, radius_km=250)) == 8


def test_town_name_resolves_with_country_hint():
    london = find_town("London")
    assert (london.country, round(london.lat)) == ("GB", 52)
    assert find_town("London", country="CA").country == "CA"
    assert find_town("Nowhereville Zzz") is None


def test_parts_of_a_bigger_city_are_skipped():
    assert [c.name for c in big_cities_near(23.7104, 90.4074, radius_km=10)] == ["Dhaka"]
    assert [c.name for c in big_cities_near(51.5073, -0.1276, radius_km=10)] == ["London"]
    near_sylhet = [c.name for c in big_cities_near(*SYLHET, radius_km=100)]
    assert near_sylhet[0] == "Sylhet" and "Shillong" in near_sylhet
