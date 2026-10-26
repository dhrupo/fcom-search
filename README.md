# fcommerce-search

Search and compare phone, clothes and outdoor offers from Facebook and Instagram shops in Sylhet and Dhaka.

## Live search

A public page (https://fcomsearch.dhrupo.workers.dev/) that searches Facebook Marketplace live, logged out: type
what you want, pick a place (or use your location) and a radius, a price range and how recent, then press Search.

- `web/`: the static Nuxt page (Node 22, `.nvmrc`).
- `worker/`: one Cloudflare Worker (Workers Free) that serves the page and the API. D1 holds the city list,
  the listing pool, the Facebook session and search state. Turnstile and per-IP rate limits guard searches.
- `pipeline/`: the earlier phone-offer pipeline; live search does not use it.

Tests (no request reaches Facebook, Photon or Cloudflare):

```sh
cd worker && npm install && FCS_SCRUB_MAP=~/.config/fcommerce-search/scrub_map.txt npm test
cd web && npm install && npx nuxi generate && npx playwright test   # two local Workers on wrangler dev
```

Deploy (from `worker/`):

```sh
npx wrangler d1 create fcommerce-search            # put the database_id into wrangler.toml
npx wrangler d1 execute fcommerce-search --remote --file schema.sql
# once only: the city list writes ~109k rows (34k cities + 3 indexes), over Workers Free's 100k rows/day
node scripts/geo-sql.ts > /tmp/geo.sql && npx wrangler d1 execute fcommerce-search --remote --file /tmp/geo.sql
scripts/load-names.sh                              # private name list, never committed
npx wrangler secret put TURNSTILE_SECRET
(cd ../web && NUXT_PUBLIC_TURNSTILE_SITE_KEY=<site key> npx nuxi generate) && npx wrangler deploy
```

Places © OpenStreetMap contributors (Photon). Cities: GeoNames (CC BY 4.0), in `worker/geo/`.
