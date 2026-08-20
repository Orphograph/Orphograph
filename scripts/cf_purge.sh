#!/usr/bin/env bash
# cf_purge.sh — purge specific file(s) from the Cloudflare edge cache for orphograph.com.
#
# WHY: deleting a file from the origin (Fly) does NOT evict Cloudflare's edge cache.
# The old /favicon.svg (the retired anchor icon) was cached `immutable, max-age=2592000`,
# so CF keeps serving it (~29 days) until purged. This forces it gone now.
#
# USAGE (run in YOUR terminal so the token stays out of any transcript):
#   export CLOUDFLARE_API_TOKEN='...your token with Zone > Cache Purge...'
#   bash scripts/cf_purge.sh                       # purges the default list below
#   bash scripts/cf_purge.sh https://orphograph.com/foo.css https://orphograph.com/bar.js
#
# The token needs the "Zone > Cache Purge" permission on the orphograph.com zone.
# CLOUDFLARE_ZONE_ID is optional — if unset, it is derived from the domain via the API.
set -euo pipefail

DOMAIN="orphograph.com"
CF_API="https://api.cloudflare.com/client/v4"

# default purge targets
DEFAULT_URLS=(
  "https://orphograph.com/favicon.svg"
)
URLS=("$@"); [ ${#URLS[@]} -eq 0 ] && URLS=("${DEFAULT_URLS[@]}")

# pull token/zone from env, else from .env.local (same convention as cf_point_to_fly.sh)
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] && [ -f "$(dirname "$0")/../.env.local" ]; then
  # shellcheck disable=SC1090
  set +u; . <(grep -E '^\s*CLOUDFLARE_(API_TOKEN|ZONE_ID)=' "$(dirname "$0")/../.env.local" | sed 's/^\s*//'); set -u
fi
if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
  echo "error: CLOUDFLARE_API_TOKEN is not set. Run:" >&2
  echo "  export CLOUDFLARE_API_TOKEN='<your token with Zone>Cache Purge>'" >&2
  exit 2
fi
AUTH=(-H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}")

# verify the token works
whoami=$(curl -s "${AUTH[@]}" "${CF_API}/user/tokens/verify")
if ! printf '%s' "$whoami" | grep -q '"status":"active"'; then
  echo "error: token verify failed — check the token / its permissions:" >&2
  printf '%s\n' "$whoami" | head -c 400 >&2; echo >&2; exit 3
fi
echo "token OK (active)."

# resolve zone id if not provided
ZONE="${CLOUDFLARE_ZONE_ID:-}"
if [ -z "$ZONE" ]; then
  ZONE=$(curl -s "${AUTH[@]}" "${CF_API}/zones?name=${DOMAIN}" \
    | python3 -c "import sys,json;r=json.load(sys.stdin).get('result') or [];print(r[0]['id'] if r else '')")
  [ -z "$ZONE" ] && { echo "error: could not resolve zone id for ${DOMAIN} (token needs Zone>Read)"; exit 4; }
  echo "resolved zone id for ${DOMAIN}."
fi

# build JSON body {"files":[...]}
BODY=$(python3 -c "import json,sys;print(json.dumps({'files':sys.argv[1:]}))" "${URLS[@]}")
echo "purging ${#URLS[@]} url(s):"; printf '  %s\n' "${URLS[@]}"

resp=$(curl -s -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  "${CF_API}/zones/${ZONE}/purge_cache" --data "$BODY")
if printf '%s' "$resp" | grep -q '"success":true'; then
  echo "purge accepted by Cloudflare."
else
  echo "purge FAILED:"; printf '%s\n' "$resp" | head -c 500; echo; exit 5
fi

# verify the cache actually flipped (give the edge a moment)
echo "verifying (waiting 5s for edge propagation)…"; sleep 5
# Honest UA. NEVER a browser-spoofing string here: this loop exists to find out
# whether the edge is still serving the old bytes, and a spoofed UA hides the
# exact blocking the check is for. If Cloudflare ever treats this agent
# differently, that IS the finding.
UA="Orphograph-cache-purge/1.0 (+https://orphograph.com)"
for u in "${URLS[@]}"; do
  hdr=$(curl -s -A "$UA" -D - -o /dev/null "$u" || true)
  code=$(printf '%s' "$hdr" | awk 'NR==1{print $2}')
  ccs=$(printf '%s' "$hdr" | tr -d '\r' | awk -F': ' 'tolower($1)=="cf-cache-status"{print $2}')
  echo "  $u -> HTTP ${code:-?} cf-cache-status=${ccs:-none}  ($( [ "$code" = "404" ] && echo "GONE (origin 404)" || echo "cache no longer HIT of the old file" ))"
done
echo "done."
