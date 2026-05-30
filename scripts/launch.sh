#!/usr/bin/env bash
# scripts/launch.sh — interactive go-live driver for Orphograph.
#
# Detects what's done, prompts only for what's missing, runs each
# step end-to-end with verification. Skips steps that are already
# complete. Pauses for browser-only steps with clear instructions.
#
# Designed to be re-runnable: every step is idempotent. Run it
# again after fixing any failure; it picks up where you left off.
#
# Usage:
#   scripts/launch.sh                 # interactive
#   scripts/launch.sh --check         # report state, do nothing
#   scripts/launch.sh --step github   # jump to a specific step
#
# Steps: github → resend → stripe → fly → secrets → deploy → verify
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_blu=$'\033[34m'
c_dim=$'\033[2m'; c_bld=$'\033[1m'; c_off=$'\033[0m'

step()   { printf "\n${c_bld}${c_blu}── %s ──${c_off}\n" "$1"; }
ok()     { printf "${c_grn}✓${c_off} %s\n" "$1"; }
warn()   { printf "${c_yel}!${c_off} %s\n" "$1"; }
fail()   { printf "${c_red}✗${c_off} %s\n" "$1"; }
prompt() { printf "${c_bld}? %s${c_off} " "$1"; }
note()   { printf "${c_dim}  %s${c_off}\n" "$1"; }

CHECK_ONLY=0
STEP_FILTER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK_ONLY=1; shift;;
    --step) STEP_FILTER="$2"; shift 2;;
    *) shift;;
  esac
done

# ─── helpers ────────────────────────────────────────────────────────────

have() { command -v "$1" >/dev/null 2>&1; }

pause() {
  if [ "$CHECK_ONLY" -eq 1 ]; then return; fi
  prompt "${1:-press enter when done}"
  read -r _
}

ask() {
  if [ "$CHECK_ONLY" -eq 1 ]; then echo ""; return; fi
  prompt "$1"
  read -r REPLY
  echo "$REPLY"
}

# ─── pre-flight ─────────────────────────────────────────────────────────

echo "${c_bld}Orphograph launch driver${c_off}"
echo "Mode: $([ "$CHECK_ONLY" -eq 1 ] && echo "check-only" || echo "interactive")"
echo

step "Tooling check"
for tool in git python3 curl; do
  if have "$tool"; then ok "$tool"; else fail "$tool missing"; exit 1; fi
done
have gh && ok "gh (GitHub CLI)" || warn "gh missing — install with 'brew install gh' for automated push"
have fly && ok "fly (Fly.io CLI)" || warn "fly missing — install with 'brew install flyctl' for deploy step"
have node && ok "node (for JS syntax check in CI mirror)" || warn "node missing — optional"

# ─── state detection ────────────────────────────────────────────────────

step "Detecting current state"
DOMAIN_REGISTERED=0
if grep -q "DONE (2026-05-13)" "$ROOT/deploy/FOUNDER_TODO.md" 2>/dev/null; then
  DOMAIN_REGISTERED=1; ok "orphograph.com registered (per FOUNDER_TODO.md)"
else
  warn "orphograph.com registration not confirmed in FOUNDER_TODO.md"
fi

# The standalone verifier is no longer published to a separate GitHub repo.
# It ships inside THIS repository (server/verify_cli.py + web/verify/) and is
# served live at https://orphograph.com/verify/, so there is no
# dist/orphograph-verify repo to detect, init, or push. Step 1 below is a
# retired no-op kept only so --step github and full runs stay valid.
ok "verifier ships in-repo (server/verify_cli.py + web/verify/) — no separate repo"

FLY_AUTHED=0
if have fly && fly auth whoami >/dev/null 2>&1; then
  FLY_AUTHED=1
  ok "fly authenticated as $(fly auth whoami 2>&1)"
fi

GH_AUTHED=0
if have gh && gh auth status >/dev/null 2>&1; then
  GH_AUTHED=1
  ok "gh authenticated"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  echo
  echo "${c_dim}(check-only; not modifying anything)${c_off}"
  exit 0
fi

# ─── STEP 1: GITHUB ─────────────────────────────────────────────────────

if [ -z "$STEP_FILTER" ] || [ "$STEP_FILTER" = "github" ]; then
step "Step 1 — GitHub (retired: verifier ships in-repo)"

