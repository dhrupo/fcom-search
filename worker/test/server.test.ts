import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./d1.ts";
import { replayFetch } from "./replay.ts";
import { makeHandler, type Env } from "../src/index.ts";
import { slug } from "../src/places.ts";

const NOW = 1792924206 + 3600;
const SITE = "https://fcomsearch.dhrupo.workers.dev";
const SYLHET = [24.8949, 91.8687];
const SEARCH = { keyword: "iphone", lat: SYLHET[0], lon: SYLHET[1], radius_km: 10, days: 7, turnstile: "pass" };
const TOWNS: Record<string, [number, number]> = { chhatak: [91.6694, 25.0381], "maulvi bazar": [91.7774, 24.4829],
  "lalabazar sylhet bangladesh": [91.83, 24.86] };

function limiter(allowed = Infinity) {
  const used = new Map<string, number>();
  return { used, limit: async ({ key }: { key: string }) => {
    used.set(key, (used.get(key) ?? 0) + 1);
    return { success: used.get(key)! <= allowed };
  } };
}

/** Upstream stand-ins: Facebook (replay), Photon (answers by query) and Turnstile (token "pass" is valid). */
function setup({ replay = replayFetch(), searchLimit = limiter(), secret = "test-secret" } = {}) {
  const verified: Record<string, string>[] = [];
  const upstream = async (url: string, init?: RequestInit) => {
    if (url.startsWith("https://challenges.cloudflare.com/")) {
      const form = Object.fromEntries(new URLSearchParams(String(init?.body)));
      verified.push(form);
      return Response.json({ success: form.secret === "test-secret" && form.response === "pass" });
    }
    if (url.startsWith("https://photon.test/")) {
      const q = (new URL(url).searchParams.get("q") ?? "").replaceAll(",", "").toLowerCase();
      const hit = Object.entries(TOWNS).find(([k]) => q.startsWith(k));
      return Response.json({ features: hit ? [{ properties: { name: hit[0].replace(/^./, (c) => c.toUpperCase()), countrycode: "BD",
        country: "Bangladesh" }, geometry: { coordinates: hit[1] } }] : [] });
    }
    return replay.fetch(url, init);
  };
  const env: Env = { DB: fakeD1({ geo: true }), ASSETS: { fetch: async () => new Response("<h1>fcommerce-search</h1>") },
    SEARCH_LIMIT: searchLimit, MORE_LIMIT: limiter(), PLACES_LIMIT: limiter(), PHOTON_LIMIT: limiter(),
    FB_ORIGIN: "https://www.facebook.com", PHOTON_ORIGIN: "https://photon.test", TURNSTILE_SECRET: secret };
  const handler = makeHandler({ fetch: upstream, now: () => NOW });
  const call = async (method: string, path: string, body?: unknown, headers: Record<string, string> = {}) => {
    const data = body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body);
    const response = await handler.fetch(new Request(SITE + path, { method, body: data,
      headers: { "cf-connecting-ip": "203.0.113.7", ...(data !== undefined ? { "content-type": "application/json" } : {}), ...headers } }), env);
    return { status: response.status, text: await response.text(), type: response.headers.get("content-type") };
  };
  return { call, replay, verified, env };
}
const lines = (text: string) => text.split("\n").filter(Boolean).map((l) => JSON.parse(l));
const ORIGIN = { origin: SITE };

test("streams_ndjson", async () => {
  const { call } = setup();
  const { status, text, type } = await call("POST", "/api/search", SEARCH, ORIGIN);
  const events = lines(text);
  assert.equal(status, 200);
  assert.match(type ?? "", /^application\/x-ndjson/);
  assert.deepEqual(events.map((e) => e.type), ["start", "city", "done"]);
  assert.ok(events[1].results.length && events.at(-1).search_id);
});

test("places_and_reverse", async () => {
  const { call } = setup();
  const { status, text } = await call("GET", "/api/places?q=chhatak");
  const found = JSON.parse(text);
  assert.equal(status, 200);
  assert.deepEqual([slug(found[0].name), Math.round(found[0].lat * 100) / 100], ["chhatak", 25.04]);
  assert.equal(found.filter((p: any) => slug(p.name) === "chhatak").length, 1);
  assert.equal((await call("GET", "/api/reverse?lat=91&lon=500")).status, 400);
  assert.equal((await call("GET", "/api/reverse?lat=24.89&lon=91.87")).status, 200);
});

test("foreign_origin_refused", async () => {
  const { call, replay } = setup();
  for (const origin of ["https://evil.example", "null", "http://fcomsearch.dhrupo.workers.dev"]) {
    assert.equal((await call("POST", "/api/search", SEARCH, { origin })).status, 403, origin);
    assert.equal((await call("GET", "/api/places?q=london", undefined, { origin })).status, 403, origin);
  }
  assert.equal(replay.sent.length, 0);
});

test("text_plain_post_refused", async () => {
  const { call } = setup();
  assert.equal((await call("POST", "/api/search", JSON.stringify(SEARCH), { "content-type": "text/plain" })).status, 415);
  assert.equal((await call("POST", "/api/search", JSON.stringify(SEARCH), { "content-type": "application/json; charset=utf-8" })).status, 200);
});

