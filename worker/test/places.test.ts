import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./d1.ts";
import { Places, slug } from "../src/places.ts";
import { citiesByPrefix } from "../src/geo.ts";

type Feature = [name: string, country: string, code: string, lon: number, lat: number, alias?: string];
const WORLD: Feature[] = [
  ["London", "United Kingdom", "GB", -0.1276, 51.5073], ["London", "Canada", "CA", -81.2453, 42.9849],
  ["Chittaguppa", "India", "IN", 77.1, 17.8], ["Chattogram", "Bangladesh", "BD", 91.8, 22.3, "Chittagong"],
  ["Sreemangal", "Bangladesh", "BD", 91.73, 24.31],
];
const geojson = (features: Feature[]) => ({ features: features.map(([name, country, countrycode, lon, lat]) =>
  ({ properties: { name, country, countrycode }, geometry: { coordinates: [lon, lat] } })) });

/** Photon stand-in: answers by the query it is sent; unknown text gives no features. */
function fakePhoton(body?: unknown) {
  const urls: string[] = [];
  const fetch = async (url: string) => {
    urls.push(url);
    if (body !== undefined) return Response.json(body);
    const u = new URL(url);
    if (u.pathname === "/reverse") return Response.json(geojson([["Sylhet", "Bangladesh", "BD", 91.8687, 24.8949]]));
    const q = (u.searchParams.get("q") ?? "").toLowerCase();
    return Response.json(geojson(WORLD.filter(([name, , , , , alias]) =>
      [name, alias ?? ""].some((n) => n.toLowerCase().startsWith(q.slice(0, 4))))));
  };
  return { fetch, urls };
}
const allowAll = { limit: async () => ({ success: true }) };
const clock = () => { const c = Object.assign(() => c.now, { now: 1000 }); return c; };
const db = fakeD1({ geo: true });
const places = (photon = fakePhoton(), limit = allowAll, now = clock(), store = fakeD1({ geo: true })) =>
  new Places(store, photon.fetch, "https://photon.komoot.io", now, limit);

test("suggestions_are_cached_and_rate_limited", async () => {
  const photon = fakePhoton(), p = places(photon);
  const first = await p.suggest("sreem");
  assert.deepEqual(first.map((s) => [s.name, s.countrycode]), [["Sreemangal", "BD"]]);
  assert.deepEqual(await p.suggest("SREEM "), first);
  assert.equal(photon.urls.length, 1);
  assert.ok(photon.urls[0].startsWith("https://photon.komoot.io/api/?") && photon.urls[0].includes("q=sreem"));
  const refused = fakePhoton(), limited = places(refused, { limit: async () => ({ success: false }) });
  assert.deepEqual(await limited.suggest("sreem"), [], "over the global Photon limit: bundled list only");
  assert.equal(refused.urls.length, 0);
});

test("photon_is_asked_only_when_the_bundled_list_is_short", async () => {
  const photon = fakePhoton(), p = places(photon);
  const kuala = await p.suggest("kuala");
  assert.deepEqual(kuala.slice(0, 2).map((s) => [s.name, s.country, s.countrycode]),
    [["Kuala Lumpur", "Malaysia", "MY"], ["Kuala Terengganu", "Malaysia", "MY"]]);
  assert.equal(photon.urls.length, 0);
  const london = await p.suggest("londo");
  assert.deepEqual(london.slice(0, 2).map((s) => [s.name, s.countrycode]), [["London", "GB"], ["London", "CA"]]);
  assert.equal(photon.urls.length, 1, "3 bundled matches are short: Photon is asked");
  const bundledLondon = await citiesByPrefix(db, "london", 1);
  assert.deepEqual([london[0].lat, london[0].country], [bundledLondon[0].lat, "United Kingdom"], "our own row wins a duplicate");
  assert.equal((await p.suggest("sao pa"))[0].name, "São Paulo");
});

test("long_or_empty_query_is_not_sent", async () => {
  const photon = fakePhoton(), p = places(photon);
  assert.deepEqual([await p.suggest(""), await p.suggest("x")], [[], []]);
  await p.suggest("q".repeat(500));
  assert.equal(photon.urls.length, 1);
  assert.ok(photon.urls.every((u) => u.length < 400));
});

