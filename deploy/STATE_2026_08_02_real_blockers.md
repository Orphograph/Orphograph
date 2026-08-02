# Orphograph — Real Blockers (2026-08-02)

**This file supersedes** `LAUNCH_BLOCKER_STATUS.md` (2026-05-14),
`LAUNCH_READINESS_MEGA_TODO.md` (2026-05-14), and `../outreach/TODO.md`
(2026-06-06) as the current blocker list. Those are historical snapshots —
do not treat their blockers as live.

**Canonical live status:** `python3 ~/.claude/orphograph_launch_monitor.py`
(runs via launchd every ~30 min; checks health, Resend, card checkout wiring,
OTS calendars, ledger-backup freshness, www TLS, GitHub access).

## Resolved since the old docs (do not re-litigate)

- GitHub access: RESTORED (monitor `github_access: true`).
- Crypto-only launch: RETIRED — Stripe **card checkout LIVE since 2026-07-26**
  (`/api/config` `card_charges_enabled: true`; pack / pack50 / personal-monthly
  Payment Links all answer 200).
- PR #36 / staged deploys: merged; site healthy (`health_ok: true`, 238 receipts).

## Real blockers as of 2026-08-02

### 1. www.orphograph.com TLS — FOUNDER: one Cloudflare toggle
- Root cause: `www` is a **grey-cloud CNAME → orphograph.com** in Cloudflare.
  Cloudflare flattens that through the proxied apex to the Fly *origin* IPs,
  so browsers reach Fly directly with SNI `www.…` — and Fly had no cert.
- Machine-side DONE: `fly certs add www.orphograph.com` staged 2026-08-02.
  It cannot validate while the CNAME answer points at the proxied apex.
- **Founder action (pick one, ~60s in the Cloudflare DNS dashboard):**
  - **A (recommended):** click the cloud on the `www` CNAME record → **orange
    (Proxied)**. Cloudflare Universal SSL then terminates `www` immediately;
    no Fly cert needed. Optionally add a redirect rule www → apex.
  - **B:** add `CNAME _acme-challenge.www` →
    `www.orphograph.com.gmzrg5k.flydns.net.` (DNS only) so the staged Fly
    cert issues.
- Verify after: `curl -sI https://www.orphograph.com/` returns a status line.
  Monitor flips `www_tls_ok: true` and sends a recovery notice on its own.
- Note: no Cloudflare API token exists on this machine (`.env.local` token is
  empty) — that's why this is founder-gated.

### 2. Ledger backup freshness — machine-side, fix in flight
- Snapshots stalled after 2026-07-29T1133Z: the 07:15 launchd runs hit local
  DNS failures mid-pull (`lookup flyctl-metrics.fly.dev: no such host` — the
  known WARP/network-flap pattern), exhausted retries, exit 2.
- Manual run kicked off 2026-08-02 ~22:48 UTC; monitor's `backup_fresh` flips
  true when a snapshot lands. If the 07:15 slot keeps flaking, the fix is a
  retry-later wrapper, not a new backup system.

### 3. Listing / account submissions — FOUNDER-ONLY
- Machine-side MCP package done; registry + Glama already LIVE (07-27 / 07-31).
- Remaining: any listing requiring account login (PyPI publish, directory
  forms). Nothing here is scriptable without founder credentials.

### 4. Demand test — FOUNDER-ONLY, gated on #1 + checkout
- Drafts + posting kit exist in `outreach/` (gitignored). Demand is NOT tested
  until posts are actually made. Per plan: paced outreach only after www is
  clean and one founder browser click-through of checkout.

### Checkout verification status
- Wire-level chain VERIFIED 2026-08-02: `/api/config` → `checkout-cta.js?v=4`
  wires `#buy-pack`/`#buy-pack50`/`#buy-personal` → the three Payment Links,
  each returning 200 "Stripe Checkout". Remaining: one founder click in a real
  browser (open https://orphograph.com/pricing, click a card button, see the
  Stripe page — do not complete payment).
