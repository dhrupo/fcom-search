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


def test_video_resolution_is_not_a_price():
    [offer] = extract_offers("iPhone 15 Pro 256GB, 4K video, 8k zoom, price 1,20,000 tk")
    assert offer.price == 120000


def test_ram_size_is_not_a_storage():
    [offer] = extract_offers("Galaxy S25 Ultra 16GB RAM 1TB 1,60,000tk")
    assert (offer.storage_gb, offer.ram_gb, offer.price) == (1024, 16, 160000)


def test_heading_region_applies_to_table_rows():
    offers = extract_offers("All UK version, brand new\niPhone 15\n128GB ---- 90,000tk\n256GB ---- 1,00,000tk")
    assert [(o.region, o.condition, o.storage_gb, o.price) for o in offers] == [
        ("UK", "new", 128, 90000), ("UK", "new", 256, 100000)]


def test_price_next_to_delivery_note_is_kept():
    [offer] = extract_offers("iPhone 13 128GB 55,000tk, delivery charge free")
    assert offer.price == 55000


def offers_of(text, replies=()):
    return [(o.model, o.storage_gb, o.price) for o in extract_offers(text, list(replies))]


def test_phone_number_or_number_list_is_not_a_price():
    assert offers_of("iPhone 15 Pro 256GB used\nPrice: 01711223344 (whatsapp)") == [("iPhone 15 Pro", 256, None)]
    assert offers_of("iPhone 15 Pro 256GB\nTk 85000,90000") == [("iPhone 15 Pro", 256, None)]


def test_apple_chip_name_is_not_a_galaxy_phone():
    assert offers_of("iPhone 13 128GB, A15 Bionic chip, 55k") == [("iPhone 13", 128, 55000)]
    assert offers_of("iPhone 15 Pro 256GB A17 Pro chip, price 95k") == [("iPhone 15 Pro", 256, 95000)]
    assert offers_of("Galaxy A15 128GB 18k") == [("Galaxy A15", 128, 18000)]


def test_exchange_or_compare_mention_is_not_an_offer():
    assert offers_of("iPhone 15 Pro 256GB 1,20,000tk. Exchange your iPhone 11 accepted") == [
        ("iPhone 15 Pro", 256, 120000)]
    assert offers_of("iPhone 15 Pro 256GB 1,20,000tk, much better than iPhone 14 Pro") == [
        ("iPhone 15 Pro", 256, 120000)]


def test_two_phones_in_one_post_keep_their_own_storage_and_price():
    assert offers_of("Selling iPhone 15 Pro 256GB 95k. Also have iPhone 13 128GB 55k") == [
        ("iPhone 15 Pro", 256, 95000), ("iPhone 13", 128, 55000)]


def test_accessory_post_is_not_a_phone_offer():
    assert offers_of("iPhone 15 Pro Max original silicone case with MagSafe 4,500tk") == []
    assert offers_of("iPhone 13 Pro Max case 1200tk") == []
    assert offers_of("iPhone 13 Pro 128GB with original charger, 72k") == [("iPhone 13 Pro", 128, 72000)]


def test_discount_in_seller_reply_is_not_the_price():
    for reply in ("2k kom hobe na bhai", "5k kom hobe", "৫ হাজার কম"):
        [offer] = extract_offers("iPhone 15 Pro 256GB price 95k", [reply])
        assert (offer.price, offer.price_source) == (95000, "post")


def test_like_new_is_used_and_intact_display_is_not_new():
    assert extract_offers("iPhone 13 128GB like new condition 55k")[0].condition == "used"
    assert extract_offers("iPhone 13 128GB display intact battery 88% 55k")[0].condition is None
    assert extract_offers("iPhone 13 128GB box intact, 55k")[0].condition == "new"


def test_shop_hours_are_not_pro_max_models():
    assert offers_of("iPhone 15 Pro 256GB 95k. Shop open 10am-11pm") == [("iPhone 15 Pro", 256, 95000)]
    assert offers_of("Samsung original charger 1,500tk. Shop open till 12 pm") == []


def test_ram_plus_storage_is_not_a_plus_model():
    [offer] = extract_offers("Galaxy S25 Ultra 16+512 1,40,000tk")
    assert (offer.model, offer.storage_gb, offer.ram_gb, offer.price) == ("Galaxy S25 Ultra", 512, 16, 140000)


