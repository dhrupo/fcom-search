import type { DB } from "./geo.ts";
import { LiveSearch, type Event } from "./live.ts";
import type { Fetch } from "./marketplace.ts";
import { Places, type Limiter } from "./places.ts";

export interface Env {
  DB: DB; ASSETS: { fetch(request: Request): Promise<Response> };
  SEARCH_LIMIT: Limiter; MORE_LIMIT: Limiter; PLACES_LIMIT: Limiter; PHOTON_LIMIT: Limiter;
  FB_ORIGIN: string; PHOTON_ORIGIN: string; TURNSTILE_SECRET?: string; TURNSTILE_VERIFY_URL?: string;
}

const MAX_BODY = 10_000;
const DAYS = new Set([3, 7, 15, 30, 45, 60]);
const SEARCH_FIELDS = new Set(["keyword", "lat", "lon", "radius_km", "min_price", "max_price", "days", "turnstile"]);
const SEARCH_ID = /^[0-9a-f]{32}$/;
const SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
const NAMES_SECONDS = 10 * 60;

class BadRequest extends Error {}

const json = (status: number, payload: unknown) => Response.json(payload, { status });
const isObject = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

function number(value: unknown, low: number, high: number, name: string): number {
  if (!Number.isFinite(value) || (value as number) < low || (value as number) > high) throw new BadRequest(name);
  return value as number;
}

function text(value: unknown, max: number, name: string): string {
  if (typeof value !== "string" || !value.trim() || value.trim().length > max) throw new BadRequest(name);
  return value.trim();
}

function parseSearch(body: unknown) {
  if (!isObject(body) || Object.keys(body).some((k) => !SEARCH_FIELDS.has(k))) throw new BadRequest("body");
  const price = (key: string) => (body[key] == null ? null : number(body[key], 0, 1e12, key));
  if (body.days != null && !DAYS.has(body.days as number)) throw new BadRequest("days");
  if (body.turnstile != null && (typeof body.turnstile !== "string" || body.turnstile.length > 2048)) throw new BadRequest("turnstile");
  return { keyword: text(body.keyword, 100, "keyword"), lat: number(body.lat, -90, 90, "lat"), lon: number(body.lon, -180, 180, "lon"),
    radius_km: number(body.radius_km, 10, 100, "radius"), min_price: price("min_price"), max_price: price("max_price"),
    days: (body.days as number | undefined) ?? null, turnstile: (body.turnstile as string | undefined) ?? "" };
}

function searchId(body: Record<string, unknown>): string {
  if (typeof body.search_id !== "string" || !SEARCH_ID.test(body.search_id)) throw new BadRequest("search_id");
  return body.search_id;
}

function parseMore(body: unknown): string {
  if (!isObject(body) || Object.keys(body).join() !== "search_id") throw new BadRequest("body");
  return searchId(body);
}

function parseCity(body: unknown): [string, string] {
  if (!isObject(body) || Object.keys(body).sort().join() !== "city,search_id") throw new BadRequest("body");
  return [searchId(body), text(body.city, 100, "city")];
}

function ndjson(events: AsyncGenerator<Event>): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    async pull(controller) {
      try {
        const { value, done } = await events.next();
        if (done) return controller.close();
        controller.enqueue(encoder.encode(JSON.stringify(value) + "\n"));
      } catch {
        controller.enqueue(encoder.encode(JSON.stringify({ type: "error", error: "search failed" }) + "\n"));
        controller.close();
      }
    },
    // The page went away: stop the search instead of finishing it.
    async cancel() { await events.return(undefined); },
  });
  return new Response(body, { headers: { "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store" } });
}

async function readJson(request: Request): Promise<unknown> {
  const declared = request.headers.get("content-length");
  if (declared !== null && !/^\d+$/.test(declared)) throw new BadRequest("content-length");
  if (Number(declared) > MAX_BODY) throw new RangeError("too large");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).length > MAX_BODY) throw new RangeError("too large");
  try {
    return JSON.parse(raw || "null");
  } catch {
    throw new BadRequest("json");
  }
}

