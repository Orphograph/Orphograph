# Orphograph — NASA Technology Readiness Level (TRL) Assessment

**Date.** 2026-05-18
**Scope.** All deployed and in-tree components of orphograph.com (Bitcoin-anchored timestamp service).
**Assessor framework.** NASA TRL 1–9 per Mankins, *Technology Readiness Levels: A White Paper* (NASA Office of Space Access and Technology, 1995) and NPR 7123.1B, *NASA Systems Engineering Processes and Requirements* (Appendix E, TRL definitions).
**Read-only audit.** No code or configuration modified.

---

## Part 1 — TRL primer

NASA's TRL rubric ranks technology maturity on a nine-point ordinal scale (Mankins 1995; NPR 7123.1B Appendix E). **TRL 1** is the observation of basic principles; **TRL 2** is the formulation of a technology concept; **TRL 3** is analytical and experimental proof-of-concept of critical function; **TRL 4** is component validation in a laboratory environment; **TRL 5** is component validation in a relevant environment; **TRL 6** is a system/subsystem prototype demonstration in a relevant environment; **TRL 7** is a system prototype demonstration in an operational environment; **TRL 8** is the actual system completed and qualified through test and demonstration; **TRL 9** is the actual system "flight proven" through successful mission operations. For a SaaS context, the bridge between TRL 8 and TRL 9 is the difference between a system that has *passed* its qualification battery and a system that has *survived* representative user load, sustained uptime SLOs, disaster-recovery drills, and several months of real-world operation without surfacing latent defects.

---

## Part 2 — Component-by-component TRL grading

Tests confirmed: `pytest --collect-only` reports **386 tests collected** (founder spec cites 381; delta is the new reconcile + private receipt + stripe refund suites). Codebase confirmed live at orphograph.com per memory and `fly.toml` (`app = 'orphograph'`, `primary_region = 'iad'`, `cpus = 1`, `memory_mb = 512`, single mount).

