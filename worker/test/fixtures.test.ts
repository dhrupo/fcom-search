import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const FIXTURES = join(import.meta.dirname, "fixtures");
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

test("fixtures_hold_no_real_names", (t) => {
  const mapPath = process.env.FCS_SCRUB_MAP;
  if (!mapPath) return t.skip("set FCS_SCRUB_MAP to the scrub map kept outside the repo");
  const names = readFileSync(mapPath, "utf8").split("\n").filter(Boolean);
  assert.ok(names.length, "scrub map is empty");
  const files = readdirSync(FIXTURES, { recursive: true, withFileTypes: true }).filter((f) => f.isFile());
  assert.ok(files.length, "no fixtures found");
  const pattern = new RegExp(`(?<![\\w.])(${names.map(escape).join("|")})(?!\\w)`, "iu");
  const leaks = files.flatMap((f) => {
    const found = pattern.exec(readFileSync(join(f.parentPath, f.name), "utf8"));
    return found ? [`${f.name}: ${found[1].slice(0, 2)}…`] : [];
  });
  assert.deepEqual(leaks, []);
});
