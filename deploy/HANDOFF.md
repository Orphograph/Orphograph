# Handing the page off to Claude

You asked **"what to give Claude to manage the page wholly."** This
doc enumerates the exact tokens, credentials, and decisions that get
me from "I can suggest things" to "I can deploy, monitor, rotate
secrets, respond to incidents, and ship the SEO calendar without
you in the loop." Plus the hard limits I keep regardless of what
you give me, so you know exactly where you must stay involved.

The trade-off:

| You stay involved on | You hand off |
|---|---|
| Initial account activations (KYC at Stripe, your bank) | Day-to-day deploys, content updates, secret rotations |
| Credit-card billing limits | Stripe restricted-key operations (refunds, sub cancels) |
| Outbound emails to third parties (I draft, you send) | Inbound support triage |
| Remote pushes to `main` | Everything else |
| Anything I'd spend > $50 on in one shot | Routine sub-$5 things |

---

## What you actually give me

### 1. Fly.io API token (scoped to the orphograph app)

```bash
fly auth token
# copy the output (starts with `fm1_`)
# paste it into one of:
#   ~/.fly/config.yml  (default location flyctl reads)
#   environment variable FLY_API_TOKEN
```

**What this lets me do:**
- `fly deploy`, `fly secrets set`, `fly volumes list/snapshots`
- `fly ssh console` for ops queries
- `fly logs` for incident triage
- Roll back releases (`fly releases rollback`)

**What this does NOT let me do:**
- Add or remove payment methods (you keep the card)
- Delete the app or the volume (Fly requires extra confirm)
- Spin up unrelated apps under your account

### 2. GitHub fine-grained personal access token

Generate at https://github.com/settings/personal-access-tokens
with these specifics:

- **Resource owner:** `orphograph` org (create it first if it doesn't
  exist; ~3 minutes at github.com/account/organizations/new)
- **Repository access:** only the orphograph repos
- **Permissions:**
  - Contents: Read and write
  - Pull requests: Read and write
  - Issues: Read and write
  - Workflows: Read and write
  - Metadata: Read (required)

**What this lets me do:**
- Push the verifier release (`dist/orphograph-verify/`)
- Open PRs for code changes (you review + merge)
- Tag releases
- Update the SEO blog posts in a content branch

**What this does NOT let me do:**
- Push directly to `main` (hard stop in my autonomy rules)
- Manage your personal account or other orgs
- Pay for anything

Save to `~/.config/gh/hosts.yml` (the `gh` CLI default).

### 3. Stripe restricted API key

Generate at https://dashboard.stripe.com/apikeys with these
permissions ONLY:

| Resource | Permission |
|---|---|
| Subscriptions | Read + Write |
| Customers | Read |
| Charges | Read |
| Refunds | Write |
| Webhook Endpoints | Read |
| Payment Links | Read |
| Products | Read |
| Prices | Read |

**Disable** Files, Connect, Issuing, Terminal, Treasury, Identity.

Set on Fly:
```bash
fly secrets set STRIPE_SECRET_KEY=rk_live_xxxxx
```

**What this lets me do:**
- Process customer support refunds (`scripts/refund_pack.py` can be
  extended to call Stripe directly)
- Cancel + reactivate subscriptions on user request via
  `/api/me/cancel-subscription` and `/api/me/reactivate-subscription`
- Look up subscription state when handling support tickets

**What this does NOT let me do:**
- Create new Products or Prices (you set the pricing)
- Charge customers off-cycle
- Refund > $500 in one call (Stripe rate-limits restricted keys)
- See your bank balance or transfers

### 4. Resend API key

Generate at https://resend.com/api-keys.

```bash
fly secrets set RESEND_API_KEY=re_xxxxx
```

**What this lets me do:**
- Send Pack claim emails, sign-in magic links, receipt emails
  (already wired through `server/mailer.py`)
