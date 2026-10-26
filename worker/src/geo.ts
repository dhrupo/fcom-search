export interface Statement {
  bind(...values: unknown[]): Statement;
  all<T = Record<string, any>>(): Promise<{ results: T[] }>;
  first<T = Record<string, any>>(): Promise<T | null>;
  run(): Promise<unknown>;
}
export interface DB {
  prepare(sql: string): Statement;
  batch(statements: Statement[]): Promise<unknown[]>;
}
export interface City { name: string; country: string; lat: number; lon: number; population: number }

const BIG_CITY = 100_000;
const SAME_AREA_KM = 25;
const EARTH_KM = 6371.0;
const KM_PER_DEGREE = 111.32;
const COLUMNS = "name, country, lat, lon, population";

export const nameKey = (name: string) => name.trim().toLowerCase();

export function distanceKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180, p1 = lat1 * rad, p2 = lat2 * rad, dl = (lon2 - lon1) * rad;
  const a = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.sqrt(a));
}

/** Cities of 100,000+ people within the radius, nearest first, without parts of a bigger city. */
export async function bigCitiesNear(db: DB, lat: number, lon: number, radiusKm: number, limit = 8): Promise<City[]> {
  const dLat = radiusKm / KM_PER_DEGREE, dLon = radiusKm / (KM_PER_DEGREE * Math.max(Math.cos(lat * Math.PI / 180), 0.01));
  // A box that crosses the date line or a pole cannot be one lon range: filter by latitude only.
  const lonFilter = lon - dLon >= -180 && lon + dLon <= 180 ? " AND lon BETWEEN ? AND ?" : "";
  const { results } = await db.prepare(`SELECT ${COLUMNS} FROM cities WHERE population >= ? AND lat BETWEEN ? AND ?${lonFilter}`)
    .bind(BIG_CITY, lat - dLat, lat + dLat, ...(lonFilter ? [lon - dLon, lon + dLon] : []))
    .all<City>();
  const inside = results.filter((c) => distanceKm(lat, lon, c.lat, c.lon) <= radiusKm);
  const kept: City[] = [];
  // A bigger city's Marketplace search already covers places this close (Dhaka covers Gulshan).
  for (const city of inside.sort((a, b) => b.population - a.population)) {
    if (kept.every((k) => distanceKm(city.lat, city.lon, k.lat, k.lon) > SAME_AREA_KM)) kept.push(city);
  }
  return kept.sort((a, b) => distanceKm(lat, lon, a.lat, a.lon) - distanceKm(lat, lon, b.lat, b.lon)).slice(0, limit);
}

/** The most populous city with this name (in this country code, when given). */
export async function findTown(db: DB, name: string, country?: string): Promise<City | null> {
  const key = nameKey(name);
  return db.prepare(`SELECT ${COLUMNS} FROM cities WHERE (name_key = ? OR ascii_key = ?)${country ? " AND country = ?" : ""}
    ORDER BY population DESC LIMIT 1`).bind(key, key, ...(country ? [country] : [])).first<City>();
}

export interface NamedCity extends City { country_name: string | null }

/** Bundled cities whose name starts with the prefix, most populous first, with the country's name. */
export async function citiesByPrefix(db: DB, prefix: string, limit = 4): Promise<NamedCity[]> {
  const low = nameKey(prefix), high = low + "\uffff";
  const { results } = await db.prepare(`SELECT c.name, c.country, c.lat, c.lon, c.population, n.name AS country_name
    FROM cities c LEFT JOIN countries n ON n.code = c.country
    WHERE (c.name_key >= ? AND c.name_key < ?) OR (c.ascii_key >= ? AND c.ascii_key < ?)
    ORDER BY c.population DESC LIMIT ?`).bind(low, high, low, high, limit).all<NamedCity>();
  return results;
}

/** Bundled cities for each name (by name or ASCII name), in one query. */
export async function townsNamed(db: DB, names: string[]): Promise<Map<string, City[]>> {
  const keys = [...new Set(names.map(nameKey))].slice(0, 40), found = new Map<string, City[]>();
  if (!keys.length) return found;
  const marks = keys.map(() => "?").join(",");
  const { results } = await db.prepare(`SELECT name_key, ascii_key, ${COLUMNS} FROM cities
    WHERE name_key IN (${marks}) OR ascii_key IN (${marks})`).bind(...keys, ...keys).all<City & { name_key: string; ascii_key: string }>();
  for (const { name_key, ascii_key, ...city } of results) {
    for (const key of new Set([name_key, ascii_key])) if (keys.includes(key)) found.set(key, [...(found.get(key) ?? []), city]);
  }
  return found;
}
