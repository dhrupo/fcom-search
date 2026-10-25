import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .live import LiveSearch
from .places import Places

MAX_BODY = 10_000
DAYS = {3, 7, 15, 30, 45, 60}
FIELDS = {"keyword", "lat", "lon", "radius_km", "min_price", "max_price", "days"}


class BadRequest(ValueError):
    pass


def _number(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise BadRequest(name)
    return value


def parse_search(body: object) -> dict:
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise BadRequest("body")
    keyword = body.get("keyword")
    if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 100:
        raise BadRequest("keyword")
    out = {"keyword": keyword.strip(), "lat": _number(body.get("lat"), -90, 90, "lat"),
           "lon": _number(body.get("lon"), -180, 180, "lon"), "radius_km": _number(body.get("radius_km"), 10, 100, "radius")}
    for key in ("min_price", "max_price"):
        out[key] = None if body.get(key) is None else _number(body[key], 0, 1e12, key)
    if body.get("days") is not None and body["days"] not in DAYS:
        raise BadRequest("days")
    out["days"] = body.get("days")
    return out


def parse_more(body: object) -> str:
    if not isinstance(body, dict) or set(body) != {"search_id"}:
        raise BadRequest("body")
    search_id = body["search_id"]
    if not isinstance(search_id, str) or not re.fullmatch(r"[0-9a-f]{32}", search_id):
        raise BadRequest("search_id")
    return search_id


def make_server(port: int, live: LiveSearch, places: Places, web_dir: Path, host: str = "127.0.0.1"):
    root = Path(web_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _allowed(self) -> bool:
            # A page on any site can make the browser call 127.0.0.1, and DNS rebinding can fake the name:
            # only our own Host and Origin get in.
            here = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            return self.headers.get("Host") in here and (origin is None or origin in {f"http://{h}" for h in here})

        def _json(self, status: int, payload) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._allowed():
                return self._json(403, {"error": "forbidden"})
            url = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/places":
                return self._json(200, places.suggest(query.get("q", "")))
            if url.path == "/api/reverse":
                try:
                    lat, lon = _number(float(query["lat"]), -90, 90, "lat"), _number(float(query["lon"]), -180, 180, "lon")
                except (KeyError, ValueError):
                    return self._json(400, {"error": "bad request"})
                return self._json(200, places.reverse(lat, lon))
            self._static(unquote(url.path))

        def _static(self, path: str) -> None:
            target = (root / path.lstrip("/")).resolve()
            if target.is_dir():
                target = target / "index.html"
            if not target.is_relative_to(root):
                return self._json(403, {"error": "forbidden"})
            if not target.is_file():
                target = root / "index.html"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if not self._allowed():
                return self._json(403, {"error": "forbidden"})
            path = urlsplit(self.path).path
            if path not in ("/api/search", "/api/more"):
                return self._json(404, {"error": "not found"})
            if self.headers.get_content_type() != "application/json":
                return self._json(415, {"error": "send JSON"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0:
                    raise ValueError
            except ValueError:
                return self._json(400, {"error": "bad request"})
            if length > MAX_BODY:
                return self._json(413, {"error": "too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"null")
                if path == "/api/more":
                    stream = live.more(parse_more(body))
                else:
                    stream = live.run(**parse_search(body))
            except (ValueError, BadRequest):
                return self._json(400, {"error": "bad request"})
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for event in stream:
                    self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                stream.close()  # the page went away: queued cities are not fetched

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
