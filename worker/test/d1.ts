import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { geoSql } from "../scripts/geo-sql.ts";

const SCHEMA = readFileSync(join(import.meta.dirname, "../schema.sql"), "utf8");
let geoCache: string | undefined;

/** The slice of the D1 API the Worker uses, on node:sqlite, with the real schema (and the real city list). */
export function fakeD1({ geo = false } = {}) {
  const db = new DatabaseSync(":memory:");
  db.exec(SCHEMA);
  if (geo) db.exec(`BEGIN; ${(geoCache ??= geoSql().join("\n"))} COMMIT;`);
  const statement = (sql: string, params: unknown[] = []) => ({
    bind: (...values: unknown[]) => statement(sql, values),
    all: async () => ({ results: db.prepare(sql).all(...(params as any[])) }),
    first: async () => db.prepare(sql).get(...(params as any[])) ?? null,
    run: async () => ({ success: true, meta: { changes: Number(db.prepare(sql).run(...(params as any[])).changes) } }),
  });
  return {
    prepare: (sql: string) => statement(sql),
    batch: async (statements: ReturnType<typeof statement>[]) => Promise.all(statements.map((s) => s.all())),
    exec: async (sql: string) => { db.exec(sql); return { count: 1 }; },
  };
}