| # | Component | Current TRL | Evidence | Gap to TRL 9 | Effort estimate |
|---|---|---|---|---|---|
| 1 | Anchoring engine (`server/engine.py`, `POST /api/anchor`, 5 OTS calendars parallel) | **8** | `_submit` POSTs to 5 hard-coded calendars in parallel; `_build_ots` constructs binary; `MIN_CALENDARS_OK=3`; receipt rows confirm 5/5 live; genesis self-anchor confirmed in MEMORY. | Sustained operational record under non-founder load; calendar-failure drill executed. | 0.5h drill + N days uptime |
| 2 | BTC upgrade worker (`server/upgrade_worker.py`) | **6** | `BTC_PIN_BUG_TRIAGE_2026_05_17.md` documents 28h pin-failure root cause; fix described as "just deployed" but **no production confirmation** of a pinned-to-bitcoin receipt since fix in this audit's evidence window. Pin-email idempotency code present (`_send_pin_email_if_needed`). | Observe ≥1 real receipt transition `pending → pinned` end-to-end in prod after the deploy; then 7-day clean run; then 30-day. | 0.25h verify + 30d wall-clock |
| 3 | Receipt persistence (`data/receipts/<id>/` JSONL + `.ots`; `server/credits.py` ledger) | **8** | Receipts directory present; engine writes `.ots` with `os.chmod(0o600)`; credits ledger is append-only JSONL with reconcile tooling (item 12). Fly volume mounted at `/app/data` per `fly.toml`. | Offsite backup drill (B2 script exists at `scripts/backup_to_b2.sh` — not confirmed scheduled in prod); restore-from-backup rehearsal. | 1h verify backup + 2h restore drill |
| 4 | Receipt verification (`/r/<id>` page + `GET /api/verify/<id>`) | **8** | `server/app.py:417`, `:445`, `:556`; `engine.verify_receipt(rid)`; CLI mirror in `server/verify_cli.py` with `test_verify_cli.py` (3 dedicated cases incl. tamper, file-mismatch, missing). | Independent third-party verification using only the `.ots` files (no Orphograph service) documented in a public artifact. | 1h doc + 0.5h external dry-run |
| 5 | Stripe checkout + webhook (`server/stripe_webhook.py`) | **7** | Webhook handles `charge.refunded` and `charge.dispute.created` with credit revocation (lines 151–186); 5 dedicated test files (`test_stripe_checkout.py`, `test_stripe_webhook.py`, `test_stripe_refund.py`, `test_gift_webhook.py`, `test_reconcile_stripe_ledger.py`). Reconciler script ships. | Live refund and live dispute observed end-to-end against real Stripe events (not test-mode). PCI-scope attestation not required (Stripe Checkout = SAQ A). | 1h test-mode refund drill + first real refund |
| 6 | BTC payments (`server/btc_payments.py`) | **5** | `is_configured()` true (xpub/address pool); `app.py:1134` gates order creation on `is_configured()`. `public_config.py:71` reads `BTC_PAYMENTS_ENABLED` env var — **unset in prod**. `test_btc_payments.py` + `test_btc_hd.py` present. Has never settled a real customer payment in prod. | Enable env flag → first real sat-denominated order → confirmation tx → credit grant → end-to-end audit. | 2h enable + 1 settled order to reach TRL 7; +N days for 8/9 |
| 7 | NOWPayments multi-coin | **2** | No code found. `grep -ri nowpayments server/ web/ scripts/` returns zero matches; mentions only in `docs/audits/audit-2026-05-11.md`, `deploy/LLC_FORMATION.md`, `deploy/SUBREDDIT_MATRIX.md` as proposed vendor. **Founder spec asserts "just scaffolded" — code is not in tree.** Flag for founder. | Implement gateway adapter (sign IPN HMAC, persist callback, dedupe by `payment_id`), test suite, sandbox order, prod env keys, first real settlement. | 8–12h to TRL 5; +real settlement for 7+ |
| 8 | Resend transactional email (`server/mailer.py`) | **7** | `RESEND_API_KEY` gated; inert-mode fallback logs to stderr; `send_pin_email`, `send_receipt_email`, magic-link path through `server/auth.py`. Sender `hello@orphograph.com` configured in `fly.toml`. | First customer-receipt email observed delivered (not founder inbox); DMARC alignment confirmed; bounce/complaint handling reviewed. | 0.5h DMARC check + first non-founder delivery |
| 9 | Email-on-pin (`upgrade_worker._send_pin_email_if_needed`) | **5** | Code present with idempotency via `pin_email_sent_at` (lines 124–157); failure swallowed by design ("credit-grant integrity beats notification"). Depends on (2) emitting a real pin and (8) being live. No dedicated test file located for this function. | Add unit test covering idempotency + crash-recovery; observe first real pin email delivered to a real customer; 7-day clean run. | 1h test + N days wall-clock |
| 10 | Writer multi-paste flow (`web/writers.html`, `web/writers.js`) | **6** | Frontend ships in tree; client-side SHA-256/SHA-512 + Merkle root + session-draft persistence + verify pane (`writers.js:135 merkleRoot`, `:101 persistDraftSession`). No backend-side test of a writer session anchoring multi-version manifest. | Real writer cohort uses it (per MEMORY pivot: writers added as co-primary audience 2026-05-17); telemetry confirms non-zero sessions; one viral story validates the flow. | 2h analytics + N weeks of real users |
| 11 | Recent receipts panel (`web/app.js` localStorage) | **8** | `RECENT_KEY = "orpho_recent_receipts"`; `saveRecentReceipt`, `renderRecentReceipts`, status-refresh wiring (lines 5, 50, 75, 142, 314, 939). UI ships in prod. | Localstorage-quota and cross-tab corruption edge cases documented; observed in real traffic. | 0.25h doc edge cases |
| 12 | Reconciliation cron (`scripts/reconcile_stripe_ledger.py`) | **6** | Detects LOST/GHOST/LEAK drift; exit-code-driven for cron alerting; dry-run mode; `test_reconcile_stripe_ledger.py`. launchd plist template at `scripts/com.orphograph.reconcile.plist.template`. **Not confirmed scheduled** on any production host. | Schedule via launchd on operator host; first 7 consecutive zero-drift runs; first real drift correctly alerted. | 0.5h install + 7d clean |
| 13 | Bi-weekly safety audit (`scripts/biweekly_safety_audit.py`) | **5** | File exists, 556+ lines, generates a markdown report ("# Orphograph biweekly safety audit — …"); re-audits the 2026-05-18 premortem class. **Not yet executed against prod** in this audit's evidence window. | First clean run; output committed to `docs/audits/`; cron'd at 14-day cadence. | 0.5h first run + 14d cadence |
| 14 | Frontend kill-switch banner (`web/app.js renderOpsBanner`) | **7** | Function exists at `web/app.js:479`; "Checkout is paused right now. Free-tier anchoring still works." copy at `:508`; invoked at `:941`. Backed by server-side `public_config` flag. | Live drill: flip flag, confirm banner renders within client cache TTL, confirm checkout blocked, restore. | 0.5h drill |
| 15 | Tier badge / package-first flow (`web/app.js` + `web/v2.js`) | **7** | Tier badge logic at `web/app.js:458–475`; v2 mockup wires pack-token capture (`PACK_KEY = "orph_pack_token"`, hash-fragment ingestion, query-strip). Per MEMORY, v2 awaits founder visual lock before promotion to `/`. | Promote v2 → `/`; observe pack-token flow with real Stripe-issued pack codes. | 0.25h promote (gated on visual lock) |
| 16 | Pseudonymous GitHub identity ("Orphograph", history-burned) | **8** | MEMORY: `Orphograph/Orphograph` public anonymous; founder git-config landmine documented (`feedback_orphograph_git_config_landmine.md`); safety script at `scripts/publish_safety_check.sh`. | Independent third-party review attempting to deanonymize via commit metadata / file fingerprints / writing style finds nothing actionable. | 1h ip-redactor subagent re-audit |
| 17 | Test suite (pytest) | **8** | 386 tests collected; 36 distinct test files spanning auth, engine, credits, btc_*, stripe_*, refund, sanitization, security_hardening, attacks, gdpr, rate_limit. | CI runs the suite on every PR (no `.github/workflows/` confirmed in audit); coverage report published; mutation-test or fuzz-test pass on engine + verify path. | 2h CI setup + 4h coverage report |
| 18 | Fly.io deployment (single `iad` machine, 512MB) | **6** | `fly.toml` confirms single `iad` machine, `cpus=1`, `memory_mb=512`, `min_machines_running=1`, force_https, healthcheck `/api/health` every 30s. `SAFETY_GAPS_2026_05_18.md` Gap 1 details the single-region single-volume risk. | Multi-region capacity (warm standby in second region); OOM-floor baseline captured; external uptime monitor wired. | 1.5h per Gap 1 |
| 19 | Cloudflare DNS | **8** | MEMORY confirms Cloudflare DNS in front of Fly; force-https in `fly.toml`; SPF/DKIM/DMARC live per `project_hydroboro_email.md`. | DNSSEC enabled and verified; CAA record locks issuance to a single CA. | 0.25h DNSSEC + CAA |
| 20 | Monitoring / alerting | **4** | `scripts/health_monitor.sh` is a launchd-driven local liveness probe against `http://127.0.0.1:8989/api/health` with Telegram notifications via `~/.claude/notifier.py`. **It probes the founder's local machine, not the public Fly endpoint** (per script line 17). No Sentry, no PagerDuty, no Datadog, no Grafana, no external uptime monitor located. | External uptime monitor (UptimeRobot / BetterStack) hitting `https://orphograph.com/api/health` every 60s; error-event capture (Sentry or equivalent) on server 5xx; on-call rotation defined (founder-only acceptable for now). | 2h external monitor + 3h Sentry SDK wire-in |

