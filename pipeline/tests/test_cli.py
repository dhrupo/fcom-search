import fcntl
import sqlite3

from crawler.store import Store

from fcommerce_search.cli import main


class Pushed:
    sent, expired = 1, 0


def make_config(tmp_path, posts=True):
    (tmp_path / "crawler").mkdir()
    (tmp_path / "crawler" / "crawler.toml").write_text('places = ["Sylhet"]\ndata_dir = "data"\n')
    store = Store(tmp_path / "crawler" / "data")
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    if posts:
        store.upsert_item(shop.id, {"item_id": "p1", "text": "iPhone 13 Pro 128GB, 72k", "created_at": None,
                                    "permalink": "https://www.facebook.com/shop/posts/p1"})
    config = tmp_path / "fcommerce-search.toml"
    config.write_text('crawler_config = "crawler/crawler.toml"\n[site]\nurl = "http://127.0.0.1:1"\n')
    return config


def test_push_command_builds_offers_first(tmp_path, monkeypatch):
    pushed = []
    monkeypatch.setattr("fcommerce_search.cli.push_offers", lambda config: pushed.append(config) or Pushed())
    config = make_config(tmp_path)
    assert main(["--config", str(config), "push"]) == 0
    db = sqlite3.connect(tmp_path / "data" / "fcommerce-search.sqlite3")
    assert db.execute("SELECT count(*) FROM offers").fetchone() == (1,)
    assert len(pushed) == 1


def test_locked_database_prints_an_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    def locked(config, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("fcommerce_search.cli.build_offers", locked)
    config = make_config(tmp_path)
    assert main(["--config", str(config), "push"]) == 1
    assert "database is locked" in capsys.readouterr().err


def test_second_push_while_one_runs_is_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("fcommerce_search.cli.push_offers", lambda config: Pushed())
    config = make_config(tmp_path)
    assert main(["--config", str(config), "push"]) == 0
    with open(tmp_path / "data" / "fcommerce-search.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(["--config", str(config), "push"]) == 1
    assert "another push is running" in capsys.readouterr().err
    assert main(["--config", str(config), "push"]) == 0


def test_allow_empty_flag_reaches_build(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr("fcommerce_search.cli.build_offers", lambda config, allow_empty: seen.append(allow_empty) or [])
    monkeypatch.setattr("fcommerce_search.cli.push_offers", lambda config: Pushed())
    config = make_config(tmp_path, posts=False)
    assert main(["--config", str(config), "push", "--allow-empty"]) == 0
    assert main(["--config", str(config), "push"]) == 0
    assert seen == [True, False]


def test_serve_command_starts_the_local_server(tmp_path, monkeypatch):
    started = {}

    class FakeServer:
        server_port = 8799

        def serve_forever(self):
            started["serving"] = True

    def fake_make_server(port, live, places, web_dir, host="127.0.0.1"):
        started.update(port=port, web_dir=web_dir, names=live._names)
        return FakeServer()

    monkeypatch.setattr("fcommerce_search.cli.make_server", fake_make_server)
    names = tmp_path / "names.txt"
    names.write_text("Rahim Uddin\n\n")
    monkeypatch.setenv("FCS_SCRUB_MAP", str(names))
    config = make_config(tmp_path)
    web = tmp_path / "web"
    web.mkdir()
    assert main(["--config", str(config), "serve", "--port", "8799", "--web-dir", str(web)]) == 0
    assert started == {"port": 8799, "web_dir": web, "names": ("Rahim Uddin",), "serving": True}
