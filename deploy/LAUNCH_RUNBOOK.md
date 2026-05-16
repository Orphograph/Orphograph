# Orphograph — Launch Runbook

**Generated:** 2026-05-14
**Audience:** founder, day-of launch. Read top-to-bottom.
**Single doc rule:** every other doc in `~/orphograph/deploy/` exists to support this one. If a section says "see `deploy/X.md`", that doc has the deep detail; this runbook has the action.

---

## Header

**What this is.** Orphograph is a Bitcoin-anchored file-hashing service. Browsers SHA-256 a file locally, the server anchors that 32-byte hash through OpenTimestamps to the Bitcoin chain, and the customer gets a receipt anyone can verify against the public chain without trusting us. Files never upload.

**Who it's for.** Photographers and creators in 2026 who need to prove their work predates AI training datasets — and the long tail of journalists, indie musicians, manuscript authors, and crypto-curious users that follow.

**What success looks like (six months out).** $200 MRR floor. Below that, per `~/orphograph/CLAUDE.md`, Orphograph demotes to a side-project (≤5 hr/week) and primary time shifts to the AI-services agency. $200 MRR is the kill-criteria threshold, not a stretch goal.

**How this runbook is sequenced.** Three phases. **T-7** is preparation (Cloudflare, Stripe, Phantom, Fly setup, end-to-end test in test mode). **T-3 → T-1** is copy review + DNS cutover + production smoke. **T-0** is launch morning. **T+1 / T+7 / T+30+** is the followup ladder and kill-criteria review.

---

## T-7 days — preparation week

Goal by end of week: a working orphograph.com running on Fly with Stripe in test mode, Resend signing email, a 50-address Phantom pool loaded, and a successful end-to-end test purchase that emails a receipt.

### 1. Email + DNS provisioning (Cloudflare + Resend)

```bash
bash ~/orphograph/scripts/launch_email_setup.sh
```

This wraps `scripts/setup_email.py` — the cozy wizard provisions Cloudflare Email Routing (`hello@orphograph.com` → personal Gmail) and Resend (SPF / DKIM / DMARC records pushed to Cloudflare via API).

When Cloudflare emails the destination verification link, click it from Gmail. The wizard won't proceed past that step until you do.

Output to verify:
- `dig TXT orphograph.com` shows v=spf1 include:_spf.resend.com ...
- `dig TXT resend._domainkey.orphograph.com` returns the DKIM key
- `dig TXT _dmarc.orphograph.com` returns `v=DMARC1; p=quarantine; ...`
- A test email sent through Resend's dashboard lands in Gmail inbox (not spam).

Reference: `deploy/EMAIL_AND_LEGAL_COMPLIANCE.md` Section "What's implemented".

### 2. Business-address env var (already done — verify only)

The CAN-SPAM physical-address requirement is satisfied by the PMB at:

```
405 Ave. Esmeralda, San Juan, PR 00901
```

Verify the value is set in `.env.local`:

```bash
grep ORPHO_BUSINESS_ADDRESS ~/orphograph/.env.local
# Expected: ORPHO_BUSINESS_ADDRESS="..., 405 Ave. Esmeralda, ..., San Juan, PR 00901"
```

If missing, append it; it will be pushed to Fly secrets in step 7. Without it, every outbound email is non-compliant (`server/mailer.py:_footer_*`).

### 3. Phantom — generate 50 receive addresses

Per `deploy/PHANTOM_BTC_SETUP.md`:

1. Open Phantom → switch to Bitcoin chain.
2. Tap **Receive**. Copy the `bc1q...` or `bc1p...` address.
3. Repeat tap-Receive-copy until you have **50** addresses.
4. Paste them one per line into `~/orphograph/data/btc_address_pool.txt`.
5. `chmod 600 ~/orphograph/data/btc_address_pool.txt`.

Verify:

```bash
cd ~/orphograph
python3 -c "
import sys; sys.path.insert(0, 'server')
import btc_payments
print(f'pool size: {btc_payments.pool_size()}')
"
```

Expected: `pool size: 50`. If it shows 0, your lines don't match the `bc1q*` / `bc1p*` shape — fix and re-run.

### 4. Stripe activation (per `deploy/STRIPE_ACTIVATION.md`)

