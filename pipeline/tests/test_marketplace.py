import json

from crawler.facebook.marketplace import parse_listing
from crawler.facebook.parse import walk
from replay import FIXTURES, ReplayTransport

from fcommerce_search.marketplace import PointSearch

SYLHET = (24.8949, 91.8687)


def listings(path):
    doc = json.loads((FIXTURES / path).read_text(encoding="utf-8"))
    return [parse_listing(d) for d in walk(doc) if isinstance(d, dict) and d.get("__typename") == "GroupCommerceProductItem"]


class Clock:
    def __init__(self):
        self.now = 1_792_927_806.0

    def __call__(self):
        return self.now


def test_reduced_graphql_fixture_parses():
    point, band = listings("graphql_sylhet_point.json"), listings("graphql_sylhet_band_50k_80k.json")
    assert (len(point), len(band)) == (24, 24)
    assert all(50_000 <= l["price"]["amount"] <= 80_000 for l in band)
    in_band = {l["item_id"] for l in point if 50_000 <= l["price"]["amount"] <= 80_000}
    assert {l["item_id"] for l in point} & {l["item_id"] for l in band} == in_band
    assert len({l["item_id"] for l in band} - {l["item_id"] for l in point}) == 18


def test_bootstrap_reads_request_id_token_and_template():
    ps = PointSearch(ReplayTransport())
    ps.bootstrap()
    assert (ps.doc_id, ps.lsd) == ("28792298773764420", "test-lsd-token")
    assert "filter_radius_km" in ps.template["params"]["browse_request_params"]


def test_point_search_sends_coordinates_radius_and_band():
    transport = ReplayTransport()
    status, found, full = PointSearch(transport).search("iphone", *SYLHET, radius_km=40, band=(50_000, 80_000))
    sent = transport.posts[-1]
    variables = json.loads(sent["form"]["variables"])
    browse = variables["params"]["browse_request_params"]
    assert variables["params"]["bqf"]["query"] == "iphone"
    assert (browse["filter_location_latitude"], browse["filter_location_longitude"], browse["filter_radius_km"]) == (*SYLHET, 40)
    assert variables["buyLocation"] == {"latitude": SYLHET[0], "longitude": SYLHET[1]}
    assert (browse["filter_price_lower_bound"], browse["filter_price_upper_bound"]) == (5_000_000, 8_000_000)
    assert sent["form"]["lsd"] == "test-lsd-token" and sent["headers"]["x-fb-lsd"] == "test-lsd-token"
    assert (status, len(found), full) == ("ok", 24, True)


def test_open_ended_band_uses_facebooks_maximum():
    transport = ReplayTransport()
    PointSearch(transport).search("iphone", *SYLHET, radius_km=40, band=(1000, None))
    browse = json.loads(transport.posts[-1]["form"]["variables"])["params"]["browse_request_params"]
    assert (browse["filter_price_lower_bound"], browse["filter_price_upper_bound"]) == (100_000, 214748364700)


def test_session_reused_then_renewed():
    transport, clock = ReplayTransport(), Clock()
    ps = PointSearch(transport, clock=clock)
    ps.search("iphone", *SYLHET, radius_km=40)
    ps.search("phone", *SYLHET, radius_km=40)
    assert len(transport.urls) == 1
    clock.now += 31 * 60
    ps.search("phone", *SYLHET, radius_km=40)
    assert len(transport.urls) == 2


def test_logged_out_form_only():
    transport = ReplayTransport()
    PointSearch(transport).search("iphone", *SYLHET, radius_km=40)
    form = transport.posts[-1]["form"]
    assert (form["av"], form["__user"], form["fb_dtsg"]) == ("0", "0", "")


def test_schema_change_is_reported():
    for body, status in (('{"data": {}}', "schema_changed"), ('{"errors":[{"code":1675004}]}', "blocked")):
        assert PointSearch(ReplayTransport(graphql_body=body)).search("iphone", *SYLHET, radius_km=40)[0] == status
    assert PointSearch(ReplayTransport(graphql_status=429)).search("iphone", *SYLHET, radius_km=40)[0] == "blocked"


def test_bootstrap_page_without_request_id_is_unavailable():
    transport = ReplayTransport(status={"london": 200})
    assert PointSearch(transport).search("iphone", *SYLHET, radius_km=40)[0] == "unavailable"
