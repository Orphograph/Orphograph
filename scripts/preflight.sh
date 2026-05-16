#!/usr/bin/env bash
# scripts/preflight.sh — production-readiness probe against a LIVE URL.
#
# Run after `fly deploy`, before pasting the Show HN link, and any time
# the founder wants assurance the public site is functioning.
#
# Usage:
#   scripts/preflight.sh                          # defaults to https://orphograph.com
#   scripts/preflight.sh https://staging-url.com  # against staging
#
# Exit code 0 = all green. Non-zero = at least one check failed.
set -u

URL="${1:-https://orphograph.com}"
URL="${URL%/}"  # strip trailing slash

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
FAILS=0; CHECKS=0

step()  { printf "\n${c_dim}—— %s ——${c_off}\n" "$1"; }
pass()  { CHECKS=$((CHECKS+1)); printf "${c_grn}✓${c_off} %s\n" "$1"; }
fail()  { CHECKS=$((CHECKS+1)); FAILS=$((FAILS+1)); printf "${c_red}✗${c_off} %s\n" "$1"; }
warn()  { printf "${c_yel}!${c_off} %s\n" "$1"; }

probe_status() {
  # Print HTTP status code or 000 on error. Single curl call so non-2xx
  # statuses don't cascade into a fallback that re-prints the code.
  local target="$1"
  local method="${2:-GET}"
  local out
  out=$(curl -sS -o /dev/null -w "%{http_code}" -X "$method" --max-time 10 "$target" 2>/dev/null)
  if [ -z "$out" ]; then echo "000"; else echo "$out"; fi
}

probe_header() {
  # Return the header value or empty. We use GET (not HEAD) because our
  # stdlib server only implements do_GET — HEAD wouldn't carry our
  # custom security headers. Portable case-insensitive match: lowercase
  # both the wire response and the needle.
  local target="$1"; local name_lower
  name_lower=$(echo "$2" | tr '[:upper:]' '[:lower:]')
  curl -sS -D - -o /dev/null --max-time 10 "$target" 2>/dev/null \
    | tr -d '\r' \
    | awk -v needle="^${name_lower}:" '
        {
          line_lower = tolower($0)
          if (line_lower ~ needle) {
            # strip the "name: " prefix, preserving the rest of the line
            sub(/^[^:]+:[ \t]*/, "", $0)
            print
            exit
          }
        }'
}

probe_json_field() {
  local target="$1"; local field="$2"
  curl -fsS --max-time 10 "$target" 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null
}

echo "Probing ${URL}"

# ── HTTPS reachability ────────────────────────────────────────────────────
step "TLS + reachability"
code=$(probe_status "$URL/")
case "$code" in
  200|301|302|303|307|308) pass "GET / → $code";;
  *) fail "GET / → $code"; warn "if 000, the host is unreachable or cert invalid";;
esac

# ── Critical pages ────────────────────────────────────────────────────────
step "Critical pages"
for path in "/" "/terms.html" "/privacy.html" "/signin.html" "/account.html" \
            "/status.html" "/sample/index.json" "/sample/sample.txt" \
            "/favicon.svg" "/og.svg"; do
  code=$(probe_status "$URL$path")
  if [ "$code" = "200" ]; then pass "$path → 200"
  else fail "$path → $code"
  fi
done

# ── Health endpoint ───────────────────────────────────────────────────────
step "Health endpoint"
health_ok=$(probe_json_field "$URL/api/health" "ok")
if [ "$health_ok" = "True" ]; then pass "/api/health ok=True"
else fail "/api/health ok=$health_ok"
fi

# Calendar reachability — at least 3 of 5 expected.
cals=$(curl -fsS --max-time 10 "$URL/api/health" 2>/dev/null \
       | python3 -c "import json,sys; d=json.load(sys.stdin); cals=d.get('calendars',[]); ok=sum(1 for c in cals if c.get('reachable')); print(f'{ok}/{len(cals)}')" 2>/dev/null \
       || echo "?")
case "$cals" in
  5/5|4/5|3/5) pass "calendars reachable: $cals";;
  *) fail "calendars reachable: $cals (need ≥3)";;
esac

# ── Security headers ──────────────────────────────────────────────────────
step "Security headers"
hsts=$(probe_header "$URL/" "strict-transport-security")
if echo "$hsts" | grep -qi "max-age"; then pass "HSTS: $hsts"
else fail "HSTS missing"
fi

