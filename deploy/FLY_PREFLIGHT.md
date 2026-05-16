# Fly.io Deploy Preflight — go/no-go report

**Generated:** 2026-05-14
**Verdict:** Conditional GO. Two soft blockers (memory + business-address secret), no hard blockers. Five-command deploy path at the bottom.

---

## Gate-by-gate audit

| # | Gate | Status | Notes |
|---|---|---|---|
| 1 | `fly.toml` syntactically valid | ✓ PASS | `app = "orphograph"`, region `iad`, single shared VM |
| 2 | Dockerfile builds non-root | ✓ PASS | UID 10001, `orpho` user, `/usr/sbin/nologin` shell |
| 3 | Stdlib-only — zero pip install | ✓ PASS | No `pip install` line; image stays ~50MB |
| 4 | `.dockerignore` present | ✓ PASS (just added) | Strips `.env*`, tests, deploy docs, marketplace tree, `.git` |
| 5 | Persistent volume mounted | ✓ PASS | `orphograph_data` → `/app/data`, 1GB initial |
| 6 | Health check wired | ✓ PASS | `GET /api/health` every 30s with 5s timeout |
| 7 | `internal_port` matches `PORT` env | ✓ PASS | Both 8080 |
| 8 | `force_https` enabled | ✓ PASS | LetsEncrypt auto-managed by Fly |
| 9 | Min machine count ≥ 1 | ✓ PASS | `min_machines_running = 1` — no cold-start customer-facing |
| 10 | Memory headroom | ⚠ SOFT BLOCK | 256MB is tight for Python 3.11 + 5 threads + Resend + Stripe webhook handling. **Recommend 512MB.** |
| 11 | Secrets enumerated | ⚠ SOFT BLOCK | `ORPHO_BUSINESS_ADDRESS` from compliance audit not yet set; CAN-SPAM blocker for any marketing email |
| 12 | Ledger NOT baked into image | ⚠ SUBOPTIMAL | `COPY ledger.jsonl* /app/` copies the 12KB dev ledger. Inert (lives outside `/app/data`) but should be removed from Dockerfile after first deploy |
| 13 | `.env.local` excluded from build | ✓ PASS | `.dockerignore` covers it |
| 14 | Fly CLI installed | ✗ MISSING | Founder must install — see step 0 below |
| 15 | DNS pointed at Fly | ✗ DEFERRED | After first deploy, point `orphograph.com` A/AAAA at the Fly anycast IPs |
| 16 | Stripe webhook URL configured | ✗ DEFERRED | After deploy, set webhook in Stripe Dashboard to `https://orphograph.com/api/stripe/webhook` |

**One-line summary:** the application stack is production-ready. The remaining gates are environment provisioning (install Fly CLI, set secrets, point DNS) — all founder-side, all reversible.

---

## Step 0 — Install Fly CLI (founder action)

Per the throttle that bit us earlier on flyctl download, install via the Brave-window Terminal so Claude Code's network constraints don't apply:

```bash
brew install flyctl                          # macOS, simplest
# or:
curl -L https://fly.io/install.sh | sh       # falls back
fly auth login                               # opens browser, sign in / sign up
```

Verify:
```bash
fly version
fly auth whoami
```

---

## Step 1 — Set secrets (founder action, one-time)

Required for production:

```bash
cd ~/orphograph
fly secrets set \
  ORPHO_BUSINESS_ENTITY="Orphograph" \
  ORPHO_BUSINESS_ADDRESS="PO Box 12345, San Juan, PR 00901" \
  STRIPE_WEBHOOK_SECRET="whsec_..." \
  STRIPE_API_KEY="sk_live_..." \
  RESEND_API_KEY="re_..." \
  BTC_RECEIVE_ADDRESS="bc1qclvjjmwmr294rydv4x0dc787nx9jd8j4ny4jaz" \
  ORPHO_COOKIE_SECURE="1"
```

**Replace placeholders** with the real values. The BTC address is your existing one; the Stripe + Resend keys come from those respective dashboards; the business address is whatever PO box / virtual mailbox you set up per the compliance audit.

Optional but recommended:
```bash
fly secrets set \
  HMAC_SECRET="$(openssl rand -hex 32)" \
  ORPHO_RATE_LIMIT_PER_HOUR="10" \
  MIN_CALENDARS_OK="3"
```

---

## Step 2 — Bump memory (soft-block remediation)

Edit `fly.toml`:

```toml
[[vm]]
  cpu_kind = "shared"
  cpus = 1
  memory_mb = 512    # was 256 — Python 3.11 stable floor under load
```

Cost delta: shared-cpu-1x with 256MB is ~$2/mo on the free allowance; 512MB stays in free allowance (3 shared-cpu-1x machines free up to 256MB, but for a single 512MB the cost is ~$2/mo). Trade is fine.

---

## Step 3 — First deploy

```bash
cd ~/orphograph
fly launch --copy-config --no-deploy   # imports existing fly.toml, creates app
fly volumes create orphograph_data --region iad --size 1
fly deploy
```

Watch the deploy stream — health check passes within ~20s on a fresh VM.

Verify the deploy:
```bash
fly status
fly logs                                # tail of init_volume + first requests
curl https://orphograph.fly.dev/api/health
```

