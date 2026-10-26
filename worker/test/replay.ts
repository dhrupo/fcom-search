import { readFileSync } from "node:fs";
import { join } from "node:path";

export const FIXTURES = join(import.meta.dirname, "fixtures");
export const fixture = (name: string) => readFileSync(join(FIXTURES, name), "utf8");
const PAGES: Record<string, string> = { sylhet: "slug_sylhet", london: "bootstrap_london", dhaka: "dhaka_slug",
  paris: "slug_paris", moulvibazar: "slug_moulvibazar" };
const REDIRECTS: Record<string, string> = { moulvibazar: "https://www.facebook.com/marketplace/category/search/?query=iphone" };
const MAX_PRICE = 2147483647;

interface Sent { url: string; method: string; headers: Record<string, string>; form: Record<string, string> }

function respond(body: string, status: number, url: string, headers: [string, string][] = []) {
  const response = new Response(body, { status, headers });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

/** Like Facebook: up to 24 saved listings whose price lies in the requested band. */
function graphqlAnswer(low: number, high: number): string {
  if (low <= 0 && high >= MAX_PRICE) return fixture("graphql_sylhet_point.json");
  const edges: unknown[] = [], seen = new Set<string>();
  for (const name of ["graphql_sylhet_point.json", "graphql_sylhet_band_50k_80k.json"]) {
    for (const edge of JSON.parse(fixture(name)).data.marketplace_search.feed_units.edges) {
      const price = edge.node.listing.listing_price?.amount;
      if (price != null && low <= Number(price) && Number(price) <= high && !seen.has(edge.node.listing.id)) {
        seen.add(edge.node.listing.id);
        edges.push(edge);
      }
    }
  }
  return JSON.stringify({ data: { marketplace_search: { feed_units: {
    edges: edges.slice(0, 24), page_info: { end_cursor: "test-cursor", has_next_page: true } } } } });
}

/** A fetch that answers Marketplace URLs from the saved, reduced logged-out pages; nothing reaches Facebook. */
export function replayFetch({ status = {} as Record<string, number>, graphqlStatus = 200, graphqlBody = undefined as string | undefined,
  bootstrapHtml = undefined as string | undefined, pages = {} as Record<string, string> } = {}) {
  const sent: Sent[] = [];
  const fetch = async (input: string, init: RequestInit = {}) => {
    const headers = Object.fromEntries(new Headers(init.headers).entries());
    const form = init.body ? Object.fromEntries(new URLSearchParams(String(init.body))) : {};
    sent.push({ url: input, method: init.method ?? "GET", headers, form });
    if (init.method === "POST") {
      const variables = JSON.parse(form.variables);
      if (!variables.params.bqf.query.toLowerCase().includes("phone")) {
        return respond(JSON.stringify({ data: { marketplace_search: { feed_units: {
          edges: [], page_info: { end_cursor: null, has_next_page: false } } } } }), graphqlStatus, input);
      }
      const bounds = variables.params.browse_request_params;
      const body = graphqlBody ?? graphqlAnswer(bounds.filter_price_lower_bound / 100, bounds.filter_price_upper_bound / 100);
      return respond(body, graphqlStatus, input);
    }
    const slug = new URL(input).pathname.match(/^\/marketplace\/([^/]+)\/search/)?.[1] ?? "";
    if (slug in status) return respond("", status[slug], input);
    const page = pages[slug] ?? PAGES[slug];
    if (!page) return respond("", 404, input);
    const html = slug === "london" && bootstrapHtml !== undefined ? bootstrapHtml : fixture(`${page}.html`);
    const cookies: [string, string][] = slug === "london"
      ? [["set-cookie", "datr=test-browser-id; Path=/; Secure"], ["set-cookie", "fr=test-fr; Path=/"]] : [];
    return respond(html, 200, REDIRECTS[slug] ?? input, cookies);
  };
  return { fetch, sent, gets: () => sent.filter((s) => s.method === "GET"), posts: () => sent.filter((s) => s.method === "POST") };
}
