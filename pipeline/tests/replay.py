import json
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

FIXTURES = Path(__file__).parents[2] / "worker" / "test" / "fixtures"
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
        variables = json.loads(form["variables"])
        if "phone" not in variables["params"]["bqf"]["query"].lower():
            empty = {"edges": [], "page_info": {"end_cursor": None, "has_next_page": False}}
            return SimpleNamespace(status_code=self.graphql_status, url=url,
                                   text=json.dumps({"data": {"marketplace_search": {"feed_units": empty}}}))
        bounds = variables["params"]["browse_request_params"]
        low, high = bounds["filter_price_lower_bound"] / 100, bounds["filter_price_upper_bound"] / 100
        text = self.graphql_body if self.graphql_body is not None else self._answer(low, high)
        return SimpleNamespace(status_code=self.graphql_status, url=url, text=text)

    @staticmethod
    def _answer(low: float, high: float) -> str:
        """Like Facebook: up to 24 saved listings whose price lies in the requested band."""
        if low <= 0 and high >= 2147483647:
            return (FIXTURES / "graphql_sylhet_point.json").read_text(encoding="utf-8")
        edges, seen = [], set()
        for name in ("graphql_sylhet_point.json", "graphql_sylhet_band_50k_80k.json"):
            doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            for edge in doc["data"]["marketplace_search"]["feed_units"]["edges"]:
                listing = edge["node"]["listing"]
                price = (listing.get("listing_price") or {}).get("amount")
                if price is not None and low <= float(price) <= high and listing["id"] not in seen:
                    seen.add(listing["id"])
                    edges.append(edge)
        page = {"edges": edges[:24], "page_info": {"end_cursor": "test-cursor", "has_next_page": True}}
        return json.dumps({"data": {"marketplace_search": {"feed_units": page}}})

    def get(self, url, **kwargs):
        self.urls.append(url)
        slug = re.match(r"https://www\.facebook\.com/marketplace/([^/]+)/search", url).group(1)
        if slug in self.status:
            return SimpleNamespace(status_code=self.status[slug], url=url, text="")
        if slug not in PAGES:
            return SimpleNamespace(status_code=404, url=url, text="")
        text = (FIXTURES / f"{PAGES[slug]}.html").read_text(encoding="utf-8")
        return SimpleNamespace(status_code=200, url=REDIRECTS.get(slug, url), text=text)
