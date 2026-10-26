import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./d1.ts";
import { bigCitiesNear, distanceKm, findTown } from "../src/geo.ts";

const SYLHET: [number, number] = [24.8949, 91.8687];
const db = fakeD1({ geo: true });
const names = async (lat: number, lon: number, radiusKm: number, limit?: number) =>
  (await bigCitiesNear(db, lat, lon, radiusKm, limit)).map((c) => c.name);

test("distance_km", () => {
  assert.ok(Math.abs(distanceKm(51.5072, -0.1276, 48.8566, 2.3522) - 344) <= 5);
  assert.equal(distanceKm(...SYLHET, ...SYLHET), 0);
});

test("big_cities_within_radius_nearest_first", async () => {
  const near = await bigCitiesNear(db, ...SYLHET, 100);
  assert.equal(near[0].name, "Sylhet");
  assert.ok(!near.some((c) => c.name === "Dhaka"));
  assert.ok(near.every((c) => c.population >= 100_000));
  assert.ok((await names(...SYLHET, 250, 50)).includes("Dhaka"));
  assert.equal((await bigCitiesNear(db, ...SYLHET, 250)).length, 8);
});

test("town_name_resolves_with_country_hint", async () => {
  const london = await findTown(db, "London");
  assert.deepEqual([london?.country, Math.round(london!.lat)], ["GB", 52]);
  assert.equal((await findTown(db, " london ", "CA"))?.country, "CA");
  assert.equal(await findTown(db, "Nowhereville Zzz"), null);
  assert.equal((await findTown(db, "sao paulo"))?.name, "São Paulo");
  const twins = fakeD1();
  await twins.exec("INSERT INTO cities (name, ascii, name_key, ascii_key, country, lat, lon, population) VALUES "
    + "('Springfield', 'Springfield', 'springfield', 'springfield', 'US', 1, 1, 1000), "
    + "('Springfield', 'Springfield', 'springfield', 'springfield', 'US', 2, 2, 900000)");
  assert.equal((await findTown(twins, "Springfield"))?.population, 900000);
});

test("parts_of_a_bigger_city_are_skipped", async () => {
  assert.deepEqual(await names(23.7104, 90.4074, 10), ["Dhaka"]);
  assert.deepEqual(await names(51.5073, -0.1276, 10), ["London"]);
  const nearSylhet = await names(...SYLHET, 100);
  assert.ok(nearSylhet[0] === "Sylhet" && nearSylhet.includes("Shillong"));
});

test("box_is_wide_enough_far_from_the_equator", async () => {
  assert.ok((await names(59.94, 31.9, 100)).includes("Saint Petersburg"));
});

test("search_box_crossing_the_date_line_still_finds_cities", async () => {
  const sea = fakeD1({ geo: true });
  await sea.exec("INSERT INTO cities (name, ascii, name_key, ascii_key, country, lat, lon, population) "
    + "VALUES ('Testville', 'Testville', 'testville', 'testville', 'FJ', -16.5, -179.9, 200000)");
  assert.deepEqual((await bigCitiesNear(sea, -16.5, 179.9, 50)).map((c) => c.name), ["Testville"]);
});
