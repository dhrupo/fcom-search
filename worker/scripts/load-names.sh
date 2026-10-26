#!/bin/bash
# Loads the private name list (kept outside the repo) into D1 for title scrubbing; prints counts only.
#   scripts/load-names.sh [--local --persist-to <dir>]   (default: the remote database)
set -euo pipefail
cd "$(dirname "$0")/.."
names="${FCS_SCRUB_MAP:-$HOME/.config/fcommerce-search/scrub_map.txt}"
where=("${@:---remote}")
sql=$(mktemp)
chmod 600 "$sql"
trap 'rm -f "$sql"' EXIT
python3 - "$names" "$sql" <<'PY'
import sys
names = sorted({n.strip() for n in open(sys.argv[1], encoding="utf-8") if n.strip()})
if not names:
    sys.exit("name list is empty: refusing to clear the table")
quoted = ",".join("('" + n.replace("'", "''") + "')" for n in names)
open(sys.argv[2], "w", encoding="utf-8").write(f"DELETE FROM private_names;\nINSERT INTO private_names (name) VALUES {quoted};\n")
print(f"{len(names)} names prepared")
PY
npx wrangler d1 execute fcommerce-search "${where[@]}" --file "$sql" >/dev/null
count=$(npx wrangler d1 execute fcommerce-search "${where[@]}" --json --command "SELECT count(*) AS n FROM private_names" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["results"][0]["n"])')
echo "private_names now holds $count names"