- Send launch announcements **to people who opted in via the
  waitlist** — drafts go to you first per autonomy rule

**What this does NOT let me do:**
- Spam your contacts list
- Buy Resend volume tiers without asking
- Send to addresses we haven't verified opt-in for

### 5. DNS API access

Both Porkbun and Namecheap support DNS API tokens that don't grant
domain-transfer authority.

**Porkbun:** account.porkbun.com → API ACCESS → generate API key +
secret. Give me both, plus enable API access on `orphograph.com`
specifically.

**Namecheap:** ap.www.namecheap.com → Profile → Tools → Namecheap
API Access → enable + add your home IP + my server IP to the
whitelist.

```bash
# Stored in:
fly secrets set DNS_API_KEY=xxx DNS_API_SECRET=yyy DNS_REGISTRAR=porkbun
```

**What this lets me do:**
- Create the A/AAAA records that point `orphograph.com` to Fly
- Add the SPF/DKIM TXT records Resend needs for sending-domain verify
- Add subdomain records if we ever need `api.orphograph.com`,
  `status.orphograph.com`, etc.

**What this does NOT let me do:**
- Transfer the domain to another registrar
- Change the domain owner contact
- Renew the domain (you keep that on auto-renew at the registrar)

### 6. Support inbox forwarding

Decide one of:

**Option A — Fastmail / Proton catch-all (recommended).** Set up
`*@orphograph.com` → `your-personal@protonmail.com`. You keep
the inbox. I read it via you forwarding me triage drafts.

**Option B — Dedicated mailbox I can poll.** Create
`triage@orphograph.com` as an actual mailbox with IMAP creds. Add
forwarding rules at the registrar/email-host for `support@`,
`hello@`, `privacy@`, `press@`, `security@` → `triage@`. Give me
the IMAP credentials.

**My preference: Option A.** Keeps you in the loop on every
customer interaction; I draft the response and you click send.
Lower trust surface, same speed.

### 7. A simple budget envelope

Pick one cap that I'll honor without asking:

```
Routine ops (Fly hosting, Stripe fees, Resend volume):
    auto-approve up to $50/month total

Anything beyond that (new infrastructure, paid services, ads):
    drafts a request to deploy/budget_requests/<date>.md
    waits for your "yes"
```

Memorialize the cap as `~/orphograph/.claude/budget.txt` (one line:
`monthly_cap_usd=50`) and I'll check it before any spend.

---

## What I will NOT do, regardless of credentials

These are encoded in my autonomous-mode skill and won't change
without you explicitly disabling the rules:

1. **Push to `main` directly.** I work on branches and open PRs for
   you to merge.
2. **Send outbound email to third parties (journalists, prospects,
   customers, regulators) without your explicit "send" approval.**
   Customer-support replies to inbound tickets are an exception
   *if* the response is a canned answer from
   `deploy/RUNBOOK.md §10`; anything bespoke goes to you first.
3. **Spend > $50 in one action** without saving a budget request
   and waiting.
4. **Install system packages or anything requiring sudo** on your
   machine.
5. **Touch the Hydroboro / HSI / Boroscope / Trail-Audit / Thermohydro
   trees.** Per memory rules, those are additive-only and
   air-gapped from Orphograph anyway.
6. **Make claims I can't substantiate** in any external-facing
   copy: no "court-admissible," no "legally binding," no medical /
   pharma / insurance applicability. Principle #5.

---

## Step-by-step bring-up sequence

You do this once. ~90 minutes of active time, plus 1–3 days of
waiting on Stripe and DNS.

1. **GitHub org + repo (10 min)**
   - Create `github.com/orphograph` org (free).
   - Push `~/orphograph/dist/orphograph-verify/` as `orphograph/orphograph-verify`
     (MIT, public).
   - Generate the fine-grained PAT per §2 above, paste it where
     `gh` reads it.