test("oversized_or_bad_input_refused", async () => {
  const { call, replay } = setup();
  const bads: unknown[] = [{ ...SEARCH, keyword: "" }, { ...SEARCH, keyword: "x".repeat(101) }, { ...SEARCH, lat: 95 },
    { ...SEARCH, lon: "east" }, { ...SEARCH, radius_km: 5 }, { ...SEARCH, radius_km: 101 }, { ...SEARCH, days: 10 },
    { ...SEARCH, min_price: -1 }, { ...SEARCH, lat: true }, { ...SEARCH, extra: 1 }, ["not", "an", "object"], "{broken",
    { ...SEARCH, turnstile: 5 }, { ...SEARCH, turnstile: "x".repeat(3000) }, { ...SEARCH, min_price: 1e13 }];
  for (const bad of bads) {
    assert.equal((await call("POST", "/api/search", typeof bad === "string" ? bad : JSON.stringify(bad))).status, 400, JSON.stringify(bad));
  }
  assert.equal((await call("POST", "/api/search", "{" + " ".repeat(20_000) + "}")).status, 413);
  for (const length of ["abc", "-1"]) {
    assert.equal((await call("POST", "/api/search", JSON.stringify(SEARCH), { "content-length": length })).status, 400, length);
  }
  assert.equal(replay.sent.length, 0);
});

test("search_without_valid_turnstile_refused", async () => {
  const { call, replay, verified } = setup();
  for (const turnstile of ["wrong", undefined]) {
    const response = await call("POST", "/api/search", { ...SEARCH, turnstile }, ORIGIN);
    assert.equal(response.status, 403, String(turnstile));
  }
  assert.equal(replay.sent.length, 0, "no Facebook request without a valid token");
  assert.deepEqual([verified[0].remoteip, verified[0].secret], ["203.0.113.7", "test-secret"]);
});

test("missing_turnstile_secret_fails_closed", async () => {
  const { call, replay } = setup({ secret: "" });
  assert.equal((await call("POST", "/api/search", SEARCH, ORIGIN)).status, 503);
  assert.equal(replay.sent.length, 0);
});

test("over_limit_gets_429", async () => {
  const searchLimit = limiter(6), { call } = setup({ searchLimit });
  const statuses: number[] = [];
  for (let i = 0; i < 7; i++) statuses.push((await call("POST", "/api/search", SEARCH, ORIGIN)).status);
  assert.deepEqual(statuses, [200, 200, 200, 200, 200, 200, 429]);
  assert.deepEqual([...searchLimit.used.keys()], ["203.0.113.7"]);
  const slow = await call("POST", "/api/search", SEARCH, ORIGIN);
  assert.deepEqual(JSON.parse(slow.text), { error: "slow down" });
});

test("more_streams_and_checks_input", async () => {
  const { call } = setup();
  const first = lines((await call("POST", "/api/search", { ...SEARCH, radius_km: 40 }, ORIGIN)).text);
  const searchId = first.at(-1).search_id;
  const { status, text } = await call("POST", "/api/more", { search_id: searchId }, ORIGIN);
  assert.equal(status, 200);
  assert.deepEqual(lines(text).map((e) => e.type), ["city", "done"]);
  for (const bad of [{ search_id: 5 }, { search_id: "x".repeat(100) }, { search_id: searchId, extra: 1 }, {}]) {
    assert.equal((await call("POST", "/api/more", bad)).status, 400, JSON.stringify(bad));
  }
  assert.equal((await call("POST", "/api/more", { search_id: searchId }, { origin: "https://evil.example" })).status, 403);
  assert.equal((await call("POST", "/api/more", JSON.stringify({ search_id: searchId }), { "content-type": "text/plain" })).status, 415);
});

test("city_searches_one_listed_city_once", async () => {
  const { call } = setup({ replay: replayFetch({ graphqlStatus: 500 }) });
  const head = lines((await call("POST", "/api/search", SEARCH, ORIGIN)).text);
  assert.deepEqual(head.map((e) => e.type), ["mode", "start"]);
  const searchId = head[1].search_id;
  const city = lines((await call("POST", "/api/city", { search_id: searchId, city: "Sylhet" }, ORIGIN)).text);
  assert.deepEqual(city.map((e) => e.type), ["city", "done"]);
  const again = lines((await call("POST", "/api/city", { search_id: searchId, city: "Sylhet" }, ORIGIN)).text);
  assert.deepEqual(again, [{ type: "error", error: "this city is not part of the search" }]);
  for (const bad of [{ search_id: searchId }, { search_id: searchId, city: "" }, { search_id: searchId, city: "x".repeat(101) },
    { search_id: "nope", city: "Sylhet" }, { search_id: searchId, city: "Sylhet", extra: 1 }]) {
    assert.equal((await call("POST", "/api/city", bad)).status, 400, JSON.stringify(bad));
  }
});

test("pages_and_unknown_api_paths", async () => {
  const { call } = setup();
  assert.equal((await call("GET", "/")).text, "<h1>fcommerce-search</h1>");
  assert.equal((await call("GET", "/api/nothing")).status, 404);
  assert.equal((await call("PUT", "/api/search", SEARCH, ORIGIN)).status, 405);
});