csp=$(probe_header "$URL/" "content-security-policy")
if echo "$csp" | grep -qi "default-src 'self'"; then pass "CSP restrictive: default-src 'self'"
else fail "CSP missing or permissive: $csp"
fi

xfo=$(probe_header "$URL/" "x-frame-options")
if [ "$xfo" = "DENY" ]; then pass "X-Frame-Options: DENY"
else fail "X-Frame-Options: $xfo (want DENY)"
fi

xcto=$(probe_header "$URL/" "x-content-type-options")
if [ "$xcto" = "nosniff" ]; then pass "X-Content-Type-Options: nosniff"
else fail "X-Content-Type-Options: $xcto (want nosniff)"
fi

# ── Auth boundary ─────────────────────────────────────────────────────────
step "Auth boundary"
code=$(probe_status "$URL/api/me")
if [ "$code" = "401" ]; then pass "/api/me unauthenticated → 401"
else fail "/api/me unauthenticated → $code (want 401)"
fi

code=$(probe_status "$URL/api/me/export")
if [ "$code" = "401" ]; then pass "/api/me/export unauthenticated → 401"
else fail "/api/me/export unauthenticated → $code"
fi

# ── Webhook posture ───────────────────────────────────────────────────────
step "Webhook posture"
# A POST with no signature should be 400 or 503 (503 if STRIPE_WEBHOOK_SECRET unset).
code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 10 -X POST \
       --data '{}' --header "Content-Type: application/json" \
       "$URL/api/stripe/webhook" 2>/dev/null || echo "000")
case "$code" in
  400) pass "/api/stripe/webhook unsigned → 400 (Stripe secret set, sig validation working)";;
  503) warn "/api/stripe/webhook → 503 (Stripe secret not configured yet; OK pre-Stripe activation)";;
  *)   fail "/api/stripe/webhook unsigned → $code (want 400 or 503)";;
esac

# ── Sample receipt verify roundtrip ───────────────────────────────────────
step "Sample receipt round-trip"
rid=$(probe_json_field "$URL/sample/index.json" "receipt_id")
if [ -n "$rid" ]; then
  found=$(probe_json_field "$URL/api/verify/$rid" "found")
  if [ "$found" = "True" ]; then pass "GET /api/verify/$rid → found=True"
  else fail "GET /api/verify/$rid → found=$found"
  fi
else
  fail "/sample/index.json missing receipt_id"
fi

# ── Rate limit behaviour ──────────────────────────────────────────────────
step "Rate limit"
# Fire 15 anchor probes with bad hash; expect at least one 429.
saw_429=0
for i in $(seq 1 15); do
  code=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 5 -X POST \
         --data '{"hash_hex":"00"}' --header "Content-Type: application/json" \
         "$URL/api/anchor" 2>/dev/null || echo "000")
  if [ "$code" = "429" ]; then saw_429=1; break; fi
done
if [ "$saw_429" = "1" ]; then pass "rate limit kicks in within 15 attempts"
else warn "no 429 in 15 attempts (rate limit may be high or limiter reset just before run)"
fi

# ── DNS + cert sanity ─────────────────────────────────────────────────────
step "TLS certificate"
host_part="${URL#https://}"; host_part="${host_part%%/*}"
if [ "${URL#https://}" != "$URL" ]; then
  cert_subject=$(echo | openssl s_client -servername "$host_part" -connect "$host_part:443" 2>/dev/null \
                 | openssl x509 -noout -subject 2>/dev/null | head -1)
  if [ -n "$cert_subject" ]; then pass "TLS cert subject: $cert_subject"
  else fail "could not read TLS certificate"
  fi
else
  warn "URL is http://; skipping cert check"
fi

# ── Final tally ───────────────────────────────────────────────────────────
echo
if [ "$FAILS" -eq 0 ]; then
  printf "${c_grn}===== PREFLIGHT OK — %d/%d checks passed =====${c_off}\n" "$CHECKS" "$CHECKS"
  exit 0
else
  printf "${c_red}===== PREFLIGHT FAILED — %d/%d checks failed =====${c_off}\n" "$FAILS" "$CHECKS"
  echo "Do not paste the Show HN link or send press releases until all checks are green."
  exit 1
fi
