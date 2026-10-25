"""Run the real live-search server for browser tests: saved Marketplace pages, canned place look-ups."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from crawler.config import Config as CrawlerConfig
from replay import ReplayTransport

from fcommerce_search.config import Config
from fcommerce_search.live import LiveSearch
from fcommerce_search.places import Places
from fcommerce_search.server import make_server

NOW = 1792924206 + 3600
PLACES = {
    "sylhet": ("Sylhet", "Bangladesh", "BD", 91.8687, 24.8949), "london": ("London", "United Kingdom", "GB", -0.1276, 51.5073),
    "dhaka": ("Dhaka", "Bangladesh", "BD", 90.4074, 23.7104), "paris": ("Paris", "France", "FR", 2.3522, 48.8566),
    "maulvi bazar": ("Maulvi Bazar", "Bangladesh", "BD", 91.7774, 24.4829),
    "chhatak": ("Chhatak", "Bangladesh", "BD", 91.6694, 25.0381),
    "lalabazar": ("Lalabazar", "Bangladesh", "BD", 91.83, 24.86),
    "shaistaganj": ("Shaistaganj", "Bangladesh", "BD", 91.45, 24.2833),
}


def photon(url: str) -> str:
    if "/reverse" in url:
        name, country, code, lon, lat = PLACES["sylhet"]
        return json.dumps({"features": [{"properties": {"name": name, "country": country, "countrycode": code},
                                         "geometry": {"coordinates": [lon, lat]}}]})
    q = url.split("q=")[1].split("&")[0].replace("+", " ").replace("%2C", " ").lower()
    hits = [v for k, v in PLACES.items() if k.startswith(q[:4])]
    return json.dumps({"features": [{"properties": {"name": n, "country": c, "countrycode": cc},
                                     "geometry": {"coordinates": [lon, lat]}} for n, c, cc, lon, lat in hits]})


if __name__ == "__main__":
    port, web_dir = int(sys.argv[1]), Path(sys.argv[2])
    basic = len(sys.argv) > 3 and sys.argv[3] == "basic"
    state = Path(tempfile.mkdtemp()) / "state.sqlite3"
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=state.parent, state_db=state)
    places = Places(state, fetch=photon, clock=lambda: NOW, sleep=lambda s: None)
    transport = ReplayTransport(graphql_status=500 if basic else 200)
    server = make_server(port, LiveSearch(config, places, transport=transport, now=lambda: NOW), places, web_dir)
    print(f"replay server on {server.server_port}", flush=True)
    server.serve_forever()
