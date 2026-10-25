from pathlib import Path

from fcommerce_search.extract.rules import extract_offers

FIXTURES = Path(__file__).parent / "fixtures"


def facts(offer):
    return (offer.model, offer.storage_gb, offer.region, offer.price, offer.price_source)


def test_seller_reply_price_wins_with_source():
    [offer] = extract_offers("iPhone 13 Pro 128GB used, price 75k", ["last price 72k bhai"])
    assert (offer.price, offer.price_source) == (72000, "seller_reply")


def test_inbox_is_unknown():
    [offer] = extract_offers("iPhone 13 Pro 128GB, price inbox", ["Please check your inbox!"])
    assert offer.model == "iPhone 13 Pro"
    assert (offer.price, offer.price_source) == (None, None)


def test_two_storages_make_two_offers():
    offers = extract_offers("iPhone 15 Pro 128GB / 256GB available, ৳1,05,000")
    # One price for two storages: which one it belongs to is unclear, so it stays unknown.
    assert [(o.model, o.storage_gb, o.price) for o in offers] == [
        ("iPhone 15 Pro", 128, None), ("iPhone 15 Pro", 256, None)]


def test_place_name_is_not_a_region():
    [offer] = extract_offers("iPhone 13 Pro 128GB UK, Zindabazar Sylhet, 72 hajar")
    assert (offer.region, offer.price) == ("UK", 72000)
    [offer] = extract_offers("iPhone 13 Pro 128GB, Sylhet")
    assert offer.region is None


def test_tiny_marketplace_phone_price_is_unknown():
    [offer] = extract_offers("iPhone 13 with box", listing_price=35)
    assert (offer.price, offer.box) == (None, "with box")
    [offer] = extract_offers("iPhone 13 with box, 35k", listing_price=35)
    assert (offer.price, offer.price_source) == (35000, "post")
    [offer] = extract_offers("Iphone 13", listing_price=32000)
    assert (offer.price, offer.price_source) == (32000, "listing")


def test_price_table_post_gives_one_offer_per_line():
    offers = extract_offers((FIXTURES / "price_table_post.txt").read_text(encoding="utf-8"))
    assert [facts(o) for o in offers] == [
        ("iPhone 18 Pro Max", 256, "Singapore", 207999, "post"),
        ("iPhone 18 Pro Max", 512, "Singapore", 244999, "post"),
        ("iPhone 17 Pro", 512, "Australia", 191999, "post"),
        ("iPhone 17 Pro", 256, "Japan", 149999, "post"),
        ("iPhone 16", 128, "USA", 91999, "post"),
        ("iPhone Air", 256, "Japan", 113999, "post"),
    ]


def test_bonus_amount_is_not_a_price():
    [offer] = extract_offers((FIXTURES / "bonus_post.txt").read_text(encoding="utf-8"))
    assert (offer.model, offer.price) == ("iPhone 18 Pro", None)


def test_two_regions_stay_unknown():
    [offer] = extract_offers("iPhone 16 (Global/ Australia Variant)-----------128 GB-------------BDT 1,06,999/-")
    assert (offer.region, offer.price) == (None, 106999)
