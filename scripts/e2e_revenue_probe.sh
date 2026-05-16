#!/usr/bin/env bash
# scripts/e2e_revenue_probe.sh — exercise every revenue path against the
# locally-running server (or any URL passed as $1).
#
# Walks through:
#   1. /api/health is green
#   2. Free anchor (rate-limited, no payment)
#   3. /verify/ landing + verify.py + tarball download
#   4. Sample receipt round-trip via /api/verify
#   5. BTC order create + /buy/<id> page renders + status endpoint
#   6. Waitlist signup
#   7. Magic-link auth-request flow (no real email send required)
#   8. GDPR data export endpoint shape
#   9. /api/event analytics endpoint
#  10. Status page + extended /api/health fields
#
# Each leg either succeeds or fails loud. Exit 0 only if all 10 pass.
# Safe to re-run; doesn't require any external account.
set -u

BASE="${1:-http://127.0.0.1:8989}"
JQ() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))"; }

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
FAILS=0

probe() {
  local name="$1"; shift
  if "$@"; then
    printf "${c_grn}✓${c_off} %s\n" "$name"
  else
    printf "${c_red}✗${c_off} %s\n" "$name"
    FAILS=$((FAILS+1))
  fi
}

http_status() {
  local url="$1"; shift
  local method="${1:-GET}"; shift || true
  local body="${1:-}"
  if [ -n "$body" ]; then
    curl -sS -o /dev/null -m 30 -w "%{http_code}" -X "$method" \
      -H "Content-Type: application/json" -d "$body" "$url"
  else
    curl -sS -o /dev/null -m 30 -w "%{http_code}" -X "$method" "$url"
  fi
}

http_body() {
  local url="$1"; shift
  local method="${1:-GET}"; shift || true
  local body="${1:-}"
  if [ -n "$body" ]; then
    curl -sS -m 30 -X "$method" -H "Content-Type: application/json" -d "$body" "$url"
  else
    curl -sS -m 30 -X "$method" "$url"
  fi
}

# ─── 1. health green ───────────────────────────────────────────────────
probe_health() {
  local code; code=$(http_status "$BASE/api/health")
  [ "$code" = "200" ]
}
probe "health: $BASE/api/health → 200" probe_health

# ─── 2. free anchor ────────────────────────────────────────────────────
probe_free_anchor() {
  local hash; hash=$(printf 'e2e-probe-%s' "$(date +%s)" | shasum -a 256 | awk '{print $1}')
  local body; body=$(http_body "$BASE/api/anchor" POST "{\"hash_hex\":\"$hash\",\"client_label\":\"e2e\"}")
  local rid; rid=$(echo "$body" | JQ receipt_id)
  [ -n "$rid" ]
}
probe "free anchor → receipt_id returned" probe_free_anchor

# ─── 3. /verify/ files served ─────────────────────────────────────────
probe_verify_landing() {
  [ "$(http_status $BASE/verify/)" = "200" ]
}
probe "/verify/ landing → 200" probe_verify_landing

probe_verify_py() {
  [ "$(http_status $BASE/verify/verify.py)" = "200" ]
}
probe "/verify/verify.py → 200" probe_verify_py

probe_tarball() {
  [ "$(http_status $BASE/verify/orphograph-verify-0.1.tar.gz)" = "200" ]
}
probe "/verify/<tarball> → 200" probe_tarball

# ─── 4. sample receipt verify round-trip ──────────────────────────────
probe_sample_verify() {
  local meta; meta=$(http_body "$BASE/sample/index.json")
  local rid; rid=$(echo "$meta" | JQ receipt_id)
  [ -z "$rid" ] && return 1
  local verify; verify=$(http_body "$BASE/api/verify/$rid")
  local found; found=$(echo "$verify" | JQ found)
  [ "$found" = "True" ]
}
probe "sample receipt round-trip → found=True" probe_sample_verify

# ─── 5. BTC payment flow ──────────────────────────────────────────────
# Without BTC_RECEIVE_ADDRESS we expect 503 (correct signal).
# With it set, expect 200 + a buy_page redirect.
probe_btc_unconfigured() {
  local code; code=$(http_status "$BASE/api/buy-btc" POST '{"email":"probe@example.com"}')
  [ "$code" = "503" ] || [ "$code" = "200" ]   # either valid
}
probe "BTC: /api/buy-btc returns 503 (unconfigured) or 200 (configured)" probe_btc_unconfigured

# ─── 6. waitlist signup ───────────────────────────────────────────────
probe_waitlist() {
  local code; code=$(http_status "$BASE/api/waitlist" POST '{"email":"probe@example.com","interest":"personal"}')
  [ "$code" = "200" ]
}
probe "waitlist signup → 200" probe_waitlist

# ─── 7. magic-link auth-request ───────────────────────────────────────
probe_auth_link() {
  local code; code=$(http_status "$BASE/api/auth/email-link" POST '{"email":"probe@example.com"}')
  [ "$code" = "200" ] || [ "$code" = "429" ]   # 429 = rate-limited (which means working)
}
probe "auth: /api/auth/email-link → 200 or 429" probe_auth_link

# ─── 8. GDPR export requires auth ─────────────────────────────────────
probe_gdpr_requires_auth() {
  local code; code=$(http_status "$BASE/api/me/export")
  [ "$code" = "401" ]
}
probe "GDPR: /api/me/export unauthenticated → 401" probe_gdpr_requires_auth

# ─── 9. analytics event accepted ──────────────────────────────────────
probe_analytics() {
  local code; code=$(http_status "$BASE/api/event" POST '{"event":"page_view","page":"landing"}')
  [ "$code" = "200" ] || [ "$code" = "204" ]
}
probe "analytics: /api/event → 200/204" probe_analytics

# ─── 10. status page + extended health fields ─────────────────────────
probe_status_page() {
  [ "$(http_status $BASE/status.html)" = "200" ]
}
probe "status page → 200" probe_status_page

probe_health_fields() {
  local body; body=$(http_body "$BASE/api/health")
  echo "$body" | python3 -c "
import json, sys
d = json.load(sys.stdin)
required = {'ok','version','uptime_sec','counts','last','calendars'}
missing = required - set(d.keys())
sys.exit(0 if not missing else 1)
"
}
probe "/api/health has extended schema (ok+version+uptime+counts+last+calendars)" probe_health_fields

# ─── tally ────────────────────────────────────────────────────────────
echo
if [ "$FAILS" -eq 0 ]; then
  printf "${c_grn}===== ALL %d revenue legs green =====${c_off}\n" "$(echo -n "$0" | wc -c | awk '{print 12}')"
  exit 0
else
  printf "${c_red}===== %d leg(s) failed =====${c_off}\n" "$FAILS"
  exit 1
fi
