import { test } from "node:test";
import assert from "node:assert/strict";
import { fakeD1 } from "./d1.ts";
import { matchingIds, AI_DAILY_CALLS } from "../src/ai.ts";

const CARDS = [
  { item_id: "111", title: "iPhone 13 128GB", price_text: "BDT55,000" },
  { item_id: "222", title: "iPhone 13 silicone case", price_text: "BDT300" },
  { item_id: "333", title: "iPhone 13 Pro", price_text: null },
];
const allowAll = { limit: async () => ({ success: true }) };

/** Stand-in for OpenRouter: records what it was sent and answers with `content`. */
function fakeAi(content: string | (() => never), status = 200) {
  const sent: any[] = [];
  const fetch = async (url: string, init: RequestInit = {}) => {
    sent.push({ url, headers: Object.fromEntries(new Headers(init.headers).entries()), body: JSON.parse(String(init.body)) });
    if (typeof content === "function") content();
    return Response.json({ choices: [{ message: { content } }] }, { status });
  };
  return { fetch, sent };
}
const config = (ai: ReturnType<typeof fakeAi>, over: Record<string, unknown> = {}) => ({ key: "sk-or-test", origin: "https://openrouter.test",
  model: "test/model:free", fetch: ai.fetch, db: fakeD1(), limiter: allowAll, now: () => 1_792_924_206, ...over });

test("sends_scrubbed_titles_only", async () => {
  const ai = fakeAi('{"match": [1]}');
  await matchingIds(config(ai), "iphone 13", CARDS.map((c) => ({ ...c, town: "Sylhet", seller: "Rahim" } as any)));
  const [{ url, headers, body }] = ai.sent;
  assert.equal(url, "https://openrouter.test/api/v1/chat/completions");
  assert.equal(headers.authorization, "Bearer sk-or-test");
  assert.equal(body.model, "test/model:free");
  const prompt = JSON.stringify(body.messages);
  assert.ok(prompt.includes("iPhone 13 silicone case") && prompt.includes("BDT55,000") && prompt.includes("iphone 13"));
  for (const leak of ["Sylhet", "Rahim", "111", "222"]) assert.ok(!prompt.includes(leak), `prompt carries ${leak}`);
});

test("reads_matching_numbers_from_messy_json", async () => {
  for (const content of ['{"match": [1, 3]}', 'Sure!\n```json\n{"match":[1,3]}\n```', '{"match": ["1", "3"]}']) {
    assert.deepEqual(await matchingIds(config(fakeAi(content)), "iphone 13", CARDS), ["111", "333"], content);
  }
});

test("answers_outside_the_list_are_ignored", async () => {
  assert.deepEqual(await matchingIds(config(fakeAi('{"match": [0, 2, 4, 2.5, -1, "x"]}')), "iphone 13", CARDS), ["222"]);
});

test("empty_verdict_folds_nothing", async () => {
  for (const content of ['{"match": []}', "I cannot help with that", '{"match": [9]}', ""]) {
    assert.equal(await matchingIds(config(fakeAi(content)), "iphone 13", CARDS), null, content);
  }
});

test("timeout_or_error_means_not_checked", async () => {
  assert.equal(await matchingIds(config(fakeAi('{"match": [1]}', 429)), "iphone 13", CARDS), null);
  assert.equal(await matchingIds(config(fakeAi(() => { throw new DOMException("timed out", "TimeoutError"); })), "iphone 13", CARDS), null);
  const notJson = { fetch: async () => new Response("<html>bad gateway</html>", { status: 200 }), sent: [] };
  assert.equal(await matchingIds(config(notJson as any), "iphone 13", CARDS), null);
});

test("daily_budget_stops_calls", async () => {
  const ai = fakeAi('{"match": [1]}'), db = fakeD1();
  let now = 1_792_924_206;
  for (let i = 0; i < AI_DAILY_CALLS + 3; i++) await matchingIds(config(ai, { db, now: () => now }), "iphone 13", CARDS);
  assert.equal(ai.sent.length, AI_DAILY_CALLS);
  now += 86400;
  await matchingIds(config(ai, { db, now: () => now }), "iphone 13", CARDS);
  assert.equal(ai.sent.length, AI_DAILY_CALLS + 1, "a new day gets a new budget");
  const limited = fakeAi('{"match": [1]}');
  assert.equal(await matchingIds(config(limited, { limiter: { limit: async () => ({ success: false }) } }), "iphone 13", CARDS), null);
  assert.equal(limited.sent.length, 0, "the per-minute limit also stops calls");
});

test("no_key_no_call", async () => {
  const ai = fakeAi('{"match": [1]}');
  assert.equal(await matchingIds(config(ai, { key: undefined }), "iphone 13", CARDS), null);
  assert.equal(await matchingIds(config(ai), "iphone 13", []), null);
  assert.equal(ai.sent.length, 0);
});