You should see `{"ok": true, ...}` from the health endpoint.

---

## Step 4 — Point orphograph.com at Fly

In the Cloudflare dashboard for `orphograph.com`:

```
Type   Name    Content                       Proxy   TTL
A      @       <Fly IPv4 from `fly ips list`> orange  auto
AAAA   @       <Fly IPv6>                     orange  auto
CNAME  www     orphograph.com                 orange  auto
```

Or by API (if you have a token from the email-setup wizard already):

```bash
# Get the Fly IPs
fly ips list

# Push them to Cloudflare (the API token from setup_email.py works here)
source ~/orphograph/.env.local
ZONE_ID="$CLOUDFLARE_ZONE_ID"
FLY_V4="$(fly ips list -j | jq -r '.[] | select(.Type=="v4") | .Address')"
FLY_V6="$(fly ips list -j | jq -r '.[] | select(.Type=="v6") | .Address')"

curl -X POST "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"type":"A","name":"@","content":"'"${FLY_V4}"'","ttl":3600,"proxied":true}'

curl -X POST "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"type":"AAAA","name":"@","content":"'"${FLY_V6}"'","ttl":3600,"proxied":true}'
```

Then add a Fly certificate so HTTPS works on the custom domain:

```bash
fly certs add orphograph.com
fly certs add www.orphograph.com
fly certs check orphograph.com    # repeat until "Ready"
```

LetsEncrypt issues the cert in 1-5 minutes once DNS propagates.

---

## Step 5 — Wire Stripe webhook to production URL

Stripe Dashboard → Developers → Webhooks → Add endpoint:

```
URL:       https://orphograph.com/api/stripe/webhook
Events:    checkout.session.completed
           customer.subscription.created
           customer.subscription.updated
           customer.subscription.deleted
```

Copy the signing secret it generates and:

```bash
fly secrets set STRIPE_WEBHOOK_SECRET="whsec_..."
```

The deploy automatically restarts when secrets change.

---

## Step 6 — Upload the BTC address file (if not using env var)

If you prefer the file-based path (per `btc_payments.py:_load_btc_address`):

```bash
fly ssh console -C 'sh -c "echo bc1qclvjjmwmr294rydv4x0dc787nx9jd8j4ny4jaz > /app/data/btc_address.txt && chmod 600 /app/data/btc_address.txt"'
```

Either env var or file works. Env var is simpler; file is rotatable without restart.

---

## Step 7 — Smoke test prod

```bash
curl -s https://orphograph.com/api/health | jq .
curl -s -o /dev/null -w "%{http_code}\n" https://orphograph.com/
curl -s -o /dev/null -w "%{http_code}\n" https://orphograph.com/blog/written-by-an-ai
curl -s -o /dev/null -w "%{http_code}\n" "https://orphograph.com/api/unsubscribe?e=smoke@example.com"
```

All four should return 200.

Now anchor a real file (your own photo, anything):

```bash
F=/path/to/any/file
HASH=$(shasum -a 256 "$F" | awk '{print $1}')
SHA512=$(shasum -a 512 "$F" | awk '{print $1}')
curl -X POST https://orphograph.com/api/anchor \
  -H "Content-Type: application/json" \
  -d "{\"hash_hex\":\"$HASH\",\"sha512_hex\":\"$SHA512\"}" | jq .
```

Expected: receipt JSON with `receipt_id` and `calendars_ok: 5`. Open `https://orphograph.com/r/<receipt_id>` to see the live receipt page.

---

## Step 8 — Tear-down or rollback (in case of disaster)

Visibility flip (preserves data, hides site):
```bash
fly scale count 0
```

Full rollback to previous release:
```bash
fly releases
fly deploy --image registry.fly.io/orphograph:<previous-version>
```

Hard nuke (destroys everything — only if you mean it):
```bash
fly apps destroy orphograph
fly volumes destroy <volume-id>
```

---

## What this preflight deliberately does NOT do

- Does not autoscale beyond `min_machines_running = 1`. For a $0-MRR launch, a single machine is correct. Add `auto_start_machines = true` + a second region only after seeing real traffic patterns.
- Does not configure a CDN beyond Cloudflare's orange-cloud proxy. Fly's anycast + CF's edge is sufficient for tens of thousands of requests/day.
- Does not set up a staging environment. For a solo founder with 188 passing tests, the prod-only workflow is appropriate. Promote to staging when there's a paid customer to protect.
- Does not configure backups. Fly volumes are SSD-backed but not auto-snapshotted. After first revenue, add `fly volumes snapshot create` to a cron.

---

## Cost estimate

| Component | Monthly |
|---|---|
| 1× shared-cpu-1x 512MB | ~$2 (free up to 3× 256MB, +$2 for the bump) |
| 1GB volume | ~$0.15 |
| Bandwidth | ~$0 (160GB free egress) |
| Cloudflare proxy | $0 (free tier) |
| Resend (≤3k/mo) | $0 |
| Stripe | 2.9% + 30¢/tx variable |
| Domain | ~$1 (orphograph.com amortized) |
| **Total fixed** | **~$3–4/mo** at zero customers |

The launch operates within the free Fly allowance plus ~$2/mo for the memory bump. Stay frugal until paid revenue covers it; current monthly burn is the cost of a vending-machine coffee.
