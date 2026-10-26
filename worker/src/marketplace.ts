import { classify, listingsIn, type Listing } from "./facebook.ts";
import type { DB } from "./geo.ts";

export type Fetch = (url: string, init?: RequestInit) => Promise<Response>;
export type Status = "ok" | "blocked" | "schema_changed" | "failed" | "unavailable";
export interface PointResult { status: Status; listings: Listing[]; full: boolean }
interface Session { docId: string; lsd: string; template: Record<string, any>; cookie: string }

export const PAGE_SIZE = 24;
const FACEBOOK = "https://www.facebook.com";
const BOOTSTRAP_PATH = "/marketplace/london/search?query=phone";
const FRIENDLY = "CometMarketplaceSearchContentContainerQuery";
const PRELOADED = /ContentContainerQueryRelayPreloader[^"]*","queryID":"(\d+)","variables":/;
const LSD = /"LSD",\[\],\{"token":"([^"]+)"/;
const SESSION_SECONDS = 30 * 60;
const NO_UPPER_BOUND = 214748364700;
const FEED = ["marketplace_search", "feed_units"];
const CHROME = {
  "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
  "sec-ch-ua": '"Chromium";v="141", "Google Chrome";v="141", "Not?A_Brand";v="8"', "sec-ch-ua-mobile": "?0",
  "sec-ch-ua-platform": '"macOS"', "accept-language": "en-US,en;q=0.9",
};
// Without the navigation headers Facebook answers the bootstrap page with HTTP 400 (Cloudflare spike, 2026-10-25).
export const NAVIGATION_HEADERS = { ...CHROME,
  accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
  "sec-fetch-dest": "document", "sec-fetch-mode": "navigate", "sec-fetch-site": "none", "sec-fetch-user": "?1",
  "upgrade-insecure-requests": "1" };
const STATUS: Record<string, Status> = { ok: "ok", nothing_new: "ok", blocked: "blocked", schema_changed: "schema_changed" };

/** The object starting at `start`, or undefined while the text ends before it closes. */
function objectAt(text: string, start: number): Record<string, any> | undefined {
  let depth = 0;
  for (let i = start; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c === 123) depth++;
    else if (c === 125 && --depth === 0) return JSON.parse(text.slice(start, i + 1));
  }
  return undefined;
}

function sessionIn(text: string, cookie: string): Session | null | undefined {
  const found = PRELOADED.exec(text), lsd = LSD.exec(text);
  if (!found || !lsd) return undefined;
  const template = objectAt(text, found.index + found[0].length);
  if (!template) return undefined;
  const params = template.params ?? {};
  const usable = [params.browse_request_params, params.bqf].every((p) => typeof p === "object" && p && !Array.isArray(p));
  return usable ? { docId: found[1], lsd: lsd[1], template, cookie } : null;
}

/** Marketplace search at a point, radius and price band, logged out, through the page's own request. */
export class PointSearch {
  private db: DB; private fetch: Fetch; private origin: string; private now: () => number;

  constructor(db: DB, fetch: Fetch, origin: string, now: () => number) {
    this.db = db; this.fetch = fetch; this.origin = origin; this.now = now;
  }

  private async bootstrap(): Promise<Session | null> {
    let response: Response;
    try {
      response = await this.fetch(this.origin + BOOTSTRAP_PATH, { headers: NAVIGATION_HEADERS });
    } catch {
      return null;
    }
    if (!response.body) return null;
    const cookie = response.headers.getSetCookie().map((c) => c.split(";")[0]).join("; ");
    // Only the page's first ~250 KB holds the tokens: stop reading there (CPU budget, W0 spike).
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let text = "";
    try {
      for (;;) {
        const { value, done } = await reader.read();
        text += decoder.decode(value, { stream: !done });
        const session = sessionIn(text, cookie);
        if (session !== undefined || done) {
          if (!done) await reader.cancel();
          if (!session) return null;
          await this.db.prepare("INSERT OR REPLACE INTO sessions (name, data, at) VALUES ('marketplace', ?, ?)")
            .bind(JSON.stringify(session), this.now()).run();
          return session;
        }
      }
    } catch {
      return null;
    }
  }

  private async session(): Promise<Session | null> {
    const row = await this.db.prepare("SELECT data, at FROM sessions WHERE name = 'marketplace'").first<{ data: string; at: number }>();
    if (row && this.now() - row.at <= SESSION_SECONDS) return JSON.parse(row.data);
    return this.bootstrap();
  }

  /** `full` means the band held a whole page, so more listings may be inside it. */
  async search(keyword: string, lat: number, lon: number, radiusKm: number,
               band: [number | null, number | null] = [null, null]): Promise<PointResult> {
    const session = await this.session();
    if (!session) return { status: "unavailable", listings: [], full: false };
    const variables = structuredClone(session.template);
    Object.assign(variables, { cursor: null, count: PAGE_SIZE, buyLocation: { latitude: lat, longitude: lon } });
    variables.params.bqf.query = keyword;
    const [low, high] = band;
    Object.assign(variables.params.browse_request_params, {
      filter_location_latitude: lat, filter_location_longitude: lon, filter_radius_km: radiusKm,
      // Facebook takes prices in minor units (poisha, pence).
      filter_price_lower_bound: Math.round((low ?? 0) * 100),
      filter_price_upper_bound: high === null ? NO_UPPER_BOUND : Math.round(high * 100),
    });
    const form = new URLSearchParams({ av: "0", __user: "0", __a: "1", __comet_req: "15", fb_dtsg: "",
      fb_api_caller_class: "RelayModern", fb_api_req_friendly_name: FRIENDLY, server_timestamps: "true",
      doc_id: session.docId, variables: JSON.stringify(variables), lsd: session.lsd });
    let response: Response, text: string;
    try {
      response = await this.fetch(this.origin + "/api/graphql/", { method: "POST", body: form.toString(), headers: { ...CHROME,
        "content-type": "application/x-www-form-urlencoded", origin: FACEBOOK, referer: FACEBOOK + BOOTSTRAP_PATH,
        "x-fb-friendly-name": FRIENDLY, "x-fb-lsd": session.lsd, "sec-fetch-site": "same-origin", "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty", ...(session.cookie ? { cookie: session.cookie } : {}) } });
      text = await response.text();
    } catch {
      return { status: "failed", listings: [], full: false };
    }
    const status = STATUS[classify(response.status, response.url, text, FEED)] ?? "failed";
    if (status !== "ok") return { status, listings: [], full: false };
    const docs = text.split("\n").flatMap((line) => { try { return [JSON.parse(line)]; } catch { return []; } });
    const listings = listingsIn(docs);
    return { status, listings, full: listings.length >= PAGE_SIZE };
  }
}