Activate as an **individual** (not LLC). PR-resident, SSN-based. Sole-prop is fine until MRR > $200 (see T+30+ ladder below).

The companion doc `deploy/STRIPE_ACTIVATION.md` is being authored in parallel; if it's not on disk yet at the time you launch, the inline summary is:

1. Stripe Dashboard → Activate account → Individual / sole proprietor.
2. Verify identity (SSN-4 + DOB + PR address).
3. Add bank account (PR-resident bank or USAA-equivalent that accepts PR ACH).
4. Confirm: Dashboard banner says "Your account is fully activated."

### 5. Stripe — create products + Payment Links

In Stripe Dashboard → Products:

| Product | Price | Type | Notes |
|---|---|---|---|
| Orphograph Pack | $7.00 USD | One-time | 10 anchors, no expiry |
| Orphograph Personal | $5.00 USD/mo | Recurring | Unlimited, monthly |
| Orphograph Creator | $19.00 USD/mo | Recurring | Capture-time app + 100 receipts/mo; DO NOT advertise publicly until beta exists |

For Pack and Personal: enable **Payment Links**. Copy the resulting URLs into `.env.local`:

```bash
STRIPE_PAYMENT_LINK_PACK="https://buy.stripe.com/..."
STRIPE_PAYMENT_LINK_PERSONAL="https://buy.stripe.com/..."
# Creator: omit until beta lands per CLAUDE.md "Creator tier" rules
```

### 6. Fly — create app + volume (no deploy yet)

```bash
cd ~/orphograph
fly auth login                          # opens browser
fly launch --copy-config --no-deploy    # imports fly.toml, creates app
fly volumes create orphograph_data --region iad --size 1
```

Reference: `deploy/FLY_PREFLIGHT.md` Step 3. If `flyctl` isn't installed, install via `brew install flyctl` from a non-Claude Terminal (Claude session throttles downloads >5MB per `feedback_claude_session_network_throttling.md`).

### 7. Fly — set all production secrets

```bash
cd ~/orphograph
fly secrets set \
  ORPHO_BUSINESS_ENTITY="Orphograph" \
  ORPHO_BUSINESS_ADDRESS="$(grep ^ORPHO_BUSINESS_ADDRESS .env.local | cut -d= -f2- | tr -d '\"')" \
  STRIPE_API_KEY="$(grep ^STRIPE_API_KEY .env.local | cut -d= -f2- | tr -d '\"')" \
  STRIPE_WEBHOOK_SECRET="$(grep ^STRIPE_WEBHOOK_SECRET .env.local | cut -d= -f2- | tr -d '\"')" \
  STRIPE_PAYMENT_LINK_PACK="$(grep ^STRIPE_PAYMENT_LINK_PACK .env.local | cut -d= -f2- | tr -d '\"')" \
  STRIPE_PAYMENT_LINK_PERSONAL="$(grep ^STRIPE_PAYMENT_LINK_PERSONAL .env.local | cut -d= -f2- | tr -d '\"')" \
  RESEND_API_KEY="$(grep ^RESEND_API_KEY .env.local | cut -d= -f2- | tr -d '\"')" \
  BTC_RECEIVE_ADDRESS="$(grep ^BTC_RECEIVE_ADDRESS .env.local | cut -d= -f2- | tr -d '\"')" \
  ORPHO_COOKIE_SECURE="1" \
  HMAC_SECRET="$(openssl rand -hex 32)" \
  ORPHO_RATE_LIMIT_PER_HOUR="10" \
  MIN_CALENDARS_OK="3" \
  ORPHO_FOUNDER_TOKEN="$(openssl rand -hex 24)"
```

Save `ORPHO_FOUNDER_TOKEN` separately — you'll need it for `/api/founder/payout-status` on launch morning (step T-0/4).

### 8. End-to-end test in Stripe **test mode**

1. Anchor a real personal file in the dev server (`python3 server/app.py` running locally).
2. Buy a $7 Pack with Stripe test card `4242 4242 4242 4242` (any future expiry, any CVC).
3. Verify the receipt email arrives at your Gmail.
4. Verify the receipt page renders with all 5 OTS calendars confirmed.
5. Verify `/account.html` shows the new Pack credit after sign-in.

