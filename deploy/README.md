# Orphograph deploy/ docs — index

This folder is the operational reference. The doc you open most often is `LAUNCH_RUNBOOK.md`. Everything else is referenced from there or used by specialist tasks.

## Launch + go-live (open these in order on launch day)
- `LAUNCH_RUNBOOK.md` — sequenced T-7 → T+1 launch runbook (most important, single source of truth).
- `FLY_PREFLIGHT.md` — Fly.io deploy preflight gate audit + 5-command go-live path.
- `LAUNCH_DRAFTS.md` — paste-ready Show HN / r/photography / X thread / LinkedIn / PetaPixel drafts.
- `LAUNCH_WALKTHROUGH.md` — manual step-by-step go-live walkthrough (~90 min active work, ~3 days clock).
- `2_HOUR_LAUNCH.md` — condensed 5-command "print this" path from registered domain to live BTC site.
- `GO_LIVE_NOW.md` — shortest path from "domain registered" to "phone loads the site" (~45 min).

## Payments + Bitcoin
- `STRIPE_ACTIVATION.md` — individual Stripe signup → 4 products → Payment Links → webhook → live mode.
- `STRIPE_WEBHOOK_DEV.md` — three webhook URL paths (Stripe CLI / tunnel / production endpoint).
- `BTC_OPERATOR.md` — receive-only Bitcoin model: server holds public addresses only, keys stay offline.
- `BTC_PAYOUT_PIPELINE.md` — BTC-only sweep flow: customer → Phantom hot → cold wallet, no fiat hop.
- `PHANTOM_BTC_SETUP.md` — generate 20–100 fresh Phantom addresses → pool file → server rotation.
- `WALLET_QUICK.md` — "I need a BTC receive address in 10 min" path (Phoenix / BlueWallet / Sparrow).
- `PAYMENT_PII_AUDIT.md` — 2026-05-12 audit of every money/PII surface; HIGH findings shipped.

## Legal + compliance
- `EMAIL_AND_LEGAL_COMPLIANCE.md` — CAN-SPAM, GDPR, RFC 8058, multi-jurisdiction matrix + implementation.
- `LLC_FORMATION.md` — sole-prop vs WY vs DE vs PR Act 60 decision doc; WY at $200 MRR; not DE.
- `SECURITY.md` — runtime security posture (transport, CSP, HSTS, headers) — single auditor reference.
- `PUBLISH_SAFETY.md` — leak audit of verifier publish dir; Hydroboro / identity strings clean; git config landmine.
- `compliance/` — folder: `DPA_TEMPLATE.md`, `SECURITY_QUESTIONNAIRE.md`, `SUBPROCESSORS.md` for B2B asks.

## Plugins + distribution
- `PLUGIN_PUBLISH.md` — publish the Claude Code plugin at `marketplace/orphograph-plugin/` (4 commands).
- `LIGHTROOM_PLUGIN_SPEC.md` — Lightroom export-pipeline plugin design + install + file inventory.
- `RECEIPT_PDF.md` — receipt → PDF via browser print / headless Brave / server endpoint (stdlib-only).

## Marketing + positioning
- `ARTICLE_WRITTEN_BY_AN_AI.md` — AI-transparency launch article (disclosure-paradox framing) + distribution.

## Finance + strategy
- `FINANCE_TOOLING_TRIAGE.md` — which of 9 finance slash-commands actually apply pre-revenue (3 of 9).
- `MARKET_ROADMAP.md` — what's missing to hit each MRR band ($1–5k → Y3 $20k+), ROI per founder hour.
- `VALUATION_2026_05_12_EVENING.md` — most recent honest valuation refresh after audit pass.

## Operational reference
- `RUNBOOK.md` — incident + routine ops playbook (chargebacks, refunds, secret rotation, OTS outage).
- `FOUNDER_TODO.md` — founder-only unblocking checklist (registrar / Stripe / Resend / Fly).
- `HANDOFF.md` — what to give Claude to manage the page end-to-end (tokens, scopes, hard limits).
- `PLAN_B_TUNNEL.md` — Cloudflare Tunnel fallback if Fly path blocked by ISP / GitHub throttle.
- `README.md` — this file.

## When in doubt

Open `LAUNCH_RUNBOOK.md`. It sequences every other doc in this folder by phase (T-7 → T+1) and tells you which specialist doc to consult at each step. If the runbook says "see `deploy/X.md`" — the deep detail is there; the action stays in the runbook.
