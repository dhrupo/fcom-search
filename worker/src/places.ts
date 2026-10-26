import { bigCitiesNear, citiesByPrefix, findTown, type City, type DB } from "./geo.ts";
import type { Fetch } from "./marketplace.ts";

export interface Suggestion { name: string; country: string | null; countrycode: string | null; state: string | null; lat: number; lon: number }
export interface Limiter { limit(options: { key: string }): Promise<{ success: boolean }> }

const USER_AGENT = "fcommerce-search (public site, https://fcomsearch.dhrupo.workers.dev)";
const MAX_SUGGESTIONS = 6;
const ENOUGH_BUNDLED = 4;
const FALLBACK_KM = 150;
const CACHE_SECONDS = 30 * 86400;

const normalized = (text: string) => text.split(/\s+/).filter(Boolean).join(" ").toLowerCase().slice(0, 100);
export const slug = (name: string) => name.normalize("NFKD").toLowerCase().replace(/[^a-z0-9]/g, "");

function features(body: any): Suggestion[] {
  const list = body && typeof body === "object" && Array.isArray(body.features) ? body.features : [];
  return list.flatMap((f: any) => {
    const props = f?.properties ?? {}, coords = f?.geometry?.coordinates ?? [];
    return props.name && coords.length === 2
      ? [{ name: props.name, country: props.country ?? null, countrycode: props.countrycode ?? null, state: props.state ?? null,
           lon: coords[0], lat: coords[1] }]
      : [];
  });
}

/** Place suggestions (our city list, then OpenStreetMap via Photon) and which Marketplace city addresses work. */
export class Places {
  private db: DB; private fetch: Fetch; private origin: string; private now: () => number; private photonLimit: Limiter;

  constructor(db: DB, fetch: Fetch, origin: string, now: () => number, photonLimit: Limiter) {
    this.db = db; this.fetch = fetch; this.origin = origin; this.now = now; this.photonLimit = photonLimit;
  }

  private async photon(path: string, params: [string, string][]): Promise<unknown> {
    const key = `${path}?${new URLSearchParams([...params].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))}`;
    const now = this.now();
    const row = await this.db.prepare("SELECT body FROM photon_cache WHERE key = ? AND at > ?").bind(key, now - CACHE_SECONDS)
      .first<{ body: string }>();
    if (row) return JSON.parse(row.body);
    // Photon's fair use: one shared allowance for every visitor of the site.
    if (!(await this.photonLimit.limit({ key: "photon" })).success) return {};
    let body: unknown;
    try {
      body = await (await this.fetch(this.origin + key, { headers: { "user-agent": USER_AGENT } })).json();
    } catch {
      return {};
    }
    await this.db.batch([
      this.db.prepare("INSERT OR REPLACE INTO photon_cache (key, body, at) VALUES (?, ?, ?)").bind(key, JSON.stringify(body), now),
      this.db.prepare("DELETE FROM photon_cache WHERE at <= ?").bind(now - CACHE_SECONDS),
    ]).catch(() => undefined);
    return body;
  }

  /** Cities and towns for a typed name, the most populous first (villages are left out). */
  async suggest(query: string): Promise<Suggestion[]> {
    const q = normalized(query);
    if (q.length < 2) return [];
    const bundled = (await citiesByPrefix(this.db, q)).map((c) => ({ name: c.name, country: c.country_name, countrycode: c.country,
      state: null, lat: c.lat, lon: c.lon, population: c.population }));
    const found = bundled.length >= ENOUGH_BUNDLED ? [] : await this.photonPlaces(q);
    const ranked = await Promise.all(found.map(async (f) =>
      ({ ...f, population: (await findTown(this.db, f.name, f.countrycode?.toUpperCase() || undefined))?.population ?? 0 })));
    const merged = new Map<string, Suggestion>();
    for (const { population, ...s } of [...bundled, ...ranked].sort((a, b) => b.population - a.population)) {
      const key = `${slug(s.name)}|${(s.countrycode ?? "").toUpperCase()}`;
      if (!merged.has(key)) merged.set(key, s);
    }
    return [...merged.values()].slice(0, MAX_SUGGESTIONS);
  }

  private async photonPlaces(q: string): Promise<Suggestion[]> {
    return features(await this.photon("/api/",
      [["q", q], ["limit", String(MAX_SUGGESTIONS)], ["lang", "en"], ["osm_tag", "place:city"], ["osm_tag", "place:town"]]));
  }

  /** Map points for a town name OpenStreetMap knows (shares the type-ahead cache). */
  async locate(town: string): Promise<[number, number][]> {
    const q = normalized(town);
    return q.length < 2 ? [] : (await this.photonPlaces(q)).map((p) => [p.lat, p.lon]);
  }

  async reverse(lat: number, lon: number): Promise<Suggestion | null> {
    const round = (x: number) => String(Math.round(x * 1000) / 1000);
    return features(await this.photon("/reverse", [["lat", round(lat)], ["lon", round(lon)], ["limit", "1"]]))[0] ?? null;
  }

  async slugStatus(name: string, country: string): Promise<boolean | null> {
    const row = await this.db.prepare("SELECT works FROM slugs WHERE key = ?").bind(`${name}|${country}`).first<{ works: number }>();
    return row ? Boolean(row.works) : null;
  }

  async rememberSlug(name: string, country: string, works: boolean): Promise<void> {
    await this.db.prepare("INSERT OR REPLACE INTO slugs (key, works, checked_at) VALUES (?, ?, ?)")
      .bind(`${name}|${country}`, works ? 1 : 0, this.now()).run().catch(() => undefined);
  }

  /** Big cities inside the radius whose address is not known to fail; else the nearest big city. */
  async citiesToSearch(lat: number, lon: number, radiusKm: number): Promise<City[]> {
    const usable = async (cities: City[]) => {
      const bad = new Set<string>();
      for (const c of cities) if ((await this.slugStatus(c.name, c.country)) === false) bad.add(`${c.name}|${c.country}`);
      return cities.filter((c) => !bad.has(`${c.name}|${c.country}`));
    };
    const inside = await usable(await bigCitiesNear(this.db, lat, lon, radiusKm));
    return inside.length ? inside : (await usable(await bigCitiesNear(this.db, lat, lon, FALLBACK_KM))).slice(0, 1);
  }
}
