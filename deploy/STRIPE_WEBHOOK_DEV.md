# Stripe webhook — three paths from "URL unreachable" to working

**The error you saw:** Stripe Dashboard → Webhooks → Add endpoint → paste `https://orphograph.com/api/stripe/webhook` → "URL couldn't be reached".

**Why:** the URL doesn't resolve yet — DNS not pointed, no Fly deploy. Three fixes, in order of preference:

---

## Path 1 (RECOMMENDED for development) — Stripe CLI forwards to localhost

The canonical Stripe dev pattern. Stripe opens a WebSocket from their server → your machine and forwards every event. No public URL needed.

### Install + run (one command each)

```bash
# Install (already happens in our launch flow)
brew install stripe/stripe-cli/stripe

# Start the forwarder — leave it running in a Terminal tab
bash ~/orphograph/scripts/stripe_listen.sh
```

First run opens a browser for OAuth. After that, you see:

```
> Ready! You are using Stripe API Version [2024-XX-XX]. Your webhook signing secret is whsec_xxxxxxxxxx
```

Copy that `whsec_xxxxxxxxxx` value. Add to `.env.local`:

```bash
echo 'STRIPE_WEBHOOK_SECRET="whsec_xxxxxxxxxx"' >> ~/orphograph/.env.local
```

**Restart the local server** so it picks up the new secret:

```bash
pkill -f "orphograph/server/app.py"
nohup python3 ~/orphograph/server/app.py > ~/orphograph/logs/server.out 2>&1 &
```

Now in Stripe Dashboard → make a test charge with card `4242 4242 4242 4242` → watch the `stripe listen` terminal tab → it shows the event → server processes it → check `/api/me/anchors` to confirm the credits landed.

**This is the right path until you deploy.** Stripe's dashboard tracks this CLI-forwarded path separately from your "real" webhook endpoint, so you don't need to add a Dashboard webhook at all during dev.

---

## Path 2 (TEMPORARY, ROTATES HOURLY) — current pinggy tunnel URL

Your local server is currently exposed via pinggy at the rotating URL in `~/orphograph/data/tunnel_url.txt`. Stripe will accept it as a webhook destination but the URL changes every ~60 minutes when `tunnel_keeper.sh` rotates.

Don't use this for real customer charges. Use it ONLY if you want to test Stripe's "Send test webhook" button in the Dashboard once.

```bash
# Get current URL
cat ~/orphograph/data/tunnel_url.txt
```

Output looks like: `https://xginn-104-28-165-56.run.pinggy-free.link`

In Stripe Dashboard → Webhooks → Add endpoint → paste **that URL + `/api/stripe/webhook`**:

```
https://xginn-104-28-165-56.run.pinggy-free.link/api/stripe/webhook
```

Stripe verifies the URL is reachable (it is, via the tunnel), assigns a signing secret. Copy the secret into `.env.local`. Restart server.

**Why this is bad as a default:** the URL changes hourly. Stripe will fail to deliver after the next rotation. If you want a stable tunnel: pay Pinggy $3/mo for a non-rotating subdomain, OR run cloudflared once installed.

---

## Path 3 (PRODUCTION) — orphograph.com after Fly deploy

Once `fly deploy` succeeds and `scripts/cf_point_to_fly.sh` has pushed DNS:

```
https://orphograph.com/api/stripe/webhook
```

In Stripe Dashboard:

1. Switch toggle in the top-right from **Test mode** to **Live mode** (if you're ready to take real money) — or stay in test mode for the dry-run
2. **Developers → Webhooks → Add endpoint**
3. Endpoint URL: `https://orphograph.com/api/stripe/webhook`
4. Description: `Orphograph anchor + subscription events`
5. Listen to events:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`
6. Click **Add endpoint** → Stripe verifies the URL is reachable → assigns a `whsec_...` signing secret
7. Copy the secret. Set as Fly secret:

```bash
fly secrets set STRIPE_WEBHOOK_SECRET="whsec_..."
```

Fly auto-restarts to apply. Verify by sending a test event from the Dashboard (the webhook's "Send test webhook" button).

---

## Compatibility matrix

| Path | Public URL? | Stable? | When |
|---|---|---|---|
| 1. Stripe CLI | none | yes (local) | dev / pre-launch |
| 2. Pinggy tunnel | yes (rotating) | no (60-min rotation) | one-off Stripe Dashboard test |
| 3. orphograph.com | yes | yes | production |

---

## Verifying the webhook is wired correctly

Whichever path you pick, this works to confirm end-to-end:

```bash
# Stripe Dashboard → Webhooks → click your endpoint → "Send test webhook"
# Choose: checkout.session.completed
# Watch the server logs:
tail -f ~/orphograph/logs/server.out
```

You should see a line indicating the webhook was received + the signature verified. The server's `stripe_webhook.py` module is idempotent — sending the same event twice does NOT double-credit.

---

## Common errors

| Stripe error | Cause | Fix |
|---|---|---|
| "URL couldn't be reached" | URL doesn't resolve / DNS not propagated yet | Use Path 1 (CLI), or wait 5 min after `cf_point_to_fly.sh` |
| "Signature mismatch" | Wrong `STRIPE_WEBHOOK_SECRET` in env | Re-copy the secret from Dashboard or CLI startup line |
| "Event not subscribed" | Endpoint subscribed to wrong events | Edit endpoint → add the missing event type |
| 500 from server | `stripe_webhook.py` raised | Check `~/orphograph/logs/server.out` for the traceback |
| Test charge succeeds but no credits appear | Webhook delivered AFTER the test page redirect — race condition. Refresh `/account.html` | Inherent to test-mode; doesn't happen in prod |

---

## What happens if a webhook is missed

Stripe retries automatically up to 3 days, with exponential backoff. So a 5-minute downtime → no lost events. If you delete the endpoint entirely → events for that period are permanently dropped. Don't delete endpoints; disable them instead.

---

## Summary — what to do RIGHT NOW

Since `orphograph.com` isn't live yet, use **Path 1** today:

```bash
# Terminal tab 1 — keep this running
bash ~/orphograph/scripts/stripe_listen.sh

# Terminal tab 2 — paste the whsec_... it printed
echo 'STRIPE_WEBHOOK_SECRET="whsec_PASTE_HERE"' >> ~/orphograph/.env.local
pkill -f "orphograph/server/app.py"; sleep 1
nohup python3 ~/orphograph/server/app.py > ~/orphograph/logs/server.out 2>&1 &
```

After Fly deploys → switch to **Path 3** via the Dashboard. Path 2 (pinggy) is the fallback if Path 1 has CLI issues.
