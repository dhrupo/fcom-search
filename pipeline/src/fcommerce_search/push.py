import json
import logging
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass

from .config import Config
from .offers import WRITE_WAIT_SECONDS

log = logging.getLogger(__name__)
BATCH = 200
TIMEOUT_SECONDS = 30
EXPIRED = "expired"


class PushError(RuntimeError):
    pass


@dataclass(frozen=True)
class PushResult:
    sent: int
    expired: int


def _read_secret(config: Config) -> str:
    try:
        lines = config.secret_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        lines = []
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == "FCS_PUSH_SECRET" and value.strip():
            return value.strip()
    raise PushError(f"no push secret: put FCS_PUSH_SECRET=... in {config.secret_file}")


def _send(config: Config, secret: str, upsert: list[dict], expire: list[str]) -> None:
    request = urllib.request.Request(
        config.site_url.rstrip("/") + "/api/ingest",
        data=json.dumps({"upsert": upsert, "expire": expire}, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "X-Ingest-Secret": secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise PushError(f"the site refused the push (HTTP {e.code})") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise PushError(f"the push did not reach the site ({type(e).__name__})") from None
    # The site's own answer counts, not just HTTP 200: a 200 with ok=false is a failed batch.
    if not (isinstance(answer, dict) and answer.get("ok") is True):
        raise PushError("the site did not confirm the push")


def push_offers(config: Config) -> PushResult:
    """Send offers changed since the last push, and expired ones, in batches; stop at the first failure."""
    if not config.site_url:
        raise PushError("no site url: set [site] url in fcommerce-search.toml")
    secret = _read_secret(config)
    db = sqlite3.connect(config.crawler_data_dir / "crawler.sqlite3", timeout=WRITE_WAIT_SECONDS)
    sent = expired = 0
    try:
        upserts = db.execute("SELECT offer_id, payload, payload_hash FROM offers WHERE expired = 0 "
                             "AND (pushed_hash IS NULL OR pushed_hash != payload_hash) ORDER BY offer_id").fetchall()
        expires = [r[0] for r in db.execute("SELECT offer_id FROM offers WHERE expired = 1 "
                                            "AND pushed_hash IS NOT NULL AND pushed_hash != ? ORDER BY offer_id",
                                            (EXPIRED,))]
        for i in range(0, len(upserts), BATCH):
            batch = upserts[i:i + BATCH]
            _send(config, secret, [json.loads(r[1]) for r in batch], [])
            with db:
                db.executemany("UPDATE offers SET pushed_hash = ? WHERE offer_id = ?", [(r[2], r[0]) for r in batch])
            sent += len(batch)
        for i in range(0, len(expires), BATCH):
            batch = expires[i:i + BATCH]
            _send(config, secret, [], batch)
            with db:
                db.executemany("UPDATE offers SET pushed_hash = ? WHERE offer_id = ?", [(EXPIRED, o) for o in batch])
            expired += len(batch)
    finally:
        db.close()
    log.info("pushed %d offers, expired %d", sent, expired)
    return PushResult(sent, expired)
