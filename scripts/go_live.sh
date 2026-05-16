#!/usr/bin/env bash
# scripts/go_live.sh — drive Orphograph from local-only to publicly-live.
#
# Designed for a 2-hour launch window. Idempotent: safe to re-run after
# any failure; picks up wherever you left off. Each step prints what it
# did, what comes next, and how long that step typically takes.
#
# Founder, run this ONCE from your real Terminal.app:
#   cd ~/orphograph
#   bash scripts/go_live.sh
#
# Prerequisites:
#   - `brew install flyctl` already done
#   - A credit card ready (Fly needs one even for free tier)
#   - 5-30 min for DNS propagation later
#
# What this does NOT do:
#   - Sign up for Fly for you (they need your phone + card)
#   - Generate your Bitcoin receive address (run scripts/btc_address_pick.sh)
#   - Activate Stripe (1-3 day review you start in parallel)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Resolve flyctl — prefer brew, then ~/.fly/bin.
FLY=""
for candidate in \
  "$(command -v flyctl 2>/dev/null)" \
  "$(command -v fly 2>/dev/null)" \
  "/opt/homebrew/bin/flyctl" \
  "$HOME/.fly/bin/flyctl"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then
    FLY="$candidate"
    break
  fi
done

# Colors.
red=$'\033[31m'; grn=$'\033[32m'; yel=$'\033[33m'; blu=$'\033[34m'
bld=$'\033[1m'; dim=$'\033[2m'; off=$'\033[0m'

step()    { printf "\n${bld}${blu}━━ %s ━━${off}\n" "$1"; }
ok()      { printf "${grn}✓${off} %s\n" "$1"; }
warn()    { printf "${yel}!${off} %s\n" "$1"; }
fail()    { printf "${red}✗${off} %s\n" "$1"; }
ask()     { printf "${bld}? %s${off} " "$1"; read -r REPLY; echo "$REPLY"; }
pause()   { printf "${bld}↩ %s${off}" "${1:-press enter when done}"; read -r _; }
notify()  { [ -x "$HOME/.claude/notifier.py" ] && python3 "$HOME/.claude/notifier.py" "$@" >/dev/null 2>&1 || true; }
banner()  {
  echo
  echo "${grn}${bld}═══════════════════════════════════════════════════════════════════${off}"
  echo "${grn}${bld}  $*${off}"
  echo "${grn}${bld}═══════════════════════════════════════════════════════════════════${off}"
  echo
}

# ─── 0. preflight ───────────────────────────────────────────────────────

banner "Orphograph go-live driver"

step "Step 0/7 — flyctl + tests check"
if [ -z "$FLY" ]; then
  fail "flyctl not found anywhere on PATH or in ~/.fly/bin"
  echo "  Install: ${bld}brew install flyctl${off}"
  exit 1
fi
ok "flyctl: $FLY"
"$FLY" version 2>&1 | head -1 | sed 's/^/  /'

# Run tests + safety check before doing anything irreversible.
echo
echo "${dim}Running 183-test suite (~17s)...${off}"
if ! PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q >/tmp/orpho_pytest.log 2>&1; then
  fail "tests failed. Read /tmp/orpho_pytest.log and fix before deploying."
  tail -20 /tmp/orpho_pytest.log
  exit 1
fi
ok "183/183 tests passing"

echo "${dim}Running publish safety check on web/verify/...${off}"
if ! bash "$ROOT/scripts/publish_safety_check.sh" >/tmp/orpho_safety.log 2>&1; then
  fail "publish safety check failed."
  cat /tmp/orpho_safety.log
  exit 1
fi
ok "publish safety check green"

# ─── 1. fly auth ────────────────────────────────────────────────────────

step "Step 1/7 — Fly authentication"
if "$FLY" auth whoami >/dev/null 2>&1; then
  ok "already authenticated as $("$FLY" auth whoami 2>&1)"
else
  echo "Run this in another terminal window or here:"
  echo "  ${bld}$FLY auth signup${off}    (if you don't have a Fly account)"
  echo "  ${bld}$FLY auth login${off}     (if you already do)"
  echo
  echo "You'll need a credit card. Free tier covers Orphograph at launch."
  pause "press enter once 'fly auth whoami' returns your email"
  if ! "$FLY" auth whoami >/dev/null 2>&1; then
    fail "still not authenticated. Aborting."
    exit 1
  fi
  ok "authenticated as $("$FLY" auth whoami 2>&1)"
fi
notify "🚀 Orphograph go-live started — Fly auth confirmed"

# ─── 2. app + volume ────────────────────────────────────────────────────

step "Step 2/7 — Create Fly app + persistent volume"
if "$FLY" apps list 2>/dev/null | grep -q "^orphograph"; then
  ok "app 'orphograph' already exists"
else
  echo "${dim}Creating app (uses fly.toml as the template)...${off}"
  if ! "$FLY" launch --copy-config --no-deploy --region iad --name orphograph 2>&1; then
    fail "fly launch failed. Check fly.toml + retry."
    exit 1
  fi
  ok "app created"
fi

if "$FLY" volumes list -a orphograph 2>/dev/null | grep -q orphograph_data; then
  ok "volume 'orphograph_data' already exists"
