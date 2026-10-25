import json
import os
import sqlite3
import threading
import time

import pytest
from crawler.config import Config as CrawlerConfig
from crawler.store import Store

from fcommerce_search import offers as offers_module
from fcommerce_search.config import Config
from fcommerce_search.offers import OffersError, build_offers, serialize

NOW = 1_792_900_000.0
BUYER = "Rahim Uddin Chowdhury"
PRIVATE_ID = "100089998887776"


def setup(tmp_path, data="data"):
    (tmp_path / data).mkdir(parents=True, exist_ok=True)
    store = Store(tmp_path / data, clock=lambda: NOW)
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=tmp_path / data,
                    state_db=tmp_path / "state.sqlite3")
    return store, config


def post(store, source, item_id, text, created_at=NOW - 3600, **extra):
    store.upsert_item(source.id, {"item_id": item_id, "text": text, "created_at": created_at,
                                  "permalink": f"https://www.facebook.com/shop/posts/{item_id}", **extra})


def shop(store, url="https://www.facebook.com/shop", name="Shop"):
    return store.add_source(url, "fb_page", "Sylhet", "phones", name=name)


def built(config):
    return [serialize(r) for r in build_offers(config, now=lambda: NOW)]


def state(config, sql):
    return sqlite3.connect(config.state_db).execute(sql).fetchall()