If any of these fail: stop launch, debug, do not advance to T-3.

---

## T-3 days — final-week tuning

Goal: copy is honest, the AI-transparency post is live, social posts are scheduled.

### 9. Re-read all 6 existing blog posts

Open `web/blog/`. For each post, scan for:

- Any phrase implying legal admissibility ("court-admissible", "legally binding", "notarized"). Per `~/orphograph/CLAUDE.md` principle #5, these must NOT appear.
- Any claim about competitor capabilities you can't verify.
- Any pricing language that contradicts the canonical ladder ($7 / $5 / $19).
- Any "we" claims about a team — single founder, the copy must reflect it.

The deny-phrase scanner already gates `docs/regulatory/*` (per memory `project_regulatory_self_audit.md`); run it against blog posts too if available, otherwise eyeball.

### 10. Publish the AI-transparency article

The article is live in dev at `/blog/written-by-an-ai`. Confirm production:

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://orphograph.com/blog/written-by-an-ai
# Expected: 200 after T-1 deploy. For now in dev: http://127.0.0.1:8989/blog/written-by-an-ai
```

Reference: `deploy/ARTICLE_WRITTEN_BY_AN_AI.md` (full body — already published into `web/blog/written-by-an-ai.html`).

### 11. Schedule social posts (drafts in `deploy/LAUNCH_DRAFTS.md`)

| When | Where | Source |
|---|---|---|
| Tue 8:30 AM ET (T-0) | Show HN | `LAUNCH_DRAFTS.md` §1 |
| Tue 10:00 AM ET (T-0) | X thread (10 tweets) | `LAUNCH_DRAFTS.md` §3 |
| Wed 7:00 PM ET (T+1) | r/photography | `LAUNCH_DRAFTS.md` §2 |
| Thu 9:00 AM ET (T+2) | LinkedIn | improvise from §3 |
| Thu 8:30 AM ET (T+3) | PetaPixel cold email pitch | `LAUNCH_DRAFTS.md` cold-email template |

Pre-draft into Buffer / Hypefury / Typefully. **Do not auto-post Show HN** — must be manually posted by founder account from the launch morning desk.

---

## T-1 day — go-live preflight

Goal: orphograph.com resolves over HTTPS, the Stripe webhook receives test events, five real receipts work end-to-end.

### 12. Deploy

```bash
cd ~/orphograph
fly deploy
```

Watch the build/push/release stream. Health check should pass within ~20s.

### 13. Volume (skip if already created in T-7/6)

```bash
fly volumes list                 # confirm orphograph_data exists, 1GB
# If missing:
fly volumes create orphograph_data --region iad --size 1
fly deploy                       # re-deploy so it gets mounted
```

### 14. DNS — point Cloudflare to Fly

```bash
bash ~/orphograph/scripts/cf_point_to_fly.sh
```

This reads `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ZONE_ID` from `.env.local` and pushes the A/AAAA/CNAME records via Cloudflare API (per `deploy/FLY_PREFLIGHT.md` Step 4).

If the script isn't on disk yet, do it manually in the Cloudflare dashboard:

```
Type   Name    Content                        Proxy   TTL
A      @       <Fly IPv4 from `fly ips list`> orange  auto
AAAA   @       <Fly IPv6 from `fly ips list`> orange  auto
CNAME  www     orphograph.com                 orange  auto
```

### 15. TLS certificates

```bash
fly certs add orphograph.com
fly certs add www.orphograph.com
fly certs check orphograph.com    # repeat until "Ready"
```

LetsEncrypt issues in 1–5 min after DNS propagates.

### 16. Production smoke test

```bash
curl -sI https://orphograph.com/api/health
# Expected: HTTP/2 200, body {"ok": true, ...}

curl -s -o /dev/null -w "/blog post: %{http_code}\n" https://orphograph.com/blog/written-by-an-ai
curl -s -o /dev/null -w "/unsubscribe: %{http_code}\n" "https://orphograph.com/api/unsubscribe?e=smoke@example.com"
```

All should return 200.

Anchor a real photo:

```bash
F=~/Pictures/some_real_photo.jpg
HASH=$(shasum -a 256 "$F" | awk '{print $1}')
SHA512=$(shasum -a 512 "$F" | awk '{print $1}')
curl -X POST https://orphograph.com/api/anchor \
  -H "Content-Type: application/json" \
  -d "{\"hash_hex\":\"$HASH\",\"sha512_hex\":\"$SHA512\"}" | jq .
