import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass

from crawler.store import DAY, HISTORY_DAYS, Source, Store

from .config import Config
from .extract.rules import Offer, extract_offers

OFFERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    offer_id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL, pushed_hash TEXT,
    expired INTEGER NOT NULL DEFAULT 0, seen_run TEXT NOT NULL, updated_at REAL NOT NULL);
"""
INBOX = re.compile(r"\binbox\b|ইনবক্স|\bmessenger\b", re.IGNORECASE)
WRITE_WAIT_SECONDS = 30


@dataclass(frozen=True)
class OfferRecord:
    offer_id: str
    source: Source
    offer: Offer
    posts: tuple[dict, ...]
    text: str
    sold: bool | None
    seller_reply: str | None
    comment_count: int
    last_activity_at: float | None


def _fold_key(source: Source, item: dict, offer: Offer) -> str:
    # Marketplace sellers have no id, so a listing only ever folds with itself; group posters are
    # private people, so their id is used here and never leaves this function.
    seller = {"fb_marketplace": item["item_id"], "fb_group": item.get("author_id") or item["item_id"]}.get(source.kind, "")
    facts = (offer.brand, offer.model, offer.storage_gb, offer.ram_gb, offer.region, offer.condition, offer.box)
    raw = json.dumps([source.id, seller, *facts], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _read_offers(store: Store, cutoff: float):
    for source in store.list_sources():
        for item in store.items_for(source.id):
            if item.get("created_at") is not None and item["created_at"] < cutoff:
                continue
            comments = store.comments_for(source.id, item["item_id"])
            replies = [c.get("text") or "" for c in comments if c.get("is_owner")]
            listing = (item.get("price") or {}).get("amount") if source.kind == "fb_marketplace" else None
            text = item.get("text") or item.get("title") or ""
            for offer in extract_offers(text, replies, listing_price=listing):
                yield source, item, comments, replies, offer


def build_offers(config: Config, now=time.time) -> list[OfferRecord]:
    """Rebuild every live offer from the crawler's store and save it to the offers table."""
    store = Store(config.crawler_data_dir, clock=now)
    groups: dict[str, list] = {}
    for source, item, comments, replies, offer in _read_offers(store, now() - HISTORY_DAYS * DAY):
        groups.setdefault(_fold_key(source, item, offer), []).append((source, item, comments, replies, offer))

    records = []
    for offer_id, rows in groups.items():
        source, item, _, replies, offer = rows[0]  # items_for() lists newest first
        comments = [c for r in rows for c in r[2]]
        times = [c["created_at"] for c in comments if c.get("created_at") is not None]
        if offer.price_source == "seller_reply":
            seller_reply = "price"
        else:
            seller_reply = "inbox" if any(INBOX.search(r) for r in replies) else None
        records.append(OfferRecord(
            offer_id=offer_id, source=source, offer=offer,
            posts=tuple({"permalink": r[1].get("permalink"), "created_at": r[1].get("created_at")} for r in rows),
            text=item.get("text") or item.get("title") or "",
            sold=item.get("sold"), seller_reply=seller_reply,
            comment_count=sum(not c.get("is_owner") for c in comments),
            last_activity_at=max(times) if times else None,
        ))
    _save(config, records, now())
    return records


def _save(config: Config, records: list[OfferRecord], now: float) -> None:
    run = uuid.uuid4().hex
    db = sqlite3.connect(config.crawler_data_dir / "crawler.sqlite3", timeout=WRITE_WAIT_SECONDS)
    try:
        with db:
            db.executescript(OFFERS_SCHEMA)
            for record in records:
                payload = json.dumps(serialize(record), ensure_ascii=False, sort_keys=True)
                db.execute(
                    "INSERT INTO offers (offer_id, payload, payload_hash, seen_run, updated_at) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(offer_id) DO UPDATE SET payload=excluded.payload, payload_hash=excluded.payload_hash, "
                    "expired=0, seen_run=excluded.seen_run, updated_at=excluded.updated_at",
                    (record.offer_id, payload, hashlib.sha256(payload.encode()).hexdigest(), run, now),
                )
            db.execute("UPDATE offers SET expired = 1, updated_at = ? WHERE seen_run != ? AND expired = 0", (now, run))
    finally:
        db.close()


def serialize(record: OfferRecord) -> dict:
    """The offer as the site receives it: facts only, no person's name, id or reply text."""
    source, offer = record.source, record.offer
    name = source.name or source.url.rstrip("/").rsplit("/", 1)[-1]
    seller = {"fb_marketplace": "Marketplace seller", "fb_group": "Group member"}.get(source.kind, name)
    created = [p["created_at"] for p in record.posts if p["created_at"] is not None]
    return {
        "offer_id": record.offer_id,
        "source_kind": source.kind,
        "source_name": None if source.kind == "fb_marketplace" else name,
        "seller": seller,
        "place": source.place,
        "category": "phones",
        "brand": offer.brand, "model": offer.model,
        "storage_gb": offer.storage_gb, "ram_gb": offer.ram_gb,
        "condition": offer.condition, "box": offer.box, "region": offer.region,
        "price": offer.price, "price_source": offer.price_source,
        "sold": record.sold,
        "seller_reply": record.seller_reply,
        "comment_count": record.comment_count,
        "last_activity_at": record.last_activity_at,
        "posted_at": min(created) if created else None,
        "updated_at": max(created) if created else None,
        "post_count": len(record.posts),
        "posts": list(record.posts),
        "text": record.text,
    }
