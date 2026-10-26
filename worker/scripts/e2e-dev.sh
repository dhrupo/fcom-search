#!/bin/bash
# One E2E Worker: fresh local D1 (schema + cities), Facebook/Photon/Turnstile pointed at the replay upstream.
#   scripts/e2e-dev.sh <worker port> <upstream port>
set -euo pipefail
cd "$(dirname "$0")/.."
state=$(mktemp -d)
npx wrangler d1 execute fcommerce-search --local --persist-to "$state" --file schema.sql >/dev/null
node scripts/geo-sql.ts > "$state/geo.sql"
npx wrangler d1 execute fcommerce-search --local --persist-to "$state" --file "$state/geo.sql" >/dev/null
up="http://127.0.0.1:$2"
exec npx wrangler dev --port "$1" --ip 127.0.0.1 --persist-to "$state" --var "FB_ORIGIN:$up" --var "PHOTON_ORIGIN:$up/photon" \
  --var TURNSTILE_SECRET:e2e-secret --var "TURNSTILE_VERIFY_URL:$up/turnstile/siteverify" \
  --var "AI_ORIGIN:$up/ai" --var OPENROUTER_API_KEY:e2e-key </dev/null