```

Expected: `"calendars_ok": 5` and a receipt URL. Open the URL in Brave — full receipt page with mempool.space + blockstream.info explorer links.

### 17. Marketplace plugin smoke test

Per `deploy/PLUGIN_PUBLISH.md` §1:

```bash
cd ~/orphograph
python3 -m py_compile marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py
python3 -m py_compile marketplace/orphograph-plugin/skills/orphograph-verify/verify.py
echo "smoke" > /tmp/anchor-smoke.txt
python3 marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py \
  /tmp/anchor-smoke.txt --endpoint https://orphograph.com --json
```

Expected: `"ok": true`, `calendars_ok` 5.

### 18. Stripe webhook smoke

Stripe Dashboard → Developers → Webhooks → your endpoint (`https://orphograph.com/api/stripe/webhook`) → **Send test webhook** → `checkout.session.completed`.

Verify in Fly logs:

```bash
fly logs | grep stripe
# Expected: 200 OK and a "webhook verified" log line
```

If verification fails, the `STRIPE_WEBHOOK_SECRET` set in T-7 step 7 doesn't match the live endpoint. Copy the current secret from the Stripe Dashboard and `fly secrets set STRIPE_WEBHOOK_SECRET=...` again.

### 19. Five real-file dry runs

Anchor 5 of your own files (photos, docs — anything). For each:

- Receipt page renders.
- 5/5 calendars confirmed.
- Receipt URL is reachable from a different device (test on phone with cellular, not home WiFi).
- After signing in to `/account.html` with the email you used, all 5 receipts appear in the history.

If any fail: **pause launch** and investigate. Do not proceed to T-0.

---

## T-0 morning — launch

Be at the keyboard by 8:00 AM ET. Brave open. Coffee. No Slack, no Twitter feed, no Discord.

### 20. 8:30 AM ET — Post Show HN

Open `deploy/LAUNCH_DRAFTS.md` §1. Copy the title and body. Post via your existing HN account. Title:

```
Show HN: Orphograph – Bitcoin-anchored file timestamping (files never upload)
```

URL field: `https://orphograph.com`

Paste body. Submit. Open in a new tab and verify the post is live.

### 21. 8:35–12:30 PM ET — at the keyboard

For the first **four hours**:

- Refresh the HN thread every 5–10 min.
- Reply to top comments within ~10 min of them landing.
- Don't argue. If someone says "this is just OTS", agree and point to the UX differentiation reply pre-drafted in `LAUNCH_DRAFTS.md` ("Why not just use OpenTimestamps directly?").
- Don't promise features beyond the ladder.
- Don't claim legal admissibility under any phrasing.

### 22. 10:00 AM ET — Post X thread

Open `LAUNCH_DRAFTS.md` §3. Tweets 1 through 10. Post Tweet 1, then reply-chain the others within 1 minute each (Twitter algorithm slightly favors quick chains). Pin Tweet 1 to your profile.

Tag `@AnthropicAI` only on Tweet 6 (the technical-detail one) — tagging on Tweet 1 looks like a request for attention rather than a description.

### 23. Hourly — check payout status

```bash
# Use the founder token you saved in T-7 step 7
curl -s "https://orphograph.com/api/founder/payout-status?token=$ORPHO_FOUNDER_TOKEN" | jq .
```

This shows hot-wallet (Phantom) balance, lifetime received sats, and recommended sweep amount. The launchd monitor pings Telegram automatically when the threshold is hit (per `deploy/BTC_PAYOUT_PIPELINE.md` — note the monitor is currently TODO; manual `/api/founder/payout-status` is the launch-day substitute).

### 24. Late afternoon — first revenue check

If by 4 PM ET no Pack purchase or Personal subscription has come in:

- **DO NOT panic.** Most launches don't convert day-0.
- Re-read top HN comments for sentiment signal.
- Check Stripe Dashboard → Events for any `checkout.session.completed` (verifies the webhook is actually receiving).
- Check Resend Dashboard → Emails for delivery health (any bounces, any spam complaints).

---

