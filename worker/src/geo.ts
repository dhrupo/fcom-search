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