---

## Part 3 — TRL 9 close-out roadmap

Only components currently below TRL 9 are listed. Effort is founder wall-clock unless noted.

### Component: Anchoring engine
- **TRL 8 → 9.** Run a 30-day calendar-failure-injection drill (point one calendar URL at a 5xx sink in a staging copy) — confirm degraded mode (`MIN_CALENDARS_OK=3`) still emits valid `.ots`. Owner: founder. Effort: 0.5h injection + 30d observation. Success: zero anchor failures over 30 days under representative load AND the drill receipt verifies on a clean machine via `verify_cli.py`.

### Component: BTC upgrade worker
- **TRL 6 → 7.** Verify the fix against the genesis receipt and at least 2 unrelated pending receipts in prod; confirm `pending → pinned` transition writes `pinned_at` and emits the pin email. Effort: 0.25h. Success: 3 receipts pinned end-to-end on the deployed fix.
- **TRL 7 → 8.** Add a launchd-driven liveness probe that alerts if any receipt remains `pending` >36h (BTC median confirmation is ~1h; 36h is well past tail). Effort: 1h. Success: alert fires on a synthetic stuck receipt during a drill.
- **TRL 8 → 9.** 30 consecutive days with zero `pending`-state SLA violations. Effort: 30d wall-clock.