# RETIRED. This step used to create + push a separate
# github.com/orphograph/orphograph-verify repo. The verifier is now part of the
# main repository (server/verify_cli.py + web/verify/) and is served live at
# https://orphograph.com/verify/, so there is nothing separate to publish.
# The main repo (Orphograph/Orphograph) is published through normal git
# workflow + CI, not this script. Kept as a no-op so the step list is intact.
note "verifier is consolidated into the main repo — no separate publish step."
note "  • CLI verifier:  server/verify_cli.py"
note "  • web verifier:  web/verify/  (served at https://orphograph.com/verify/)"
ok "Step 1 retired (no action needed)"
fi  # STEP_FILTER github

# ─── STEP 2: RESEND ─────────────────────────────────────────────────────

if [ -z "$STEP_FILTER" ] || [ "$STEP_FILTER" = "resend" ]; then
step "Step 2 — Resend (email)"
note "Resend signup + domain verify is browser-only. See:"
note "  deploy/LAUNCH_WALKTHROUGH.md §2"
note "After verification, paste the API key now (re_...), or skip and run again later."
RESEND_KEY=$(ask "RESEND_API_KEY (or empty to skip):")
if [ -n "$RESEND_KEY" ]; then
  echo "$RESEND_KEY" > "$ROOT/.local_secrets/resend.txt" 2>/dev/null || {
    mkdir -p "$ROOT/.local_secrets"
    chmod 700 "$ROOT/.local_secrets"
    echo "$RESEND_KEY" > "$ROOT/.local_secrets/resend.txt"
    chmod 600 "$ROOT/.local_secrets/resend.txt"
  }
  ok "saved to .local_secrets/resend.txt — will be applied in step 5"
fi
fi

# ─── STEP 3: STRIPE ─────────────────────────────────────────────────────

if [ -z "$STEP_FILTER" ] || [ "$STEP_FILTER" = "stripe" ]; then
step "Step 3 — Stripe"
note "Stripe activation is browser-only + needs 1–3 day review."
note "Once activated, create products + webhook + restricted key."
note "See: deploy/LAUNCH_WALKTHROUGH.md §3"
STRIPE_WHSEC=$(ask "STRIPE_WEBHOOK_SECRET (whsec_..., or empty to skip):")
STRIPE_RK=$(ask "STRIPE_SECRET_KEY (rk_live_..., or empty to skip):")
PACK_URL=$(ask "STRIPE_PACK_URL (Payment Link, or empty to skip):")
PERSONAL_MONTHLY=$(ask "STRIPE_PERSONAL_MONTHLY_URL (or empty):")
PERSONAL_ANNUAL=$(ask "STRIPE_PERSONAL_ANNUAL_URL (or empty):")

mkdir -p "$ROOT/.local_secrets" && chmod 700 "$ROOT/.local_secrets"
[ -n "$STRIPE_WHSEC" ] && echo "$STRIPE_WHSEC" > "$ROOT/.local_secrets/stripe_whsec.txt" && chmod 600 "$ROOT/.local_secrets/stripe_whsec.txt" && ok "stripe webhook secret stored"
[ -n "$STRIPE_RK" ] && echo "$STRIPE_RK" > "$ROOT/.local_secrets/stripe_rk.txt" && chmod 600 "$ROOT/.local_secrets/stripe_rk.txt" && ok "stripe restricted key stored"

# Patch app.js constants if URLs were provided.
if [ -n "$PACK_URL" ] || [ -n "$PERSONAL_MONTHLY" ] || [ -n "$PERSONAL_ANNUAL" ]; then
  note "updating web/app.js constants..."
  APP_JS="$ROOT/web/app.js"
  [ -n "$PACK_URL" ] && python3 -c "
import re, sys
p = '$APP_JS'
t = open(p).read()
t = re.sub(r'const STRIPE_PACK_URL = \"[^\"]*\";', 'const STRIPE_PACK_URL = \"$PACK_URL\";', t)
open(p, 'w').write(t)
"
  [ -n "$PERSONAL_MONTHLY" ] && python3 -c "
