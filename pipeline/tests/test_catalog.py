import tomllib
from pathlib import Path

import pytest

from fcommerce_search.extract.catalog import load_catalog

LINES = tomllib.loads((Path(__file__).parent / "fixtures" / "phone_lines.toml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def phones():
    return load_catalog("phones")


def names(models):
    return [f"{m.brand} {m.model}" for m in models]


@pytest.mark.parametrize("text, expected", [
    ("17pm", "Apple iPhone 17 Pro Max"),
    ("17 pro max", "Apple iPhone 17 Pro Max"),
    ("iphone 17promax", "Apple iPhone 17 Pro Max"),
    ("১৭ প্রো ম্যাক্স", "Apple iPhone 17 Pro Max"),
    ("Galaxy S25 Ultra", "Samsung Galaxy S25 Ultra"),
    *[(line["text"], line["models"][0]) for line in LINES["line"]],
])
def test_nickname_maps_to_canonical_model(phones, text, expected):
    assert names(phones.find_models(text)) == [expected]


@pytest.mark.parametrize("text", ["iPhone 99 Ultra", "Samsung", "iphone", "১২ মাসের EMI", "iPhone 18 Pro Series"])
def test_unknown_model_stays_none(phones, text):
    assert phones.find_models(text) == []


@pytest.mark.parametrize("text", [h["text"] for h in LINES["hashtags"]])
def test_hashtag_brand_list_is_not_a_model(phones, text):
    assert phones.find_models(text) == []
