# Orphograph — NASA TRL Assessment (2026-05-31 re-grade)

**Date.** 2026-05-31
**Scope.** Deployed and in-tree components of orphograph.com.
**Framework.** NASA TRL 1–9 (Mankins 1995; NPR 7123.1B App. E). For SaaS: TRL 7 = prototype demonstrated in the operational environment; TRL 8 = qualified through test + demonstration; TRL 9 = proven through sustained real-user operation.
**Method.** Supersedes `TRL_ASSESSMENT_2026_05_18.md`. Multi-agent re-grade against the source, **verified against live production on 2026-05-31** (`/api/health`, `/api/config`, a live `/api/anchor`, and an offline `verify_cli.py` run) plus the 728-test suite. Grades credit only what is on `master` + live in prod — not what is staged in unmerged branches.

---

## Headline

**Overall product TRL = 6** (up from **TRL 4** on 2026-05-18). A credible TRL-7 core with several legitimately TRL-8 subcomponents, held below 7 overall by one binding constraint (monitoring) and below 9 by the absence of any sustained real-user operating record.

The system is genuinely operational and self-verifying in production:
- `/api/health` live: `ok:true`, ~150 receipts on disk, recent anchor + upgrade-worker runs.
- Live `POST /api/anchor` returned **5/5 calendar confirmations** in real time.
- `verify_cli.py` validated a receipt **offline, no service** — SHA-256 + SHA-512 match, all 5 OTS blobs `[OK]`.
- `/api/config` confirms the **crypto rail (NOWPayments) is live**; the card-checkout honesty guard is verifiably firing (`checkout.ready:false` with precise warnings while the Stripe links are unset).

---

## The binding constraint (honest)

**Monitoring/alerting is still TRL 4 on `master`.** The only health probe that runs against the live default branch targets `http://127.0.0.1:8989` — the operator's *localhost*, not the public endpoint. A production outage is therefore invisible to any external observer.

An external uptime monitor (`.github/workflows/uptime.yml`, a 5-min GitHub-infra probe of the public `/api/health`) **and** a fix to the dead PR-CI (`test.yml` triggers on `branches:[main]` but the default branch is `master`, so it has never fired) are **written and ready in PR #16 — but not yet merged**. Until that PR merges to `master`, the monitoring component remains TRL 4 and caps the product's upper ceiling. **Merging PR #16 is the single highest-TRL-leverage action available** (it lifts monitoring and starts the SLO clock; see `SLO.md`).

---

## Component grades (2026-05-18 → 2026-05-31)

| Component | Was | Now | Note |
|---|---|---|---|
| Anchoring engine (5-calendar OTS) | 8 | 8 | live, 5/5 demonstrated; gap is real-load drill |
| BTC upgrade/pin worker | 6 | **7** | freeze-guard + log-rotation shipped & proven; pin transition awaits wall-clock |
| Receipt persistence (JSONL + fsync/flock) | 8 | 8 | torn-write guard + refund-on-failed-anchor merged |
| Receipt verification (CLI + API + page) | 8 | 8 | offline verify demonstrated; CI roundtrip green |
| Stripe checkout/webhook/refund/reconcile | 7 | **8** | sig + idempotency + clawback + reconcile all tested |
| NOWPayments multi-coin (crypto rail) | **2** | **6** | was *no code*; now HMAC-SHA512 IPN, per-order mint dedup, live (`nowpayments_enabled:true`) |
| BTC native payments | 5 | 5 | optional/redundant; env-gated off |
| Resend email | 7 | 7 | bounce/complaint webhook still missing |
| Email-on-pin | 5 | 5 | idempotency code present; needs dedicated test + real pin |
| Newsletter/waitlist | 0 | **4** | code exists, **no test file** — autonomous gap |
| Writer multi-paste flow | 6 | 6 | needs e2e test + real writer session |
| Money-surface hardening | 4 | **7** | 13 bugs fixed (double-grant, refund, XFF bypass) + tests |
| GDPR export/delete | 5 | **7** | append-only tombstone, tested |
| Rate limiting (XFF bypass closed) | 4 | **6** | Fly-Client-IP/rightmost fix |
| Public config (secret non-leak + checkout honesty) | 6 | **7** | `is_live_stripe_url()` guard live |
| Test suite | 8 | 8 | 386 → **728** collected |
| CI (deploy gate) | 7 | 7 | deploy.yml test job gates push; PR-CI dead until #16 |
| **Monitoring / alerting** | 4 | **4** | localhost-only on master; external probe ready in PR #16 (unmerged) |
| Fly.io deployment | 6 | 6 | single `iad` 512MB — multi-region is founder-gated |
| Cloudflare DNS | 8 | 8 | DNSSEC/CAA still open |
| Pseudonymous identity | 8 | 8 | — |