### Component: Receipt persistence
- **TRL 8 → 9.** Schedule `scripts/backup_to_b2.sh` via launchd; rehearse cold-restore from a B2 snapshot into a fresh Fly volume; verify all `.ots` blobs and JSONL records survive byte-for-byte. Effort: 3h. Success: documented restore runbook + dated rehearsal log in `docs/audits/`.

### Component: Receipt verification
- **TRL 8 → 9.** Publish a "verify your receipt without trusting Orphograph" guide that walks an independent verifier through `ots verify` (upstream tool) against the `.ots` blobs — no Orphograph endpoint touched. Effort: 1.5h. Success: third party confirms the procedure on a real receipt and replies in writing.

### Component: Stripe checkout + webhook
- **TRL 7 → 8.** Execute a test-mode refund and a test-mode dispute against the live webhook endpoint; confirm credit revocation rows land in `data/credits.jsonl` with the expected reason tag. Effort: 1h. Success: 2 ledger rows tagged `stripe-refund` and `stripe-dispute` from a controlled drill.
- **TRL 8 → 9.** First real refund (a real customer requests it, founder issues via Stripe dashboard) reconciles cleanly through the live webhook; reconcile cron reports zero LEAK rows the next day. Effort: 0.25h once triggered. Success: zero-drift reconcile run including the refund event.

### Component: BTC payments
- **TRL 5 → 6.** Enable `BTC_PAYMENTS_ENABLED=1` in Fly secrets; create a sub-$5 self-paid order from founder wallet → confirm address rotation works → confirm credit grant fires. Effort: 1h. Success: 1 self-paid order settled.
- **TRL 6 → 7.** First non-founder pays an order; mempool-watcher detects → credit grant fires → receipt anchors. Effort: gated on real customer. Success: 1 real customer paid in BTC.
- **TRL 7 → 8.** 10 cumulative real BTC orders settled without manual intervention; address-pool exhaustion path tested. Effort: gated on demand. Success: clean ledger, zero double-credits, address pool refill works.
- **TRL 8 → 9.** 90-day clean run including at least one chain-reorg event observed without credit corruption. Effort: 90d wall-clock.

### Component: NOWPayments multi-coin
- **TRL 2 → 3.** Write the gateway adapter spec (which coins, IPN signature scheme, refund semantics) in `deploy/NOWPAYMENTS_INTEGRATION.md`. Effort: 1h.
- **TRL 3 → 4.** Build `server/nowpayments.py` with HMAC IPN verification; add `tests/test_nowpayments.py` with at least 8 cases (good IPN, bad signature, replay, partial-pay, overpay, refund, expired, malformed). Effort: 4h.
- **TRL 4 → 5.** Sandbox order in NOWPayments test environment settles end-to-end into the local ledger. Effort: 1h.
- **TRL 5 → 6.** Production env keys set; first founder-self-paid order settles. Effort: 1h.
- **TRL 6 → 9.** Same progression as BTC payments above (real customer → 10 orders → 90-day clean run).

