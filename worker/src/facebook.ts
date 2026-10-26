export type Outcome = "ok" | "nothing_new" | "blocked" | "schema_changed" | "gone" | "crash";
export type Price = { amount: number; formatted: string | null } | "unknown";
export interface Listing {
  item_id: string; kind: "listing"; created_at: number | null; title: string | null; price: Price;
  city: string | null; images: string[]; sold: boolean; permalink: string;
}

const BLOCK_CODES = new Set([1675004]);
const LOGIN_REQUIRED_CODES = new Set([1357001]);
const LOGIN_URL_PARTS = ["/login", "/accounts/login", "/checkpoint"];
const HTML_BLOCK_MARKERS = ["CometErrorRoot", "blocked by our security", "temporarily blocked"];
const JSON_SCRIPT = '<script type="application/json"';

const isObject = (x: unknown): x is Record<string, any> => typeof x === "object" && x !== null && !Array.isArray(x);

/** Every object under obj, shallowest first. */
export function* walk(obj: unknown): Generator<Record<string, any>> {
  const queue: unknown[] = [obj];
  for (let i = 0; i < queue.length; i++) {
    const node = queue[i];
    if (Array.isArray(node)) queue.push(...node);
    else if (isObject(node)) {
      yield node;
      queue.push(...Object.values(node));
    }
  }
}

/** Zero or missing means the seller did not give a price ("inbox"): keep it as unknown. */
export function parsePrice(price: any): Price {
  const amount = Number(price?.amount || 0);
  return amount > 0 ? { amount, formatted: price.formatted_amount ?? null } : "unknown";
}

export function parseListing(node: Record<string, any>): Listing {
  const geo = node.location?.reverse_geocode ?? {};
  const photo = node.primary_listing_photo?.image?.uri;
  return {
    item_id: String(node.id), kind: "listing", created_at: node.creation_time ?? null,
    title: node.marketplace_listing_title ?? null, price: parsePrice(node.listing_price),
    city: geo.city_page?.display_name || geo.city || null, images: photo ? [photo] : [], sold: Boolean(node.is_sold),
    permalink: `https://www.facebook.com/marketplace/item/${node.id}/`,
  };
}

/** Listings from a Marketplace search page; only the JSON blocks that mention the feed are parsed (CPU budget). */
export function parseSearch(html: string): [Outcome, Listing[]] {
  const feeds: Record<string, any>[] = [];
  let from = 0, hit: number;
  while ((hit = html.indexOf("marketplace_search", from)) !== -1) {
    const open = html.lastIndexOf(JSON_SCRIPT, hit), start = html.indexOf(">", open) + 1, end = html.indexOf("</script>", hit);
    from = hit + 1;
    if (end === -1) continue;
    from = end;
    let data: unknown;
    try { data = JSON.parse(html.slice(start, end)); } catch { continue; }
    for (const d of walk(data)) if (isObject(d.marketplace_search)) feeds.push(d.marketplace_search);
  }
  if (!feeds.length) return ["schema_changed", []];
  const listings = listingsIn(feeds);
  return [listings.length ? "ok" : "nothing_new", listings];
}

export function listingsIn(roots: unknown[]): Listing[] {
  const listings: Listing[] = [], seen = new Set<string>();
  for (const root of roots) {
    for (const d of walk(root)) {
      if (d.__typename === "GroupCommerceProductItem" && "marketplace_listing_title" in d && d.id && !seen.has(d.id)) {
        seen.add(d.id);
        listings.push(parseListing(d));
      }
    }
  }
  return listings;
}

/** Every top-level JSON object in a reply; Facebook streams one per line after `for (;;);`. */
export function jsonDocuments(text: string): Record<string, any>[] {
  const body = text.replaceAll("for (;;);", "");
  try {
    const whole = JSON.parse(body);
    return isObject(whole) ? [whole] : [];
  } catch { /* streamed: one object per line */ }
  const docs: Record<string, any>[] = [];
  for (const line of body.split("\n")) {
    if (!line.trim().startsWith("{")) continue;
    try { const doc = JSON.parse(line); if (isObject(doc)) docs.push(doc); } catch { break; }
  }
  return docs;
}

const dig = (obj: any, path: string[]) => path.reduce((o, key) => (isObject(o) ? o[key] : undefined), obj);
const samePath = (a: unknown, b: string[]) => Array.isArray(a) && a.length === b.length && a.every((x, i) => x === b[i]);

/** Label a raw reply. `connection` is the key path under `data` that must hold the result. */
export function classify(status: number, url: string, text: string, connection?: string[]): Outcome {
  if ([401, 403, 429].includes(status)) return "blocked";
  if ([404, 410].includes(status)) return "gone";
  if (status !== 200) return "crash";
  if (LOGIN_URL_PARTS.some((part) => url.includes(part))) return "blocked";
  if (!connection) {
    const head = text.slice(0, 20000);
    return HTML_BLOCK_MARKERS.some((m) => head.includes(m)) ? "blocked" : "ok";
  }
  const docs = jsonDocuments(text);
  const errors = docs.flatMap((d) => (Array.isArray(d.errors) ? d.errors : [])).filter(isObject);
  if (errors.some((e) => BLOCK_CODES.has(e.code)) || docs.some((d) => d.require_login)
      || docs.some((d) => LOGIN_REQUIRED_CODES.has(d.error))) return "blocked";
  const found = docs.filter((d) => !("path" in d)).map((d) => dig(d.data, connection))[0];
  if (!isObject(found)) return "schema_changed";
  const streamed = docs.some((d) => samePath((d.path ?? []).slice(0, connection.length + 1), [...connection, "edges"]));
  const hasPageInfo = "page_info" in found
    || docs.some((d) => samePath(d.path, connection) && isObject(d.data) && "page_info" in d.data);
  if ((Array.isArray(found.edges) && found.edges.length) || streamed || !("edges" in found)) return "ok";
  return hasPageInfo ? "nothing_new" : "schema_changed";
}