test("bad_photon_answer_is_an_empty_list", async () => {
  const bundledOnly = await places(fakePhoton({ features: [] })).suggest("chittag");
  for (const body of [{ unexpected: true }, ["a", "list"], { features: "nope" }, { features: [{ properties: null }] }]) {
    assert.deepEqual(await places(fakePhoton(body)).suggest("chittag"), bundledOnly);
  }
  const down = new Places(fakeD1({ geo: true }), async () => { throw new TypeError("network"); }, "https://photon.komoot.io", clock(), allowAll);
  assert.deepEqual(await down.suggest("chittag"), bundledOnly);
});

test("slug_that_redirects_is_remembered_as_bad", async () => {
  const store = fakeD1({ geo: true }), p = places(fakePhoton(), allowAll, clock(), store);
  assert.equal(await p.slugStatus("Maulvi Bazar", "BD"), null);
  assert.deepEqual([slug("Kuala Lumpur"), slug("Brāhmanbāria")], ["kualalumpur", "brahmanbaria"]);
  await p.rememberSlug("Maulvi Bazar", "BD", false);
  await p.rememberSlug("Sylhet", "BD", true);
  const again = places(fakePhoton(), allowAll, clock(), store);
  assert.deepEqual([await again.slugStatus("Maulvi Bazar", "BD"), await again.slugStatus("Sylhet", "BD")], [false, true]);
});

test("small_town_uses_nearest_big_city", async () => {
  const p = places();
  assert.deepEqual((await p.citiesToSearch(24.4829, 91.7774, 10)).map((c) => c.name), ["Sylhet"]);
  const nearSylhet = (await p.citiesToSearch(24.8949, 91.8687, 100)).map((c) => c.name);
  assert.ok(nearSylhet[0] === "Sylhet" && nearSylhet.length <= 8);
});

test("cities_with_a_bad_slug_are_skipped", async () => {
  const p = places();
  await p.rememberSlug("Sylhet", "BD", false);
  assert.ok(!(await p.citiesToSearch(24.8949, 91.8687, 100)).some((c) => c.name === "Sylhet"));
});

test("suggestions_rank_big_cities_first_and_add_bundled_matches", async () => {
  const photon = fakePhoton(), p = places(photon);
  assert.deepEqual((await p.suggest("chittag")).slice(0, 2).map((s) => s.name), ["Chattogram", "Chittaguppa"]);
  const sent = new URL(photon.urls[0]);
  assert.deepEqual(sent.searchParams.getAll("osm_tag"), ["place:city", "place:town"]);
  const many = geojson(Array.from({ length: 10 }, (_, i): Feature => [`Zzqtown ${i}`, "Nowhere", "ZZ", i, i]));
  assert.equal((await places(fakePhoton(many)).suggest("zzqtown")).length, 6);
  const kuala = await places(fakePhoton({ features: [] })).suggest("kuala");
  assert.deepEqual([kuala[0].name, kuala[0].country, kuala[0].countrycode], ["Kuala Lumpur", "Malaysia", "MY"]);
});

test("reverse_is_cached", async () => {
  const photon = fakePhoton(), p = places(photon);
  const near = await p.reverse(24.89491, 91.86871);
  assert.deepEqual([near?.name, near?.countrycode], ["Sylhet", "BD"]);
  await p.reverse(24.8949, 91.8687);
  assert.equal(photon.urls.length, 1, "points within ~100 m share one cache row");
  assert.ok(new URL(photon.urls[0]).pathname === "/reverse");
});

test("old_photon_cache_rows_are_pruned", async () => {
  const photon = fakePhoton(), now = clock(), store = fakeD1({ geo: true }), p = places(photon, allowAll, now, store);
  await p.suggest("sreem");
  await p.suggest("chittag");
  now.now += 31 * 86400;
  await p.suggest("sreem");
  assert.equal(photon.urls.length, 3, "a 31-day-old answer is fetched again");
  const rows = await store.prepare("SELECT key FROM photon_cache").all<{ key: string }>();
  assert.deepEqual(rows.results.map((r) => new URL("https://x" + r.key).searchParams.get("q")), ["sreem"]);
});
