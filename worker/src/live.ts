import { classify, parseSearch, type Listing } from "./facebook.ts";
import { bigCitiesNear, distanceKm, nameKey, townsNamed, type City, type DB } from "./geo.ts";
import { NAVIGATION_HEADERS, PAGE_SIZE, PointSearch, type Fetch } from "./marketplace.ts";
import { slug, type Places } from "./places.ts";

export interface Wanted { min_price?: number | null; max_price?: number | null; days?: number | null }
export interface Card {
  item_id: string; title: string; price: number | null; price_text: string | null; town: string | null;
  created_at: number | null; photo: string | null; permalink: string; sold: boolean; distance_km: number | null;
}
export type Event =
  | { type: "mode"; mode: "basic" }
  | { type: "start"; cities: string[]; mode: "point" | "basic"; search_id: string }
  | { type: "city"; city: string; status: string; found: number; results: Card[] }
  | { type: "done"; total: number; search_id: string; can_load_more: boolean }
  | { type: "error"; error: string };
type Band = [number | null, number | null];
interface Area { lat: number; lon: number; radius_km: number; min_price: number | null; max_price: number | null; days: number | null }
interface PointState { kind: "point"; keyword: string; key: string; wanted: Area; area: string; bands: Band[]; more_left: number; shown: string[] }
interface BasicState { kind: "basic"; keyword: string; wanted: Area; pending: City[]; shown: string[]; total: number }
type State = PointState | BasicState;

const FRESH_SECONDS = 600;
const SEARCH_SECONDS = 30 * 60;
const POOL_SECONDS = 60 * 86400;
const MAX_MORE = 5;
const MAX_TOWN_LOOKUPS = 8;
const EXPIRED = { type: "error", error: "this search has expired; search again" } as const;
const NOT_LISTED = { type: "error", error: "this city is not part of the search" } as const;
const CITY_STATUS: Record<string, string> = { ok: "ok", nothing_new: "ok", blocked: "blocked" };
// Python's \w and \d are Unicode-aware; these match the pipeline's scrubbing, Bengali digits included.
const WORD = "[\\p{L}\\p{N}_]";
const PHONE = new RegExp(`(?<![\\p{L}\\p{N}_+])(?:\\+\\p{Nd}[\\p{Nd}\\s-]{8,}\\p{Nd}|\\p{Nd}(?:[-.]?\\p{Nd}){9,})(?!${WORD})`, "gu");
const HANDLE = new RegExp(`@${WORD}[\\p{L}\\p{N}_.]*`, "gu");
const nameRules = new Map<string, RegExp>();

/** Remove phone numbers, @handles and known private names from seller text. */
export function scrub(text: string | null | undefined, names: string[] = []): string {
  let out = (text ?? "").replace(PHONE, "•••").replace(HANDLE, "•••");
  if (names.length) {
    const cacheKey = names.join("\n");
    let rule = nameRules.get(cacheKey);
    if (!rule) {
      const escaped = [...names].sort((a, b) => b.length - a.length).map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
      rule = new RegExp(`(?<![\\p{L}\\p{N}_.])(?:${escaped.join("|")})(?!${WORD})`, "giu");
      nameRules.clear();
      nameRules.set(cacheKey, rule);
    }
    out = out.replace(rule, "•••");
  }
  return out.split(/\s+/).filter(Boolean).join(" ");
}

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b), mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
};
const amount = (item: Listing) => (item.price === "unknown" ? null : item.price.amount);

/** A band that came back full hides more listings: split it at its middle price. */
function split([low, high]: Band, prices: (number | null)[]): Band[] {
  const inside = prices.filter((p): p is number => p !== null && (low === null || p >= low) && (high === null || p <= high));
  if (!inside.length) return [];
  const middle = median(inside);
  if ((low !== null && middle <= low) || (high !== null && middle >= high)) return [];
  return [[low ?? 0, middle], [middle, high]];
}
const canMore = (state: PointState) => state.bands.length > 0 && state.more_left > 0;

/** Search Marketplace live, logged out, around a point (or the big cities near it), and keep a pool in D1. */
export class LiveSearch {
  private db: DB; private fetch: Fetch; private origin: string; private places: Places; private now: () => number;
  private loadNames: () => Promise<string[]>; private names?: Promise<string[]>;

