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
  | { type: "start"; cities: string[]; mode: "point" | "basic"; search_id: string | null }
  | { type: "city"; city: string; status: string; found: number; results: Card[] }
  | { type: "done"; total: number; search_id: string | null; can_load_more: boolean; degraded?: boolean }
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
const POOL_MAX = 200;
const EXPIRED = { type: "error", error: "this search has expired; search again" } as const;
const NOT_LISTED = { type: "error", error: "this city is not part of the search" } as const;
const BUSY = { type: "error", error: "search is busy right now; please try again later" } as const;
const CITY_STATUS: Record<string, string> = { ok: "ok", nothing_new: "ok", blocked: "blocked" };
// Explicit script ranges, not Unicode property classes: those cost 4-10 ms CPU to compile in a cold isolate (Free: 10 ms).
// Latin, Greek, Cyrillic, Arabic, Devanagari (without the danda) and Bengali letters, marks and digits.
const LETTERS = "A-Za-z0-9_\\u00C0-\\u024F\\u0370-\\u03FF\\u0400-\\u04FF\\u0600-\\u06FF\\u0900-\\u0963\\u0966-\\u097F\\u0980-\\u09FF";
const DIGIT = "[0-9\\u0966-\\u096F\\u09E6-\\u09EF]";
const WORD = `[${LETTERS}]`;
const PHONE = new RegExp(`(?<![${LETTERS}+])(?:\\+${DIGIT}(?:${DIGIT}|[\\s-]){8,}${DIGIT}|${DIGIT}(?:[-.]?${DIGIT}){9,})(?!${WORD})`, "g");
// Bangladeshi mobiles as sellers write them: 01712 345678, 017 1234 5678, (017) 1234-5678, +880..., Bengali digits.
const BD_MOBILE = new RegExp(`(?<!${DIGIT})\\(?(?:\\+?[8৮]{2}[\\s-]?)?[0০]?[1১][3-9৩-৯]`
  + `(?:${DIGIT}{2}[\\s.-]?${DIGIT}{6}|[\\s.)-]{0,2}${DIGIT}{4}[\\s.-]?${DIGIT}{4})(?!${DIGIT})`, "g");
const HANDLE = new RegExp(`@${WORD}[${LETTERS}.]*`, "g");
const TOKEN = new RegExp(`${WORD}+`, "g");
const WORD_EDGES = new RegExp(`^${WORD}(?:.*${WORD})?$`, "s");
interface NameIndex { key: string; words: Set<string>; firsts: Set<string>; maxWords: number; rest: RegExp | null }
let nameIndex: NameIndex | undefined;

