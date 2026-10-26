// E2E stand-in for Facebook, Photon and Turnstile's siteverify, served from the saved fixtures:
//   node test/replay-upstream.ts <port> [basic]
import { createServer } from "node:http";
import { replayFetch } from "./replay.ts";

const [port, mode] = [Number(process.argv[2]), process.argv[3]];
const FIXTURE_NOW = 1792924206 + 3600;
const E2E_TOKEN = "e2e-pass";
const PLACES: Record<string, [string, string, string, number, number]> = {
  sylhet: ["Sylhet", "Bangladesh", "BD", 91.8687, 24.8949], london: ["London", "United Kingdom", "GB", -0.1276, 51.5073],
  dhaka: ["Dhaka", "Bangladesh", "BD", 90.4074, 23.7104], paris: ["Paris", "France", "FR", 2.3522, 48.8566],
  "maulvi bazar": ["Maulvi Bazar", "Bangladesh", "BD", 91.7774, 24.4829], chhatak: ["Chhatak", "Bangladesh", "BD", 91.6694, 25.0381],
  lalabazar: ["Lalabazar", "Bangladesh", "BD", 91.83, 24.86], shaistaganj: ["Shaistaganj", "Bangladesh", "BD", 91.45, 24.2833],
};
const replay = replayFetch({ graphqlStatus: mode === "basic" ? 500 : 200 });
const geojson = (hits: (typeof PLACES)[string][]) => JSON.stringify({ features: hits.map(([name, country, countrycode, lon, lat]) =>
  ({ properties: { name, country, countrycode }, geometry: { coordinates: [lon, lat] } })) });
// Listing dates in the fixtures are fixed; keep them as old relative to today as they were when saved.
const shiftDates = (text: string) =>
  text.replace(/"creation_time":(\d+)/g, (_, t) => `"creation_time":${Number(t) - FIXTURE_NOW + Math.floor(Date.now() / 1000)}`);

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://127.0.0.1:${port}`);
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const body = Buffer.concat(chunks).toString();
  if (url.pathname === "/turnstile/siteverify") {
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ success: new URLSearchParams(body).get("response") === E2E_TOKEN }));
  }
  if (url.pathname === "/ai/api/v1/chat/completions") {
    // Like the AI check: a listing matches when its title holds every search word.
    const prompt = JSON.parse(body).messages.at(-1).content as string;
    const words = (prompt.match(/^Search: (.*)$/m)?.[1] ?? "").toLowerCase().split(/\s+/).filter(Boolean);
    const match = [...prompt.matchAll(/^(\d+)\. (.*)$/gm)].filter(([, , line]) => words.every((w) => line.toLowerCase().includes(w)))
      .map(([, n]) => Number(n));
    res.writeHead(200, { "content-type": "application/json" });
    return res.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify({ match }) } }] }));
  }
  if (url.pathname.startsWith("/photon/")) {
    res.writeHead(200, { "content-type": "application/json" });
    if (url.pathname === "/photon/reverse") return res.end(geojson([PLACES.sylhet]));
    const q = (url.searchParams.get("q") ?? "").replaceAll(",", " ").toLowerCase();
    return res.end(geojson(Object.entries(PLACES).filter(([k]) => k.startsWith(q.slice(0, 4))).map(([, v]) => v)));
  }
  const answer = await replay.fetch(`https://www.facebook.com${url.pathname}${url.search}`,
    { method: req.method, body: body || undefined, headers: req.headers as Record<string, string> });
  const moved = new URL(answer.url);
  if (moved.pathname !== url.pathname) {
    res.writeHead(302, { location: `http://127.0.0.1:${port}${moved.pathname}${moved.search}` });
    return res.end();
  }
  const headers: Record<string, string | string[]> = { "content-type": answer.headers.get("content-type") ?? "text/html" };
  if (answer.headers.getSetCookie().length) headers["set-cookie"] = answer.headers.getSetCookie();
  res.writeHead(answer.status, headers);
  res.end(shiftDates(await answer.text()));
}).listen(port, "127.0.0.1", () => console.log(`replay upstream on ${port}${mode ? ` (${mode})` : ""}`));
