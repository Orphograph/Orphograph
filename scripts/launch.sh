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

VERIFIER_GIT_INIT=0
VERIFIER_DIR="$ROOT/dist/orphograph-verify"
if [ -d "$VERIFIER_DIR/.git" ]; then
  VERIFIER_GIT_INIT=1
  cur_email=$(git -C "$VERIFIER_DIR" config user.email 2>/dev/null)
  cur_name=$(git -C "$VERIFIER_DIR" config user.name 2>/dev/null)
  ok "verifier repo initialized ($cur_name <$cur_email>)"
else
  warn "verifier repo not initialized — run safety check first"
fi

VERIFIER_REMOTE=""
if [ "$VERIFIER_GIT_INIT" -eq 1 ]; then
  VERIFIER_REMOTE=$(git -C "$VERIFIER_DIR" remote get-url origin 2>/dev/null || echo "")
  if [ -n "$VERIFIER_REMOTE" ]; then
    ok "remote configured: $VERIFIER_REMOTE"
  else
    warn "no remote 'origin' configured yet"
  fi
fi

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
step "Step 1 — GitHub account, org, repo, push"

if [ "$VERIFIER_GIT_INIT" -eq 0 ]; then
  fail "verifier repo not initialized. Run safety check first:"
  note "  bash scripts/publish_safety_check.sh"
  exit 1
fi

# 1a. Safety check before any push.
note "running publish safety check..."
if ! bash "$ROOT/scripts/publish_safety_check.sh" >/dev/null 2>&1; then
  fail "publish safety check failed. Run it manually and fix issues:"
  note "  bash scripts/publish_safety_check.sh"
  exit 1
fi
ok "publish safety check: 9/9 green"

# 1b. Browser-gated account creation.
if [ "$GH_AUTHED" -eq 0 ]; then
  warn "you don't appear to be authenticated to GitHub via 'gh'."
  note "If you haven't created the dedicated orphograph account yet:"
  note "  1. Open an incognito window."
  note "  2. Go to https://github.com/signup"
  note "  3. Use a fresh email (Cloudflare alias on orphograph.com is cleanest)."
  note "  4. Username: orphograph"
  note "  5. Enable TOTP 2FA (NOT SMS)."
  note "  6. Create the 'orphograph' organization."
  note "  7. Generate a fine-grained PAT scoped to the org's repos."
  note "Full guide: deploy/PUBLISH_SAFETY.md §3 + deploy/LAUNCH_WALKTHROUGH.md §1"
  pause "press enter once you have a PAT ready to paste"

  PAT=$(ask "paste the github_pat_ token (input will echo)")
  if [ -z "$PAT" ]; then fail "no PAT provided. aborting."; exit 1; fi

  echo "$PAT" | gh auth login --hostname github.com --git-protocol https --with-token
  if ! gh auth status >/dev/null 2>&1; then
    fail "gh auth failed. Check token validity + scopes."
    exit 1
  fi
  ok "gh authenticated successfully"
fi

# 1c. Create the repo if it doesn't exist.
if ! gh repo view orphograph/orphograph-verify >/dev/null 2>&1; then
  note "creating empty repo at github.com/orphograph/orphograph-verify..."
  gh repo create orphograph/orphograph-verify \
    --public \
    --description "Standalone verifier for Orphograph receipts. MIT." \
    --homepage "https://orphograph.com" || {
      fail "gh repo create failed. Maybe the 'orphograph' org doesn't exist yet?"
      note "Create it manually at https://github.com/organizations/new"
      exit 1
    }
  ok "repo created"
else
  ok "repo orphograph/orphograph-verify already exists"
fi

# 1d. Wire the remote.
if [ -z "$VERIFIER_REMOTE" ]; then
  git -C "$VERIFIER_DIR" remote add origin https://github.com/orphograph/orphograph-verify.git
  ok "remote 'origin' added"
fi

# 1e. Final pre-push safety check + push.
if ! bash "$ROOT/scripts/publish_safety_check.sh" >/dev/null 2>&1; then
  fail "publish safety check failed at the final gate. Aborting push."
  exit 1
fi

prompt "ready to push to github.com/orphograph/orphograph-verify? type 'push' to confirm: "
read -r confirm
if [ "$confirm" != "push" ]; then
  warn "skipped push (did not type 'push')"
else
  git -C "$VERIFIER_DIR" push -u origin main || {
    fail "git push failed. Check auth and remote."
    exit 1
  }
  ok "pushed to github.com/orphograph/orphograph-verify"
  note "verify in incognito: https://github.com/orphograph/orphograph-verify"
fi
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