## T+1 day — followup

### 25. 7:00 PM ET — r/photography

Open `LAUNCH_DRAFTS.md` §2. Copy/paste into r/photography. Self-promo flair if the sub requires it. Don't crosslink the HN thread in the body (sub allergic); reply in comments with the link if asked.

### 26. Reply to every Show HN comment <24h old

Open HN thread. Sort by new. Reply substantively to anyone who hasn't gotten one yet. Don't reply to bots / one-word replies / clear trolls.

### 27. Email any DMs / inquiries

If anyone DM'd `hello@orphograph.com` or X DM'd:

- Reply same day.
- If they're a prospective B2B buyer, offer a 15-min call.
- If they're a journalist, send the PetaPixel cold-email body (`LAUNCH_DRAFTS.md` cold-email template) re-framed for their outlet.

### 28. Push the Claude marketplace plugin

Per `deploy/PLUGIN_PUBLISH.md` §2:

```bash
cd ~/orphograph/marketplace/orphograph-plugin
git init -b main
git add .
git commit -m "Orphograph plugin v0.1 — Bitcoin-anchored file timestamping for Claude Code"
gh repo create orphograph/orphograph-plugin \
  --public \
  --description "Anchor files to Bitcoin from inside Claude Code. Privacy-by-construction — files never upload, only their SHA-256." \
  --homepage "https://orphograph.com" \
  --source=. \
  --remote=origin \
  --push
```

If the `orphograph/` org doesn't exist on GitHub yet, swap to your personal GH username. The repo can be transferred later.

### 29. Verify plugin install path

In any fresh Claude Code session:

```
git clone https://github.com/orphograph/orphograph-plugin ~/.claude/plugins/orphograph
```

Restart Claude Code. `/help` should list `/orphograph:anchor` and `/orphograph:verify`. Run `/orphograph:anchor` on a real file — expect a receipt URL.

---

## T+7 days — first-week metrics review

Run on the same weekday as launch (so the comparison cohort is one full week).

### 30. Pull the metrics

| Metric | Where to find it |
|---|---|
| Unique visitors | Cloudflare Analytics (free tier) or PostHog if enabled |
| Free-tier signups | `wc -l ~/orphograph/data/waitlist.jsonl` + email-authenticated user count from `/api/founder/stats` |
| Pack purchases ($7) | Stripe Dashboard → Payments → filter "one-time" |
| Personal subscriptions ($5/mo) | Stripe Dashboard → Subscriptions |
| Total MRR | sum of Personal subs × $5 (Pack revenue is non-recurring, exclude from MRR) |
| HN points + comments | Show HN thread |
| X impressions | X analytics, sum across thread |
| Email replies | Gmail inbox filtered for `hello@orphograph.com` |

### 31. Compare against thresholds

Per `LAUNCH_DRAFTS.md` "first 72 hours":

| Signal | Threshold | Met? |
|---|---|---|
| Show HN points (first 4h) | ≥40 = front page | |
| Show HN comments (first 6h) | ≥20 substantive | |
| r/photography upvotes (first 12h) | ≥100 | |
| X Tweet 1 impressions (first 24h) | ≥10,000 | |
| Day-0 unique visitors | ≥1,000 | |
| Day-3 free-tier signups | ≥100 | |
| Day-7 Pack purchases | ≥10 | |
| Substantive hello@ replies | ≥5 | |

If ≥6/8 hit: the launch worked. Continue.
If 4–5/8 hit: ship the Creator-tier beta to email list, run next launch cycle in 6 weeks.
If 0–3/8 hit: re-read every HN/Reddit/email comment; the market is telling you something. Pivot copy before pivoting product.

---

## T+30 / T+90 — quarterly cadence

### 32. Phantom sweep

Per `deploy/BTC_PAYOUT_PIPELINE.md`:

1. Open Phantom → BTC → Send.
2. Paste your **cold address** (the one in `~/orphograph/data/cold_wallet_address.txt`).
3. Enter amount (typically "send all" minus a small buffer to keep matching working).
4. Face ID → confirm.

Total time: ~10 seconds. This is your one manual security action — don't automate it.

### 33. Re-read kill-criteria

Open `~/orphograph/CLAUDE.md` "Realistic revenue targets" section. Apply the current MRR against the thresholds:

| Date | Threshold | Action if missed |
|---|---|---|
| Month 3 (Aug 2026) | $50 MRR floor | Reassess landing-page conversion; re-interview 5 customers |
| Month 6 (Nov 2026) | **$200 MRR floor** | Demote to side-project (≤5 hr/wk); primary time → AI-services agency |
| Month 12 (May 2027) | $500 MRR floor | If hit: build Capture SDK. If missed: open-source the codebase and move on. |

These thresholds execute mechanically. They are not negotiable on the day-of based on feelings.

### 34. At MRR > $200 — form Wyoming LLC

Per `deploy/LLC_FORMATION.md` (companion doc being authored in parallel; if not yet on disk, the inline summary):

1. Wyoming Secretary of State → file Articles of Organization ($102 filing fee).
2. Use a Wyoming registered-agent service (~$50/yr).
3. EIN via IRS online (free, instant).
4. Open a Mercury or Relay business account (free).
5. Migrate Stripe from individual sole-prop to LLC.
6. Update `ORPHO_BUSINESS_ENTITY` Fly secret to `Orphograph LLC` (or the legal name registered).
7. Update GDPR/privacy controller identity in `/privacy.html`.

Total cost first year: ~$200. Total cost ongoing: ~$110/yr.

Do not form before $200 MRR — it's overhead the product doesn't need yet.

### 35. At MRR > $500 — build Capture-time desktop app distribution

Per `CLAUDE.md` Creator-tier section: $19/mo Creator plan is anchored by Orphograph Capture, a desktop app that hashes at shutter-press.

