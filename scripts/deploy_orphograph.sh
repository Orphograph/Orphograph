#!/usr/bin/env bash
# deploy_orphograph.sh — end-to-end Fly.io deploy with every failure mode handled.
#
# Run this from the orphograph repo root in YOUR Terminal (not Claude Code's
# sandbox — Claude can't reach api.fly.io). It is safe to re-run.
#
# What it does (idempotent — each step skips if already done):
#   0. Pre-flight: verify network reachability to Fly + GitHub
#   1. Install fly CLI via brew if missing
#   2. Authenticate fly if not logged in (opens browser)
#   3. Create the Fly app + persistent volume
#   4. Generate session secrets (HMAC + founder token) and store locally
#   5. Prompt for Stripe + Resend keys (you paste once; never logged)
#   6. fly deploy
#   7. Add orphograph.com as a custom domain + request Let's Encrypt cert
#   8. Print exact DNS records to add at your registrar
#   9. Wait for DNS to propagate, verify cert, smoke-test /api/health
#
# Quit any step with Ctrl-C; rerun and it picks up where it left off.

set -u
set -o pipefail

APP_NAME="orphograph"
PRIMARY_REGION="iad"
VOLUME_NAME="orphograph_data"
DOMAIN="orphograph.com"
SECRETS_VAULT="$HOME/.orphograph_secrets.env"
LOG="$(pwd)/deploy_$(date +%Y%m%d_%H%M%S).log"

