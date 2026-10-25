import json
import sqlite3
import threading
import time

from crawler.config import Config as CrawlerConfig
from crawler.store import Store

from fcommerce_search.config import Config
from fcommerce_search.offers import build_offers, serialize

NOW = 1_792_900_000.0
BUYER = "Rahim Uddin Chowdhury"
PRIVATE_ID = "100089998887776"


def setup(tmp_path):
    store = Store(tmp_path, clock=lambda: NOW)
    config = Config(crawler=CrawlerConfig(places=("Sylhet",)), crawler_data_dir=tmp_path)
    return store, config


def post(store, source, item_id, text, created_at, **extra):
    store.upsert_item(source.id, {"item_id": item_id, "text": text, "created_at": created_at,
                                  "permalink": f"https://www.facebook.com/shop/posts/{item_id}", **extra})


def test_repost_folds_with_post_count(tmp_path):
    store, config = setup(tmp_path)
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    post(store, shop, "p1", "iPhone 13 Pro 128GB UK, price 72k", NOW - 3 * 86400)
    post(store, shop, "p2", "iphone 13pro 128gb uk 70k", NOW - 86400)
    [offer] = [serialize(r) for r in build_offers(config, now=lambda: NOW)]
    assert (offer["model"], offer["post_count"], offer["price"]) == ("iPhone 13 Pro", 2, 70000)
    assert [p["permalink"].rsplit("/", 1)[1] for p in offer["posts"]] == ["p2", "p1"]


def test_marketplace_listing_never_folds(tmp_path):
    store, config = setup(tmp_path)
    mp = store.add_source("marketplace:Sylhet", "fb_marketplace", "Sylhet", None)
    for item_id in ("m1", "m2"):
        store.upsert_item(mp.id, {"item_id": item_id, "title": "iPhone 13", "price": {"amount": 32000},
                                  "created_at": NOW - 3600, "permalink": f"https://www.facebook.com/marketplace/item/{item_id}"})
    offers = [serialize(r) for r in build_offers(config, now=lambda: NOW)]
    assert [(o["post_count"], o["seller"]) for o in offers] == [(1, "Marketplace seller"), (1, "Marketplace seller")]


def test_offers_table_leaves_store_tables_unchanged(tmp_path):
    store, config = setup(tmp_path)
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k", NOW - 3600)
    store.upsert_comments(shop.id, "p1", [{"comment_id": "c1", "item_id": "p1", "text": "price?", "is_owner": False}])

    def snapshot():
        db = sqlite3.connect(tmp_path / "crawler.sqlite3")
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name != 'offers'")]
        return {t: sorted(map(tuple, db.execute(f"SELECT * FROM {t}"))) for t in tables}

    before = snapshot()
    build_offers(config, now=lambda: NOW)
    assert snapshot() == before


def test_offers_write_waits_while_crawler_holds_the_lock(tmp_path):
    store, config = setup(tmp_path)
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k", NOW - 3600)
    crawler = sqlite3.connect(tmp_path / "crawler.sqlite3", isolation_level=None, check_same_thread=False)
    crawler.execute("BEGIN IMMEDIATE")
    threading.Timer(0.5, lambda: crawler.execute("COMMIT")).start()
    started = time.monotonic()
    [record] = build_offers(config, now=lambda: NOW)
    assert time.monotonic() - started >= 0.4
    assert serialize(record)["price"] == 72000


def test_offer_has_no_buyer_or_seller_names(tmp_path):
    store, config = setup(tmp_path)
    group = store.add_source("https://www.facebook.com/groups/123", "fb_group", "Sylhet", "phones", name="Sylhet Phones")
    post(store, group, "g1", "iPhone 13 Pro 128GB, 72k", NOW - 3600, author_id=PRIVATE_ID, owner_id=PRIVATE_ID)
    store.upsert_comments(group.id, "g1", [
        {"comment_id": "c1", "item_id": "g1", "text": f"{BUYER} here, price?", "is_owner": False},
        {"comment_id": "c2", "item_id": "g1", "parent_id": "c1", "text": f"{BUYER} inbox please", "is_owner": True},
    ])
    payload = json.dumps([serialize(r) for r in build_offers(config, now=lambda: NOW)], ensure_ascii=False)
    for planted in (BUYER, "Rahim", PRIVATE_ID):
        assert planted not in payload


def test_payload_carries_no_seller_reply_text(tmp_path):
    store, config = setup(tmp_path)
    shop = store.add_source("https://www.facebook.com/shop", "fb_page", "Sylhet", "phones", name="Shop")
    post(store, shop, "p1", "iPhone 13 Pro 128GB, price inbox", NOW - 3600)
    store.upsert_comments(shop.id, "p1", [
        {"comment_id": "c1", "item_id": "p1", "text": "price?", "is_owner": False, "created_at": NOW - 600},
        {"comment_id": "c2", "item_id": "p1", "text": "Karim bhai last price 72k", "is_owner": True, "created_at": NOW - 300},
    ])
    [offer] = [serialize(r) for r in build_offers(config, now=lambda: NOW)]
    assert (offer["price"], offer["price_source"], offer["seller_reply"]) == (72000, "seller_reply", "price")
    assert (offer["comment_count"], offer["last_activity_at"]) == (1, NOW - 300)
    assert "Karim" not in json.dumps(offer, ensure_ascii=False)


def test_page_without_a_name_shows_its_address_name(tmp_path):
    store, config = setup(tmp_path)
    shop = store.add_source("https://www.facebook.com/iStoreSyl", "fb_page", "Sylhet", "phones")
    post(store, shop, "p1", "iPhone 13 Pro 128GB, 72k", NOW - 3600)
    [offer] = [serialize(r) for r in build_offers(config, now=lambda: NOW)]
    assert (offer["seller"], offer["source_name"]) == ("iStoreSyl", "iStoreSyl")
