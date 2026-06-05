# Orphograph — Service Level Objectives (SLOs)

**Service.** orphograph.com — Bitcoin-anchored file-timestamping (Fly.io `iad`, Cloudflare in front).
**Status.** SLOs defined 2026-05-31. Measurement begins now that an external uptime monitor exists (`.github/workflows/uptime.yml`). Closes the "define and document SLOs" item in `TRL_ASSESSMENT_2026_05_18.md` (monitoring component). Sustained adherence is a wall-clock gate (see §6).

This document is the operational contract the product holds itself to. It is deliberately conservative for a single-operator service — the point is an honest, measurable bar, not an aspirational one.

---

## 1. Objectives

| # | SLO | Target | Window | Measured by |
|---|-----|--------|--------|-------------|
| 1 | **Availability** — `GET /api/health` returns `200` with `ok:true` | **99.5%** | rolling 30 days | External monitor (`uptime.yml`, every 5 min on GitHub infra); run history is the record |
| 2 | **Anchor success** — `POST /api/anchor` reaches **≥ 3 of 5** OTS calendars (`MIN_CALENDARS_OK=3`) | **99.9%** | rolling 30 days | Anchor ledger / `calendars_ok` field; spot-probes |
| 3 | **BTC pin latency** — a receipt transitions `pending → pinned` | **≥ 99% within 36 h** | rolling 30 days | `upgrade_worker` logs; BTC median confirmation ≈ 1 h, 36 h is deep into the tail. Stuck-partial receipts freeze (guard) rather than spin |
| 4 | **Verification integrity** — a valid issued receipt verifies **offline** via `server/verify_cli.py` (no Orphograph service) | **100%** (non-negotiable) | every receipt | `verifier_roundtrip` CI job + standalone `verify_cli.py` |
| 5 | **Read latency** — p95 of `/api/health` and `/api/verify/<id>` | **< 1 s** | rolling 7 days | Monitor timing / Fly metrics |
| 6 | **Anchor latency** — p95 of `/api/anchor` (submits to 5 calendars in parallel) | **< 5 s** | rolling 7 days | Live probe |

Objective 4 is the load-bearing trust claim ("your receipt outlives the company") and admits **no** error budget — a receipt that cannot be verified without us is a product failure, not an availability blip.

---

## 2. Error budget

- **Availability (99.5% / 30 d)** → ~**3.6 hours**/month of allowable downtime. A single-region single-machine topology (one Fly `iad` VM) means a region event can consume this in one incident — see `TRL_ASSESSMENT_2026_05_18.md` and `SAFETY_GAPS_2026_05_18.md` Gap 1 (multi-region warm standby is the documented next step; founder-gated).
- **Anchor success (99.9%)** → ~43 minutes/month of degraded-anchor budget. Degraded mode (3–4 of 5 calendars) still produces a valid `.ots`; a full anchor failure (0 calendars) spends budget faster and should page.

---

## 3. Alerting

- **Primary (external):** `uptime.yml` runs every 5 min on GitHub's infrastructure. A failed run (`/api/health` ≠ 200/`ok`, or the standalone-verifier asset down) notifies repo admins by email (GitHub default) and pings the optional `UPTIME_WEBHOOK` secret if set.
- **Revenue path:** `/api/health` `checkout.ready` + `warnings[]` surface a dead/placeholder Stripe link (guard added 2026-05-30). A `ready:false` with a `STRIPE_*_URL ... not a valid Stripe payment link` warning means card checkout is down even while the service is "up."
- **Not yet wired (founder-gated):** Sentry/5xx-rate capture, a 36 h `pending`-state watchdog, and a true 60 s-cadence external monitor (GitHub cron floor is 5 min). These are the next monitoring rungs (TRL 5→7 in the assessment roadmap).

---

## 4. What counts as a breach

- Any 30-day window where availability < 99.5%, anchor-success < 99.9%, or pin-latency SLO < 99% within 36 h.
- **Any** receipt that fails offline verification (Objective 4) — immediate, regardless of window.
- A breach is logged to `docs/audits/` with cause + remediation; repeated breaches trigger a topology review (multi-region, capacity bump).

---

## 5. Measurement sources

- `.github/workflows/uptime.yml` run history — availability + verifier-asset liveness (external, auditable).
- `GET /api/health` — `ok`, `uptime_sec`, `counts.receipts_on_disk`, `checkout`, `ledger_bytes`, `last.{anchor,upgrade}_run_at`.
- Anchor ledger + `upgrade_worker` logs — anchor success + pin latency.
- `verifier_roundtrip` CI job (`.github/workflows/test.yml`) — verification integrity on every PR.

---

## 6. Honest maturity note

SLO **measurement** starts 2026-05-31 (the external monitor is new). Per the NASA-TRL framing in `TRL_ASSESSMENT_2026_05_18.md`, the bridge to TRL 9 for the monitoring/operations layer is **meeting these SLOs for 90 consecutive days under representative real-user load, with at least one real (not drilled) alert observed and resolved.** Until that track record exists, these are *defined and instrumented* SLOs (a real step up from "no SLO, localhost-only probe"), not *proven* ones. The distinction is the whole point of the TRL rubric.
