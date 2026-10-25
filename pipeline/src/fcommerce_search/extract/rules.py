import re
from dataclasses import dataclass, replace
from functools import cache

from .catalog import Catalog, clean, load_catalog

MIN_PHONE_PRICE = 1000
MAX_PHONE_PRICE = 1_000_000
STORAGE_GB = {64, 128, 256, 512}
REGIONS = {
    "uk": "UK", "usa": "USA", "australia": "Australia", "aus": "Australia", "singapore": "Singapore",
    "japan": "Japan", "jp": "Japan", "canada": "Canada", "uae": "UAE", "dubai": "UAE", "global": "Global",
    "india": "India", "indian": "India", "hong kong": "Hong Kong", "official": "Official",
}
# An amount is not the phone's price when these words sit just before it ("delivery charge 100tk",
# "exchange hole 20000tk", "old price 95000") or just after it ("10,000 bonus", "2k kom").
NOT_A_PRICE_BEFORE = re.compile(r"\b(bonus|discount|cashback|off|save|exchange|trade|emi|delivery|charge|advance|"
                                r"booking|kom|less|add|old price|previous price|regular price)\b|কম")
NOT_A_PRICE_AFTER = re.compile(r"\b(bonus|discount|cashback|off|kom|less|add)\b|কম")
NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
PRICES = [
    (re.compile(rf"(?:bdt|tk|taka|৳|টাকা)\s*[:.]?\s*{NUMBER}"), 1),
    (re.compile(rf"{NUMBER}\s*(?:/-|tk\b|taka|টাকা|bdt|৳)"), 1),
    (re.compile(r"(\d+(?:\.\d+)?)\s*k\b(?!\s*(?:video|recording|display|screen|hdr|uhd|zoom|cycles?|mah|@|\d))"), 1000),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:hajar|hazar|হাজার)"), 1000),
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:lakh|lac|লাখ|লক্ষ)"), 100_000),
    (re.compile(rf"\b(?:price|দাম)\s*[:\-]?\s*{NUMBER}"), 1),
    (re.compile(r"(?<![\d,])(\d{1,2},\d{2},\d{3})(?![\d,])"), 1),
]
TRADE_IN = re.compile(r"\b(your old|bring your|exchange your|trade in your|than|vs|compared?|difference)\b|পুরোনো|পুরাতন")
NOT_AN_OFFER = re.compile(r"\b(exchange|trade|old)\b|এক্সচেঞ্জ")
# Customer stories, thank-yous and sold-out news name a phone that is not for sale (Dhrupo, 2026-10-25).
STORY = re.compile(r"কিনে নিলেন|কিনলেন|বেছে নিয়েছেন|নিয়ে গেলেন|"
                   r"\b(congratulations|congrats|purchased|bought|has chosen|happy customer|sold out|giveaway|winner)\b")
ACCESSORY = re.compile(r"\b(case|cover|glass|protector|charger|adapter|cable|skin|lens|airpods|earphones?)\b")
INCLUDED = re.compile(r"\b(with|and|including|full box|free)\b|[&+]|সহ")
# Split sentences, but not "Tk. 85,000" / "BDT. 85,000" (a currency word, a dot, then the amount).
CLAUSES = re.compile(r"(?<!\d)(?<!(?<!\d)tk)(?<!(?<!\d)bdt)(?<!taka)[.!?।](?!\d)|[;|\n]|,\s")


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


def _prices(line: str) -> list[int]:
    found = []
    for pattern, scale in PRICES:
        for m in pattern.finditer(line):
            before, after = " ".join(line[:m.start(1)].split()[-3:]), " ".join(line[m.end():].split()[:3])
            if NOT_A_PRICE_BEFORE.search(before) or NOT_A_PRICE_AFTER.search(after):
                continue
            raw = m.group(1)
            if scale == 1000 and float(raw) < 5:
                continue
            value = round(float(raw.replace(",", "")) * scale)
            if MIN_PHONE_PRICE <= value <= MAX_PHONE_PRICE and value not in found:
                found.append(value)
    return found


def _storages(line: str) -> list[tuple[int, int | None]]:
    found = []
    for ram, storage in re.findall(r"\b(\d{1,2})(?:\s*/\s*|\+)(\d{2,4})\b", line):
        if int(storage) in STORAGE_GB and int(ram) <= 24:
            found.append((int(storage), int(ram)))
    for storage in re.findall(r"\b(\d{2,4})\s*(?:gb|জিবি)\b(?!\s*ram)", line):
        if int(storage) in STORAGE_GB:
            found.append((int(storage), None))
    for storage in re.findall(r"\b(64|128|256|512)\b(?!\s*(?:gb|tb|mp|hz|mah|w\b|tk|taka|k\b|[/,.]\d|\d))", line):
        found.append((int(storage), None))
    for tb in re.findall(r"\b([12])\s*tb\b(?!\s*(?:icloud|cloud|backup|ssd|google))", line):
        found.append((int(tb) * 1024, None))
    rams = set(re.findall(r"\b(\d{1,2})\s*gb\s*ram\b", line))
    if found:
        rams |= {r for r in re.findall(r"\b(\d{1,2})\s*gb\b(?!\s*ram)", line) if int(r) <= 24}
    ram_alone = int(r) if (r := _one(rams)) else None
    unique: dict[int, int | None] = {}
    for storage, ram in found:
        unique.setdefault(storage, ram if ram is not None else ram_alone)
    return list(unique.items())