---

## Critical path to a higher grade

1. **Merge PR #16** — external uptime probe + PR-CI fix. Unblocks monitoring TRL 4→5 and the product ceiling. *(Code-only; founder merge = deploy.)*
2. **First real non-founder settlement (crypto rail)** — NOWPayments is the only live rail; first paid order → IPN → claim email → credit grant → anchor is the business TRL 5→7 jump. *(Traffic-gated.)*
3. **Card rail** — founder sets the real Stripe Payment Link URLs; `/api/health` then reports `checkout.ready:true`. The webhook/refund/reconcile code is already TRL-8 and dormant. *(Founder-gated.)*
4. **BTC-pin proof** — observe ≥3 real `pending→pinned` transitions with pin-email fired. *(Wall-clock.)*
5. **Sustained-ops record** — 30/90-day clean runs (uptime, zero double-grant, zero reconcile drift, delivery >99%) — the only path to TRL 8/9. *(Wall-clock + real traffic.)*

---

## Autonomous gap-closures still available (no founder/wall-clock needed)

- Surface a **read-only reconciliation-counts** section in `/api/health` (ledger rows vs Stripe/NOWPayments processed-events) so a silently-failed webhook is detectable.
- Add the **missing test suites**: `test_newsletter.py` (newsletter has none), pin-email idempotency, a golden real-IPN NOWPayments fixture + end-to-end mint-exactly-once flow, Resend bounce/complaint handler.
- Wire **UTM/referral attribution** end-to-end (currently absent) so channel conversion is measurable.
- Publish the **"verify your receipt without trusting Orphograph"** guide (real-receipt `ots upgrade` walkthrough).
- **Compliance/PII hygiene** (route via ip-redactor): parametrize a hard-coded operator path in two launchd `.plist.template` files; reword absolutist "never/cannot" warranty strings to behavioral phrasing; publish the DPA template.

## Founder-gated blockers

Stripe Payment Link URLs · real customer traffic / first settlement · ≥3 observed BTC-pin transitions · multi-region Fly + failover drill · legal entity (LLC deferred to MRR>$200) + CAN-SPAM postal address · 30/90-day sustained-ops record.

---

## Differentiation / innovation (the moat is novelty, honestly scoped)

The cryptographic core is settled prior art (OpenTimestamps, Bitcoin, and the eIDAS/C2PA standards are technical references, not inventions to claim). **Orphograph's genuine differentiation is in product, UX, capture surface, and form factor — where and how provenance is captured and carried**, not in new cryptography. Directions:

- **On-drive portable provenance** — a USB whose receipts ride *on the drive* (`.orphograph/` sidecar), so provenance travels with the medium across machines. (Productization is novel; the primitive is standard.)
- **Ambient capture-time anchoring** — every file saved is auto-anchored by a daemon, so provenance is continuous and effortless rather than a deliberate per-file act.
- **Verifiable edit-lineage** — Merkle-linked version chains (the writer flow already computes roots) extended into an auditable "this draft preceded that draft" graph.
- **Hardware-attested timestamps** — pairing a secure-element device identity with the timestamp (the moonshot "smart USB"), so the *device* attests capture context.
- **Provenance for AI-assisted work** — anchoring a prompt+output pair so a creator holds an independent record of when *their* version existed.

Each is differentiated by being **not-yet-productized**, not by claiming novel math. Honesty here is itself a moat: the MIT open verifier means proofs survive the company, which raises trust and willingness to rely on the standard-bearer.

---

## TRL-9 realism

TRL 9 = "proven through sustained successful operation." Orphograph today has: a live, self-verifying service; a battle-tested money surface; a live crypto rail; 728 tests. It does **not** yet have: a merged external monitor, a single confirmed non-founder paid settlement, an observed BTC-pin transition in the audit window, or any 30/90-day clean-run record. The honest grade is **TRL 6 overall, TRL 8 on the core chain + payment-verification subcomponents**, uncapped only by founder-gated real traffic and the still-unmerged monitoring. Claiming higher before that bar is met would be marketing, not the NASA rubric.