import re
p = '$APP_JS'
t = open(p).read()
t = re.sub(r'const STRIPE_PERSONAL_MONTHLY_URL = \"[^\"]*\";', 'const STRIPE_PERSONAL_MONTHLY_URL = \"$PERSONAL_MONTHLY\";', t)
open(p, 'w').write(t)
"
  [ -n "$PERSONAL_ANNUAL" ] && python3 -c "
import re
p = '$APP_JS'
t = open(p).read()
t = re.sub(r'const STRIPE_PERSONAL_ANNUAL_URL = \"[^\"]*\";', 'const STRIPE_PERSONAL_ANNUAL_URL = \"$PERSONAL_ANNUAL\";', t)
open(p, 'w').write(t)
"
  ok "app.js patched — commit + redeploy to apply"
fi
fi

# ─── STEP 4: FLY ────────────────────────────────────────────────────────

if [ -z "$STEP_FILTER" ] || [ "$STEP_FILTER" = "fly" ]; then
step "Step 4 — Fly.io deploy"
if ! have fly; then
  warn "fly not installed. Install: brew install flyctl"
  exit 1
fi
if [ "$FLY_AUTHED" -eq 0 ]; then
  note "running fly auth login..."
  fly auth login
fi

# Check if app exists
if ! fly apps list 2>/dev/null | grep -q orphograph; then
  note "running fly launch (no-deploy)..."
  cd "$ROOT"
  fly launch --copy-config --no-deploy --region iad --name orphograph || {
    fail "fly launch failed"
    exit 1
  }
fi

# Volume
if ! fly volumes list -a orphograph 2>/dev/null | grep -q orphograph_data; then
  note "creating volume orphograph_data..."
  fly volumes create orphograph_data --region iad --size 1 -a orphograph
fi

# Apply collected secrets.
SECRETS_ARGS=()
[ -f "$ROOT/.local_secrets/resend.txt" ] && SECRETS_ARGS+=("RESEND_API_KEY=$(cat "$ROOT/.local_secrets/resend.txt")")
[ -f "$ROOT/.local_secrets/stripe_whsec.txt" ] && SECRETS_ARGS+=("STRIPE_WEBHOOK_SECRET=$(cat "$ROOT/.local_secrets/stripe_whsec.txt")")
[ -f "$ROOT/.local_secrets/stripe_rk.txt" ] && SECRETS_ARGS+=("STRIPE_SECRET_KEY=$(cat "$ROOT/.local_secrets/stripe_rk.txt")")
if [ ${#SECRETS_ARGS[@]} -gt 0 ]; then
  note "setting ${#SECRETS_ARGS[@]} secrets on Fly..."
  fly secrets set "${SECRETS_ARGS[@]}" -a orphograph
fi

note "deploying..."
fly deploy -a orphograph || { fail "fly deploy failed"; exit 1; }
ok "deployed"

# Custom domain
if ! fly certs list -a orphograph 2>/dev/null | grep -q orphograph.com; then
  note "adding orphograph.com cert..."
  fly certs create orphograph.com -a orphograph
  note "Add the printed DNS records at your registrar, then re-run to check status."
fi
fly certs check orphograph.com -a orphograph 2>&1 | grep -i ready && ok "cert ready" || warn "cert not yet ready"
fi

# ─── STEP 5: VERIFY ─────────────────────────────────────────────────────

if [ -z "$STEP_FILTER" ] || [ "$STEP_FILTER" = "verify" ]; then
step "Step 5 — Live verification"
note "running preflight against https://orphograph.com..."
bash "$ROOT/scripts/preflight.sh" https://orphograph.com
fi

# ─── DONE ───────────────────────────────────────────────────────────────

echo
echo "${c_grn}${c_bld}═══════════════════════════════════════════════${c_off}"
echo "${c_grn}${c_bld}Launch driver finished.${c_off}"
echo "${c_grn}${c_bld}═══════════════════════════════════════════════${c_off}"
echo
echo "What's left (founder-only):"
echo "  • 5 photographer interviews — outreach/cold_dm_twitter.md"
echo "  • Schedule Show HN — outreach/show_hn_draft.md (Tuesday 9 AM ET)"
echo
echo "Re-run any time:"
echo "  scripts/launch.sh             # interactive"
echo "  scripts/launch.sh --check     # state report only"
echo "  scripts/launch.sh --step fly  # jump to a step"
