import tomllib
from pathlib import Path

import pytest

from fcommerce_search.extract.rules import extract_offers

CASES = tomllib.loads((Path(__file__).parent / "fixtures" / "review_corpus.toml").read_text(encoding="utf-8"))["case"]


def unknown(value):
    return None if value == "" else value


@pytest.mark.parametrize("case", CASES, ids=[c["text"][:40] for c in CASES])
def test_review_corpus(case):
    offers = extract_offers(case["text"], case.get("replies", []))
    assert [(o.model, o.storage_gb, o.price) for o in offers] == [tuple(map(unknown, o)) for o in case["offers"]]
    for field in ("ram", "region", "condition"):
        if field in case:
            attr = "ram_gb" if field == "ram" else field
            assert {getattr(o, attr) for o in offers} == {unknown(case[field])}