def _one(found: set[str]) -> str | None:
    return next(iter(found)) if len(found) == 1 else None


def _region(text: str) -> str | None:
    text = re.sub(r"\b(non|un)[- ]?official\b", " ", text)
    return _one({name for word, name in REGIONS.items() if re.search(rf"\b{word}\b", text)})


def _condition(text: str) -> str | None:
    return _one({name for name, pattern in (("new", r"(?<!like )(?<!as )\b(brand new|new|sealed|box intact|intact box)\b"),
                                            ("used", r"\b(used|like new|second hand|2nd hand|pre-?owned)\b"))
                 if re.search(pattern, text)})


def _box(text: str) -> str | None:
    return _one({name for name, pattern in (("only device", r"\b(only device|device only|without box)\b"),
                                            ("with box", r"\b(with ?box|full box)\b"))
                 if re.search(pattern, text)})


def _is_accessory(text: str) -> bool:
    # "iPhone 15 Pro Max case 4,500tk" sells a case; "128GB with original charger" sells a phone.
    if _storages(text):
        return False
    return any(not INCLUDED.search(" ".join(text[:m.start()].split()[-3:])) for m in ACCESSORY.finditer(text))


def extract_offers(text: str, seller_replies=(), *, listing_price: int | None = None) -> list[Offer]:
    """Read a post (and the seller's replies) into offers; any fact not clearly stated stays None."""
    catalog = _phones()
    post = clean(text)
    lines = post.splitlines()
    base = {"condition": _condition(post), "box": _box(post)}
    post_region = _region(post)

    table, current = [], []
    for line in lines:
        prices, storages = _prices(line), _storages(line)
        if models := catalog.find_models(line):
            current = models
        elif line.strip() and not prices:
            current = []  # a heading for something else ("iPad", "MacBook") ends the phone's rows
        if current and len(prices) == 1 and len(storages) == 1 and not _is_accessory(line):
            storage, ram = storages[0]
            table.append(Offer(current[0].brand, current[0].model, storage, ram, region=_region(line) or post_region,
                               price=prices[0], price_source="post", **base))
    if len(table) >= 2:
        return table

    clauses = [c for c in CLAUSES.split(post) if c.strip() and not TRADE_IN.search(c)]
    if _is_accessory(post) or (STORY.search(post) and not any(_prices(c) for c in clauses)):
        return []
    if len(catalog.find_models(post)) > 1:
        # Only with several models: "Exchange your iPhone 11" next to the phone on sale is not an offer.
        clauses = [c for c in clauses if not NOT_AN_OFFER.search(c)]
    models = catalog.find_models("\n".join(clauses))
    if len(models) > 1:
        return _one_offer_per_clause(catalog, clauses, base, post_region)
    storages = _storages(post) or [(None, None)]
    offers = [Offer(m.brand, m.model, storage, ram, region=post_region, **base)
              for m in models for storage, ram in storages]
    if len(offers) != 1:
        return offers

    post_prices = [p for clause in clauses for p in _prices(clause)]
    reply_prices = list(dict.fromkeys(p for reply in seller_replies for p in _prices(clean(reply))))
    if len(reply_prices) == 1:
        price, source = reply_prices[0], "seller_reply"
    elif len(set(post_prices)) == 1:
        price, source = post_prices[0], "post"
    elif not post_prices and listing_price is not None and listing_price >= MIN_PHONE_PRICE:
        price, source = int(listing_price), "listing"
    else:
        price, source = None, None
    return [replace(offers[0], price=price, price_source=source)]


def _one_offer_per_clause(catalog: Catalog, clauses: list[str], base: dict, region: str | None) -> list[Offer]:
    offers = []
    for clause in clauses:
        models = catalog.find_models(clause)
        if len(models) > 1:
            offers += [Offer(m.brand, m.model, region=_region(clause) or region, **base) for m in models]
            continue
        if not models:
            continue
        storages, prices = _storages(clause) or [(None, None)], _prices(clause)
        price = prices[0] if len(prices) == 1 and len(storages) == 1 else None
        offers += [Offer(models[0].brand, models[0].model, storage, ram, region=_region(clause) or region,
                         price=price, price_source="post" if price else None, **base) for storage, ram in storages]
    return offers
