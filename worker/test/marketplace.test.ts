import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./d1.ts";
import { fixture, replayFetch } from "./replay.ts";
import { listingsIn } from "../src/facebook.ts";
import { PointSearch } from "../src/marketplace.ts";

const SYLHET: [number, number] = [24.8949, 91.8687];
const ORIGIN = "https://www.facebook.com";
const clock = () => { const c = Object.assign(() => c.now, { now: 1_792_927_806 }); return c; };
const pointSearch = (replay = replayFetch(), now = clock()) => new PointSearch(fakeD1(), replay.fetch, ORIGIN, now);
const variablesOf = (form: Record<string, string>) => JSON.parse(form.variables);

test("reduced_graphql_fixture_parses", () => {
  const ids = (name: string) => listingsIn([JSON.parse(fixture(name))]);
  const point = ids("graphql_sylhet_point.json"), band = ids("graphql_sylhet_band_50k_80k.json");
  assert.deepEqual([point.length, band.length], [24, 24]);
  assert.ok(band.every((l) => l.price !== "unknown" && l.price.amount >= 50_000 && l.price.amount <= 80_000));
  const inBand = new Set(point.filter((l) => l.price !== "unknown" && l.price.amount >= 50_000 && l.price.amount <= 80_000)
    .map((l) => l.item_id));
  const bandIds = new Set(band.map((l) => l.item_id));
  assert.deepEqual(new Set(point.map((l) => l.item_id).filter((id) => bandIds.has(id))), inBand);
  assert.equal([...bandIds].filter((id) => !point.some((l) => l.item_id === id)).length, 18);
});

test("bootstrap_reads_request_id_token_and_template", async () => {
  const replay = replayFetch(), ps = pointSearch(replay);
  await ps.search("iphone", ...SYLHET, 40);
  const sent = replay.posts()[0];
  assert.deepEqual([sent.form.doc_id, sent.form.lsd], ["28792298773764420", "test-lsd-token"]);
  assert.ok("filter_radius_km" in variablesOf(sent.form).params.browse_request_params);
});

test("bootstrap_sends_chrome_navigation_headers", async () => {
  const replay = replayFetch();
  await pointSearch(replay).search("phone", ...SYLHET, 40);
  const headers = replay.gets()[0].headers;
  assert.match(headers["user-agent"], /Chrome\/\d+/);
  assert.match(headers["sec-ch-ua"], /Chromium/);
  assert.deepEqual([headers["sec-fetch-mode"], headers["sec-fetch-dest"], headers["upgrade-insecure-requests"]],
    ["navigate", "document", "1"]);
  assert.match(headers.accept, /^text\/html/);
});

test("bootstrap_stops_reading_after_tokens", async () => {
  let pulled = 0, cancelled = false;
  const page = fixture("bootstrap_london.html"), chunks = [page, ...Array(500).fill("x".repeat(1024))];
  const body = new ReadableStream({ pull(c) { if (pulled >= chunks.length) return c.close(); c.enqueue(new TextEncoder().encode(chunks[pulled++])); },
    cancel() { cancelled = true; } });
  const fetch = async (url: string, init: RequestInit = {}) =>
    init.method === "POST" ? replayFetch().fetch(url, init) : new Response(body, { status: 200 });
  const result = await new PointSearch(fakeD1(), fetch, ORIGIN, clock()).search("phone", ...SYLHET, 40);
  assert.equal(result.status, "ok");
  assert.ok(pulled < 10, `read ${pulled} of ${chunks.length} chunks`);
  assert.ok(cancelled, "the rest of the page is not downloaded");
});

