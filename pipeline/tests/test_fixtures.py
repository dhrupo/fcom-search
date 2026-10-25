import os
import re
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def test_fixtures_hold_no_real_names():
    map_path = os.environ.get("FCS_SCRUB_MAP")
    if not map_path:
        pytest.skip("set FCS_SCRUB_MAP to the scrub map kept outside the repo")
    names = [n for n in Path(map_path).read_text(encoding="utf-8").splitlines() if n]
    assert names, "scrub map is empty"
    files = sorted(p for p in FIXTURES.rglob("*") if p.is_file())
    assert files, "no fixtures found"
    pattern = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, names)) + r")(?!\w)", re.IGNORECASE)
    leaks = [f"{f.name}: {m.group(1)[:2]}…" for f in files if (m := pattern.search(f.read_text(errors="replace")))]
    assert not leaks