  constructor(db: DB, fetch: Fetch, origin: string, places: Places, now: () => number, loadNames: () => Promise<string[]>) {
    this.db = db; this.fetch = fetch; this.origin = origin; this.places = places; this.now = now; this.loadNames = loadNames;
  }

  private point() { return new PointSearch(this.db, this.fetch, this.origin, this.now); }

  async *run(keyword: string, lat: number, lon: number, radiusKm: number, wanted: Wanted = {}): AsyncGenerator<Event> {
    keyword = keyword.split(/\s+/).filter(Boolean).join(" ").slice(0, 100);
    const area: Area = { lat, lon, radius_km: radiusKm, min_price: wanted.min_price ?? null, max_price: wanted.max_price ?? null,
      days: wanted.days ?? null };
    await this.prune();
    const key = `pt:${lat.toFixed(3)},${lon.toFixed(3)},${radiusKm},${area.min_price},${area.max_price},${keyword.toLowerCase()}`;
    const band: Band = [area.min_price, area.max_price];
    let status = "ok", full = (await this.poolSize(key)) >= PAGE_SIZE;
    if (!(await this.fresh(key, keyword))) {
      const found = await this.point().search(keyword, lat, lon, radiusKm, band);
      ({ status, full } = found);
      if (status === "ok") await this.save(key, keyword, found.listings);
    }
    if (status === "ok") {
      yield* this.pointEvents(key, keyword, area, band, full);
      return;
    }
    yield { type: "mode", mode: "basic" };
    const cities = await this.places.citiesToSearch(lat, lon, radiusKm);
    const id = await this.remember({ kind: "basic", keyword, wanted: area, pending: cities, shown: [], total: 0 });
    yield { type: "start", cities: cities.map((c) => c.name), mode: "basic", search_id: id };
    if (!cities.length) yield { type: "done", total: 0, search_id: id, can_load_more: false };
  }

  private async *pointEvents(key: string, keyword: string, area: Area, band: Band, full: boolean): AsyncGenerator<Event> {
    const near = await bigCitiesNear(this.db, area.lat, area.lon, area.radius_km, 1);
    const name = near[0]?.name ?? "this area";
    const matches = await this.pool(key, keyword, area, false);
    const state: PointState = { kind: "point", keyword, key, wanted: area, area: name, more_left: MAX_MORE,
      bands: full ? split(band, matches.map((r) => r.price)) : [], shown: matches.map((r) => r.item_id) };
    const id = await this.remember(state);
    yield { type: "start", cities: [name], mode: "point", search_id: id };
    yield { type: "city", city: name, status: "ok", found: matches.length, results: matches };
    yield { type: "done", total: matches.length, search_id: id, can_load_more: canMore(state) };
  }

  /** The next price band of an earlier point search: up to MAX_MORE times, never repeating a listing. */
  async *more(searchId: string): AsyncGenerator<Event> {
    let band: Band | undefined;
    const claimed = await this.update(searchId, (s) => {
      if (s.kind !== "point" || !canMore(s)) return null;
      band = s.bands[0];
      return { ...s, bands: s.bands.slice(1), more_left: s.more_left - 1 };
    });
    if (claimed === undefined) return void (yield EXPIRED);
    if (!claimed) band = undefined;
    if (!band) {
      const state = claimed ?? (await this.read(searchId))?.state;
      return void (yield { type: "done", total: 0, search_id: searchId, can_load_more: state?.kind === "point" && canMore(state) });
    }
    const state = claimed as PointState, { wanted } = state;
    const found = await this.point().search(state.keyword, wanted.lat, wanted.lon, wanted.radius_km, band);
    if (found.status === "ok") await this.save(state.key, state.keyword, found.listings, false);
    const fresh = new Set(found.listings.map((l) => l.item_id));
    const matches = (await this.pool(state.key, state.keyword, wanted, false))
      .filter((r) => fresh.has(r.item_id) && !state.shown.includes(r.item_id));
    const after = await this.update(searchId, (s) => {
      if (s.kind !== "point") return null;
      if (found.status !== "ok") return { ...s, bands: [band!, ...s.bands], more_left: s.more_left + 1 };
      return { ...s, shown: [...s.shown, ...matches.map((r) => r.item_id)],
        bands: [...s.bands, ...(found.full ? split(band!, found.listings.map(amount)) : [])] };
    });
    yield { type: "city", city: state.area, status: found.status, found: matches.length, results: matches };
    yield { type: "done", total: matches.length, search_id: searchId,
      can_load_more: Boolean(after && after.kind === "point" && canMore(after)) };
  }

