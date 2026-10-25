import copy
import json
import re
import time

from crawler.facebook.marketplace import parse_listing
from crawler.facebook.pages import GRAPHQL_URL, graphql_form, headers
from crawler.facebook.parse import walk
from crawler.outcomes import Outcome, classify
from crawler.transport import DROPS, HostBlocked

BOOTSTRAP_URL = "https://www.facebook.com/marketplace/london/search?query=phone"
FRIENDLY = "CometMarketplaceSearchContentContainerQuery"
PRELOADED = re.compile(r'ContentContainerQueryRelayPreloader[^"]*","queryID":"(\d+)","variables":')
LSD = re.compile(r'"LSD",\[\],\{"token":"([^"]+)"')
SESSION_SECONDS = 30 * 60
NO_UPPER_BOUND = 214748364700
PAGE_SIZE = 24
STATUS = {Outcome.OK: "ok", Outcome.NOTHING_NEW: "ok", Outcome.BLOCKED: "blocked", Outcome.SCHEMA_CHANGED: "schema_changed"}


def _object_at(text: str, start: int) -> dict:
    depth = 0
    for i in range(start, len(text)):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return json.loads(text[start:i + 1])
    raise ValueError("unterminated object")


class PointSearch:
    """Marketplace search at a point, radius and price band, logged out, through the page's own request."""

    def __init__(self, transport, clock=time.time):
        self._transport, self._clock = transport, clock
        self.doc_id = self.lsd = self.template = None
        self._since = 0.0

    def bootstrap(self) -> bool:
        try:
            html = self._transport.get(BOOTSTRAP_URL, headers={"accept-language": "en-US,en;q=0.9"}).text or ""
            found, lsd = PRELOADED.search(html), LSD.search(html)
            if not (found and lsd):
                return False
            self.doc_id, self.lsd, self.template = found.group(1), lsd.group(1), _object_at(html, found.end())
            params = self.template["params"]
            if not (isinstance(params["browse_request_params"], dict) and isinstance(params["bqf"], dict)):
                raise TypeError("template")
        except (HostBlocked, *DROPS, ValueError, KeyError, TypeError):
            self.doc_id = None
            return False
        self._since = self._clock()
        return True

    def search(self, keyword: str, lat: float, lon: float, radius_km: float, band=None):
        """(status, listings, full): full means the band held a whole page, so more may be inside it."""
        if (not self.doc_id or self._clock() - self._since > SESSION_SECONDS) and not self.bootstrap():
            return "unavailable", [], False
        variables = copy.deepcopy(self.template)
        variables.update(cursor=None, count=PAGE_SIZE, buyLocation={"latitude": lat, "longitude": lon})
        variables["params"]["bqf"]["query"] = keyword
        low, high = band or (None, None)
        variables["params"]["browse_request_params"].update(
            filter_location_latitude=lat, filter_location_longitude=lon, filter_radius_km=radius_km,
            # Facebook takes prices in minor units (poisha, pence).
            filter_price_lower_bound=round((low or 0) * 100),
            filter_price_upper_bound=NO_UPPER_BOUND if high is None else round(high * 100))
        form = graphql_form(self.doc_id, FRIENDLY, variables) | {"lsd": self.lsd}
        try:
            response = self._transport.post(GRAPHQL_URL, data=form,
                                            headers=headers(BOOTSTRAP_URL, FRIENDLY) | {"x-fb-lsd": self.lsd})
        except HostBlocked:
            return "blocked", [], False
        except DROPS:
            return "failed", [], False
        outcome = classify(response, ("marketplace_search", "feed_units"))
        if STATUS.get(outcome) != "ok":
            return STATUS.get(outcome, "failed"), [], False
        listings, seen = [], set()
        for line in response.text.splitlines():
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            for d in walk(doc):
                if (isinstance(d, dict) and d.get("__typename") == "GroupCommerceProductItem"
                        and "marketplace_listing_title" in d and d.get("id") not in seen):
                    seen.add(d["id"])
                    listings.append(parse_listing(d))
        return "ok", listings, len(listings) >= PAGE_SIZE
