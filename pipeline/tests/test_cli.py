import sqlite3

from crawler.store import Store

from fcommerce_search.cli import main


class Pushed:
    sent, expired = 1, 0


def test_push_command_builds_offers_first(tmp_path, monkeypatch):
    pushed = []
    monkeypatch.setattr("fcommerce_search.cli.push_offers", lambda config: pushed.append(config) or Pushed())
    (tmp_path / "crawler").mkdir()
    (tmp_path / "crawler" / "crawler.toml").write_text('places = ["Sylhet"]\ndata_dir = "data"\n')
    store = Store(tmp_path / "crawler" / "data")
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    store.upsert_item(shop.id, {"item_id": "p1", "text": "iPhone 13 Pro 128GB, 72k", "created_at": None,
                                "permalink": "https://www.facebook.com/shop/posts/p1"})
    config = tmp_path / "fcommerce-search.toml"
    config.write_text('crawler_config = "crawler/crawler.toml"\n[site]\nurl = "http://127.0.0.1:1"\n')

    assert main(["--config", str(config), "push"]) == 0
    db = sqlite3.connect(tmp_path / "crawler" / "data" / "crawler.sqlite3")
    assert db.execute("SELECT count(*) FROM offers").fetchone() == (1,)
    assert len(pushed) == 1


def test_locked_database_prints_an_error_not_a_traceback(tmp_path, monkeypatch, capsys):
    def locked(config):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("fcommerce_search.cli.build_offers", locked)
    (tmp_path / "crawler.toml").write_text('places = ["Sylhet"]\n')
    config = tmp_path / "fcommerce-search.toml"
    config.write_text('crawler_config = "crawler.toml"\n')
    assert main(["--config", str(config), "push"]) == 1
    assert "database is locked" in capsys.readouterr().err
