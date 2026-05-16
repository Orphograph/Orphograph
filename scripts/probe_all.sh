#!/usr/bin/env bash
#
# probe_all.sh — Readiness gate for Orphograph deploys.
#
# Hits every public Orphograph endpoint and asserts expected HTTP status
# codes. Designed to run against a freshly-started instance (dev or prod)
# as the final go/no-go before a deploy is declared healthy.
#
# Usage:
#   ./scripts/probe_all.sh                          # defaults to local dev
#   ./scripts/probe_all.sh http://127.0.0.1:8989
#   ./scripts/probe_all.sh https://orphograph.com
#
# Behavior notes:
#   - Public endpoints assert HTTP 200.
#   - Authenticated-only endpoints (no session cookie) assert HTTP 401.
#   - Founder-token endpoints (no token in header) assert HTTP 404.
#   - The anchor flow POSTs a deterministic "probe-ignore" hash and then
#     verifies the four receipt-bound endpoints against that fresh id.
#     When BASE_URL points at production, the anchor probe is SKIPPED
#     (to avoid polluting the production ledger). Override with
#     PROBE_ANCHOR_ALLOW_PROD=1 if you really mean it.
#   - Dangerous routes (signout, account delete, cancel subscription) are
#     NEVER hit — too easy to nuke real state.
#
# Exit codes:
#   0  all probes passed
#   1  1–3 probes failed
#   2  4+ probes failed (deploy is unhealthy; do not promote)
#
# Stdlib only — curl + python3 fallback for JSON when jq is absent.

set -u

BASE_URL="${1:-http://127.0.0.1:8989}"
BASE_URL="${BASE_URL%/}"

# --- Palette (amber / sage / red) matching launch_email_setup.sh -------------
AMBER=$'\033[38;2;192;138;62m'
SAGE=$'\033[38;2;74;154;115m'
ERR=$'\033[38;2;178;80;80m'
MUTED=$'\033[38;2;131;126;117m'
BOLD=$'\033[1m'
RESET=$'\033[0m'

# --- JSON helper: jq if present, else python3 --------------------------------
json_get() {
  # Usage: json_get <field>  (reads JSON from stdin)
  local field="$1"
  if command -v jq >/dev/null 2>&1; then
    jq -r ".${field} // empty"
  else
    python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    v=d.get('${field}','')
    print(v if v is not None else '')
except Exception:
    pass"
  fi
}

# --- Counters ----------------------------------------------------------------
PASS=0
FAIL=0
TOTAL=0
FAILED_PROBES=()

# --- Determine production-ness ----------------------------------------------
IS_PROD=0
case "$BASE_URL" in
  https://orphograph.com|https://www.orphograph.com|https://*.orphograph.com)
    IS_PROD=1
    ;;
esac

probe() {
  # Usage: probe <method> <path> <expected_status> [curl_extra_args...]
  local method="$1"
  local rel_path="$2"
  local expected="$3"
  shift 3
  TOTAL=$((TOTAL + 1))
  local url="${BASE_URL}${rel_path}"
  local actual
  actual=$(curl -sS -o /dev/null -m 10 \
    -w "%{http_code}" \
    -X "$method" \
    "$@" \
    "$url" 2>/dev/null || echo "000")
  if [ "$actual" = "$expected" ]; then
    printf "  ${SAGE}PASS${RESET}  %-4s %-40s ${MUTED}(%s)${RESET}\n" \
      "$method" "$rel_path" "$actual"
    PASS=$((PASS + 1))
  else
    printf "  ${ERR}FAIL${RESET}  %-4s %-40s ${ERR}got %s, want %s${RESET}\n" \
      "$method" "$rel_path" "$actual" "$expected"
    FAIL=$((FAIL + 1))
    FAILED_PROBES+=("${method} ${rel_path} expected=${expected} got=${actual}")
  fi
}

skip() {
  # Usage: skip <method> <path> <reason>
  local method="$1"
  local rel_path="$2"
  local reason="$3"
  printf "  ${AMBER}SKIP${RESET}  %-4s %-40s ${MUTED}%s${RESET}\n" \
    "$method" "$rel_path" "$reason"
}

# --- Header ------------------------------------------------------------------
printf "\n${BOLD}Orphograph readiness probe${RESET}\n"
printf "${MUTED}target:${RESET} %s\n" "$BASE_URL"
if [ "$IS_PROD" = "1" ]; then
  printf "${AMBER}mode:${RESET}   PRODUCTION (anchor probe will be skipped)\n"
else
  printf "${MUTED}mode:${RESET}   dev / staging\n"
fi
printf "\n"

# === 1. Public endpoints (200 expected) =====================================
printf "${BOLD}Public surface${RESET}\n"
probe GET "/"                              200
probe GET "/api/health"                    200
probe GET "/sitemap.xml"                   200
probe GET "/robots.txt"                    200
probe GET "/blog/"                         200
probe GET "/blog/atom.xml"                 200
probe GET "/blog/written-by-an-ai"         200
probe GET "/verify/"                       200
probe GET "/buy.html"                      200
probe GET "/docs/api.html"                 200
probe GET "/terms.html"                    200
probe GET "/privacy.html"                  200
probe GET "/lp/index.html"                 200
probe GET "/lp/prove-photo-pre-ai.html"    200
probe GET "/status.html"                   200

# === 2. Authenticated routes — without auth → 401 ===========================
printf "\n${BOLD}Authenticated surface (no cookie → 401)${RESET}\n"
probe GET  "/api/me"                       401
probe GET  "/api/me/anchors"               401
probe GET  "/api/me/export"                401
probe POST "/api/me/delete"                401