def test_small_gb_values_and_dates_are_not_storage():
    [offer] = extract_offers("Pixel 10 Pro 16GB 256GB 1,05,000tk")
    assert (offer.storage_gb, offer.ram_gb, offer.price) == (256, 16, 105000)
    assert offers_of("iPhone 15 Pro 256GB 95k, offer till 10/16") == [("iPhone 15 Pro", 256, 95000)]


def test_non_official_is_not_official():
    assert extract_offers("iPhone 15 Pro 256GB non-official 95k")[0].region is None


def test_cloud_storage_is_not_phone_storage():
    assert offers_of("iPhone 15 Pro 256GB 95k, 2 TB icloud backup free") == [("iPhone 15 Pro", 256, 95000)]


def test_battery_cycles_are_not_a_price():
    assert offers_of("iPhone 15 Pro 256GB battery 2k cycle\nprice inbox") == [("iPhone 15 Pro", 256, None)]


def test_lakh_prices_and_camera_4k():
    assert offers_of("iPhone 13 Pro 128GB price 1.2 lakh") == [("iPhone 13 Pro", 128, 120000)]
    assert offers_of("iPhone 13 Pro 128GB 1,25,000") == [("iPhone 13 Pro", 128, 125000)]
    assert offers_of("iPhone 15 Pro 48MP camera 4K. price 95k") == [("iPhone 15 Pro", None, 95000)]


def test_single_phone_post_keeps_exchange_and_age_notes():
    assert offers_of("iPhone 13 Pro 128GB price 85000tk exchange possible") == [("iPhone 13 Pro", 128, 85000)]
    assert offers_of("iPhone 13 Pro Max 256GB 3 months old 90000tk") == [("iPhone 13 Pro Max", 256, 90000)]


def test_phone_listing_its_included_accessories_is_a_phone():
    assert offers_of("iPhone 13 Pro full box with charger, price 85000tk") == [("iPhone 13 Pro", None, 85000)]


def test_plus_model_followed_by_ram_storage():
    [offer] = extract_offers("Samsung Galaxy S23+ 8/256 price 85000tk")
    assert (offer.model, offer.storage_gb, offer.ram_gb, offer.price) == ("Galaxy S23+", 256, 8, 85000)


def test_phone_spec_naming_its_chip_is_still_a_phone():
    assert offers_of("iPhone 14 Pro A16 Bionic 128GB price 95000tk") == [("iPhone 14 Pro", 128, 95000)]


def test_tk_with_a_dot_is_a_price():
    assert offers_of("iPhone 13 Pro 128GB Price Tk. 85,000") == [("iPhone 13 Pro", 128, 85000)]


def test_spaced_pm_is_pro_max():
    assert offers_of("iPhone 13 pm 256gb 90000tk") == [("iPhone 13 Pro Max", 256, 90000)]


def test_two_models_in_one_sentence_are_two_unpriced_offers():
    assert offers_of("iPhone 13 and iPhone 14 available, inbox") == [("iPhone 13", None, None), ("iPhone 14", None, None)]


def test_customer_story_or_sold_out_is_not_an_offer():
    assert offers_of("আমাদের সম্মানিত কাস্টমার Apple Station থেকে পছন্দের iPhone 18 Pro Max কিনে নিলেন।") == []
    assert offers_of("Congratulations on your new iPhone 15 Pink") == []
    assert offers_of("First iPhone 18 Pro Sold Out") == []
    assert offers_of("Our customer bought an iPhone 15 Pro 256GB, you can too: 1,20,000tk") == [
        ("iPhone 15 Pro", 256, 120000)]


def test_other_brands_number_is_not_a_bare_iphone():
    assert offers_of("From the original Pixel to the latest Pixel 11, Pixel 11 Pro & Pixel 11 Pro XL") == []


def test_storage_size_without_gb_next_to_a_phone():
    assert offers_of("iPhone 13 pro 128 White") == [("iPhone 13 Pro", 128, None)]
    assert offers_of("iphone 13 mini,blue coloue 128 b storage") == [("iPhone 13 mini", 128, None)]


def test_common_typo_in_model_name():
    assert offers_of("iPhone 13 Pro Mex with box") == [("iPhone 13 Pro Max", None, None)]