/** Names as a word-sequence set (a 529-name regex costs ~20 ms CPU on a cold isolate); odd-edged names keep a regex. */
function indexNames(names: string[]): NameIndex {
  const key = names.join("\n");
  if (nameIndex?.key === key) return nameIndex;
  const plain = names.filter((n) => WORD_EDGES.test(n.trim())), odd = names.filter((n) => !WORD_EDGES.test(n.trim()));
  const escaped = odd.sort((a, b) => b.length - a.length).map((n) => n.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const lowered = plain.map((n) => n.trim().toLowerCase());
  nameIndex = { key, words: new Set(lowered), firsts: new Set(lowered.map((n) => n.match(TOKEN)?.[0] ?? n)),
    maxWords: Math.max(1, ...plain.map((n) => n.match(TOKEN)?.length ?? 1)),
    rest: escaped.length ? new RegExp(`(?<![${LETTERS}.])(?:${escaped.join("|")})(?!${WORD})`, "gi") : null };
  return nameIndex;
}

function hideNames(text: string, names: string[]): string {
  const { words, firsts, maxWords, rest } = indexNames(names);
  // Lowercasing can change a string's length (e.g. "İ"); then fall back to per-slice lowercasing.
  const lower = text.toLowerCase(), same = lower.length === text.length;
  const tokens = [...text.matchAll(TOKEN)].map((m) => [m.index!, m.index! + m[0].length]);
  let out = "", from = 0;
  for (let i = 0; i < tokens.length; i++) {
    const start = tokens[i][0];
    if (text[start - 1] === ".") continue;
    const piece = (end: number) => (same ? lower.slice(start, end) : text.slice(start, end).toLowerCase());
    if (!firsts.has(piece(tokens[i][1]))) continue;
    for (let j = Math.min(tokens.length - 1, i + maxWords - 1); j >= i; j--) {
      if (words.has(piece(tokens[j][1]))) {
        out += text.slice(from, start) + "•••";
        from = tokens[j][1];
        i = j;
        break;
      }
    }
  }
  out += text.slice(from);
  return rest ? out.replace(rest, "•••") : out;
}

/** Remove phone numbers, @handles and known private names from seller text. */
export function scrub(text: string | null | undefined, names: string[] = []): string {
  let out = (text ?? "").replace(PHONE, "•••").replace(BD_MOBILE, "•••").replace(HANDLE, "•••");
  if (names.length) out = hideNames(out, names);
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
    let items = await this.pooled(key), status = "ok", full = items.length >= PAGE_SIZE;
    if (!(await this.fresh(key, keyword))) {
      const found = await this.point().search(keyword, lat, lon, radiusKm, band);
      ({ status, full } = found);
      if (status === "ok") items = await this.save(key, keyword, found.listings, items);
    }
    if (status === "ok") {
      yield* this.pointEvents(key, keyword, area, band, full, items);
      return;
    }
    yield { type: "mode", mode: "basic" };
    const cities = await this.places.citiesToSearch(lat, lon, radiusKm);
    const id = await this.remember({ kind: "basic", keyword, wanted: area, pending: cities, shown: [], total: 0 });
    // Basic mode needs the saved search to answer one city per request.
    if (!id) return void (yield BUSY);
    yield { type: "start", cities: cities.map((c) => c.name), mode: "basic", search_id: id };
    if (!cities.length) yield { type: "done", total: 0, search_id: id, can_load_more: false };
  }

  private async *pointEvents(key: string, keyword: string, area: Area, band: Band, full: boolean, items: Listing[]): AsyncGenerator<Event> {
    const near = await bigCitiesNear(this.db, area.lat, area.lon, area.radius_km, 1);
    const name = near[0]?.name ?? "this area";
    const matches = await this.cards(items, keyword, area, false);
    const state: PointState = { kind: "point", keyword, key, wanted: area, area: name, more_left: MAX_MORE,
      bands: full ? split(band, matches.map((r) => r.price)) : [], shown: matches.map((r) => r.item_id) };
    const id = await this.remember(state);
    yield { type: "start", cities: [name], mode: "point", search_id: id };
    yield { type: "city", city: name, status: "ok", found: matches.length, results: matches };
    yield id ? { type: "done", total: matches.length, search_id: id, can_load_more: canMore(state) }
      : { type: "done", total: matches.length, search_id: null, can_load_more: false, degraded: true };
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
    const items = found.status === "ok" ? await this.save(state.key, state.keyword, found.listings, await this.pooled(state.key), false) : [];
    const fresh = new Set(found.listings.map((l) => l.item_id));
    const matches = (await this.cards(items, state.keyword, wanted, false))
      .filter((r) => fresh.has(r.item_id) && !state.shown.includes(r.item_id));
    const after = await this.update(searchId, (s) => {
      if (s.kind !== "point") return null;
      if (found.status !== "ok") return { ...s, bands: [band!, ...s.bands] };
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
    const state = claimed as BasicState, place = slug(city.name), key = `${place}|${state.keyword.toLowerCase()}`;
    let items = await this.pooled(key), status = "ok";
    if (!(await this.fresh(key, state.keyword))) [status, items] = await this.fetchCity(city, place, key, state.keyword, items);
    const matches = (await this.cards(items, state.keyword, state.wanted)).filter((r) => !state.shown.includes(r.item_id));
    const after = await this.update(searchId, (s) => (s.kind === "basic"
      ? { ...s, shown: [...s.shown, ...matches.map((r) => r.item_id)], total: s.total + matches.length } : null));
    yield { type: "city", city: city.name, status, found: matches.length, results: matches };
    if (after?.kind === "basic" && !after.pending.length) {
      yield { type: "done", total: after.total, search_id: searchId, can_load_more: false };
    }
  }

  private async fetchCity(city: City, place: string, key: string, keyword: string, pooled: Listing[]): Promise<[string, Listing[]]> {
    let response: Response, text: string;
    try {
      response = await this.fetch(`${this.origin}/marketplace/${place}/search?query=${encodeURIComponent(keyword)}`,
        { headers: NAVIGATION_HEADERS });
      text = await response.text();
    } catch {
      return ["failed", pooled];
    }
    // A login wall also moves us off the city's address: it is a block, not a missing address.
    const outcome = classify(response.status, response.url, text);
    if (outcome !== "ok") return [CITY_STATUS[outcome] ?? "failed", pooled];
    if (!new URL(response.url || this.origin).pathname.startsWith(`/marketplace/${place}/`)) {
      await this.places.rememberSlug(city.name, city.country, false);
      return ["no_address", pooled];
    }
    const [parsed, listings] = parseSearch(text);
    await this.places.rememberSlug(city.name, city.country, true);
    return [CITY_STATUS[parsed] ?? "failed", await this.save(key, keyword, listings, pooled)];
  }

  /** The new search's id, or null when D1 refuses writes (e.g. the Free daily quota is used up). */
  private async remember(state: State): Promise<string | null> {
    const id = crypto.randomUUID().replaceAll("-", "");
    try {
      await this.db.prepare("INSERT INTO searches (id, state, version, at) VALUES (?, ?, 0, ?)").bind(id, JSON.stringify(state), this.now()).run();
      return id;
    } catch {
      return null;
    }
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
      let result: { meta: { changes: number } };
      try {
        [result] = await this.db.batch([this.db.prepare("UPDATE searches SET state = ?, version = version + 1 WHERE id = ? AND version = ?")
          .bind(JSON.stringify(next), id, current.version)]) as { meta: { changes: number } }[];
      } catch {
        return null;
      }
      if (result.meta.changes === 1) return next;
    }
    return null;
  }

  private async prune(): Promise<void> {
    const now = this.now();
    await this.db.batch([
      this.db.prepare("DELETE FROM searches WHERE at <= ?").bind(now - SEARCH_SECONDS),
      this.db.prepare("DELETE FROM pools WHERE fetched_at <= ?").bind(now - POOL_SECONDS),
      this.db.prepare("DELETE FROM fetches WHERE at <= ?").bind(now - FRESH_SECONDS),
    ]).catch(() => undefined);
  }

  private async fresh(key: string, keyword: string): Promise<boolean> {
    const row = await this.db.prepare("SELECT at FROM fetches WHERE key = ?").bind(`${key}|${keyword.toLowerCase()}`).first<{ at: number }>();
    return Boolean(row) && this.now() - row!.at < FRESH_SECONDS;
  }

  private async pooled(key: string): Promise<Listing[]> {
    const row = await this.db.prepare("SELECT data FROM pools WHERE pool_key = ?").bind(key).first<{ data: string }>();
    return row ? JSON.parse(row.data) : [];
  }

  /** One D1 row per pool (Free plan: 100k rows written a day); newest first, at most POOL_MAX. Returns the pool even if the write fails. */
  private async save(key: string, keyword: string, listings: Listing[], pooled: Listing[], markFresh = true): Promise<Listing[]> {
    const now = this.now(), ids = new Set(listings.map((l) => l.item_id));
    const merged = [...listings, ...pooled.filter((l) => !ids.has(l.item_id))].slice(0, POOL_MAX);
    const writes = [this.db.prepare("INSERT OR REPLACE INTO pools (pool_key, data, fetched_at) VALUES (?, ?, ?)")
      .bind(key, JSON.stringify(merged), now)];
    if (markFresh) writes.push(this.db.prepare("INSERT OR REPLACE INTO fetches (key, at) VALUES (?, ?)").bind(`${key}|${keyword.toLowerCase()}`, now));
    await this.db.batch(writes).catch(() => undefined);
    return merged;
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

  private async cards(items: Listing[], keyword: string, area: Area, matchWords = true): Promise<Card[]> {
    // Facebook already matched point-search results to the keyword, sometimes without the exact words.
    const words = matchWords ? keyword.toLowerCase().match(TOKEN) ?? [] : [];
    const { min_price: min, max_price: max, days } = area;
    const kept = items.filter((item) => {
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