def test_repost_folds_with_post_count(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB UK, price 72k", NOW - 3 * 86400)
    post(store, s, "p2", "iphone 13pro 128gb uk 70k", NOW - 86400)
    [offer] = built(config)
    assert (offer["model"], offer["post_count"], offer["price"]) == ("iPhone 13 Pro", 2, 70000)
    assert [p["permalink"].rsplit("/", 1)[1] for p in offer["posts"]] == ["p2", "p1"]


def test_folded_posts_keep_their_prices(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB UK, price 75k", NOW - 3 * 86400)
    post(store, s, "p2", "iPhone 13 Pro 128GB UK, price 72k", NOW - 86400)
    [offer] = built(config)
    assert [(p["price"], p["price_source"]) for p in offer["posts"]] == [(72000, "post"), (75000, "post")]


def test_marketplace_listing_never_folds(tmp_path):
    store, config = setup(tmp_path)
    mp = store.add_source("marketplace:Sylhet", "fb_marketplace", "Sylhet", None)
    for item_id in ("m1", "m2"):
        store.upsert_item(mp.id, {"item_id": item_id, "title": "iPhone 13", "price": {"amount": 32000},
                                  "created_at": NOW - 3600, "permalink": f"https://www.facebook.com/marketplace/item/{item_id}"})
    offers = built(config)
    assert [(o["post_count"], o["seller"]) for o in offers] == [(1, "Marketplace seller"), (1, "Marketplace seller")]


def test_marketplace_listing_without_price_is_unknown(tmp_path):
    store, config = setup(tmp_path)
    mp = store.add_source("marketplace:Sylhet", "fb_marketplace", "Sylhet", None)
    store.upsert_item(mp.id, {"item_id": "m1", "title": "iPhone 13", "price": "unknown", "created_at": NOW - 60,
                              "permalink": "https://www.facebook.com/marketplace/item/m1"})
    [offer] = built(config)
    assert (offer["model"], offer["price"]) == ("iPhone 13", None)


def test_crawler_db_is_never_written(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB, 72k")
    store.upsert_comments(s.id, "p1", [{"comment_id": "c1", "item_id": "p1", "text": "price?", "is_owner": False}])
    store._db.close()
    db_file = tmp_path / "data" / "crawler.sqlite3"
    os.chmod(db_file, 0o444)
    try:
        built(config)
    finally:
        os.chmod(db_file, 0o644)
    tables = {r[0] for r in sqlite3.connect(db_file).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "offers" not in tables and "offer_posts" not in tables


def test_reading_does_not_wait_for_the_crawler_writer(tmp_path):
    store, config = setup(tmp_path)
    post(store, shop(store), "p1", "iPhone 13 Pro 128GB, 72k")
    crawler = sqlite3.connect(tmp_path / "data" / "crawler.sqlite3", isolation_level=None, check_same_thread=False)
    crawler.execute("BEGIN IMMEDIATE")
    threading.Timer(1.0, lambda: crawler.execute("COMMIT")).start()
    started = time.monotonic()
    [offer] = built(config)
    assert time.monotonic() - started < 0.8
    assert offer["price"] == 72000


def test_offer_has_no_buyer_or_seller_names(tmp_path):
    store, config = setup(tmp_path)
    group = store.add_source("https://www.facebook.com/groups/123", "fb_group", "Sylhet", "phones", name="Sylhet Phones")
    post(store, group, "g1", "iPhone 13 Pro 128GB, 72k", author_id=PRIVATE_ID, owner_id=PRIVATE_ID)
    store.upsert_comments(group.id, "g1", [
        {"comment_id": "c1", "item_id": "g1", "text": f"{BUYER} here, price?", "is_owner": False},
        {"comment_id": "c2", "item_id": "g1", "parent_id": "c1", "text": f"{BUYER} inbox please", "is_owner": True},
    ])
    payload = json.dumps(built(config), ensure_ascii=False)
    for planted in (BUYER, "Rahim", PRIVATE_ID):
        assert planted not in payload


def test_payload_carries_no_free_text(tmp_path):
    store, config = setup(tmp_path)
    post(store, shop(store), "p1", "Congrats Rahim Uddin on your new iPhone 15 Pro 256GB, 95,000 tk")
    mp = store.add_source("marketplace:Sylhet", "fb_marketplace", "Sylhet", None)
    store.upsert_item(mp.id, {"item_id": "m1", "title": "iPhone 13 Pro 128GB - Karim 01711000000",
                              "price": {"amount": 55000}, "created_at": NOW - 60,
                              "permalink": "https://www.facebook.com/marketplace/item/m1"})
    payload = json.dumps(built(config), ensure_ascii=False)
    for planted in ("Rahim", "Karim", "01711000000", "Congrats"):
        assert planted not in payload
    assert '"text"' not in payload


def test_payload_carries_no_seller_reply_text(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB, price inbox")
    store.upsert_comments(s.id, "p1", [
        {"comment_id": "c1", "item_id": "p1", "text": "price?", "is_owner": False, "created_at": NOW - 600},
        {"comment_id": "c2", "item_id": "p1", "text": "Karim bhai last price 72k", "is_owner": True, "created_at": NOW - 300},
    ])
    [offer] = built(config)
    assert (offer["price"], offer["price_source"], offer["seller_reply"]) == (72000, "seller_reply", "price")
    assert (offer["comment_count"], offer["last_activity_at"]) == (1, NOW - 300)
    assert "Karim" not in json.dumps(offer, ensure_ascii=False)


def test_photos_and_seller_answered(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB, 72k", images=["https://scontent.example/a.jpg"])
    post(store, s, "p2", "iPhone 15 128GB, 85k")
    store.upsert_comments(s.id, "p1", [
        {"comment_id": "c1", "item_id": "p1", "text": "Available, Zindabazar pickup", "is_owner": True}])
    by_model = {o["model"]: o for o in built(config)}
    assert (by_model["iPhone 13 Pro"]["photos"], by_model["iPhone 13 Pro"]["has_photos"]) == (
        ["https://scontent.example/a.jpg"], True)
    assert (by_model["iPhone 13 Pro"]["seller_answered"], by_model["iPhone 13 Pro"]["seller_reply"]) == (True, None)
    assert (by_model["iPhone 15"]["has_photos"], by_model["iPhone 15"]["seller_answered"]) == (False, False)


def test_page_without_a_name_shows_its_address_name(tmp_path):
    store, config = setup(tmp_path)
    post(store, store.add_source("https://www.facebook.com/iStoreSyl", "fb_page", "Sylhet", "phones"), "p1",
         "iPhone 13 Pro 128GB, 72k")
    [offer] = built(config)
    assert (offer["seller"], offer["source_name"]) == ("iStoreSyl", "iStoreSyl")


def test_two_rows_with_same_facts_in_one_post_are_two_offers(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 16 Pro\n256GB dual sim ---- 1,65,000tk\n256GB esim ---- 1,55,000tk")
    store.upsert_comments(s.id, "p1", [{"comment_id": "c1", "item_id": "p1", "text": "price?", "is_owner": False}])
    offers = built(config)
    assert sorted(o["price"] for o in offers) == [155000, 165000]
    assert [(o["post_count"], o["comment_count"]) for o in offers] == [(1, 1), (1, 1)]


def test_only_phone_and_marketplace_sources_are_read(tmp_path):
    store, config = setup(tmp_path)
    clothes = store.add_source("https://www.facebook.com/dresses", "fb_page", "Sylhet", "clothes", name="Dresses")
    post(store, clothes, "c1", "iPhone 15 cover 450tk and Galaxy S24 back case")
    post(store, shop(store), "p1", "iPhone 13 Pro 128GB, 72k")
    assert [(o["model"], o["category"]) for o in built(config)] == [("iPhone 13 Pro", "phones")]


def test_empty_store_does_not_expire_live_offers(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB, 72k")
    build_offers(config, now=lambda: NOW)
    store.remove_source(s.url)
    with pytest.raises(OffersError, match="--allow-empty"):
        build_offers(config, now=lambda: NOW)
    assert state(config, "SELECT count(*) FROM offers WHERE expired = 0") == [(1,)]
    build_offers(config, now=lambda: NOW, allow_empty=True)
    assert state(config, "SELECT count(*) FROM offers WHERE expired = 0") == [(0,)]


def test_missing_crawler_database_is_an_error(tmp_path):
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=tmp_path / "nowhere",
                    state_db=tmp_path / "state.sqlite3")
    with pytest.raises(OffersError, match="crawler.sqlite3"):
        build_offers(config, now=lambda: NOW)
    assert not (tmp_path / "nowhere").exists()


def test_offer_id_survives_post_edit(tmp_path):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "p1", "iPhone 13 Pro 128GB, 72k")
    [first] = built(config)
    post(store, s, "p1", "iPhone 13 Pro 128GB UK with box, 70k")
    [edited] = built(config)
    assert edited["offer_id"] == first["offer_id"]
    assert (edited["region"], edited["box"], edited["price"]) == ("UK", "with box", 70000)


def test_offer_id_survives_crawler_data_dir_switch(tmp_path):
    old, config = setup(tmp_path, "data")
    post(old, shop(old, url="https://www.facebook.com/other"), "o1", "iPhone 15 128GB, 85k")
    post(old, shop(old), "p1", "iPhone 13 Pro 128GB, 72k")
    first = {o["model"]: o["offer_id"] for o in built(config)}
    new, _ = setup(tmp_path, "data-run2")
    post(new, shop(new), "p1", "iPhone 13 Pro 128GB, 72k")
    post(new, shop(new, url="https://www.facebook.com/other"), "o1", "iPhone 15 128GB, 85k")
    switched = Config(crawler=config.crawler, crawler_data_dir=tmp_path / "data-run2", state_db=config.state_db)
    assert {o["model"]: o["offer_id"] for o in built(switched)} == first


def test_reused_source_row_id_gets_a_new_offer_id(tmp_path):
    store, config = setup(tmp_path)
    keep = shop(store, url="https://www.facebook.com/keep", name="keep")
    post(store, keep, "k1", "iPhone 15 128GB, 85k")
    b = shop(store, url="https://www.facebook.com/shopb", name="shopb")
    post(store, b, "p1", "iPhone 13 Pro 128GB, 72k")
    old = {o["source_name"]: o["offer_id"] for o in built(config)}
    store.remove_source(b.url)
    c = shop(store, url="https://www.facebook.com/shopc", name="shopc")
    assert c.id == b.id
    post(store, c, "p1", "iPhone 13 Pro 128GB, 72k")
    new = {o["source_name"]: o["offer_id"] for o in built(config)}
    assert new["shopc"] != old["shopb"] and new["keep"] == old["keep"]


def test_offer_id_reveals_no_poster_id(tmp_path):
    a, config_a = setup(tmp_path / "a")
    b, config_b = setup(tmp_path / "b")
    for store in (a, b):
        group = store.add_source("https://www.facebook.com/groups/123", "fb_group", "Sylhet", "phones", name="G")
        post(store, group, "g1", "iPhone 13 Pro 128GB, 72k", author_id=PRIVATE_ID)
    [offer_a], [offer_b] = built(config_a), built(config_b)
    assert offer_a["offer_id"] != offer_b["offer_id"]


def test_only_live_posts_comments_are_read(tmp_path, monkeypatch):
    store, config = setup(tmp_path)
    s = shop(store)
    post(store, s, "old", "iPhone 12 128GB, 40k", created_at=NOW - 90 * 86400)
    post(store, s, "p1", "iPhone 13 Pro 128GB, 72k")
    store.upsert_comments(s.id, "old", [{"comment_id": f"o{i}", "item_id": "old", "text": "x", "is_owner": False}
                                        for i in range(50)])
    rows = []
    real_open = offers_module._open_crawler

    def counting_open(path):
        db = real_open(path)
        return _Counting(db, rows)

    monkeypatch.setattr(offers_module, "_open_crawler", counting_open)
    built(config)
    assert len(rows) <= 3


class _Counting:
    def __init__(self, db, rows):
        self._db, self._rows = db, rows

    def execute(self, sql, params=()):
        result = self._db.execute(sql, params).fetchall()
        if "comments" in sql:
            self._rows.extend(result)
        return result

    def close(self):
        self._db.close()


def test_capitalised_phones_category_is_read(tmp_path):
    store, config = setup(tmp_path)
    post(store, store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "Phones", name="Shop"), "p1",
         "iPhone 13 Pro 128GB, 72k")
    assert [o["model"] for o in built(config)] == ["iPhone 13 Pro"]


def test_two_groups_never_share_an_offer_id(tmp_path):
    store, config = setup(tmp_path)
    group = store.add_source("https://www.facebook.com/groups/123", "fb_group", "Sylhet", "phones", name="G")
    post(store, group, "g1", "iPhone 13 Pro 128GB, 72k", NOW - 7200, author_id=PRIVATE_ID)
    post(store, group, "g2", "iPhone 13 Pro 128GB, 71k", NOW - 3600, author_id=PRIVATE_ID)
    [folded] = built(config)
    post(store, group, "g2", "iPhone 13 Pro 128GB with box, 71k", NOW - 3600, author_id=PRIVATE_ID)
    ids = [o["offer_id"] for o in built(config)]
    assert len(ids) == 2 and len(set(ids)) == 2 and folded["offer_id"] in ids