export function makeHandler({ fetch = globalThis.fetch.bind(globalThis) as Fetch, now = () => Date.now() / 1000 } = {}) {
  async function turnstileOk(env: Env, token: string, ip: string): Promise<boolean> {
    try {
      const answer = await fetch(env.TURNSTILE_VERIFY_URL || SITEVERIFY, { method: "POST",
        body: new URLSearchParams({ secret: env.TURNSTILE_SECRET!, response: token, remoteip: ip }).toString(),
        headers: { "content-type": "application/x-www-form-urlencoded" } });
      return (await answer.json() as { success?: boolean }).success === true;
    } catch {
      return false;
    }
  }

  let names: { at: number; list: string[] } | undefined;
  /** The private name list, kept in memory for a while: reading 529 rows per search costs CPU and the read quota. */
  async function privateNames(db: DB): Promise<string[]> {
    if (!names || now() - names.at > NAMES_SECONDS) {
      names = { at: now(), list: (await db.prepare("SELECT name FROM private_names").all<{ name: string }>()).results.map((r) => r.name) };
    }
    return names.list;
  }

  async function api(request: Request, env: Env, url: URL): Promise<Response> {
    // A page on another site can make the visitor's browser call us; only our own pages may.
    const origin = request.headers.get("origin");
    if (origin !== null && origin !== url.origin) return json(403, { error: "forbidden" });
    const ip = request.headers.get("cf-connecting-ip") ?? "unknown";
    const places = new Places(env.DB, fetch, env.PHOTON_ORIGIN, now, env.PHOTON_LIMIT);
    const live = () => new LiveSearch(env.DB, fetch, env.FB_ORIGIN, places, now, () => privateNames(env.DB));
    const slowDown = async (limiter: Limiter) => !(await limiter.limit({ key: ip })).success;

    if (request.method === "GET" && (url.pathname === "/api/places" || url.pathname === "/api/reverse")) {
      if (await slowDown(env.PLACES_LIMIT)) return json(429, { error: "slow down" });
      if (url.pathname === "/api/places") return json(200, await places.suggest(url.searchParams.get("q") ?? ""));
      const lat = Number(url.searchParams.get("lat")), lon = Number(url.searchParams.get("lon"));
      const ok = url.searchParams.has("lat") && url.searchParams.has("lon") && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
      return ok ? json(200, await places.reverse(lat, lon)) : json(400, { error: "bad request" });
    }
    if (!["/api/search", "/api/more", "/api/city"].includes(url.pathname)) return json(404, { error: "not found" });
    if (request.method !== "POST") return json(405, { error: "method not allowed" });
    if ((request.headers.get("content-type") ?? "").split(";")[0].trim().toLowerCase() !== "application/json") {
      return json(415, { error: "send JSON" });
    }
    let body: unknown;
    try {
      body = await readJson(request);
      if (url.pathname === "/api/more") {
        const id = parseMore(body);
        if (await slowDown(env.MORE_LIMIT)) return json(429, { error: "slow down" });
        return ndjson(live().more(id));
      }
      if (url.pathname === "/api/city") return ndjson(live().city(...parseCity(body)));
      const search = parseSearch(body);
      if (!env.TURNSTILE_SECRET) return json(503, { error: "search is not configured" });
      if (await slowDown(env.SEARCH_LIMIT)) return json(429, { error: "slow down" });
      if (!(await turnstileOk(env, search.turnstile, ip))) return json(403, { error: "please confirm you are human" });
      const { keyword, lat, lon, radius_km, ...wanted } = search;
      return ndjson(live().run(keyword, lat, lon, radius_km, wanted));
    } catch (e) {
      if (e instanceof RangeError) return json(413, { error: "too large" });
      if (e instanceof BadRequest) return json(400, { error: "bad request" });
      throw e;
    }
  }

  return {
    async fetch(request: Request, env: Env): Promise<Response> {
      const url = new URL(request.url);
      return url.pathname.startsWith("/api/") ? api(request, env, url) : env.ASSETS.fetch(request);
    },
  };
}

export default makeHandler();
