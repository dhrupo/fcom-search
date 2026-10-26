import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { geoSql } from "../scripts/geo-sql.ts";

const SCHEMA = readFileSync(join(import.meta.dirname, "../schema.sql"), "utf8");
let geoCache: string | undefined;

/** The slice of the D1 API the Worker uses, on node:sqlite, with the real schema (and the real city list). */
export function fakeD1({ geo = false, failWrites = false } = {}) {
  const db = new DatabaseSync(":memory:");
  const counter = { queries: 0 };
  db.exec(SCHEMA);
  if (geo) db.exec(`BEGIN; ${(geoCache ??= geoSql().join("\n"))} COMMIT;`);
  // Like D1 when the daily write quota is used up: reads work, every write throws.
  const blocked = (sql: string) => {
    if (failWrites && /^\s*(INSERT|UPDATE|DELETE)/i.test(sql)) throw new Error("D1_ERROR: row write requests are temporarily blocked");
  };
  const statement = (sql: string, params: unknown[] = []) => ({
    bind: (...values: unknown[]) => statement(sql, values),
    all: async () => { counter.queries++; return { results: db.prepare(sql).all(...(params as any[])) }; },
    first: async () => { counter.queries++; return db.prepare(sql).get(...(params as any[])) ?? null; },
    run: async () => { counter.queries++; blocked(sql); return { success: true, meta: { changes: Number(db.prepare(sql).run(...(params as any[])).changes) } }; },
    write: () => (blocked(sql), { results: [], meta: { changes: Number(db.prepare(sql).run(...(params as any[])).changes) } }),
  });
  return {
    prepare: (sql: string) => statement(sql),
    // Like D1: one batch counts as one query and runs as one transaction.
    batch: async (statements: ReturnType<typeof statement>[]) => {
      counter.queries++;
      db.exec("BEGIN");
      try {
        const out = statements.map((s) => s.write());
        db.exec("COMMIT");
        return out;
      } catch (e) { db.exec("ROLLBACK"); throw e; }
    },
    counter,
    exec: async (sql: string) => { db.exec(sql); return { count: 1 }; },
  };
}
