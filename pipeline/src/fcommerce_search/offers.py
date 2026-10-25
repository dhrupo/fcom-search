import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from crawler.store import DAY, HISTORY_DAYS

from .config import Config
from .extract.rules import Offer, extract_offers

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
    offer_id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_hash TEXT NOT NULL, pushed_hash TEXT,
    expired INTEGER NOT NULL DEFAULT 0, seen_run TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS offer_posts (post_key TEXT PRIMARY KEY, offer_id TEXT NOT NULL);
"""
INBOX = re.compile(r"\binbox\b|ইনবক্স|\bmessenger\b", re.IGNORECASE)
PHONE_CATEGORIES = ("", "phones")  # compared trimmed and lower-cased; Marketplace has none


class OffersError(RuntimeError):
    pass


@dataclass(frozen=True)
class Source:
    url: str
    kind: str
    place: str
    category: str | None
    name: str | None


@dataclass(frozen=True)
class Row:
    source: Source
    item: dict
    comments: list
    offer: Offer
    post_key: str


@dataclass(frozen=True)
class OfferRecord:
    offer_id: str
    rows: tuple[Row, ...]
    seller_reply: str | None


def _open_crawler(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _read_rows(path: Path, cutoff: float) -> list[Row]:
    db = _open_crawler(path)
    try:
        sources = {sid: Source(url, kind, place, category, name) for sid, url, kind, place, category, name in
                   db.execute("SELECT id, url, kind, place, category, name FROM sources")}
        live = "i.expired = 0 AND (i.created_at IS NULL OR i.created_at >= ?)"
        items = list(db.execute(f"SELECT i.source_id, i.data FROM items i WHERE {live} "
                                "ORDER BY i.created_at DESC", (cutoff,)))
        comments: dict[tuple, list] = {}
        for sid, item_id, data in db.execute(
                "SELECT c.source_id, c.item_id, c.data FROM comments c JOIN items i "
                f"ON i.source_id = c.source_id AND i.item_id = c.item_id WHERE {live} ORDER BY c.created_at",
                (cutoff,)):
            comments.setdefault((sid, item_id), []).append(json.loads(data))
    finally:
        db.close()

    rows = []
    for sid, data in items:
        source, item = sources[sid], json.loads(data)
        if (source.category or "").strip().lower() not in PHONE_CATEGORIES:
            continue
        thread = comments.get((sid, item["item_id"]), [])
        replies = [c.get("text") or "" for c in thread if c.get("is_owner")]
        price = item.get("price")
        listing = price.get("amount") if source.kind == "fb_marketplace" and isinstance(price, dict) else None
        seen: dict[str, int] = {}
        for offer in extract_offers(item.get("text") or item.get("title") or "", replies, listing_price=listing):
            facts = f"{offer.model}|{offer.storage_gb}"
            seen[facts] = seen.get(facts, 0) + 1
            # A second row with the same model+storage in one post (dual sim vs eSIM) is its own offer.
            key = f"{source.url}\n{item['item_id']}\n{facts}\n{seen[facts]}"
            rows.append(Row(source, item, thread, offer, key))
    return rows


def _fold_key(row: Row) -> str:
    # Marketplace sellers have no id, so a listing only folds with itself; group posters are private
    # people, so their id is used here and never leaves this function or the machine.
    item, offer = row.item, row.offer
    seller = {"fb_marketplace": item["item_id"], "fb_group": item.get("author_id") or item["item_id"]}.get(
        row.source.kind, "")
    ordinal = row.post_key.rsplit("\n", 1)[1]
    facts = (offer.brand, offer.model, offer.storage_gb, offer.ram_gb, offer.region, offer.condition, offer.box)
    return json.dumps([row.source.url, seller, ordinal, *facts], ensure_ascii=False)


def build_offers(config: Config, now=time.time, allow_empty: bool = False) -> list[OfferRecord]:
    """Rebuild every live offer from the crawler's store (read-only) and save it to the pipeline's state DB."""
    crawler_db = config.crawler_data_dir / "crawler.sqlite3"
    if not crawler_db.is_file():
        raise OffersError(f"no crawler.sqlite3 in {config.crawler_data_dir}: check crawler_config and its data_dir")
    groups: dict[str, list[Row]] = {}
    for row in _read_rows(crawler_db, now() - HISTORY_DAYS * DAY):
        rows = groups.setdefault(_fold_key(row), [])
        if all(r.item["item_id"] != row.item["item_id"] for r in rows):
            rows.append(row)

    config.state_db.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.state_db)
    try:
        db.executescript(STATE_SCHEMA)
        with db:
            live = db.execute("SELECT count(*) FROM offers WHERE expired = 0").fetchone()[0]
            if not groups and live and not allow_empty:
                # An empty or unreadable store must not tell the site that every offer is gone.
                raise OffersError(f"no offers found in the crawler store; refusing to expire {live} live offers "
                                  "(re-run with --allow-empty if the store really is empty)")
            claimed: set[str] = set()
            records = [_record(db, rows, claimed) for rows in groups.values()]
            _save(db, records)
    finally:
        db.close()
    return records


def _record(db, rows: list[Row], claimed: set) -> OfferRecord:
    known = [r[0] for r in db.execute(
        f"SELECT offer_id FROM offer_posts WHERE post_key IN ({','.join('?' * len(rows))}) ORDER BY offer_id",
        [r.post_key for r in rows])]
    offer_id = next((o for o in known if o not in claimed), None) or secrets.token_hex(8)
    claimed.add(offer_id)
    newest = rows[0]
    if newest.offer.price_source == "seller_reply":
        seller_reply = "price"
    elif any(INBOX.search(c.get("text") or "") for r in rows for c in r.comments if c.get("is_owner")):
        seller_reply = "inbox"
    else:
        seller_reply = None
    return OfferRecord(offer_id, tuple(rows), seller_reply)


def _save(db, records: list[OfferRecord]) -> None:
    run = uuid.uuid4().hex
    for record in records:
        payload = json.dumps(serialize(record), ensure_ascii=False, sort_keys=True)
        db.execute(
            "INSERT INTO offers (offer_id, payload, payload_hash, seen_run) VALUES (?,?,?,?) "
            "ON CONFLICT(offer_id) DO UPDATE SET payload=excluded.payload, payload_hash=excluded.payload_hash, "
            "expired=0, seen_run=excluded.seen_run",
            (record.offer_id, payload, hashlib.sha256(payload.encode()).hexdigest(), run),
        )
        db.executemany("INSERT INTO offer_posts (post_key, offer_id) VALUES (?, ?) "
                       "ON CONFLICT(post_key) DO UPDATE SET offer_id = excluded.offer_id",
                       [(r.post_key, record.offer_id) for r in record.rows])
    db.execute("UPDATE offers SET expired = 1 WHERE seen_run != ? AND expired = 0", (run,))


def serialize(record: OfferRecord) -> dict:
    """The offer as the site receives it: facts only. No person's name or id, no post, reply or title text."""
    newest = record.rows[0]
    source, offer, item = newest.source, newest.offer, newest.item
    name = source.name or source.url.rstrip("/").rsplit("/", 1)[-1]
    seller = {"fb_marketplace": "Marketplace seller", "fb_group": "Group member"}.get(source.kind, name)
    comments = [c for r in record.rows for c in r.comments]
    times = [c["created_at"] for c in comments if c.get("created_at") is not None]
    created = [r.item.get("created_at") for r in record.rows if r.item.get("created_at") is not None]
    photos = [u for u in item.get("images") or [] if isinstance(u, str)]
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
        "sold": item.get("sold"),
        "photos": photos, "has_photos": bool(photos),
        "seller_reply": record.seller_reply,
        "seller_answered": any(c.get("is_owner") for c in comments),
        "comment_count": sum(not c.get("is_owner") for c in comments),
        "last_activity_at": max(times) if times else None,
        "posted_at": min(created) if created else None,
        "post_count": len(record.rows),
        "posts": [{"permalink": r.item.get("permalink"), "created_at": r.item.get("created_at"),
                   "price": r.offer.price, "price_source": r.offer.price_source} for r in record.rows],
    }