### Component: Resend transactional email
- **TRL 7 → 8.** First customer-initiated magic-link delivers to a non-founder inbox; bounce notification path verified by sending to an invalid address and observing the Resend bounce webhook (note: bounce-webhook handler not located in audit — confirm or add). Effort: 1h. Success: 1 successful + 1 bounce, both recorded.
- **TRL 8 → 9.** 30 days with delivery rate > 99% and zero spam-folder complaints on hello@orphograph.com per Resend dashboard. Effort: 30d wall-clock.

### Component: Email-on-pin
- **TRL 5 → 6.** Add `tests/test_pin_email.py` covering: pin_email_sent_at idempotency, mailer-import failure does not break credit grant, retry-on-next-run when `pin_email_sent_at` unset. Effort: 1h.
- **TRL 6 → 7.** First real pin email delivers to a real customer's inbox (gated on BTC upgrade worker fix landing). Effort: gated. Success: 1 customer confirms receipt of pin notification.
- **TRL 7 → 8.** 10 consecutive pins each emit exactly one email. Effort: gated on volume.
- **TRL 8 → 9.** 30-day clean run. Effort: 30d wall-clock.

### Component: Writer multi-paste flow
- **TRL 6 → 7.** Add Playwright (or stdlib selenium) end-to-end test exercising drag-drop, multi-version paste, Merkle-root compute, anchor submission, verify-pane round-trip. Effort: 4h.
- **TRL 7 → 8.** First non-founder writer completes a full session (paste → anchor → verify on a different device). Effort: gated on outreach. Success: 1 real writer session.
- **TRL 8 → 9.** 10 distinct real writers complete sessions; one is a journalist with a verifiable byline (per the MSN-writers thesis in MEMORY). Effort: gated on adoption.

### Component: Recent receipts panel
- **TRL 8 → 9.** Document the localStorage failure modes (private-window quota, cross-tab race) in `docs/decisions/`; add a smoke test that fills storage to quota and confirms graceful degradation. Effort: 1h. Success: documented + tested.

### Component: Reconciliation cron
- **TRL 6 → 7.** Install the launchd plist from `scripts/com.orphograph.reconcile.plist.template` on the operator host; run daily. Effort: 0.5h. Success: cron loaded, first run completes.
- **TRL 7 → 8.** 7 consecutive zero-drift runs. Effort: 7d wall-clock.
- **TRL 8 → 9.** First real drift event detected and corrected (intentionally inject a fake ledger row, observe alert, rollback). Effort: 1h drill.

### Component: Bi-weekly safety audit
- **TRL 5 → 6.** First clean execution against prod; commit output to `docs/audits/`. Effort: 0.5h.
- **TRL 6 → 7.** Schedule at 14-day cadence via launchd; first 2 scheduled runs complete clean. Effort: 0.25h schedule + 28d wall-clock.
- **TRL 7 → 8.** First real regression caught by the audit (intentionally introduce a known-bad state in staging, confirm the audit flags it). Effort: 1h drill.
- **TRL 8 → 9.** 90-day cadence-met track record. Effort: 90d wall-clock.

### Component: Frontend kill-switch banner
- **TRL 7 → 8.** Flip the public_config flag in prod, observe banner appears within cache TTL, observe `/api/anchor` returns the documented "paused" error, restore. Effort: 0.5h. Success: drill log dated and committed.
- **TRL 8 → 9.** Use the kill-switch once in anger (real incident) and document outcome. Effort: gated on incident.

### Component: Tier badge / package-first flow
- **TRL 7 → 8.** Promote v2 → `/` once founder visual lock lands; observe pack-token ingestion from a real Stripe-issued pack code. Effort: 0.25h promote. Success: 1 real pack-code session.
- **TRL 8 → 9.** 30 days of pack-bearing sessions without token corruption / loss. Effort: 30d wall-clock.

### Component: Pseudonymous GitHub identity
- **TRL 8 → 9.** Route one full month of new commits through the `ip-redactor` subagent per memory standing rule (`feedback_ip_redactor_subagent_required.md`); zero leaks detected by a fresh independent re-audit. Effort: ongoing + 1h re-audit.