  /** One city of a basic-mode search; each listed city once (Q14: one city per request keeps CPU under 10 ms). */
  async *city(searchId: string, cityName: string): AsyncGenerator<Event> {
    let city: City | undefined;
    const claimed = await this.update(searchId, (s) => {
      city = s.kind === "basic" ? s.pending.find((c) => c.name === cityName) : undefined;
      return city && s.kind === "basic" ? { ...s, pending: s.pending.filter((c) => c !== city) } : null;
    });
    if (claimed === undefined) return void (yield EXPIRED);
    if (!claimed || !city) return void (yield NOT_LISTED);
    const state = claimed as BasicState, place = slug(city.name);
    const status = (await this.fresh(place, state.keyword)) ? "ok" : await this.fetchCity(city, place, state.keyword);
    const matches = (await this.pool(place, state.keyword, state.wanted)).filter((r) => !state.shown.includes(r.item_id));
    const after = await this.update(searchId, (s) => (s.kind === "basic"
      ? { ...s, shown: [...s.shown, ...matches.map((r) => r.item_id)], total: s.total + matches.length } : null));
    yield { type: "city", city: city.name, status, found: matches.length, results: matches };
    if (after?.kind === "basic" && !after.pending.length) {
      yield { type: "done", total: after.total, search_id: searchId, can_load_more: false };
    }
  }

  private async fetchCity(city: City, place: string, keyword: string): Promise<string> {
    let response: Response, text: string;
    try {
      response = await this.fetch(`${this.origin}/marketplace/${place}/search?query=${encodeURIComponent(keyword)}`,
        { headers: NAVIGATION_HEADERS });
      text = await response.text();
    } catch {
      return "failed";
    }
    if (!new URL(response.url || this.origin).pathname.startsWith(`/marketplace/${place}/`)) {
      await this.places.rememberSlug(city.name, city.country, false);
      return "no_address";
    }
    const outcome = classify(response.status, response.url, text);
    if (outcome !== "ok") return CITY_STATUS[outcome] ?? "failed";
    const [parsed, listings] = parseSearch(text);
    await this.places.rememberSlug(city.name, city.country, true);
    await this.save(place, keyword, listings);
    return CITY_STATUS[parsed] ?? "failed";
  }

  private async remember(state: State): Promise<string> {
    const id = crypto.randomUUID().replaceAll("-", "");
    await this.db.prepare("INSERT INTO searches (id, state, version, at) VALUES (?, ?, 0, ?)").bind(id, JSON.stringify(state), this.now()).run();
    return id;
  }

  private async read(id: string): Promise<{ state: State; version: number } | null> {
    const row = await this.db.prepare("SELECT state, version, at FROM searches WHERE id = ?").bind(id)
      .first<{ state: string; version: number; at: number }>();
    return row && this.now() - row.at <= SEARCH_SECONDS ? { state: JSON.parse(row.state), version: row.version } : null;
  }

  /** Compare-and-swap on a search's state: undefined = no such search, null = `change` declined. */
  private async update(id: string, change: (s: State) => State | null): Promise<State | null | undefined> {
    for (let attempt = 0; attempt < 5; attempt++) {
      const current = await this.read(id);
      if (!current) return undefined;
      const next = change(current.state);
      if (!next) return null;
      const [result] = await this.db.batch([this.db.prepare("UPDATE searches SET state = ?, version = version + 1 WHERE id = ? AND version = ?")
        .bind(JSON.stringify(next), id, current.version)]) as { meta: { changes: number } }[];
      if (result.meta.changes === 1) return next;
    }
    return null;
  }

  private async prune(): Promise<void> {
    const now = this.now();
    await this.db.batch([
      this.db.prepare("DELETE FROM searches WHERE at <= ?").bind(now - SEARCH_SECONDS),
      this.db.prepare("DELETE FROM listings WHERE fetched_at <= ?").bind(now - POOL_SECONDS),
      this.db.prepare("DELETE FROM fetches WHERE at <= ?").bind(now - FRESH_SECONDS),
    ]);
  }

