import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./d1.ts";
import { fixture, replayFetch } from "./replay.ts";
import { LiveSearch, scrub, type Event } from "../src/live.ts";
import { Places } from "../src/places.ts";

const NOW = 1792924206 + 3600;
const SYLHET: [number, number] = [24.8949, 91.8687];
const ORIGIN = "https://www.facebook.com";
const TOWNS: Record<string, [number, number]> = { "maulvi bazar": [91.7774, 24.4829], chhatak: [91.6694, 25.0381],
  "lalabazar sylhet bangladesh": [91.83, 24.86] };
const allowAll = { limit: async () => ({ success: true }) };

/** Photon stand-in for listing towns: answers by the query it is sent. */
function photon() {
  const asked: string[] = [];
  const fetch = async (url: string) => {
    const q = (new URL(url).searchParams.get("q") ?? "").replaceAll(",", "").toLowerCase();
    asked.push(q);
    const hit = Object.entries(TOWNS).find(([k]) => q.startsWith(k))?.[1];
    return Response.json({ features: hit ? [{ properties: { name: q, countrycode: "BD" }, geometry: { coordinates: hit } }] : [] });
  };
  return { fetch, asked };
}

function setup({ replay = replayFetch(), db = fakeD1({ geo: true }), now = () => NOW, names = [] as string[], towns = photon() } = {}) {
  const places = new Places(db, towns.fetch, "https://photon.komoot.io", now, allowAll);
  const live = new LiveSearch(db, replay.fetch, ORIGIN, places, now, async () => names);
  return { live, replay, db, towns, places };
}
const collect = async (events: AsyncIterable<Event>) => { const out: Event[] = []; for await (const e of events) out.push(e); return out; };
const results = (events: Event[]) => events.flatMap((e: any) => (e.type === "city" ? e.results : []));
type Wanted = { min_price?: number | null; max_price?: number | null; days?: number | null };

/** Basic mode as the page drives it: the search names the cities, then one request per city (Q14). */
async function basicSearch(live: LiveSearch, keyword: string, lat: number, lon: number, radiusKm: number, wanted: Wanted = {}) {
  const head = await collect(live.run(keyword, lat, lon, radiusKm, wanted));
  const start: any = head.find((e) => e.type === "start");
  const rest: Event[] = [];
  for (const city of start?.cities ?? []) rest.push(...await collect(live.city(start.search_id, city)));
  return [...head, ...rest];
}
const basic = (opts: Parameters<typeof setup>[0] = {}) => setup({ replay: replayFetch({ graphqlStatus: 500 }), ...opts });

test("events_stream_per_city", async () => {
  const events = await basicSearch(basic().live, "iphone", ...SYLHET, 10);
  assert.deepEqual(events.map((e) => e.type), ["mode", "start", "city", "done"]);
  const [, start, city]: any[] = events;
  assert.deepEqual(start.cities, ["Sylhet"]);
  // found counts the listings shown: 24 fetched, 22 of them inside 10 km.
  assert.deepEqual([city.city, city.status, city.found], ["Sylhet", "ok", 22]);
  const first = city.results[0];
  for (const key of ["item_id", "title", "price", "price_text", "town", "created_at", "photo", "permalink", "distance_km"]) {
    assert.ok(key in first, key);
  }
  assert.ok(first.permalink.startsWith("https://www.facebook.com/marketplace/item/"));
});

for (const [radius, expected] of [[20, 9], [100, 10]]) {
  test(`filters_price_days_radius[${radius}]`, async () => {
    const found = results(await basicSearch(basic().live, "iphone", ...SYLHET, radius, { min_price: 10000, max_price: 30000, days: 7 }));
    assert.equal(found.length, expected);
    assert.ok(found.every((r) => r.price >= 10000 && r.price <= 30000 && r.created_at >= NOW - 7 * 86400));
    assert.equal(found.some((r) => r.town === "Maulvi Bazar"), radius === 100);
  });
}

test("pool_grows_and_repeat_search_reuses_it", async () => {
  const { live, replay } = basic();
  const first = results(await basicSearch(live, "iphone", ...SYLHET, 10));
  const again = results(await basicSearch(live, "iphone", ...SYLHET, 10));
  assert.equal(replay.gets().filter((g) => g.url.includes("/marketplace/sylhet/")).length, 1);
  assert.deepEqual(again.map((r) => r.item_id).sort(), first.map((r) => r.item_id).sort());
});