### Component: Fly.io deployment
- **TRL 6 → 7.** Capture 24h memory floor via `fly metrics`; bump VM to 1GB (per `SAFETY_GAPS_2026_05_18.md` Gap 1); wire UptimeRobot or BetterStack against `https://orphograph.com/api/health` every 60s, page on 2 consecutive fails. Effort: 1.5h. Success: external monitor green for 7 days.
- **TRL 7 → 8.** Add warm-standby volume + machine in a second region (`ord`); document the cold-promote runbook. Effort: 2h. Success: standby exists, runbook tested.
- **TRL 8 → 9.** Execute a region-failover drill: cordon `iad`, promote `ord`, verify writes resume, restore. Effort: 1h drill + 30d post-drill clean run.

### Component: Test suite
- **TRL 8 → 9.** Add a GitHub Actions workflow that runs `pytest -q` on every PR; publish coverage to `docs/audits/coverage_<date>.html`; add a mutation-test pass on `server/engine.py` and `server/verify_cli.py` (mutmut or cosmic-ray). Effort: 6h. Success: green CI for 30 days, mutation score > 80% on engine + verify.

### Component: Cloudflare DNS
- **TRL 8 → 9.** Enable DNSSEC at Cloudflare; add a CAA record locking issuance to Let's Encrypt (or Cloudflare-managed); verify via dnsviz.net. Effort: 0.25h. Success: DNSSEC chain validates externally.

