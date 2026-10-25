import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

FIXTURES = Path(__file__).parent / "fixtures" / "marketplace"
PAGES = {"sylhet": "slug_sylhet", "london": "bootstrap_london", "dhaka": "dhaka_slug", "paris": "slug_paris",
         "moulvibazar": "slug_moulvibazar"}
REDIRECTS = {"moulvibazar": "https://www.facebook.com/marketplace/category/search/?query=iphone"}


class ReplayTransport:
    """Answers Marketplace search URLs from the saved, reduced logged-out pages; nothing reaches Facebook."""

    def __init__(self, status: dict[str, int] | None = None, graphql_status: int = 200, graphql_body: str | None = None):
        self.urls, self.status, self.posts = [], status or {}, []
        self.graphql_status, self.graphql_body = graphql_status, graphql_body

    def post(self, url, data=None, headers=None, **kwargs):
        form = {k: v[0] for k, v in parse_qs(data).items()} if isinstance(data, str) else dict(data or {})
        self.posts.append({"url": url, "form": form, "headers": headers or {}})
        bounds = json.loads(form["variables"])["params"]["browse_request_params"]
        banded = bounds["filter_price_lower_bound"] > 0 or bounds["filter_price_upper_bound"] < 214748364700
        name = "graphql_sylhet_band_50k_80k.json" if banded else "graphql_sylhet_point.json"
        text = self.graphql_body if self.graphql_body is not None else (FIXTURES / name).read_text(encoding="utf-8")
        return SimpleNamespace(status_code=self.graphql_status, url=url, text=text)

    def get(self, url, **kwargs):
        self.urls.append(url)
        slug = re.match(r"https://www\.facebook\.com/marketplace/([^/]+)/search", url).group(1)
        if slug in self.status:
            return SimpleNamespace(status_code=self.status[slug], url=url, text="")
        if slug not in PAGES:
            return SimpleNamespace(status_code=404, url=url, text="")
        text = (FIXTURES / f"{PAGES[slug]}.html").read_text(encoding="utf-8")
        return SimpleNamespace(status_code=200, url=REDIRECTS.get(slug, url), text=text)