- **Architectural firewall:** clean rewrite, no imports from `~/ai-provenance/` (ShutterProof is HBI-branded and sibling-imports `hsi_anchor` from Hydroboro — Orphograph principle #6 forbids any Hydroboro lineage). Anchor path reuses Orphograph's own `server/engine.py`, not Hydroboro's.
- Beta from email list only; do not advertise on public landing until at least a beta exists.
- Lightroom plugin (per `deploy/LIGHTROOM_PLUGIN_SPEC.md`) ships first as the export-time companion; the desktop app ships second as the capture-time companion.

---

## What to drop if pressed for time

**SKIPPABLE without breaking the launch:**

| Task | Why it's optional |
|---|---|
| Adobe Add-Ons store submission for Lightroom plugin | Informal install via README works; ~50 photographer installs achievable without store presence |
| Strike API integration for BTC → USD auto-conversion | Phantom sweep is already manual; per `BTC_PAYOUT_PIPELINE.md`, that's the security feature, not a gap |
| Auto-reply chatbot on hello@ | Gmail forwarding works; founder reads them manually in week 1 |
| LLC formation | Sole-prop is fine for first 6 months; LLC only at MRR > $200 (T+30+ ladder) |
| Cloudflare paid plan | Free tier covers DNS + CDN + Email Routing + Analytics |
| PostHog | Cloudflare Analytics gives enough for the first week |
| LinkedIn / PetaPixel / podcast pitches | Phase 2 distribution; HN + X + r/photography is the day-0/1 minimum |
| Capture-time desktop app | Not in launch scope; ships after $500 MRR per ladder above |

**DO NOT SKIP — these break the launch if missed:**

| Task | What breaks if you skip |
|---|---|
| Stripe webhook configured + secret matched on Fly | Pack purchases break — customer pays, no Pack credit added, refund/complaint |
| Resend domain verification (SPF/DKIM/DMARC) | Receipt emails go to spam; user opens HN comment with "I never got the email" |
| `ORPHO_BUSINESS_ADDRESS` set on Fly | Every email violates CAN-SPAM §7704(a)(5) — actionable by FTC, but also Gmail/Yahoo bulk-sender rules will deliverability-throttle you |
| BTC address rotation (50-address pool) | All customers see same address → on-chain privacy leak, public can link your customers to each other |
| Honest copy (no "court-admissible" etc.) | Violates principle #5; HN will catch it in the first hour and the launch dies in comments |
| `ORPHO_COOKIE_SECURE=1` on Fly | Session cookies sent over plain HTTP — security finding any auditor catches |
| `fly certs add` for both apex and www | HTTPS doesn't issue; site shows browser warning |

---

## Hard stops — pause and reconsider

These are moments where the right action is to **stop**, not push harder.

### Show HN posts in first 4 hours but gets <20 points

- Don't push r/photography or X harder same day. The HN audience didn't take.
- Wait for organic — sometimes HN posts climb on the 2nd or 3rd day from outside referrers.
- If still <40 points by 24h, re-read the comments. The market is telling you the framing is off, the price is off, or the use case isn't yet sharp.
- **Do not** add a "boost" tweet asking people to upvote. HN bans this.

### First 10 free-tier signups have zero conversion to Pack

- Don't spend on ads. Don't blast more launch posts.
- Email 5 of the 10 personally. Ask: "What were you hoping to do with Orphograph?" The answer tells you whether the use case is real, the pricing is wrong, or the product is missing a step.
- Re-read landing copy with the answers in hand. Edit. Re-test.
- This is the kill-criteria-adjacent signal: if 0% of free users convert and the answers are vague, the product hypothesis is weak.

### A receipt fails to anchor 5/5 calendars three times in a row

- OTS calendar outages happen. `MIN_CALENDARS_OK=3` is the default floor, so partial outages still ship receipts.
- But three consecutive full-failures suggests the server is failing, not the calendars.
- `fly logs --since 1h | grep -i ots` — look for connection refused, timeouts, DNS errors.
- Check the 5 calendar URLs in `server/engine.py:OTS_CALENDARS` from your own machine with curl — if you can reach them and the Fly machine can't, it's a Fly egress issue (rare but real).
- **Don't drive more traffic** until anchoring is back to 5/5 baseline. Customers anchoring on partial confirmations is fine; an outage during a Show HN spike is brand-damaging.

### Stripe disputes a charge in the first week

- Respond same day. Don't let it auto-resolve in the customer's favor.
- Stripe Dashboard → Disputes → submit evidence:
  - Receipt JSON (proves the service was delivered)
  - The receipt URL (proves anchor lives on the chain)
  - Email log from Resend (proves the customer received the receipt)
- If the dispute reason is "did not receive" and the email did land, you'll win. If it's "fraudulent" and Stripe sides with the customer, you eat the $7 + $15 dispute fee — accept it, refund proactively, move on.
- Pattern: **one** dispute is noise. **Three** disputes in a week is a fraud-pattern problem — pause checkout, investigate Stripe Radar rules, possibly require email verification before payment.

---

## Reference index — what to open when

| Situation | Open |
|---|---|
| DNS / TLS / Fly deploy problems | `deploy/FLY_PREFLIGHT.md` |
| Email going to spam / unsubscribe issues | `deploy/EMAIL_AND_LEGAL_COMPLIANCE.md` |
| Phantom pool exhausted | `deploy/PHANTOM_BTC_SETUP.md` |
| Time to sweep BTC | `deploy/BTC_PAYOUT_PIPELINE.md` |
| Stripe setup (parallel doc) | `deploy/STRIPE_ACTIVATION.md` |
| LLC formation (parallel doc) | `deploy/LLC_FORMATION.md` |
| Plugin publish to GitHub / Anthropic | `deploy/PLUGIN_PUBLISH.md` |
| Social post drafts | `deploy/LAUNCH_DRAFTS.md` |
| Comps + founder financial plan | `deploy/FINANCE_TOOLING_TRIAGE.md` |
| Lightroom plugin install / submission | `deploy/LIGHTROOM_PLUGIN_SPEC.md` |
| Receipt PDF generation | `deploy/RECEIPT_PDF.md` |
| AI-transparency blog post (already published) | `deploy/ARTICLE_WRITTEN_BY_AN_AI.md` |
| Kill-criteria + non-negotiable principles | `~/orphograph/CLAUDE.md` |

---

## One-paragraph close

The product is real, the receipts verify, the legal compliance is mapped. The remaining variables are distribution and pricing — and the only way to learn whether those work is to ship and listen. Post Show HN on Tuesday morning. Be at the keyboard. Reply substantively. Track the eight signals at T+7. If the floor holds at $50 MRR by month 3 and $200 by month 6, keep going. If not, fold gracefully into a side project and move primary time to the agency. None of that is failure — it's the kill-criteria working exactly as designed.
