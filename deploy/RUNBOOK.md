# Operational runbook

For incidents and routine ops that don't repeat often enough to
remember the exact commands. Update this file every time you do
one of these for real.

## Table of contents

1. Chargeback received from Stripe
2. Refund a Pack (customer support request)
3. "I lost my pack code" support email
4. All 5 OTS calendars unreachable
5. STRIPE_WEBHOOK_SECRET rotation
6. ORPHO_HMAC_SECRET rotation
7. RESEND_API_KEY rotation
8. Volume backup + restore
9. Deploy a hotfix
10. Common support emails (canned responses)

---

## 1. Chargeback received from Stripe

Stripe sends a `charge.dispute.created` event. We don't auto-handle
it; the founder triages.

Steps:

1. Open the Stripe Dashboard → Disputes. Read the customer's reason.
2. SSH to the Fly app and find the claim_code:
   ```bash
   fly ssh console
   grep -F "{customer_email}" /app/data/credit_ledger.jsonl
   ```
   (Mask the email before sharing the result anywhere — it's PII.)
3. Decide: contest or accept. Standard heuristics:
   - If the buyer used the Pack (consume events exist for their
     claim_code), contest with the consume-event timestamps as
     evidence.
   - If the buyer never used the Pack, accept and zero the
     claim_code.
4. Zero the claim_code via the refund CLI:
   ```bash
   fly ssh console
   python3 /app/scripts/refund_pack.py --claim-code pk_XXXXXXXXXXXXX --reason chargeback
   ```
5. Respond in the Stripe dashboard with the evidence (timestamps,
   verifier link, terms reference to refund policy).

Pre-cooked response template:

> Customer purchased an Orphograph Pack ($7, 10 anchors) on [date].
> Our system records [N] anchor events between [start] and [end]
> consuming credits from their claim code. We provide a 7-day
> unconditional refund per our Terms of Service, but the customer
> did not contact us prior to this dispute. Attached: ledger
> export, terms link, sample receipt link, open-source verifier.

## 2. Refund a Pack (customer support)

```bash
fly ssh console
# Find the claim code for the email
grep -F "<buyer-email>" /app/data/credit_ledger.jsonl
# Issue the refund in Stripe Dashboard manually (we don't store the
# Stripe secret key in app)
# Zero the claim code:
python3 /app/scripts/refund_pack.py --claim-code pk_XXXX --reason customer_request
```

Reply to the customer:

> Refunded $7.00 to your card. Your Orphograph claim code has been
> zeroed; any remaining anchors are no longer redeemable. The
> receipts you've already created remain valid forever — they're
> anchored to Bitcoin's chain independent of our service.

## 3. "I lost my pack code" support email

Per Terms section 4: **bearer credentials cannot be recovered.**
That's the design.

Reply template:

> Pack claim codes are bearer credentials, like gift-card numbers.
> Anyone with the code can spend the credits, so we don't store a
> recovery channel. Please check your email's spam folder for our
> launch confirmation (from hello@orphograph.com); the code is in
> the body of that email.
>
> If you definitely cannot find it: we can issue a one-time
> goodwill replacement on a new claim code, but we need to verify
> via [last 4 of the card you used or Stripe receipt number]
> first. (We do this once per customer; subsequent losses we treat
> as the user's responsibility, per Terms.)

If we issue a replacement: append a `credits_delta: +10` row to
the credit_ledger.jsonl under a new claim code, and a `credits_delta:
-<remaining>` row against the lost code to zero it. Reuse the same
email field.

## 4. All 5 OTS calendars unreachable

Check the status page first: https://orphograph.com/status.html

If all 5 show ✗ unreachable, the most likely cause is our own
egress (Fly's network blip). Wait 5 minutes and refresh.

If still all-failed after 10 min:

1. SSH to Fly: `fly ssh console`
2. From inside the container: `curl -sI https://a.pool.opentimestamps.org`
   to test egress directly.
3. If curl fails inside the container, it's a Fly networking issue
   → check status.fly.io.
4. If curl succeeds from inside the container but the app's
   `_check_calendars_parallel` reports failures, restart:
   `fly machines restart <id>`.

While calendars are down: new anchor requests still succeed if at
least one calendar is up (per `MIN_CALENDARS_OK=3`). If all 5 are
down, anchors return 200 with `calendars_ok: 0` and the receipt is
flagged `low_redundancy: true`. Users get a warning in the UI.

Manual remediation if a single calendar is permanently dead:

1. Edit `server/engine.py` `CALENDARS` list, remove the dead one.
2. Add a replacement from
   https://github.com/opentimestamps/opentimestamps-server/wiki/Calendars
3. Open a new sample receipt anchor to refresh `web/sample/`.
4. Deploy: `fly deploy`.

## 5. STRIPE_WEBHOOK_SECRET rotation

Trigger: suspected leak, or annual rotation policy.

1. Stripe Dashboard → Developers → Webhooks → the endpoint → "Roll
   secret." Stripe will give you a new signing secret.
2. During the rollover window Stripe signs with both the old and
   new secret. Our multi-`v1=` handler accepts either, so we can
   update at our leisure within the rollover window (default 24h).
3. `fly secrets set STRIPE_WEBHOOK_SECRET=whsec_NEW`
4. `fly deploy` (or restart) to pick up the env var.
5. Test by triggering a webhook redelivery from the Stripe
   dashboard. Watch the Fly logs for `[webhook]` lines.

## 6. ORPHO_HMAC_SECRET rotation

Trigger: suspected volume access leak. **Side effect:** all
subscriber anchor histories become empty until users re-anchor
(the `source` field hash no longer matches).

1. Generate a new 32-byte hex secret: `python3 -c 'import secrets; print(secrets.token_hex(32))'`
2. `fly secrets set ORPHO_HMAC_SECRET=<new>`
3. `fly deploy`.
4. Email Personal-tier subscribers: "We rotated a security secret;
   your account is unaffected but your past anchor history won't
   appear on your dashboard. Receipts themselves are unchanged
   and remain verifiable."

## 7. RESEND_API_KEY rotation

1. Resend dashboard → API keys → revoke + create new.
2. `fly secrets set RESEND_API_KEY=re_NEW`
3. `fly deploy`.
4. Trigger a test email via `/api/auth/email-link` against a test
   address.

## 8. Volume backup + restore

Fly handles volume snapshots automatically (every 24h, retained 5
days). To restore:

```bash
fly volumes list
fly volumes snapshots list <volume_id>
fly volumes create orphograph_data_restored --snapshot-id <snap_id> --region iad --size 1
```

Then update the app to mount the new volume name and redeploy.

Manual backup (paranoid mode):

```bash
fly ssh console --command "tar czf - /app/data" > orpho_backup_$(date -u +%Y%m%d).tar.gz
```

Store the tarball offline; it contains email-keyed ledgers (PII).

## 9. Deploy a hotfix

```bash
cd ~/orphograph
# make the fix locally
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q  # green?
PORT=9999 bash scripts/smoke_test.sh                          # 5/5 calendars?
git commit -am "hotfix: <thing>"
fly deploy
```

Then watch the logs for 5 minutes:

```bash
fly logs --since 5m
curl -fs https://orphograph.com/api/health | jq .
```

Roll back if anything looks wrong:

```bash
fly releases list
fly releases rollback <previous_version>
```

## 10. Common support emails — canned responses

**Q: "How do I verify without your site?"**

> Download three things: (1) your receipt JSON, (2) the five .ots
> proof files, (3) our open-source verifier at
> https://github.com/orphograph/orphograph-verify. Run
> `python3 verify.py path/to/receipt.json --file your_original_file`.
> Exit code 0 = all proofs valid.

**Q: "Can I export my data?"**

> Yes — sign in at https://orphograph.com/signin.html, then visit
> https://orphograph.com/api/me/export (you'll get a JSON
> download of every row we hold for your email).

**Q: "Can I delete my account?"**

> Yes — sign in, then POST to /api/me/delete (we can do this from
> the dashboard for you on request: email
> privacy@orphograph.com). Append-only ledgers retain the deletion
> event for audit but no live records resolve to your email after
> that.

**Q: "Is this admissible in court?"**

> No. Orphograph provides cryptographic proof-of-existence —
> strong evidence that a file existed at a moment in time —
> but we are not an eIDAS qualified trust-service provider.
> For litigation, consult a digital evidence specialist who can
> assess whether our receipts plus their own analysis satisfy
> the jurisdiction's standard.

**Q: "Why Bitcoin specifically?"**

> The OpenTimestamps protocol batches many users' hashes into a
> single Bitcoin transaction, so our marginal anchoring cost is
> essentially zero. Bitcoin has the largest cumulative proof-of-
> work, which is the property we care about for proof-of-existence.
> A future version may add Ethereum/Cardano as redundancy.

---

## Update this file

Every time you do one of these for real:

1. Update the relevant section with what actually happened.
2. Note any surprises or commands that didn't work as documented.
3. Commit with `runbook: <what changed>`.