# === 3. Founder-token routes — without token → 404 ==========================
printf "\n${BOLD}Founder surface (no token → 404)${RESET}\n"
probe GET "/api/founder/payout-status"     404

# === 4. Anchor flow — POST then GET the resulting receipt ===================
printf "\n${BOLD}Anchor + receipt round-trip${RESET}\n"
if [ "$IS_PROD" = "1" ] && [ "${PROBE_ANCHOR_ALLOW_PROD:-0}" != "1" ]; then
  skip POST "/api/anchor"                  "prod ledger — set PROBE_ANCHOR_ALLOW_PROD=1 to override"
  skip GET  "/api/receipt/<id>"            "anchor skipped"
  skip GET  "/api/verify/<id>"             "anchor skipped"
  skip GET  "/r/<id>"                      "anchor skipped"
  skip GET  "/api/badge/<id>.svg"          "anchor skipped"
else
  # Deterministic-ish hash so re-runs don't sprinkle distinct entries —
  # client_label flags it as a probe so the operator can grep + prune.
  PROBE_HASH=$(printf "orphograph-probe-%s" "$(date -u +%Y%m%d)" | \
    python3 -c "import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())")
  ANCHOR_BODY=$(python3 -c "import json; print(json.dumps({\"hash_hex\":\"${PROBE_HASH}\",\"client_label\":\"probe-ignore\"}))")
  TOTAL=$((TOTAL + 1))
  ANCHOR_RESP=$(curl -sS -m 30 \
    -H "Content-Type: application/json" \
    -d "$ANCHOR_BODY" \
    "${BASE_URL}/api/anchor" 2>/dev/null || echo "")
  RID=$(printf "%s" "$ANCHOR_RESP" | json_get receipt_id)
  if [ -n "$RID" ]; then
    printf "  ${SAGE}PASS${RESET}  %-4s %-40s ${MUTED}(rid=%s)${RESET}\n" \
      "POST" "/api/anchor" "$RID"
    PASS=$((PASS + 1))
    probe GET "/api/receipt/${RID}"        200
    probe GET "/api/verify/${RID}"         200
    probe GET "/r/${RID}"                  200
    probe GET "/api/badge/${RID}.svg"      200
  else
    printf "  ${ERR}FAIL${RESET}  %-4s %-40s ${ERR}no receipt_id in response${RESET}\n" \
      "POST" "/api/anchor"
    FAIL=$((FAIL + 1))
    FAILED_PROBES+=("POST /api/anchor — no receipt_id returned")
    skip GET "/api/receipt/<id>"           "anchor failed"
    skip GET "/api/verify/<id>"            "anchor failed"
    skip GET "/r/<id>"                     "anchor failed"
    skip GET "/api/badge/<id>.svg"         "anchor failed"
  fi
fi

# === 5. Waitlist + unsubscribe (idempotent probe email) =====================
printf "\n${BOLD}Waitlist + unsubscribe${RESET}\n"
WAITLIST_BODY='{"email":"probe@example.com","interest":"creator"}'
probe POST "/api/waitlist"                 200 \
  -H "Content-Type: application/json" \
  -d "$WAITLIST_BODY"
probe GET  "/api/unsubscribe?e=probe@example.com" 200
probe POST "/api/unsubscribe?e=probe@example.com" 200

# === 6. Stripe webhook — empty body, no signature ===========================
# Implementation MAY return 200 (probe acceptance) or 400 (signature
# verification rejection). Either is a healthy mounted route; 404 / 5xx is
# what we'd flag. We probe by accepting either.
printf "\n${BOLD}Stripe webhook${RESET}\n"
TOTAL=$((TOTAL + 1))
SW_CODE=$(curl -sS -o /dev/null -m 10 \
  -w "%{http_code}" \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{}' \
  "${BASE_URL}/api/stripe/webhook" 2>/dev/null || echo "000")
case "$SW_CODE" in
  200|400)
    printf "  ${SAGE}PASS${RESET}  %-4s %-40s ${MUTED}(%s)${RESET}\n" \
      "POST" "/api/stripe/webhook" "$SW_CODE"
    PASS=$((PASS + 1))
    ;;
  *)
    printf "  ${ERR}FAIL${RESET}  %-4s %-40s ${ERR}got %s, want 200 or 400${RESET}\n" \
      "POST" "/api/stripe/webhook" "$SW_CODE"
    FAIL=$((FAIL + 1))
    FAILED_PROBES+=("POST /api/stripe/webhook expected=200|400 got=${SW_CODE}")
    ;;
esac

# --- Summary -----------------------------------------------------------------
printf "\n${BOLD}Summary${RESET}\n"
printf "  passed: ${SAGE}%d${RESET}\n" "$PASS"
printf "  failed: ${ERR}%d${RESET}\n" "$FAIL"
printf "  total:  %d\n" "$TOTAL"
if [ "$FAIL" -gt 0 ]; then
  printf "\n${ERR}Failed probes:${RESET}\n"
  for f in "${FAILED_PROBES[@]}"; do
    printf "  - %s\n" "$f"
  done
fi

printf "\nRESULT: %d/%d ENDPOINTS HEALTHY\n" "$PASS" "$TOTAL"

if [ "$FAIL" -eq 0 ]; then
  exit 0
elif [ "$FAIL" -le 3 ]; then
  exit 1
else
  exit 2
fi