2. **Fly bring-up (45 min, waits for cert)**
   - `fly auth signup` (or login)
   - `fly launch --copy-config --no-deploy` from `~/orphograph`
   - `fly volumes create orphograph_data --region iad --size 1`
   - `fly deploy`
   - `fly certs create orphograph.com` (DNS records printed; add via
     §5 DNS API or manually at registrar; wait ~5 min for cert)
   - Run `~/orphograph/scripts/preflight.sh https://orphograph.com`
     — must return all green.

3. **Stripe activation (45 min + 1–3 day review)**
   - dashboard.stripe.com signup
   - Activate (KYC, tax info, bank)
   - Create Product: "Orphograph Pack — 10 anchors", $7 one-time
   - Create Payment Link for that product, success URL
     `https://orphograph.com/?stripe_done=1`
   - Create Product: "Orphograph Personal — Monthly", $5/mo
   - Create Product: "Orphograph Personal — Annual", $60/yr
   - Create Payment Links for both subscription products
   - Webhook endpoint at `https://orphograph.com/api/stripe/webhook`
     for events: `checkout.session.completed`,
     `customer.subscription.created/updated/deleted`
   - Generate restricted key per §3
   - Paste 4 URLs into `~/orphograph/web/app.js`
     (`STRIPE_PACK_URL`, `STRIPE_PERSONAL_MONTHLY_URL`,
     `STRIPE_PERSONAL_ANNUAL_URL`) and redeploy
   - `fly secrets set STRIPE_WEBHOOK_SECRET=whsec_xxx STRIPE_SECRET_KEY=rk_live_xxx`

4. **Resend (30 min + DNS propagation)**
   - Signup, add `orphograph.com` as sending domain
   - Add the printed TXT records via §5 DNS API
   - Wait for "Verified" status
   - Generate API key per §4, set on Fly

5. **Run preflight one more time**
   - `~/orphograph/scripts/preflight.sh https://orphograph.com`
   - Expect 21/21 green now (was 20/21 because Stripe webhook
     posture was "503 pre-activation"; once configured, becomes
     400 on unsigned probe).

6. **Tell me you're done.** I take it from there: SEO blog
   cadence, support ticket drafts, secret rotation reminders,
   backup runs, status-page monitoring, and the Y3-band features
   we just unblocked.

---

## What "I take it from there" means concretely

Once the credentials in §1–§7 are in place, here's the autonomous
operational cycle I run:

**Daily (cron):**
- Run preflight against `orphograph.com`; alert if anything degrades
- Pull error logs from Fly; triage anything weird
- Watch the credit ledger for unusual activity (refund storm,
  suspicious anchor patterns)

**Weekly:**
- Publish one SEO post from `content/blog/` (queued up to 10
  per the audit's list; I write more as needed)
- Run `scripts/backup_volume.sh` to your local gpg-encrypted
  archive
- Triage the inbox forwarded to you; draft replies; you click send

**Monthly:**
- Run the kill-criteria check from `CLAUDE.md` §12 against actual
  MRR; surface to you with a recommendation
- Review compliance pack (`deploy/compliance/`) against any new
  customer DPA requests
- Rotate the HMAC secret if any volume-snapshot access happened

**Quarterly:**
- Re-run the security audit pass (third-party suggestion welcome but
  not required for sub-100-customer scale)
- Refresh the valuation memo as state evolves

**Incident response:**
- All five OTS calendars down → log loud + try restart per
  `RUNBOOK.md §4`
- Stripe chargeback → run `scripts/refund_pack.py`, draft
  evidence response, you submit in Stripe Dashboard
- Personal data breach → 72h notification to affected per DPA,
  draft to you first

---

## Where this doc lives

`~/orphograph/deploy/HANDOFF.md`. Keep it private (not committed if
the main repo goes public). If you ever want me to operate
differently, edit this doc and tell me to re-read it — it's the
canonical "what Claude does on Orphograph" reference, and I'll
treat changes here as the standing instruction set.