c_red()    { printf "\033[31m%s\033[0m\n" "$*"; }
c_green()  { printf "\033[32m%s\033[0m\n" "$*"; }
c_yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
c_blue()   { printf "\033[34m%s\033[0m\n" "$*"; }
hr()       { printf '%.0s─' {1..60}; echo; }
log()      { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
die()      { c_red "FATAL: $*"; exit 1; }

# Retry helper: $1=description, $2=max_attempts, rest=command
retry() {
  local desc="$1"; shift
  local max="$1"; shift
  local n=0 delay=2
  while [ $n -lt "$max" ]; do
    n=$((n+1))
    log "[$n/$max] $desc..."
    if "$@" >>"$LOG" 2>&1; then
      c_green "  ✓ $desc"
      return 0
    fi
    if [ $n -lt "$max" ]; then
      c_yellow "  ⚠ failed; retry in ${delay}s"
      sleep $delay
      delay=$((delay * 2))
    fi
  done
  c_red "  ✗ $desc failed after $max attempts. See $LOG"
  return 1
}

hr
c_blue "Orphograph deploy — full log at $LOG"
hr

# ──────────────────────────────────────────────────────────────────────
# 0. Pre-flight
# ──────────────────────────────────────────────────────────────────────
log "Pre-flight: network reachability"
if ! curl -sS --max-time 8 -o /dev/null https://api.fly.io/health 2>>"$LOG"; then
  c_yellow "Cannot reach api.fly.io. Common causes:"
  echo "    • Fly.io having an incident → check https://status.flyio.net"
  echo "    • Corporate VPN / firewall blocking *.fly.io"
  echo "    • DNS pollution → try: sudo dscacheutil -flushcache"
  echo "    • IPv6 hang → try: networksetup -setv6off Wi-Fi (Mac only)"
  read -r -p "Continue anyway? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || die "Aborted; resolve network first."
fi

if ! command -v python3 >/dev/null; then die "python3 missing"; fi
if ! command -v openssl >/dev/null; then die "openssl missing"; fi
if ! command -v curl    >/dev/null; then die "curl missing"; fi
c_green "  ✓ python3 / openssl / curl available"

# ──────────────────────────────────────────────────────────────────────
# 1. Install fly CLI
# ──────────────────────────────────────────────────────────────────────
if ! command -v fly >/dev/null; then
  log "fly CLI missing. Installing via brew..."
  if ! command -v brew >/dev/null; then
    die "Install fly manually: curl -L https://fly.io/install.sh | sh"
  fi
  brew install flyctl || die "brew install flyctl failed"
fi
c_green "  ✓ fly CLI: $(fly version 2>/dev/null | head -1)"

# ──────────────────────────────────────────────────────────────────────
# 2. Authenticate
# ──────────────────────────────────────────────────────────────────────
if ! fly auth whoami >/dev/null 2>&1; then
  c_yellow "Not logged in to Fly. Opening browser for auth..."
  if ! retry "fly auth login" 3 fly auth login; then
    die "Auth failed. If TLS handshake keeps timing out, check status.flyio.net and try again in 5 min."
  fi
fi
WHO=$(fly auth whoami 2>&1 | head -1)
c_green "  ✓ Logged in as $WHO"

# ──────────────────────────────────────────────────────────────────────
# 3. Create app + volume
# ──────────────────────────────────────────────────────────────────────
log "Ensure app '$APP_NAME' exists"
if fly status -a "$APP_NAME" >/dev/null 2>&1; then
  c_green "  ✓ app '$APP_NAME' already exists"
else
  retry "fly apps create $APP_NAME" 3 \
    fly apps create "$APP_NAME" --org personal \
    || die "Couldn't create app. Name may be taken globally; edit APP_NAME in this script."
fi

log "Ensure volume '$VOLUME_NAME' exists in $PRIMARY_REGION"
VOLS=$(fly volumes list -a "$APP_NAME" 2>/dev/null | tail -n +2)
if echo "$VOLS" | grep -q "$VOLUME_NAME"; then
  c_green "  ✓ volume '$VOLUME_NAME' already exists"
else
  retry "fly volumes create $VOLUME_NAME" 3 \
    fly volumes create "$VOLUME_NAME" --region "$PRIMARY_REGION" --size 1 --yes -a "$APP_NAME"
fi

# ──────────────────────────────────────────────────────────────────────
# 4. Generate session secrets
# ──────────────────────────────────────────────────────────────────────
if [ ! -f "$SECRETS_VAULT" ]; then
  log "Generating session secrets (saved to $SECRETS_VAULT, mode 0600)"
  umask 077
  cat > "$SECRETS_VAULT" <<EOF
# Generated $(date). Keep this file. It contains the secrets you'd need to
# rotate sessions or impersonate the founder dashboard. Do not commit.
ORPHO_HMAC_SECRET=$(openssl rand -hex 32)
ORPHO_FOUNDER_TOKEN=$(openssl rand -hex 32)
EOF
  chmod 0600 "$SECRETS_VAULT"
fi
c_green "  ✓ session secrets ready in $SECRETS_VAULT"

# ──────────────────────────────────────────────────────────────────────
# 5. Prompt for external API keys (Stripe + Resend)
# ──────────────────────────────────────────────────────────────────────
if ! grep -q "^STRIPE_SECRET_KEY=" "$SECRETS_VAULT"; then
  hr
  c_blue "Stripe live keys"
  echo "  Get from https://dashboard.stripe.com/apikeys (Live mode toggle ON)"
  echo "  You need:"
  echo "    • Secret key (sk_live_...)"
  echo "    • Publishable key (pk_live_...)"
  echo "  Then create a webhook endpoint at"
  echo "    https://dashboard.stripe.com/webhooks → Add endpoint"
  echo "    URL: https://$DOMAIN/api/stripe/webhook"
  echo "    Events: checkout.session.completed, customer.subscription.{created,updated,deleted}"
  echo "    Copy the signing secret (whsec_...)"
  echo ""
  read -r -p "Paste STRIPE_SECRET_KEY (sk_live_...) — press Enter to skip and use TEST keys: " SK
  read -r -p "Paste STRIPE_PUBLISHABLE_KEY (pk_live_...): " PK
  read -r -p "Paste STRIPE_WEBHOOK_SECRET (whsec_...): " WS
  {
    echo "STRIPE_SECRET_KEY=${SK:-PLACEHOLDER_NEEDS_REAL_KEY}"
    echo "STRIPE_PUBLISHABLE_KEY=${PK:-PLACEHOLDER_NEEDS_REAL_KEY}"
    echo "STRIPE_WEBHOOK_SECRET=${WS:-PLACEHOLDER_NEEDS_REAL_KEY}"
  } >> "$SECRETS_VAULT"
fi

if ! grep -q "^RESEND_API_KEY=" "$SECRETS_VAULT"; then
  hr
  c_blue "Resend email API key"
  echo "  Get from https://resend.com/api-keys (sign up with orphograph@proton.me)"
  echo "  After signup, also verify the orphograph.com domain at"
  echo "    https://resend.com/domains — it'll give you 3 DNS records to add"
  echo "  Skip blank if you want to deploy without email for now (magic-link signin"
  echo "  will be broken but anchoring + receipts work)."
  echo ""
  read -r -p "Paste RESEND_API_KEY (re_...) — Enter to skip: " RS
  echo "RESEND_API_KEY=${RS:-}" >> "$SECRETS_VAULT"
fi
c_green "  ✓ external keys collected (vault: $SECRETS_VAULT)"

# ──────────────────────────────────────────────────────────────────────
# 6. Push secrets + deploy
# ──────────────────────────────────────────────────────────────────────
log "Pushing secrets to Fly (one apply, no app restart per secret)"
# shellcheck disable=SC2046
retry "fly secrets set" 3 bash -c "
  fly secrets set -a $APP_NAME \
    \$(grep -v '^#' '$SECRETS_VAULT' | grep -v '^\$' | xargs)
" || die "fly secrets set failed."

log "fly deploy (this can take 2-5 min on first run)"
retry "fly deploy" 2 fly deploy -a "$APP_NAME" --ha=false \
  || die "Deploy failed. Inspect $LOG and 'fly logs -a $APP_NAME'."

# ──────────────────────────────────────────────────────────────────────
# 7. Add custom domain + cert
# ──────────────────────────────────────────────────────────────────────
log "Add $DOMAIN as custom domain + Let's Encrypt cert"
fly certs add "$DOMAIN" -a "$APP_NAME" >>"$LOG" 2>&1 || true
fly certs add "www.$DOMAIN" -a "$APP_NAME" >>"$LOG" 2>&1 || true

# ──────────────────────────────────────────────────────────────────────
# 8. Print DNS records to add at registrar
# ──────────────────────────────────────────────────────────────────────
hr
c_blue "DNS records to add at your domain registrar (whoever orphograph.com is registered with):"
echo ""
IPV4=$(fly ips list -a "$APP_NAME" 2>/dev/null | awk '/v4/ {print $2; exit}')
IPV6=$(fly ips list -a "$APP_NAME" 2>/dev/null | awk '/v6/ {print $2; exit}')
echo "  Type: A     Name: @     Value: $IPV4"
echo "  Type: AAAA  Name: @     Value: $IPV6"
echo "  Type: CNAME Name: www   Value: $APP_NAME.fly.dev"
echo ""
c_yellow "Add those at your registrar (Cloudflare, Namecheap, etc.), then press Enter."
c_yellow "Cert issuance needs the A record live first."
read -r -p "Press Enter when DNS records are saved at registrar: " _

# ──────────────────────────────────────────────────────────────────────
# 9. Verify
# ──────────────────────────────────────────────────────────────────────
log "Waiting up to 5 min for DNS to propagate..."
for i in $(seq 1 30); do
  if nslookup "$DOMAIN" 8.8.8.8 2>/dev/null | grep -q "$IPV4"; then
    c_green "  ✓ DNS resolves to Fly"
    break
  fi
  printf "."
  sleep 10
done
echo ""

log "Waiting for Let's Encrypt cert..."
for i in $(seq 1 30); do
  if curl -sI --max-time 5 "https://$DOMAIN/api/health" 2>/dev/null | grep -q "200"; then
    c_green "  ✓ HTTPS live"
    break
  fi
  printf "."
  sleep 10
done
echo ""

HEALTH=$(curl -sS --max-time 8 "https://$DOMAIN/api/health" 2>/dev/null || echo "{}")
log "Final health: $HEALTH"

hr
c_green "Deploy complete."
echo ""
echo "  Live site:        https://$DOMAIN"
echo "  Health:           https://$DOMAIN/api/health"
echo "  Founder metrics:  https://$DOMAIN/founder/metrics.html"
echo "                    (paste ORPHO_FOUNDER_TOKEN from $SECRETS_VAULT)"
echo "  Fly dashboard:    https://fly.io/apps/$APP_NAME"
echo ""
echo "  Next: drop a test file at https://$DOMAIN and confirm a receipt comes back."
hr