test("point_search_sends_coordinates_radius_and_band", async () => {
  const replay = replayFetch();
  const { status, listings, full } = await pointSearch(replay).search("iphone", ...SYLHET, 40, [50_000, 80_000]);
  const sent = replay.posts().at(-1)!, variables = variablesOf(sent.form), browse = variables.params.browse_request_params;
  assert.equal(variables.params.bqf.query, "iphone");
  assert.deepEqual([browse.filter_location_latitude, browse.filter_location_longitude, browse.filter_radius_km], [...SYLHET, 40]);
  assert.deepEqual(variables.buyLocation, { latitude: SYLHET[0], longitude: SYLHET[1] });
  assert.deepEqual([browse.filter_price_lower_bound, browse.filter_price_upper_bound], [5_000_000, 8_000_000]);
  assert.ok(sent.form.lsd === "test-lsd-token" && sent.headers["x-fb-lsd"] === "test-lsd-token");
  assert.equal(sent.url, "https://www.facebook.com/api/graphql/");
  assert.deepEqual([status, listings.length, full], ["ok", 24, true]);
});

test("open_ended_band_uses_facebooks_maximum", async () => {
  const replay = replayFetch();
  await pointSearch(replay).search("iphone", ...SYLHET, 40, [1000, null]);
  const browse = variablesOf(replay.posts().at(-1)!.form).params.browse_request_params;
  assert.deepEqual([browse.filter_price_lower_bound, browse.filter_price_upper_bound], [100_000, 214748364700]);
});

test("session_reused_then_renewed", async () => {
  const replay = replayFetch(), now = clock(), db = fakeD1();
  const fresh = () => new PointSearch(db, replay.fetch, ORIGIN, now);
  await fresh().search("iphone", ...SYLHET, 40);
  await fresh().search("phone", ...SYLHET, 40);
  assert.equal(replay.gets().length, 1, "a new isolate reuses the session saved in D1");
  now.now += 31 * 60;
  await fresh().search("phone", ...SYLHET, 40);
  assert.equal(replay.gets().length, 2);
});

test("logged_out_form_only", async () => {
  const replay = replayFetch();
  await pointSearch(replay).search("iphone", ...SYLHET, 40);
  const { form, headers } = replay.posts().at(-1)!;
  assert.deepEqual([form.av, form.__user, form.fb_dtsg], ["0", "0", ""]);
  assert.equal(headers.cookie, "datr=test-browser-id; fr=test-fr", "only the bootstrap page's own cookies, no login");
});

test("schema_change_is_reported", async () => {
  for (const [body, status] of [['{"data": {}}', "schema_changed"], ['{"errors":[{"code":1675004}]}', "blocked"]]) {
    assert.equal((await pointSearch(replayFetch({ graphqlBody: body })).search("iphone", ...SYLHET, 40)).status, status);
  }
  assert.equal((await pointSearch(replayFetch({ graphqlStatus: 429 })).search("iphone", ...SYLHET, 40)).status, "blocked");
  assert.equal((await pointSearch(replayFetch({ graphqlStatus: 500 })).search("iphone", ...SYLHET, 40)).status, "failed");
  const offline = new PointSearch(fakeD1(), async () => { throw new TypeError("network"); }, ORIGIN, clock());
  assert.equal((await offline.search("iphone", ...SYLHET, 40)).status, "unavailable");
  const dropsPost = async (url: string, init: RequestInit = {}) => {
    if (init.method === "POST") throw new TypeError("network");
    return replayFetch().fetch(url, init);
  };
  assert.equal((await new PointSearch(fakeD1(), dropsPost, ORIGIN, clock()).search("iphone", ...SYLHET, 40)).status, "failed");
});

test("bootstrap_page_without_request_id_is_unavailable", async () => {
  assert.equal((await pointSearch(replayFetch({ status: { london: 200 } })).search("iphone", ...SYLHET, 40)).status, "unavailable");
});

test("template_without_query_slot_is_unavailable", async () => {
  const bootstrapHtml = fixture("bootstrap_london.html").replace('"bqf":', '"bqx":');
  assert.equal((await pointSearch(replayFetch({ bootstrapHtml })).search("iphone", ...SYLHET, 40)).status, "unavailable");
});

test("no_listings_is_an_ok_empty_answer", async () => {
  assert.deepEqual(await pointSearch().search("bicycle", ...SYLHET, 40), { status: "ok", listings: [], full: false });
});