else
  echo "${dim}Creating 1GB volume in iad...${off}"
  if ! "$FLY" volumes create orphograph_data --region iad --size 1 -a orphograph --yes 2>&1; then
    fail "volume create failed."
    exit 1
  fi
  ok "volume created"
fi

# ─── 3. deploy ──────────────────────────────────────────────────────────

step "Step 3/7 — Deploy"
echo "${dim}Building Docker image + pushing + booting (~3-5 min)...${off}"
if ! "$FLY" deploy -a orphograph 2>&1; then
  fail "fly deploy failed. See the output above for the actual error."
  exit 1
fi
ok "deploy complete"
notify "🚀 Orphograph deployed to Fly — DNS step next"

# ─── 4. TLS cert + DNS ──────────────────────────────────────────────────

step "Step 4/7 — Add custom domain + TLS cert"
echo "${dim}Requesting cert for orphograph.com (may already exist; that's fine)...${off}"
"$FLY" certs create orphograph.com -a orphograph 2>&1 || true
echo

# Pull the records Fly needs.
echo "${dim}Fetching the DNS records Fly needs you to add at your registrar...${off}"
CERT_INFO=$("$FLY" certs show orphograph.com -a orphograph 2>&1)
echo "$CERT_INFO"
echo
banner "🚨 ACTION REQUIRED — add these DNS records at your registrar"
echo "Where you registered orphograph.com (Porkbun / Namecheap / etc):"
echo "  Open the DNS panel and add the A + AAAA records shown above."
echo
echo "Use Host=@ (or blank, or orphograph.com.) for both records."
echo "TTL = 600 is fine. Most registrars propagate in 5-30 min."
echo
pause "press enter once the DNS records are saved at the registrar"

# ─── 5. wait for cert ──────────────────────────────────────────────────

step "Step 5/7 — Wait for TLS cert to issue"
echo "${dim}Polling Fly every 30s. Ctrl-C cancels; re-run safe.${off}"
TRIES=0
MAX_TRIES=60   # 30 min
while [ "$TRIES" -lt "$MAX_TRIES" ]; do
  if "$FLY" certs check orphograph.com -a orphograph 2>&1 | grep -qiE "(issued|ready|configured.*true)"; then
    ok "cert is READY"
    break
  fi
  TRIES=$((TRIES+1))
  printf "  attempt %d/%d — sleeping 30s\r" "$TRIES" "$MAX_TRIES"
  sleep 30
done
echo
if [ "$TRIES" -ge "$MAX_TRIES" ]; then
  warn "cert not ready after 30 min. Check 'fly certs check orphograph.com -a orphograph'"
  warn "Most common cause: DNS records weren't saved correctly at the registrar."
  warn "Try: dig orphograph.com  — should return Fly's IP."
  warn "You can rerun this script — it picks up where it left off."
  exit 1
fi
notify "✅ Orphograph TLS cert issued — site is publicly reachable"

# ─── 6. preflight ───────────────────────────────────────────────────────

step "Step 6/7 — Live preflight"
echo "${dim}Running 21-check preflight against https://orphograph.com...${off}"
if bash "$ROOT/scripts/preflight.sh" https://orphograph.com; then
  ok "preflight: all green"
else
  warn "some checks failed. Look at the output. Most non-fatal: webhook 503 (need to set Stripe secret) or 4xx on auth pages (that's correct unauthenticated behavior)."
fi
notify "✅ Orphograph live preflight ran — see deploy output for details"

# ─── 7. revenue wiring (optional, can defer) ────────────────────────────

step "Step 7/7 — Wire revenue (optional — can defer)"
echo "The site is live. To accept Bitcoin payments now:"
echo
echo "1. Generate a fresh bc1q receive address on a wallet you control."
echo "   Fastest path: see ${bld}deploy/WALLET_QUICK.md${off}"
echo
echo "2. Set it on Fly:"
echo "   ${bld}fly secrets set BTC_RECEIVE_ADDRESS=bc1qYOURADDRESS -a orphograph${off}"
echo
echo "3. Schedule the BTC settle worker (every 5 min):"
cat <<'EOF'
   fly machines run \
     --schedule "every-5-minutes" \
     --command "python3 scripts/btc_settle.py" \
     --env "ORPHO_DATA_DIR=/app/data" \
     --vm-memory 256 \
     -a orphograph .
EOF
echo
echo "Stripe + Resend can come later (Stripe needs 1-3 day KYC review)."
echo "Full walkthrough: ${bld}deploy/LAUNCH_WALKTHROUGH.md${off}"

# ─── done ───────────────────────────────────────────────────────────────

banner "🎉 LAUNCH COMPLETE — https://orphograph.com is publicly live"
echo "Next:"
echo "  • Visit https://orphograph.com from your phone."
echo "  • Drop a photo. Anchor it. Save the receipt."
echo "  • Set BTC_RECEIVE_ADDRESS to take real revenue."
echo "  • Submit Stripe activation tonight if you want card payments by tomorrow."
echo
echo "Re-run this script any time — it's idempotent."
notify "🎉 Orphograph LAUNCHED at https://orphograph.com"