test("blocked_city_reports_and_others_continue", async () => {
  const { live } = basic({ replay: replayFetch({ graphqlStatus: 500, status: { shillong: 429 } }) });
  const events = await basicSearch(live, "iphone", ...SYLHET, 250);
  const statuses = Object.fromEntries(events.filter((e: any) => e.type === "city").map((e: any) => [e.city, e.status]));
  assert.equal(statuses.Shillong, "blocked");
  assert.equal(statuses.Sylhet, "ok");
  assert.equal(Object.keys(statuses).length, 8, "every listed city is still searched");
  assert.equal(events.filter((e) => e.type === "done").length, 1);
  assert.equal(events.at(-2)!.type, "city", "done comes after the last city");
  assert.equal(events.at(-1)!.type, "done");
});

test("city_without_an_address_is_remembered", async () => {
  const { live, places, db } = basic();
  await db.prepare("INSERT INTO cities (name, ascii, name_key, ascii_key, country, lat, lon, population) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
    .bind("Moulvibazar", "Moulvibazar", "moulvibazar", "moulvibazar", "BD", 24.4829, 91.7774, 150000).run();
  const events = await basicSearch(live, "iphone", 24.4829, 91.7774, 10);
  assert.deepEqual(events.filter((e: any) => e.type === "city").map((e: any) => [e.city, e.status]), [["Moulvibazar", "no_address"]]);
  assert.equal(await places.slugStatus("Moulvibazar", "BD"), false);
});

test("listing_has_no_seller_name_and_scrubs_title", async () => {
  const events = await basicSearch(basic({ names: ["Rahim Uddin"] }).live, "iphone", ...SYLHET, 10);
  const text = JSON.stringify(events);
  assert.ok(!text.includes("marketplace_listing_seller") && !JSON.stringify(results(events)[0]).includes("seller"));
  assert.equal(scrub("iPhone 13 call 01711-223344 @rahim_shop Rahim Uddin", ["Rahim Uddin"]), "iPhone 13 call ••• ••• •••");
});

test("bengali_digit_phone_is_scrubbed", () => {
  assert.equal(scrub("কল ০১৭১১২২৩৩৪৪ এখনই"), "কল ••• এখনই");
  assert.equal(scrub("নাম রহিম উদ্দিন", ["রহিম উদ্দিন"]), "নাম •••");
});

test("network_drop_is_reported", async () => {
  const drops = { ...replayFetch({ graphqlStatus: 500 }) };
  const real = drops.fetch;
  drops.fetch = async (url: string, init?: RequestInit) => {
    if (url.includes("/marketplace/sylhet/")) throw new TypeError("connection reset");
    return real(url, init);
  };
  const events = await basicSearch(basic({ replay: drops as any }).live, "iphone", ...SYLHET, 10);
  assert.deepEqual(events.filter((e: any) => e.type === "city").map((e: any) => e.status), ["failed"]);
});

test("point_search_is_one_request", async () => {
  const { live, replay } = setup();
  const events: any[] = await collect(live.run("iphone", ...SYLHET, 40));
  assert.deepEqual(events.map((e) => e.type), ["start", "city", "done"]);
  assert.deepEqual([replay.gets().length, replay.posts().length], [1, 1]);
  assert.ok(events[0].mode === "point" && events.at(-1).search_id && events.at(-1).can_load_more === true);
  assert.ok(events[1].found === events[1].results.length && events[1].found > 0);
});

test("point_search_stays_under_50_d1_queries", async () => {
  const { live, db } = setup();
  const before = db.counter.queries;
  await collect(live.run("iphone", ...SYLHET, 100));
  assert.ok(db.counter.queries - before < 50, `${db.counter.queries - before} D1 queries`);
});

test("falls_back_to_city_addresses", async () => {
  const { live } = basic();
  const head: any[] = await collect(live.run("iphone", ...SYLHET, 10));
  assert.deepEqual(head[0], { type: "mode", mode: "basic" });
  assert.deepEqual([head[1].type, head[1].cities, head.length], ["start", ["Sylhet"], 2]);
  const tail: any[] = await collect(live.city(head[1].search_id, "Sylhet"));
  assert.deepEqual(tail.map((e) => [e.type, e.city ?? null, e.status ?? null]), [["city", "Sylhet", "ok"], ["done", null, null]]);
  assert.equal(tail.at(-1).can_load_more, false);
});

test("city_searches_one_listed_city_once", async () => {
  const { live } = basic();
  const head: any[] = await collect(live.run("iphone", ...SYLHET, 10));
  const id = head[1].search_id;
  assert.deepEqual(await collect(live.city(id, "Dhaka")), [{ type: "error", error: "this city is not part of the search" }]);
  await collect(live.city(id, "Sylhet"));
  assert.deepEqual(await collect(live.city(id, "Sylhet")), [{ type: "error", error: "this city is not part of the search" }]);
  assert.equal((await collect(live.city("f".repeat(32), "Sylhet")))[0].type, "error");
});

test("load_more_fetches_new_band_without_repeats", async () => {
  const { live, replay } = setup();
  const first: any[] = await collect(live.run("iphone", ...SYLHET, 40));
  const shown = new Set(results(first).map((r) => r.item_id));
  let more: any[] = [], fresh: any[] = [];
  while (!fresh.length && replay.posts().length <= 5) {
    more = await collect(live.more(first.at(-1).search_id));
    fresh = results(more);
  }
  const band = JSON.parse(replay.posts().at(-1)!.form.variables).params.browse_request_params;
  assert.ok(band.filter_price_lower_bound > 0 || band.filter_price_upper_bound < 214748364700);
  assert.ok(fresh.length && !fresh.some((r) => shown.has(r.item_id)));
  assert.ok(more.at(-1).type === "done" && more.at(-1).total === fresh.length);
});

test("load_more_keeps_the_users_price_range", async () => {
  const { live } = setup();
  const first: any[] = await collect(live.run("iphone", ...SYLHET, 40, { min_price: 50_000, max_price: 80_000 }));
  const more = await collect(live.more(first.at(-1).search_id));
  assert.ok([...results(first), ...results(more)].every((r) => r.price >= 50_000 && r.price <= 80_000));
});

test("load_more_stops_after_five", async () => {
  // Every band answers a full page, so bands never run out and only the cap can stop "more".
  const { live, replay } = setup({ replay: replayFetch({ graphqlBody: fixture("graphql_sylhet_point.json") }) });
  const id = (await collect(live.run("iphone", ...SYLHET, 40))).at(-1)!.search_id as string;
  const dones: any[] = [];
  for (let i = 0; i < 6; i++) dones.push((await collect(live.more(id))).at(-1));
  assert.deepEqual(dones.map((d) => d.can_load_more), [true, true, true, true, false, false]);
  assert.equal(replay.posts().length, 1 + 5);
});

test("unknown_or_expired_search_id", async () => {
  const now = Object.assign(() => now.t, { t: NOW });
  const { live } = setup({ now });
  const id = (await collect(live.run("iphone", ...SYLHET, 40))).at(-1)!.search_id as string;
  assert.deepEqual(await collect(live.more("not-a-search")), [{ type: "error", error: "this search has expired; search again" }]);
  now.t += 31 * 60;
  assert.equal((await collect(live.more(id)))[0].type, "error");
});

test("search_state_survives_a_new_isolate", async () => {
  const replay = replayFetch(), db = fakeD1({ geo: true });
  const id = (await collect(setup({ replay, db }).live.run("iphone", ...SYLHET, 40))).at(-1)!.search_id as string;
  const more = await collect(setup({ replay, db }).live.more(id));
  assert.equal(more.at(-1)!.type, "done");
  assert.equal(replay.posts().length, 2);
});

test("expired_searches_and_old_listings_are_pruned", async () => {
  const now = Object.assign(() => now.t, { t: NOW });
  const { live, db } = setup({ now });
  await collect(live.run("iphone", ...SYLHET, 40));
  now.t += 61 * 86400;
  await collect(live.run("phone case", ...SYLHET, 40));
  const searches = await db.prepare("SELECT count(*) AS n FROM searches").first<{ n: number }>();
  const old = await db.prepare("SELECT count(*) AS n FROM listings WHERE fetched_at < ?").bind(now.t - 60 * 86400).first<{ n: number }>();
  assert.deepEqual([searches?.n, old?.n], [1, 0]);
});

test("new_price_range_fetches_again", async () => {
  const { live, replay } = setup();
  await collect(live.run("iphone", ...SYLHET, 40));
  await collect(live.run("iphone", ...SYLHET, 40));
  await collect(live.run("iphone", ...SYLHET, 40, { min_price: 50_000, max_price: 80_000 }));
  assert.equal(replay.posts().length, 2);
});

test("point_searches_with_other_keywords_do_not_mix", async () => {
  const { live } = setup();
  const phones = new Set(results(await collect(live.run("iphone", ...SYLHET, 40))).map((r) => r.item_id));
  const other = results(await collect(live.run("bicycle", ...SYLHET, 40)));
  assert.ok(phones.size && !other.some((r) => phones.has(r.item_id)));
});

test("same_item_found_by_two_searches_stays_in_both", async () => {
  const db = fakeD1({ geo: true });
  const point = setup({ db }).live;
  const first = new Set(results(await collect(point.run("iphone", ...SYLHET, 40))).map((r) => r.item_id));
  const cityIds = results(await basicSearch(basic({ db }).live, "iphone", 24.89, 91.87, 40)).map((r) => r.item_id);
  const again = new Set(results(await collect(point.run("iphone", ...SYLHET, 40))).map((r) => r.item_id));
  assert.deepEqual(again, first);
  assert.ok(cityIds.length);
});

test("town_with_a_common_name_uses_the_one_nearest_the_search", async () => {
  const { live } = setup();
  const near = async (town: string, at: [number, number]) => (await live.townPoints([town], at)).get(town);
  assert.ok(Math.abs((await near("Cambridge, MA", [42.3736, -71.1097]))![0] - 42.37) <= 0.1);
  assert.ok(Math.abs((await near("Cambridge, United Kingdom", [52.2, 0.12]))![0] - 52.2) <= 0.1);
  assert.equal(await near("Some Tiny Village", SYLHET), null);
  assert.deepEqual(await near("Maulvi Bazar", SYLHET), [24.4829, 91.7774]);
});

test("scrub_keeps_ordinary_titles", () => {
  assert.equal(scrub("Alienware laptop", ["Ali"]), "Alienware laptop");
  assert.equal(scrub("Ali bhai's iPhone", ["Ali"]), "••• bhai's iPhone");
  assert.equal(scrub("Corolla 2015 2016 2017"), "Corolla 2015 2016 2017");
  assert.equal(scrub("Size 40 41 42 43"), "Size 40 41 42 43");
  for (const phone of ["01711-223344", "01711223344", "+44 7700 900123", "+8801711223344"]) {
    assert.equal(scrub(`call ${phone} now`), "call ••• now");
  }
});

test("cached_search_offers_more_only_if_the_page_was_full", async () => {
  const doc = JSON.parse(fixture("graphql_sylhet_point.json"));
  doc.data.marketplace_search.feed_units.edges = doc.data.marketplace_search.feed_units.edges.slice(0, 5);
  const { live } = setup({ replay: replayFetch({ graphqlBody: JSON.stringify(doc) }) });
  assert.equal((await collect(live.run("iphone", ...SYLHET, 40))).at(-1)!.can_load_more, false);
  assert.equal((await collect(live.run("iphone", ...SYLHET, 40))).at(-1)!.can_load_more, false);
});

test("failed_load_more_keeps_its_band", async () => {
  const replay = replayFetch(), db = fakeD1({ geo: true });
  const id = (await collect(setup({ replay, db }).live.run("iphone", ...SYLHET, 40))).at(-1)!.search_id as string;
  const state = async () => JSON.parse((await db.prepare("SELECT state FROM searches WHERE id = ?").bind(id).first<{ state: string }>())!.state);
  const before = await state();
  const failed: any[] = await collect(setup({ replay: replayFetch({ graphqlStatus: 429 }), db }).live.more(id));
  assert.equal(failed[0].status, "blocked");
  const after = await state();
  assert.deepEqual([after.bands, after.more_left], [before.bands, 5]);
});

test("two_load_more_at_once_never_crash", async () => {
  const { live } = setup();
  const id = (await collect(live.run("iphone", ...SYLHET, 40))).at(-1)!.search_id as string;
  const outs = await Promise.all([1, 2, 3, 4].map(() => collect(live.more(id))));
  assert.ok(outs.every((o: any[]) => o.at(-1).type === "done"));
  const posts = outs.flat().filter((e: any) => e.type === "city").length;
  assert.ok(posts <= 2, "each band is fetched by one click only");
});

test("each_unknown_town_is_looked_up_once_per_search", async () => {
  const towns = photon();
  await collect(setup({ towns }).live.run("iphone", ...SYLHET, 40));
  assert.ok(towns.asked.length > 0);
  assert.equal(towns.asked.length, new Set(towns.asked).size);
});

const graphqlWith = (change: (listings: any[]) => void) => {
  const doc = JSON.parse(fixture("graphql_sylhet_point.json"));
  change(doc.data.marketplace_search.feed_units.edges.map((e: any) => e.node.listing));
  return JSON.stringify(doc);
};

test("city_results_skip_listings_an_earlier_city_showed", async () => {
  const { live } = basic({ replay: replayFetch({ graphqlStatus: 500, pages: { shillong: "slug_sylhet" } }) });
  const events: any[] = await basicSearch(live, "iphone", ...SYLHET, 250);
  const found = Object.fromEntries(events.filter((e) => e.type === "city").map((e) => [e.city, e.found]));
  assert.ok(found.Sylhet > 0);
  assert.equal(found.Shillong, 0);
  const ids = results(events).map((r) => r.item_id);
  assert.equal(ids.length, new Set(ids).size);
});

test("city_page_older_than_ten_minutes_is_fetched_again", async () => {
  const now = Object.assign(() => now.t, { t: NOW });
  const { live, replay } = basic({ now });
  await basicSearch(live, "iphone", ...SYLHET, 10);
  now.t += 5 * 60;
  const head: any[] = await collect(live.run("iphone", ...SYLHET, 10));
  now.t += 6 * 60;
  await collect(live.city(head[1].search_id, "Sylhet"));
  assert.equal(replay.gets().filter((g) => g.url.includes("/marketplace/sylhet/")).length, 2);
});

test("city_results_need_every_keyword_word", async () => {
  assert.equal(results(await basicSearch(basic().live, "iphone zzzq", ...SYLHET, 10)).length, 0);
});

test("listing_without_a_price_is_left_out_of_a_price_range", async () => {
  const graphqlBody = graphqlWith((l) => { l[0].listing_price = { amount: "0", formatted_amount: "" }; });
  const unpriced = JSON.parse(graphqlBody).data.marketplace_search.feed_units.edges[0].node.listing.id;
  const { live } = setup({ replay: replayFetch({ graphqlBody }) });
  const any = results(await collect(live.run("iphone", ...SYLHET, 100)));
  const ranged = results(await collect(live.run("iphone", ...SYLHET, 100, { min_price: 1, max_price: 1e9 })));
  assert.ok(any.some((r) => r.item_id === unpriced && r.price === null));
  assert.ok(!ranged.some((r) => r.item_id === unpriced));
});

test("at_most_eight_unknown_towns_are_looked_up", async () => {
  const graphqlBody = graphqlWith((l) => l.forEach((x, i) => {
    x.location = { reverse_geocode: { city: `Zzq Hamlet ${i}` } };
  }));
  const towns = photon();
  const shown = results(await collect(setup({ replay: replayFetch({ graphqlBody }), towns }).live.run("iphone", ...SYLHET, 40)));
  assert.equal(towns.asked.length, 8);
  assert.equal(shown.length, 24, "unknown towns are shown without km");
  assert.ok(shown.every((r) => r.distance_km === null));
});

test("full_page_of_one_price_offers_no_more", async () => {
  const graphqlBody = graphqlWith((l) => l.forEach((x) => { x.listing_price = { amount: "50000", formatted_amount: "৳50,000" }; }));
  const { live } = setup({ replay: replayFetch({ graphqlBody }) });
  assert.equal((await collect(live.run("iphone", ...SYLHET, 40, { min_price: 50000 }))).at(-1)!.can_load_more, false);
});

test("scrub_respects_name_edges_and_longest_names", () => {
  assert.equal(scrub("Bali trip", ["Ali"]), "Bali trip");
  assert.equal(scrub("Rahim Uddin here", ["Rahim", "Rahim Uddin"]), "••• here");
  assert.equal(scrub("কল +৮৮০ ১৭১১ ২২৩৩৪৪ এখনই"), "কল ••• এখনই");
});
