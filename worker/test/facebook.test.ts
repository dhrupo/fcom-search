import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { classify, parseListing, parseSearch, walk } from "../src/facebook.ts";

const FIXTURES = join(import.meta.dirname, "fixtures");
const fixture = (name: string) => readFileSync(join(FIXTURES, name), "utf8");
const golden = JSON.parse(fixture("parsed_golden.json"));
const URL_OK = "https://www.facebook.com/marketplace/sylhet/search?query=iphone";
const FEED = ["marketplace_search", "feed_units"];
const listingBlock = (id: string) =>
  `<script type="application/json" data-sjs>${JSON.stringify({ require: [{ marketplace_search: { feed_units: { edges: [
    { node: { listing: { __typename: "GroupCommerceProductItem", id, marketplace_listing_title: "iPhone 13",
      listing_price: { amount: "55000", formatted_amount: "৳55,000" } } } }] } } }] })}</script>`;

test("reduced_fixture_parses_like_the_original", () => {
  for (const [name, expected] of Object.entries<any>(golden)) {
    if (name.endsWith(".html")) {
      const [outcome, listings] = parseSearch(fixture(name));
      assert.deepEqual({ outcome, listings }, expected, name);
    } else {
      const doc = JSON.parse(fixture(name));
      const listings = [...walk(doc)].filter((d) => d.__typename === "GroupCommerceProductItem").map(parseListing);
      assert.deepEqual({ listings }, expected, name);
    }
  }
});

test("city_page_parses_only_the_listing_block", () => {
  const html = `<html><script>var marketplace_search = 1;</script>`
    + `<script type="application/json">{"marketplace_search": broken</script>`
    + `<script type="application/json">{"other": "no listings here"}</script>`
    + listingBlock("111") + listingBlock("111") + listingBlock("222")
    + `<script type="application/json">{"marketplace_search": {"x": {"__typename": "GroupCommerceProductItem", "id": "333"}}}</script></html>`;
  const [outcome, listings] = parseSearch(html);
  assert.equal(outcome, "ok");
  assert.deepEqual(listings.map((l) => [l.item_id, l.price]), [
    ["111", { amount: 55000, formatted: "৳55,000" }], ["222", { amount: 55000, formatted: "৳55,000" }]]);
});

test("price_zero_or_missing_is_unknown", () => {
  for (const listing_price of [{ amount: "0" }, { amount: "abc" }, null, undefined]) {
    assert.equal(parseListing({ id: 1, listing_price }).price, "unknown");
  }
});

test("rate_limit_json_with_spaces_is_blocked", () => {
  assert.equal(classify(200, URL_OK, 'for (;;);{"errors": [{"code": 1675004, "message": "slow down"}]}', FEED), "blocked");
  assert.equal(classify(429, URL_OK, "", FEED), "blocked");
});

test("login_page_is_blocked", () => {
  assert.equal(classify(200, "https://www.facebook.com/login/?next=x", "<html></html>"), "blocked");
  assert.equal(classify(200, URL_OK, "<html>You have been temporarily blocked</html>"), "blocked");
  assert.equal(classify(200, URL_OK, '{"require_login": true}', FEED), "blocked");
  assert.equal(classify(200, URL_OK, fixture("slug_sylhet.html")), "ok");
});

test("missing_feed_is_schema_changed", () => {
  assert.deepEqual(parseSearch("<html><script type=\"application/json\">{}</script></html>"), ["schema_changed", []]);
  assert.deepEqual(parseSearch('<script type="application/json">{"marketplace_search": "text"}</script>'), ["schema_changed", []]);
  assert.deepEqual(parseSearch('<script type="application/json">{"marketplace_search": {"cut off'), ["schema_changed", []]);
  assert.equal(classify(200, URL_OK, '{"data": {"marketplace_search": {"feed_units": {"edges": []}}}}', FEED), "schema_changed");
  assert.equal(classify(200, URL_OK, '{"data": {"viewer": {}}}', FEED), "schema_changed");
  assert.equal(classify(200, URL_OK, fixture("graphql_sylhet_point.json"), FEED), "ok");
  assert.equal(classify(200, URL_OK, '{"data": {"marketplace_search": {"feed_units": {"edges": [], "page_info": {}}}}}', FEED),
    "nothing_new");
  assert.equal(classify(500, URL_OK, "", FEED), "crash");
  assert.equal(classify(404, URL_OK, "", FEED), "gone");
});
