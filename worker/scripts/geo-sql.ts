// Prints the SQL that loads the GeoNames TSVs into D1:
//   node scripts/geo-sql.ts > /tmp/geo.sql && wrangler d1 execute fcommerce-search --remote --file /tmp/geo.sql
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { nameKey } from "../src/geo.ts";

export const GEO_DIR = join(import.meta.dirname, "../geo");
const ROWS_PER_INSERT = 500;
const quote = (s: string) => `'${s.replaceAll("'", "''")}'`;
const rows = (file: string) =>
  readFileSync(join(GEO_DIR, file), "utf8").split("\n").filter((l) => l && !l.startsWith("#")).map((l) => l.split("\t"));

export function geoSql(): string[] {
  const cities = rows("cities15000.tsv").map(([name, ascii, country, lat, lon, pop]) =>
    `(${[quote(name), quote(ascii), quote(nameKey(name)), quote(nameKey(ascii)), quote(country), +lat, +lon, +(pop || 0)].join(",")})`);
  const out = ["DELETE FROM cities;", "DELETE FROM countries;"];
  for (let i = 0; i < cities.length; i += ROWS_PER_INSERT) {
    out.push(`INSERT INTO cities (name, ascii, name_key, ascii_key, country, lat, lon, population) VALUES ${cities.slice(i, i + ROWS_PER_INSERT).join(",")};`);
  }
  const countries = rows("countries.tsv").map(([code, name]) => `(${quote(code)},${quote(name)})`);
  out.push(`INSERT INTO countries (code, name) VALUES ${countries.join(",")};`);
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) console.log(geoSql().join("\n"));
