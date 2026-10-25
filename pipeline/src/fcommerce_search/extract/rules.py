import re
import unicodedata
from dataclasses import dataclass
from functools import cache

from .catalog import BANGLA_DIGITS, Catalog, load_catalog

MIN_PHONE_PRICE = 1000
STORAGE_GB = {16, 32, 64, 128, 256, 512}
REGIONS = {
    "uk": "UK", "usa": "USA", "australia": "Australia", "aus": "Australia", "singapore": "Singapore",
    "japan": "Japan", "jp": "Japan", "canada": "Canada", "uae": "UAE", "dubai": "UAE", "global": "Global",
    "india": "India", "indian": "India", "hong kong": "Hong Kong", "official": "Official",
}
NOT_A_PRICE = re.compile(r"\b(bonus|discount|cashback|off|save|trade|exchange|emi|delivery|charge|advance|booking)\b")
PRICES = [
    (re.compile(r"(?:bdt|tk|taka|৳|টাকা)\s*[:.]?\s*(\d[\d,]*)"), 1),
    (re.compile(r"(\d[\d,]*)\s*(?:/-|tk\b|taka|টাকা|bdt|৳)"), 1),
    (re.compile(r"(\d+(?:\.\d+)?)\s*k\b"), 1000),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:hajar|hazar|হাজার)"), 1000),
    (re.compile(r"\b(?:price|দাম)\s*[:\-]?\s*(\d[\d,]*)"), 1),
]


@dataclass(frozen=True)
class Offer:
    brand: str
    model: str
    storage_gb: int | None = None
    ram_gb: int | None = None
    condition: str | None = None
    box: str | None = None
    region: str | None = None
    price: int | None = None
    price_source: str | None = None


@cache
def _phones() -> Catalog:
    return load_catalog("phones")


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().translate(BANGLA_DIGITS)
    return re.sub(r"#\S+", " ", text)


def _prices(line: str) -> list[int]:
    if NOT_A_PRICE.search(line):
        return []
    found = []
    for pattern, scale in PRICES:
        for m in pattern.finditer(line):
            value = round(float(m.group(1).replace(",", "")) * scale)
            if value >= MIN_PHONE_PRICE and value not in found:
                found.append(value)
    return found


def _storages(line: str) -> list[tuple[int, int | None]]:
    found = []
    for ram, storage in re.findall(r"\b(\d{1,2})\s*/\s*(\d{2,4})\b", line):
        if int(storage) in STORAGE_GB and int(ram) <= 24:
            found.append((int(storage), int(ram)))
    for storage in re.findall(r"\b(\d{2,4})\s*(?:gb|জিবি)\b", line):
        if int(storage) in STORAGE_GB:
            found.append((int(storage), None))
    for tb in re.findall(r"\b(\d)\s*tb\b", line):
        found.append((int(tb) * 1024, None))
    unique = []
    for storage, ram in found:
        if storage not in [s for s, _ in unique]:
            unique.append((storage, ram))
    return unique


def _one(found: set[str]) -> str | None:
    return next(iter(found)) if len(found) == 1 else None


def _region(text: str) -> str | None:
    return _one({name for word, name in REGIONS.items() if re.search(rf"\b{word}\b", text)})


def _condition(text: str) -> str | None:
    return _one({name for name, pattern in (("new", r"\b(brand new|new|intact|sealed)\b"),
                                            ("used", r"\b(used|second hand|2nd hand|pre-?owned)\b"))
                 if re.search(pattern, text)})


def _box(text: str) -> str | None:
    return _one({name for name, pattern in (("only device", r"\b(only device|device only|without box)\b"),
                                            ("with box", r"\b(with ?box|full box)\b"))
                 if re.search(pattern, text)})


def extract_offers(text: str, seller_replies=(), *, listing_price: int | None = None,
                   catalog: Catalog | None = None) -> list[Offer]:
    """Read a post (and the seller's replies) into offers; any fact not clearly stated stays None."""
    catalog = catalog or _phones()
    post = _clean(text)
    lines = post.splitlines()
    base = {"condition": _condition(post), "box": _box(post)}

    table, current = [], []
    for line in lines:
        prices, storages = _prices(line), _storages(line)
        if models := catalog.find_models(line):
            current = models
        elif line.strip() and not prices:
            current = []  # a heading for something else ("iPad", "MacBook") ends the phone's rows
        if current and len(prices) == 1 and len(storages) == 1:
            storage, ram = storages[0]
            table.append(Offer(current[0].brand, current[0].model, storage, ram, region=_region(line),
                               price=prices[0], price_source="post", **base))
    if len(table) >= 2:
        return table

    models = catalog.find_models(post)
    storages = _storages(post) or [(None, None)]
    offers = [Offer(m.brand, m.model, storage, ram, region=_region(post), **base)
              for m in models for storage, ram in storages]
    if len(offers) != 1:
        return offers

    post_prices = [p for line in lines for p in _prices(line)]
    reply_prices = list(dict.fromkeys(p for reply in seller_replies for p in _prices(_clean(reply))))
    if len(reply_prices) == 1:
        price, source = reply_prices[0], "seller_reply"
    elif len(set(post_prices)) == 1:
        price, source = post_prices[0], "post"
    elif not post_prices and listing_price is not None and listing_price >= MIN_PHONE_PRICE:
        price, source = int(listing_price), "listing"
    else:
        price, source = None, None
    return [Offer(**{**offers[0].__dict__, "price": price, "price_source": source})]
