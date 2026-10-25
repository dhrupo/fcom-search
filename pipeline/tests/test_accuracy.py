import os
import tomllib
from pathlib import Path

from fcommerce_search.extract.rules import extract_offers

LABELLED = Path(__file__).parent / "fixtures" / "labelled"
TARGET = 0.90


def unknown(value):
    return None if value == "" else value


def test_at_least_90_percent_per_category():
    report = []
    for path in sorted(LABELLED.glob("*.toml")):
        posts = tomllib.loads(path.read_text(encoding="utf-8"))["post"]
        assert len(posts) >= 50, f"{path.name}: only {len(posts)} labelled posts"
        misses = []
        for post in posts:
            got = [(o.model, o.storage_gb, o.price)
                   for o in extract_offers(post["text"], listing_price=post.get("listing_price"))]
            want = [tuple(map(unknown, o)) for o in post["offers"]]
            if got != want:
                misses.append(f"  #{post['n']} ({post.get('note', post['source'])}): want {want}, got {got}")
        score = 1 - len(misses) / len(posts)
        report.append(f"{path.stem}: {score:.0%} ({len(posts) - len(misses)}/{len(posts)} posts exactly right)")
        report += misses
    assert report, "no labelled sets found"
    if target := os.environ.get("FCS_ACCURACY_REPORT"):
        Path(target).write_text("\n".join(report) + "\n", encoding="utf-8")
    scores = [line for line in report if not line.startswith("  ")]
    assert all(float(line.split(": ")[1].split("%")[0]) / 100 >= TARGET for line in scores), "\n".join(report)
