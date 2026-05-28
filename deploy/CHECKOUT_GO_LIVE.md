# Checkout go-live runbook (founder-only)

**Why this exists:** as of 2026-05-25 the live site cannot take a payment.
`/api/config` returns empty Stripe URLs (`pack_url: ""`), so every buy button
leads nowhere. This is the single blocker between Orphograph and its first
dollar. The steps below need *your* Stripe account, so an agent cannot do them.

Canonical entry SKU (founder-confirmed 2026-05-25): **Writer Pack — 10 anchors — $19.**

> ⚠️ Do the whole chain in **Stripe test mode first**, buy one test Pack, confirm
> the claim-code email arrives, *then* repeat with live keys. Paying customers
> who get no claim code = chargebacks. Run `/premortem` before the live deploy.

---

## The full revenue chain (all four links required)
A payment only turns into a delivered product if **all** of these are wired:

1. **A buy target** — a Stripe Payment Link (or Price ID) for the Writer Pack.
2. **Fulfillment webhook** — Stripe → `https://orphograph.com/api/stripe/webhook`
   on `checkout.session.completed`, verified with `STRIPE_WEBHOOK_SECRET`.
3. **Email delivery** — `RESEND_API_KEY` set, so the claim-code email actually sends.
4. **Correct display price** — `/api/config` and the homepage both show $19
   (the code default is now $19; set the env var to be explicit).

---

## Step 1 — Stripe: create the Writer Pack buy target
In the Stripe Dashboard (test mode first):
1. **Products → Add product:** name "Writer Pack", price **$19.00 USD, one-time**.
2. Either:
   - **Payment Link** (simplest): Payment Links → create one for that price →
     copy the `https://buy.stripe.com/...` URL. This feeds `STRIPE_PACK_URL`.
   - **or Price ID** (for the server-side `/api/stripe/checkout` route): copy the
     `price_...` id → feeds `STRIPE_PRICE_PACK`.
   The frontend currently uses `STRIPE_PACK_URL`, so the Payment Link is the
   shortest path to live.

## Step 2 — Stripe: configure the fulfillment webhook
1. **Developers → Webhooks → Add endpoint:**
   `https://orphograph.com/api/stripe/webhook`
2. Subscribe to event **`checkout.session.completed`**.
3. Copy the endpoint's **Signing secret** (`whsec_...`) → feeds `STRIPE_WEBHOOK_SECRET`.

## Step 3 — Fly: set the secrets
```bash
fly secrets set \
  STRIPE_API_KEY="sk_live_..." \
  STRIPE_PACK_URL="https://buy.stripe.com/..." \
  STRIPE_WEBHOOK_SECRET="whsec_..." \
  PACK_PRICE_USD="19" \
  PACK_CREDIT_COUNT="10" \
  RESEND_API_KEY="re_..." \
  --app orphograph     # (confirm app name via `fly apps list`)
```
Setting secrets restarts the app. (Optional second SKUs — Pack of 50 $29,
Standing Order $9/mo — add `STRIPE_PERSONAL_MONTHLY_URL` etc. once decided;
see the open pricing question below.)

## Step 4 — Verify it's actually live
```bash
curl -s https://orphograph.com/api/config  | python3 -m json.tool   # pack_url non-empty, pack_usd 19
curl -s https://orphograph.com/api/health  | python3 -m json.tool   # "checkout": {"ready": true, "warnings": []}
```
Then in **test mode**: click buy → complete a test card (`4242 4242 4242 4242`)
→ confirm the claim-code email arrives → redeem the code → confirm anchors credit.
Only after that passes, swap test keys for live keys and repeat once for real.

---

## Open pricing decisions (flag — not yet resolved)
- **Subscription price:** `/api/config` now defaults `personal_monthly_usd: 9`
  / `personal_annual_usd: 60`, matching the blog copy + your records (**Standing
  Order $9/mo**).
- **Which SKUs to actually sell:** code supports Writer Pack ($19/10), Pack of 50
  ($29/50), and Standing Order ($9/mo). Confirm the lineup before adding links.
- **Stale "$7 / Pack of Ten / $5-mo" copy** in `README.md`, `RELEASE_NOTES`,
  `content/blog/*.md`, `deploy/*.md`, `web/docs/api.html`, and the marketplace
  plugin has been scrubbed to the canonical $19 / $9 pricing.
