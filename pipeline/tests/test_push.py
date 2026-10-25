import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from crawler.config import Config as CrawlerConfig
from crawler.store import Store

from fcommerce_search.config import Config
from fcommerce_search.offers import build_offers
from fcommerce_search.push import PushError, push_offers

NOW = 1_792_900_000.0
SECRET = "s3cret-push-key-do-not-print"


@pytest.fixture
def site():
    calls, replies = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "secret": self.headers.get("X-Ingest-Secret"), "body": body})
            status, payload = replies.pop(0) if replies else (200, {"ok": True})
            self.send_response(status)
            if "location" in payload:
                self.send_header("Location", payload["location"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", calls, replies
    server.shutdown()


def setup(tmp_path, url, secret=SECRET):
    store = Store(tmp_path, clock=lambda: NOW)
    secret_file = tmp_path / "push.env"
    if secret is not None:
        secret_file.write_text(f"FCS_PUSH_SECRET={secret}\n")
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=tmp_path,
                    state_db=tmp_path / "state.sqlite3", site_url=url, secret_file=secret_file)
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    return store, shop, config


def post(store, shop, item_id, text):
    store.upsert_item(shop.id, {"item_id": item_id, "text": text, "created_at": NOW - 3600,
                                "permalink": f"https://www.facebook.com/shop/posts/{item_id}"})


def sent(calls):
    return [o["model"] for c in calls for o in c["body"]["upsert"]], [i for c in calls for i in c["body"]["expire"]]


def test_only_changed_offers_sent(tmp_path, site):
    url, calls, _ = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    post(store, shop, "p2", "iPhone 15 128GB, 85k")
    build_offers(config, now=lambda: NOW)
    assert push_offers(config).sent == 2
    assert (calls[0]["path"], calls[0]["secret"]) == ("/api/ingest", SECRET)

    build_offers(config, now=lambda: NOW)
    assert push_offers(config).sent == 0

    post(store, shop, "p2", "iPhone 15 128GB, 83k")
    build_offers(config, now=lambda: NOW)
    calls.clear()
    assert push_offers(config).sent == 1
    assert sent(calls) == (["iPhone 15"], [])


def keep_another_shop(store):
    other = store.add_source("https://www.facebook.com/other", "fb_page", "Sylhet", "phones", name="Other")
    post(store, other, "o1", "iPhone 15 128GB, 85k")


def test_vanished_offer_is_sent_as_expired(tmp_path, site):
    url, calls, _ = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    [record] = build_offers(config, now=lambda: NOW)
    push_offers(config)
    keep_another_shop(store)
    store.remove_source(shop.url)
    build_offers(config, now=lambda: NOW)
    calls.clear()
    assert push_offers(config).expired == 1
    assert sent(calls) == (["iPhone 15"], [record.offer_id])
    calls.clear()
    assert (push_offers(config).expired, calls) == (0, [])


@pytest.mark.parametrize("reply", [(500, {"error": "boom"}), (200, {"ok": False, "error": "bad batch"})])
def test_failed_push_keeps_offers_pending(tmp_path, site, reply):
    url, calls, replies = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    replies.append(reply)
    with pytest.raises(PushError):
        push_offers(config)
    assert push_offers(config).sent == 1
    assert sent(calls) == (["iPhone 13 Pro", "iPhone 13 Pro"], [])


def test_secret_never_logged(tmp_path, site, caplog, capsys):
    url, _, replies = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    caplog.set_level(logging.DEBUG)
    replies.append((403, {"error": f"wrong secret {SECRET}"}))
    with pytest.raises(PushError) as failed:
        push_offers(config)
    push_offers(config)
    out = capsys.readouterr()
    for text in (str(failed.value), caplog.text, out.out, out.err):
        assert SECRET not in text


@pytest.mark.parametrize("secret", [None, ""])
def test_missing_secret_refuses_to_push(tmp_path, site, secret):
    url, calls, _ = site
    store, shop, config = setup(tmp_path, url, secret=secret)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    with pytest.raises(PushError, match="secret"):
        push_offers(config)
    assert calls == []


def test_offer_that_vanishes_before_its_first_push_is_not_sent(tmp_path, site):
    url, calls, _ = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    keep_another_shop(store)
    store.remove_source(shop.url)
    build_offers(config, now=lambda: NOW)
    assert push_offers(config).expired == 0
    assert sent(calls) == (["iPhone 15"], [])


def test_dropped_connection_is_a_push_error(tmp_path):
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def accept_and_drop():
        conn, _ = listener.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{\"ok\"")
        conn.close()

    threading.Thread(target=accept_and_drop, daemon=True).start()
    store, shop, config = setup(tmp_path, f"http://127.0.0.1:{listener.getsockname()[1]}")
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    with pytest.raises(PushError):
        push_offers(config)


def test_redirect_is_refused_and_the_secret_stays_home(tmp_path, site):
    url, calls, replies = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    replies.append((302, {"location": url.replace("127.0.0.1", "localhost") + "/elsewhere"}))
    with pytest.raises(PushError, match="redirect"):
        push_offers(config)
    assert len(calls) == 1
    assert push_offers(config).sent == 1


def test_html_answer_is_reported_as_wrong_site(tmp_path):
    class Html(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>not the api</html>")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Html)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    store, shop, config = setup(tmp_path, f"http://127.0.0.1:{server.server_port}")
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    with pytest.raises(PushError, match="not with JSON"):
        push_offers(config)
    server.shutdown()


def test_unreadable_secret_file_says_why(tmp_path, site):
    url, calls, _ = site
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    config.secret_file.chmod(0)
    try:
        with pytest.raises(PushError, match="cannot read"):
            push_offers(config)
    finally:
        config.secret_file.chmod(0o600)
    assert calls == []


def test_partial_failure_reports_what_was_sent(tmp_path, site, monkeypatch):
    url, _, replies = site
    monkeypatch.setattr("fcommerce_search.push.BATCH", 1)
    store, shop, config = setup(tmp_path, url)
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k")
    post(store, shop, "p2", "iPhone 15 128GB, 85k")
    build_offers(config, now=lambda: NOW)
    replies.extend([(200, {"ok": True}), (500, {})])
    with pytest.raises(PushError, match="after sending 1"):
        push_offers(config)
