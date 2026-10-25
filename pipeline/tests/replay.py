import re
from pathlib import Path
from types import SimpleNamespace

FIXTURES = Path(__file__).parent / "fixtures" / "marketplace"
PAGES = {"sylhet": "slug_sylhet", "london": "london_slug", "dhaka": "dhaka_slug", "paris": "slug_paris",
         "moulvibazar": "slug_moulvibazar"}
REDIRECTS = {"moulvibazar": "https://www.facebook.com/marketplace/category/search/?query=iphone"}


class ReplayTransport:
    """Answers Marketplace search URLs from the saved, reduced logged-out pages; nothing reaches Facebook."""

    def __init__(self, status: dict[str, int] | None = None):
        self.urls, self.status = [], status or {}

    def get(self, url, **kwargs):
        self.urls.append(url)
        slug = re.match(r"https://www\.facebook\.com/marketplace/([^/]+)/search", url).group(1)
        if slug in self.status:
            return SimpleNamespace(status_code=self.status[slug], url=url, text="")
        if slug not in PAGES:
            return SimpleNamespace(status_code=404, url=url, text="")
        text = (FIXTURES / f"{PAGES[slug]}.html").read_text(encoding="utf-8")
        return SimpleNamespace(status_code=200, url=REDIRECTS.get(slug, url), text=text)
