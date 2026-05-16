# Launch walkthrough — Orphograph go-live

The single source of truth for taking Orphograph from "code on
disk" to "live at https://orphograph.com." Follow top to bottom.
Each step has a copy-paste block, an acceptance check, and a
"resume here" anchor in case you stop midway.

Estimated wall-clock: **~90 minutes of active work** spread across
~3 days due to Stripe review and DNS propagation.

State as of 2026-05-13:

- ✅ `orphograph.com` registered (you confirmed)
- ✅ Verifier repo initialized locally with clean commit identity
  (`Orphograph <orphograph@users.noreply.github.com>`)
- ✅ 9/9 publish-safety checks passing
- ⬜ Steps 1–6 below

If `scripts/launch.sh` exists and you have `gh` + `flyctl` on PATH,
running it walks you through this entire flow interactively. This
doc is the manual version.

---

## Step 1 — GitHub account, org, repo, push

**Time:** 15 minutes (mostly waiting on 2FA email).

### 1a. Create the dedicated account (browser)

1. Open a **private / incognito window**. Sign out of any existing
   GitHub session.
2. Go to https://github.com/signup
3. **Email:** a fresh address that doesn't appear anywhere else.
   Options in priority:
   - Cloudflare Email Routing alias on `orphograph.com` →
     forwards to your real inbox (best — works today since the
     domain is registered; instructions at
     https://developers.cloudflare.com/email-routing/)
   - ProtonMail / Tutanota / SimpleLogin throwaway alias
   - Your real address ONLY if you've accepted Pattern B's
     identity disclosure tradeoff (see `PUBLISH_SAFETY.md` §3)
4. **Username:** `orphograph` (the org will be `github.com/orphograph`)
5. **Display name:** `Orphograph` (leave field blank if it
   pre-fills with your real name from email)
6. **2FA:** Settings → Password & authentication →
   Two-factor authentication → Authenticator app → scan QR with
   1Password / Authy / Google Authenticator. **Do NOT use SMS.**
7. **Profile:** Settings → Profile → leave Name, Bio, Company,
   Location, Twitter all empty. The avatar can stay as the
   default identicon or upload the favicon.svg from
   `~/orphograph/web/favicon.svg`.
8. **Email privacy:** Settings → Emails → check "Keep my email
   addresses private" + "Block command line pushes that expose
   my email." Both on.

### 1b. Create the organization

1. Settings → Organizations → New organization (free plan)
2. Org name: `orphograph`
3. Contact email: same as account
4. Owner: you (this account)
5. Member visibility: Settings → People → "Members are visible
   only to other members"

### 1c. Create the empty repository

Don't initialize with a README from GitHub's side — we already
have one locally.

1. github.com/organizations/orphograph/repositories/new
2. Name: `orphograph-verify`
3. Description: `Standalone verifier for Orphograph receipts. MIT.`
4. Visibility: **Public**
5. **DO NOT** check "Add a README" / "Add .gitignore" / "Add license"
6. Create.

### 1d. Authentication (pick one)

**Option A — fine-grained PAT (simplest):**

1. Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token
2. Token name: `orphograph-laptop-push`
3. Resource owner: `orphograph` (the org)
4. Repository access: Only selected → `orphograph/orphograph-verify`
5. Permissions:
   - Contents: Read and write
   - Metadata: Read (automatic)
   - Workflows: Read and write (in case we add Actions)
6. Generate, copy the token (`github_pat_...`).

Configure locally:
```bash
gh auth login --hostname github.com --git-protocol https --with-token <<< "github_pat_PASTE_HERE"
```
(Install `gh` first via `brew install gh` if needed.)

**Option B — SSH key:**

```bash
ssh-keygen -t ed25519 -C "orphograph push key" -f ~/.ssh/id_ed25519_orphograph -N ""
cat ~/.ssh/id_ed25519_orphograph.pub
# Copy that line.
# In GitHub: Settings → SSH and GPG keys → New SSH key
# Title: "orphograph laptop", paste, save.

# Configure git to use this key for this host:
cat >> ~/.ssh/config <<'EOF'
Host github-orphograph
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_orphograph
    IdentitiesOnly yes
EOF
```

### 1e. Push

```bash
cd ~/orphograph/dist/orphograph-verify

# Final safety check — must be 9/9 green:
bash ~/orphograph/scripts/publish_safety_check.sh

# Add the remote:
# Option A (PAT):  https URL — gh CLI handles auth
git remote add origin https://github.com/orphograph/orphograph-verify.git
# Option B (SSH):  use the host alias from 1d
# git remote add origin github-orphograph:orphograph/orphograph-verify.git

git push -u origin main
```

**Acceptance:** the URL `https://github.com/orphograph/orphograph-verify`
loads in incognito and shows your 12 files + a rendered README.

---

## Step 2 — Resend (email delivery)

**Time:** 30 minutes active + DNS propagation wait.

### 2a. Account + domain

1. Sign up at https://resend.com (use the same fresh email as
   GitHub, or a separate one — doesn't matter).
2. Domains → Add Domain → `orphograph.com`.
3. Resend prints TXT records (SPF + DKIM + optional DMARC).
   Copy them.

### 2b. DNS records

Log into your domain registrar (the one you registered
orphograph.com with). Add each TXT record exactly as Resend
showed:

```
TXT @                       v=spf1 include:_spf.resend.com ~all
TXT resend._domainkey       <long DKIM string>
TXT _dmarc                  v=DMARC1; p=quarantine; rua=mailto:dmarc@orphograph.com
```

(If you want Cloudflare Email Routing for the inbound side, add
its MX records here too; they don't conflict with Resend's outbound.)

### 2c. Verify + API key

1. Back in Resend, click "Verify Domain". May take 5–60 min for
   DNS to propagate.
2. Once verified, API Keys → Create → `orphograph-prod` →
   Sending access only. Copy the key (`re_...`).

**Acceptance:** Resend domain shows "Verified" status.

---

## Step 3 — Stripe (Pack + Subscription)

**Time:** 45 minutes active + 1–3 day review.

### 3a. Account activation

1. Sign up at https://dashboard.stripe.com.
2. Activate account: business details, tax info, bank.
   - If LLC pending: register as **Individual** with your SSN.
     Set "Public business name" to `Orphograph`. Statement
     descriptor: `ORPHOGRAPH PACK` (uppercase, max 22 chars).
     Migrate to LLC entity later (Stripe supports it).
   - If LLC ready: business entity, EIN.
3. ToS URL: `https://orphograph.com/terms.html`
   (will 200 after step 5 deploy).
4. Privacy URL: `https://orphograph.com/privacy.html`
5. Wait for Stripe's activation review (1–3 days).

### 3b. Products

In dashboard → Products:

| Product | Type | Price | Notes |
|---|---|---|---|
| Orphograph Pack | One-time | $7.00 USD | "10 anchor credits, never expires" |
| Orphograph Personal — Monthly | Recurring monthly | $5.00 USD | "Unlimited anchors + history" |
| Orphograph Personal — Annual | Recurring yearly | $60.00 USD | "Save $0 vs monthly" (adjust if you want a discount) |

### 3c. Payment Links

For each Product → Pricing → Payment Links → Create:

- Success URL: `https://orphograph.com/?stripe_done=1`
- Collect customer email: **required**
- Allow promotion codes: **on** (for the LAUNCH20 coupon)
- For subscriptions: cancel at customer request: **on**

Copy each Payment Link URL.

### 3d. Webhook

1. Developers → Webhooks → Add endpoint
2. URL: `https://orphograph.com/api/stripe/webhook`
3. Events:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
4. Save. Click into the endpoint → "Signing secret" → reveal +
   copy (`whsec_...`).

### 3e. Restricted API key

1. Developers → API keys → Create restricted key
2. Name: `orphograph-prod-restricted`
3. Permissions per `deploy/HANDOFF.md` §3 (Subscriptions
   read+write, Customers read, Refunds write, etc.)
4. Copy the key (`rk_live_...`).

### 3f. Update `web/app.js` constants

Open `~/orphograph/web/app.js` and replace these constants:

```javascript
const STRIPE_PACK_URL = "https://buy.stripe.com/PASTE_PACK_LINK";
const STRIPE_PERSONAL_MONTHLY_URL = "https://buy.stripe.com/PASTE_MONTHLY";
const STRIPE_PERSONAL_ANNUAL_URL = "https://buy.stripe.com/PASTE_ANNUAL";
```

Commit + redeploy (step 5).

**Acceptance:** test purchase with `4242 4242 4242 4242` →
webhook fires → claim email arrives.

---

## Step 4 — Fly.io deploy

**Time:** 45 minutes.

### 4a. Install + signup

```bash
brew install flyctl   # or curl -L https://fly.io/install.sh | sh
fly auth signup       # or `fly auth login` if you have an account
```

Add a payment method to Fly (required even for free tier).

### 4b. Launch

```bash
cd ~/orphograph
fly launch --copy-config --no-deploy
# Pick region: iad (US east; closest to OpenTimestamps calendars)
# Decline: databases, redis
```

### 4c. Volume

```bash
fly volumes create orphograph_data --region iad --size 1
```

### 4d. Secrets

```bash
fly secrets set \
  STRIPE_WEBHOOK_SECRET=whsec_PASTE \
  STRIPE_SECRET_KEY=rk_live_PASTE \
  RESEND_API_KEY=re_PASTE
```

### 4e. Deploy

```bash
fly deploy
```

Wait for healthcheck on `/api/health`.

### 4f. Custom domain

```bash
fly certs create orphograph.com
# Output prints A + AAAA records. Add them at your registrar.
fly certs check orphograph.com   # repeat until "READY"
```

**Acceptance:**

```bash
curl -fs https://orphograph.com/api/health | python3 -m json.tool
bash ~/orphograph/scripts/preflight.sh https://orphograph.com
# 21/21 green
```

---

## Step 5 — Scheduled jobs (OTS upgrade + free-tier expiry)

```bash
fly machines run \
  --schedule "every-30-minutes" \
  --command "python3 server/upgrade_worker.py" \
  --env "ORPHO_DATA_DIR=/app/data" \
  --vm-memory 256 \
  .

fly machines run \
  --schedule "daily" \
  --command "python3 server/expire_worker.py" \
  --env "ORPHO_DATA_DIR=/app/data" \
  --env "ORPHO_EXPIRY_DAYS=30" \
  --vm-memory 256 \
  .
```

**Acceptance:**

```bash
fly ssh console --command "ls -la /app/data/upgrade_log.jsonl"
# tail after 30+ minutes
```

---

## Step 6 — Launch motion (founder-only)

Code is live. Now distribution.

### 6a. 5 photographer interviews

Draft templates in `~/orphograph/outreach/`:
- `cold_dm_twitter.md` — 3 variants
- `indie_hackers_post.md` — forum post
- `reddit_r_photography.md` — community discussion

Goal: 5 recorded 20-min conversations within 1 week. Each one
goes to `~/orphograph/docs/decisions/photographer_interview_NN.md`
with the ONE surprise from the call.

If 3 of 5 surface the same surprise, swap landing copy from
`content/copy/landing_variants.md` before public launch.

### 6b. Show HN

Schedule for **Tuesday 9:00 AM ET**. Draft at
`~/orphograph/outreach/show_hn_draft.md` includes pre-rehearsed
answers for 5 common HN reply patterns.

Lurk on the post for 2 hours after submission. Answer every
top-level comment within 5 minutes.

### 6c. Twitter + LinkedIn launch posts

After Show HN stabilizes (~2 hours):
- Twitter thread: `outreach/twitter_launch_thread.md`
- LinkedIn long post: `outreach/linkedin_launch.md`

Pin the Twitter thread to the brand account for 7 days.

### 6d. SEO

Three blog posts ready in `content/blog/`:
1. `prove-photo-existed-before-ai.md` — primary photographer-fear
2. `opentimestamps-for-non-developers.md` — developer audience
3. `bitcoin-merkle-roots-unforgeable-timestamps.md` — technical

Publish one per week starting launch week.

---

## Acceptance: launched

When all 6 steps are green, the kill-criteria timer per
`CLAUDE.md` §12 starts:

| Month | MRR threshold | Action if below |
|---|---|---|
| 3 | $50 | Side project (≤10 hr/week) |
| 6 | $200 | Maintenance only |
| 9 | $500 OR churn >10%/mo | Pivot or shut paid tier |
| 12 | $1,000 | Decide: kill, sell, or commit another year |

The kill criteria are deliberately public per
`deploy/PAYMENT_PII_AUDIT.md` and the audit doc; we don't pretend
optimism we don't have.

---

## What I (Claude) do once you're live

Per `deploy/HANDOFF.md`:

- Daily: preflight against the live site; triage Fly logs
- Weekly: publish one SEO post; run gpg-encrypted backups
- Monthly: kill-criteria check against actual MRR
- Quarterly: re-run security audit pass
- On demand: customer support drafts (you click send), Stripe
  refund flow via `scripts/refund_pack.py`, incident response
  per `deploy/RUNBOOK.md`

Hand me the tokens listed in `HANDOFF.md` and I take over the
operational cycle.
