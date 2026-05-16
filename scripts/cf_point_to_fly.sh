#!/usr/bin/env bash
# cf_point_to_fly.sh — one-shot Cloudflare DNS pointer after a Fly.io deploy.
#
# Reads CLOUDFLARE_API_TOKEN + CLOUDFLARE_ZONE_ID from .env.local
# (populated by scripts/setup_email.py). Pulls the Fly IPs from `fly ips list`,
# pushes A + AAAA + (optional) CNAME records via Cloudflare API.
#
# Run AFTER `fly deploy` succeeds:
#   bash ~/orphograph/scripts/cf_point_to_fly.sh
#
# Idempotent — re-running updates the records in place.

set -u
cd "$(dirname "$0")/.."

AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
INK=$'\033[38;2;31;29;26m'
MUTED=$'\033[38;2;131;126;117m'
ERR=$'\033[38;2;178;80;80m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

DOMAIN="orphograph.com"

# Load secrets.
if [ -f .env.local ]; then
  # Source quoted values safely.
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
else
  echo "${ERR}error: .env.local not found — run setup_email.py first${RESET}" >&2
  exit 1
fi

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ] || [ -z "${CLOUDFLARE_ZONE_ID:-}" ]; then
  echo "${ERR}error: CLOUDFLARE_API_TOKEN or CLOUDFLARE_ZONE_ID missing in .env.local${RESET}" >&2
  echo "       run setup_email.py first; it captures both" >&2
  exit 1
fi

if ! command -v fly >/dev/null 2>&1; then
  echo "${ERR}error: fly CLI not installed${RESET}" >&2
  echo "       brew install flyctl" >&2
  exit 1
fi

echo
echo "${AMBER}${BOLD}orphograph DNS — Cloudflare pointer${RESET}"
echo "${MUTED}───────────────────────────────────────${RESET}"

# Pull Fly anycast IPs.
echo "${INK}Pulling Fly IPs…${RESET}"
FLY_JSON=$(fly ips list -j 2>&1) || { echo "${ERR}fly ips list failed — are you logged in? (fly auth whoami)${RESET}"; exit 1; }
FLY_V4=$(echo "$FLY_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((x['Address'] for x in d if x.get('Type')=='v4'), ''))")
FLY_V6=$(echo "$FLY_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((x['Address'] for x in d if x.get('Type')=='v6'), ''))")

if [ -z "$FLY_V4" ] || [ -z "$FLY_V6" ]; then
  echo "${ERR}error: could not extract IPs from fly output${RESET}" >&2
  echo "$FLY_JSON" >&2
  exit 1
fi

echo "  ${SAGE}✓${RESET} ${INK}IPv4: ${FLY_V4}${RESET}"
echo "  ${SAGE}✓${RESET} ${INK}IPv6: ${FLY_V6}${RESET}"

CF_API="https://api.cloudflare.com/client/v4"

# Helper — upsert a DNS record.
upsert_record() {
  local rtype="$1"
  local name="$2"
  local content="$3"
  local proxied="${4:-true}"

  # Find existing record (if any) of this type+name.
  local existing
  existing=$(curl -s -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    "${CF_API}/zones/${CLOUDFLARE_ZONE_ID}/dns_records?type=${rtype}&name=${name}.${DOMAIN}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((r['id'] for r in d.get('result',[])), ''))" 2>/dev/null || echo "")
  # Handle name=="@" (root) specially in the API name field.
  local full_name
  if [ "$name" = "@" ]; then full_name="$DOMAIN"; else full_name="${name}.${DOMAIN}"; fi

  local payload
  payload=$(printf '{"type":"%s","name":"%s","content":"%s","ttl":300,"proxied":%s}' \
    "$rtype" "$full_name" "$content" "$proxied")

  if [ -n "$existing" ]; then
    # Update.
    local out
    out=$(curl -s -X PUT -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      "${CF_API}/zones/${CLOUDFLARE_ZONE_ID}/dns_records/${existing}" \
      -d "$payload")
    if echo "$out" | grep -q '"success":true'; then
      echo "  ${SAGE}✓${RESET} updated ${rtype} ${full_name} → ${content}"
    else
      echo "  ${ERR}✗${RESET} update ${rtype} ${full_name}: $(echo "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("errors",[]))' 2>/dev/null || echo "$out" | head -c 200)"
    fi
  else
    # Create.
    local out
    out=$(curl -s -X POST -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      "${CF_API}/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
      -d "$payload")
    if echo "$out" | grep -q '"success":true'; then
      echo "  ${SAGE}✓${RESET} created ${rtype} ${full_name} → ${content}"
    else
      echo "  ${ERR}✗${RESET} create ${rtype} ${full_name}: $(echo "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("errors",[]))' 2>/dev/null || echo "$out" | head -c 200)"
    fi
  fi
}

echo
echo "${INK}Pushing DNS records to Cloudflare…${RESET}"
upsert_record "A"     "@"   "$FLY_V4" "true"
upsert_record "AAAA"  "@"   "$FLY_V6" "true"
upsert_record "CNAME" "www" "$DOMAIN" "true"

echo
echo "${INK}Requesting Fly TLS certificates…${RESET}"
fly certs add "$DOMAIN"     2>&1 | sed 's/^/  /' || true
fly certs add "www.$DOMAIN" 2>&1 | sed 's/^/  /' || true

echo
echo "${SAGE}✓${RESET} ${INK}DNS pointed.${RESET}"
echo "${MUTED}DNS propagation typically completes in 60s with Cloudflare's anycast.${RESET}"
echo "${MUTED}Cert issuance takes 1-5 minutes after propagation.${RESET}"
echo
echo "${INK}Verify in ~2 minutes:${RESET}"
echo "  ${MUTED}curl -sI https://${DOMAIN}/api/health | head -3${RESET}"
echo "  ${MUTED}fly certs check ${DOMAIN}${RESET}"