  private async fresh(key: string, keyword: string): Promise<boolean> {
    const row = await this.db.prepare("SELECT at FROM fetches WHERE key = ?").bind(`${key}|${keyword.toLowerCase()}`).first<{ at: number }>();
    return Boolean(row) && this.now() - row!.at < FRESH_SECONDS;
  }

  private async poolSize(key: string): Promise<number> {
    return (await this.db.prepare("SELECT count(*) AS n FROM listings WHERE pool_key = ?").bind(key).first<{ n: number }>())?.n ?? 0;
  }

  private async save(key: string, keyword: string, listings: Listing[], markFresh = true): Promise<void> {
    const now = this.now();
    const writes = listings.map((item) => this.db.prepare("INSERT OR REPLACE INTO listings (item_id, pool_key, data, fetched_at) VALUES (?, ?, ?, ?)")
      .bind(item.item_id, key, JSON.stringify(item), now));
    if (markFresh) writes.push(this.db.prepare("INSERT OR REPLACE INTO fetches (key, at) VALUES (?, ?)").bind(`${key}|${keyword.toLowerCase()}`, now));
    if (writes.length) await this.db.batch(writes);
  }

  /** Each listing town's point on the map: the same-named place nearest the search (our list, then OpenStreetMap). */
  async townPoints(towns: string[], near: [number, number]): Promise<Map<string, [number, number] | null>> {
    const first = (town: string) => town.split(",")[0].trim();
    const bundled = await townsNamed(this.db, towns.map(first));
    const out = new Map<string, [number, number] | null>();
    let lookups = 0;
    for (const town of new Set(towns)) {
      let points: [number, number][] = (bundled.get(nameKey(first(town))) ?? []).map((c) => [c.lat, c.lon]);
      // Free plan: at most 50 D1 queries per request, so only a few unknown towns are looked up; the rest show no km.
      if (!points.length && lookups < MAX_TOWN_LOOKUPS) {
        lookups++;
        points = await this.places.locate(town);
      }
      out.set(town, points.length ? points.reduce((a, b) => (distanceKm(...near, ...a) <= distanceKm(...near, ...b) ? a : b)) : null);
    }
    return out;
  }

  private async pool(key: string, keyword: string, area: Area, matchWords = true): Promise<Card[]> {
    // Facebook already matched point-search results to the keyword, sometimes without the exact words.
    const words = matchWords ? keyword.toLowerCase().match(/[\p{L}\p{N}_]+/gu) ?? [] : [];
    const { results } = await this.db.prepare("SELECT data FROM listings WHERE pool_key = ? ORDER BY fetched_at DESC").bind(key)
      .all<{ data: string }>();
    const { min_price: min, max_price: max, days } = area;
    const kept = results.map((r) => JSON.parse(r.data) as Listing).filter((item) => {
      const title = (item.title ?? "").toLowerCase(), price = amount(item);
      if (words.some((w) => !title.includes(w))) return false;
      if ((min !== null || max !== null) && price === null) return false;
      if (price !== null && ((min !== null && price < min) || (max !== null && price > max))) return false;
      return !(days && (item.created_at ?? 0) < this.now() - days * 86400);
    });
    const points = await this.townPoints(kept.flatMap((i) => (i.city ? [i.city] : [])), [area.lat, area.lon]);
    const names = await (this.names ??= this.loadNames());
    return kept.flatMap((item) => {
      // Facebook's radius is loose (a 40 km search returned a town 80 km away): check it here.
      const point = item.city ? points.get(item.city) : null;
      const distance = point ? Math.round(distanceKm(area.lat, area.lon, ...point) * 10) / 10 : null;
      if (distance !== null && distance > area.radius_km) return [];
      return [{ item_id: item.item_id, title: scrub(item.title, names), price: amount(item),
        price_text: item.price === "unknown" ? null : item.price.formatted, town: item.city, created_at: item.created_at,
        photo: item.images[0] ?? null, permalink: item.permalink, sold: item.sold, distance_km: distance }];
    });
  }
}
