import http.client
import json
import threading

import pytest
from crawler.config import Config as CrawlerConfig
from replay import ReplayTransport
from test_live import NOW, SYLHET, photon

from fcommerce_search.config import Config
from fcommerce_search.live import LiveSearch
from fcommerce_search.places import Places
from fcommerce_search.server import make_server


@pytest.fixture
def server(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<h1>fcommerce-search</h1>")
    (tmp_path / "secret.txt").write_text("do not serve")
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=tmp_path, state_db=tmp_path / "s.sqlite3")
    places = Places(config.state_db, fetch=photon, clock=lambda: NOW, sleep=lambda s: None)
    live = LiveSearch(config, places, transport=ReplayTransport(), now=lambda: NOW, point_search=True)
    srv = make_server(0, live, places, web)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_port
    srv.shutdown()


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    if data is not None and "Content-Type" not in hdrs:
        hdrs["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=hdrs)
    response = conn.getresponse()
    return response.status, response.read().decode()


SEARCH = {"keyword": "iphone", "lat": SYLHET[0], "lon": SYLHET[1], "radius_km": 10, "days": 7}


def test_streams_ndjson(server):
    status, body = call(server, "POST", "/api/search", SEARCH, {"Origin": f"http://127.0.0.1:{server}"})
    events = [json.loads(line) for line in body.splitlines()]
    assert status == 200
    assert [e["type"] for e in events] == ["start", "city", "done"]
    assert events[1]["results"] and events[-1]["search_id"]


def test_places_and_reverse(server):
    status, body = call(server, "GET", "/api/places?q=chhatak")
    first = json.loads(body)[0]
    assert status == 200 and Places.slug(first["name"]) == "chhatak" and round(first["lat"], 2) == 25.04
    assert [Places.slug(p["name"]) for p in json.loads(body)].count("chhatak") == 1
    assert call(server, "GET", "/api/reverse?lat=91&lon=500")[0] == 400


@pytest.mark.parametrize("headers", [{"Host": "evil.example"}, {"Host": "attacker.test:8799"},
                                     {"Origin": "https://evil.example"}, {"Origin": "null"}])
def test_foreign_host_and_origin_refused(server, headers):
    status, _ = call(server, "POST", "/api/search", SEARCH, headers)
    assert status == 403
    assert call(server, "GET", "/api/places?q=london", headers=headers)[0] == 403


def test_text_plain_post_refused(server):
    status, _ = call(server, "POST", "/api/search", json.dumps(SEARCH).encode(), {"Content-Type": "text/plain"})
    assert status == 415


@pytest.mark.parametrize("bad", [
    {**SEARCH, "keyword": ""}, {**SEARCH, "keyword": "x" * 101}, {**SEARCH, "lat": 95}, {**SEARCH, "lon": "east"},
    {**SEARCH, "radius_km": 5}, {**SEARCH, "radius_km": 101}, {**SEARCH, "days": 10}, {**SEARCH, "min_price": -1},
    {**SEARCH, "extra": 1}, ["not", "an", "object"],
])
def test_oversized_or_bad_input_refused(server, bad):
    body = json.dumps(bad).encode()
    assert call(server, "POST", "/api/search", body)[0] == 400
    assert call(server, "POST", "/api/search", b"{" + b" " * 20_000 + b"}")[0] == 413


def test_static_files_stay_inside_the_web_folder(server):
    assert call(server, "GET", "/")[1] == "<h1>fcommerce-search</h1>"
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt"):
        status, body = call(server, "GET", path)
        assert "do not serve" not in body and status in (403, 404)


def test_more_streams_and_checks_input(server):
    origin = {"Origin": f"http://127.0.0.1:{server}"}
    _, body = call(server, "POST", "/api/search", SEARCH, origin)
    search_id = json.loads(body.splitlines()[-1])["search_id"]
    status, body = call(server, "POST", "/api/more", {"search_id": search_id}, origin)
    events = [json.loads(line) for line in body.splitlines()]
    assert status == 200 and [e["type"] for e in events] == ["city", "done"]
    for bad in ({"search_id": 5}, {"search_id": "x" * 100}, {"search_id": search_id, "extra": 1}, {}):
        assert call(server, "POST", "/api/more", bad)[0] == 400
    assert call(server, "POST", "/api/more", {"search_id": search_id}, {"Host": "evil.example"})[0] == 403
    text = call(server, "POST", "/api/more", json.dumps({"search_id": search_id}).encode(), {"Content-Type": "text/plain"})
    assert text[0] == 415


@pytest.mark.parametrize("length", ["abc", "-1"])
def test_bad_content_length_is_refused(server, length):
    import socket
    sock = socket.create_connection(("127.0.0.1", server), timeout=5)
    sock.sendall(f"POST /api/search HTTP/1.1\r\nHost: 127.0.0.1:{server}\r\nContent-Type: application/json\r\n"
                 f"Content-Length: {length}\r\n\r\n".encode())
    reply = sock.recv(200).decode()
    sock.close()
    assert reply.startswith(("HTTP/1.0 400", "HTTP/1.1 400"))