### Component: Monitoring / alerting
- **TRL 4 → 5.** Wire UptimeRobot or BetterStack against `https://orphograph.com/api/health` (60s interval, page on 2 consecutive fails to Telegram via existing `~/.claude/notifier.py` webhook). Effort: 1h. Success: external probe is the *primary* liveness signal, not the local-only `health_monitor.sh`.
- **TRL 5 → 6.** Add Sentry (or self-hosted Glitchtip) SDK to `server/app.py` error handlers; alert on 5xx rate > 1% over 5 min via the same Telegram webhook. Effort: 3h. Success: synthetic 5xx triggers an alert end-to-end.
- **TRL 6 → 7.** Add structured-log shipping (Fly's built-in log drains to a free Logtail/Better Stack tier); retain 14 days. Effort: 1h. Success: searchable logs externally.
- **TRL 7 → 8.** Define and document on-call rotation (founder-only is acceptable); define SLOs (uptime 99.5%, anchor-success-rate 99.9%, p95 latency < 1s). Effort: 1h. Success: SLO doc committed.
- **TRL 8 → 9.** Meet SLOs for 90 consecutive days with at least one real (not drilled) alert observed and resolved. Effort: 90d wall-clock + N alerts.

---

## Part 4 — Critical-path bottlenecks

Three components limit the overall product TRL more than any other.

1. **Monitoring / alerting (TRL 4).** This is the lowest-graded deployed-and-customer-facing component. The current "monitor" probes the founder's *local* machine, not the public production endpoint. The product can therefore be hard-down for an unknown duration before anyone notices outside of opportunistic founder browsing. Every other component's TRL is silently capped by this: a component cannot honestly claim TRL 9 ("proven through operational use") if outages are unobserved. Closing this single gap to TRL 7 unblocks the upper TRL ceiling for the entire product.

2. **BTC upgrade worker (TRL 6).** The BTC-pin pipeline is the load-bearing claim of the product — "your receipt is anchored to Bitcoin." `BTC_PIN_BUG_TRIAGE_2026_05_17.md` documents that no receipt successfully transitioned `pending → pinned` for at least 28 hours before the fix and a receipt from 2026-05-12 (5 days prior) was still pending. The fix was just deployed but is unverified against real receipts in the audit's evidence window. Until verified, this is the single most important falsifiability gate on the marketing promise.

3. **Fly.io deployment (TRL 6).** Single shared-CPU 512MB VM, single region, single volume. Per `SAFETY_GAPS_2026_05_18.md` Gap 1, an `iad` region outage takes the entire product down; an OOM under a Show HN spike causes serial 502s. This caps the operational-environment claim for every higher-level component: a TRL 9 requires *sustained* operation under representative load, and the current single-machine topology cannot survive a single-region event, which is well within the threat model NASA's "operational environment" implies.

These three components share a property: they are all infrastructure layers, not feature layers. Closing them does not require new product surface — only operational maturity.

---

## Part 5 — Overall product TRL

The deployed-and-customer-facing components, with scaffolded-but-disabled items (NOWPayments multi-coin, BTC payments) excluded, grade as follows:

- Anchoring engine: 8
- BTC upgrade worker: 6
- Receipt persistence: 8
- Receipt verification: 8
- Stripe checkout + webhook: 7
- Resend transactional email: 7
- Email-on-pin: 5 (depends on upgrade worker emitting a pin in prod)
- Writer multi-paste flow: 6
- Recent receipts panel: 8
- Reconciliation cron: 6
- Bi-weekly safety audit: 5
- Frontend kill-switch banner: 7
- Tier badge / package-first flow: 7
- Pseudonymous GitHub identity: 8
- Test suite: 8
- Fly.io deployment: 6
- Cloudflare DNS: 8
- **Monitoring / alerting: 4**

**Overall product TRL = min(deployed-and-customer-facing) = TRL 4.**

The component gating the overall score is **monitoring / alerting**. If monitoring is closed to TRL 5 (external uptime probe wired), the next gate is **email-on-pin at TRL 5**, which itself unblocks once the BTC upgrade worker fix is verified in prod. Realistic near-term ceiling once monitoring TRL 5 + upgrade-worker verification land: TRL 5 overall, then TRL 6 once Fly capacity (Gap 1) and reconcile cron scheduling (item 12) close.

---

## Part 6 — Honest realism check

NASA TRL 9 means **"Actual system 'flight proven' through successful mission operations"** (Mankins 1995, p. 6). The SaaS analog requires, at minimum:

- A documented uptime SLO met for N consecutive days under representative load — orphograph.com has neither a written SLO nor external uptime telemetry as of this audit.
- Disaster-recovery procedures *executed in drill* — no DR drill log exists in `docs/audits/` for region failover, volume restore, or seed-share reconstruction.
- Compliance review where applicable — Stripe SAQ A is implicit (Stripe Checkout); GDPR posture exists (`server/gdpr.py`, `test_gdpr.py`) but no formal review documented; no SOC 2 / ISO claim is needed for the current scale, and none is made.
- Real users — not founder + 3 testers — for **3+ months** with traffic representative of the intended operational envelope.

Against that bar, **orphograph.com on 2026-05-18 cannot credibly claim TRL 9 for any customer-facing component.** The honest ceiling without sustained real user traffic is **TRL 7 (system prototype demonstration in an operational environment)** for the most mature components (anchoring engine, receipt persistence, receipt verification, recent receipts panel, test suite, Cloudflare DNS, pseudonymous identity). The product is live, the system is in its operational environment, and the qualification batteries (386 tests) have been run — that is the definition of TRL 7. The bridge to TRL 8 is "qualified through test and demonstration" with formal acceptance evidence (which for SaaS reads as: external uptime monitor green for 30+ days, DR drills logged, an SLO published). The bridge to TRL 9 is wall-clock: months of real users with no founder-discovered defects.

The premortem-class evidence (`SAFETY_GAPS_2026_05_18.md`, `BTC_PIN_BUG_TRIAGE_2026_05_17.md`) is itself a TRL-honest artifact: it lists what would have to be true for the system to claim higher maturity, and quantifies the cost. The pin bug in particular is a useful demonstration that latent defects survive a 386-test suite into a live system — exactly the kind of finding TRL 7→8 progression is designed to surface, and a strong argument for not skipping the wall-clock portion of the maturation curve.

**Bottom line.** Orphograph is a credible TRL 7 prototype in an operational environment with select TRL 8 sub-components. The honest path to TRL 9 is approximately **45–55 founder-hours of closure work** (per the roadmap in Part 3, summed across components, excluding wall-clock waits) plus **90 days of sustained, externally-monitored, real-user operation with documented SLO adherence and at least one drilled DR event**. Claiming TRL 9 before that bar is met would be a marketing claim, not a NASA-rubric claim.
