# ORPHOGRAPH — full-cycle audit, 2026-07-26 (INTERNAL)

> ## CORRECTIONS — 2026-07-27. Two HIGH findings below were over-claimed.
>
> Both were diagnosed from a symptom without reading the code that consumes it.
> The corrected versions are narrower and, in one case, point somewhere else
> entirely. Left in place rather than rewritten, so the error is visible.
>
> **1. "Every receipt reports `status: partial` forever" — the field is CORRECT.**
> `upgrade_worker.py:244-249` recomputes it every run. Measured across all 214
> production receipts: `a.pool` and `b.pool.opentimestamps.org` return **HTTP 404**
> for their commitments and never upgrade; `alice`, `btc` and `finney` upgrade every
> time. 209 receipts sit at 3-of-5 pinned, 5 at 2-of-4, and **213 are
> `upgrade_frozen`** after ~24 no-progress runs. "partial" is an accurate report of
> a permanent state, not a stale field.
> **What was actually wrong:** the public API said "partial" about receipts that ARE
> Bitcoin-anchored, to the audience least able to interpret it — SDKs and
> third-party verifiers. Fixed additively with `bitcoin_attested` (commit `38ffb8c`).
> The proposed fix in the remediation table ("derive status at read time") was based
> on the wrong diagnosis and was NOT implemented.
>
> **2. "Entitlement is served off a stale `active` flag" — FALSE. It is not.**
> `subscriptions.is_active()` (`server/subscriptions.py:160-165`) already gates on
> `current_period_end > now`, not on the status string. Verified live against the
> real subscriber: stored status `active`, period end 2026-06-18, and
> `is_active()` returns **False**. Entitlement is correctly denied. Nobody is being
> served for free.
> **What remains true:** no subscription-lifecycle webhook has been processed since
> 2026-05-18, so you cannot tell whether that customer renewed or lapsed. That is a
> Stripe dashboard subscription setting — the handler already covers the events
> (`stripe_webhook.py:175`). Consequence is "you don't know", not "you're losing
> money".
>
> Method note: both errors came from inferring a cause from a data pattern instead
> of reading the consumer. The receipt-status one was caught by asking *why* 214/214
> looked identical; the entitlement one by opening `is_active` before "fixing" it.

Phase 1 reconnaissance. **Zero application files modified.** New files are confined to
`tools/audit/` and `docs/`.

Priority order executed as instructed: **item 6 first** (are existing receipts sound?),
then 1–5 and 7. Money paths follow.

---

## HEADLINE — the structural hypothesis is disproven

The brief's premise was that the two known verifier defects are *symptoms* of never-verified
three-way parity, and that this is "the real risk."

**Measured: it is not.** All four independent RFC 6962 implementations produce
**byte-identical Merkle roots on all 14 golden vectors**, including every case where such
implementations classically drift. The tree logic is sound. The two defects are genuinely
isolated, not symptoms.

Second headline: **both HIGH defects described in the brief as "still live on the site" are
already fixed on `origin/master`.** `fix/verifier-highs` is merged. What remains is one
structural gap (D2, below) and one unmerged branch.

---

## Item 6 — Backward compatibility of production receipts · **PASS**

The gating question. Run against the live Fly volume via `flyctl machine exec` (read-only).

| Check | Result |
|---|---|
| Receipts in production ledger | **214** (brief said ~149; the number has grown) |
| `verify_receipt()` finds the record | 214 / 214 |
| Stored `hash_hex` matches ledger | 214 / 214 |
| **All `.ots` proofs pass magic + embedded-digest** | **214 / 214** (`calendars_ok == calendars_total`) |
| Partial / zero attestation | 0 |
| Missing receipt files | 0 |

`verify_receipt` (`server/engine.py`) is not a self-referential lookup — it reads each `.ots`,
checks `OTS_HEADER_MAGIC`, and compares the digest embedded at `len(magic)+2` against the
receipt hash. A first pass that only compared ledger-to-ledger was discarded as circular; the
number above is the real one.

**No receipt has drifted. The verifier is safe to modify.**

### HIGH — but every receipt reports `status: "partial"` forever

All 214 have `btc_pinned_at` **set** (oldest 2026-05-18), 5/5 calendars OK — and `status`
is still `"partial"` on all 214. Nothing upgrades the field after pinning.

Customer impact splits by surface:

- The certificate **page** renders "anchored"/"complete" — it derives display state from
  `btc_pinned_at`, so web visitors see the truth.
- The public **API** does not. `GET /api/verify/XwTULwlh76PcCst9` returns
  `"status": "partial"` for a receipt pinned two months ago.

That is the surface the SDKs and any third-party integrator consume — precisely the
"issuer-independent verification" audience. They read `partial` and correctly conclude the
anchor is incomplete. **Severity HIGH** on the API contract, cosmetic on the web page.

Proposed fix: derive `status` from `btc_pinned_at` + calendar state at read time in
`verify_receipt`, rather than storing a field that is never updated. Do not backfill the
stored value until the read path is correct.

---

## Item 3 + Item 7 — Three-way parity and domain separation · **PASS**

Harness: `tools/audit/differential/parity.py` (+ `parity_bridge.mjs`). Exit 0.

Implementations exercised, all four:

| Implementation | Language | How driven |
|---|---|---|
| `server/merkle.py` | Python stdlib (canon) | `_leaf_hash` + `_build_levels` |
| `sdk-python/orphograph/_merkle.py` | Python SDK | same private API |
| `sdk-node/dist/merkle.js` | Node SDK | exported `leafHash` / `internalHash` |
| `web/folder.js` | Browser, SubtleCrypto | `_leafFor` / `_buildTree` |

`web/folder.js` keeps its core module-private, so the harness copies the file and appends
exactly one line — `export { _leafFor, _buildTree, _byteCompare };` — and stubs the DOM
globals its module-scope bootstrap touches (`folder.js:540`). The algorithm under test is
theirs, unmodified.

**All 14 vectors AGREE across all four.** Vectors (item 4), chosen where RFC 6962
implementations classically diverge:

`empty_tree` · `single_leaf` · `two_leaves` · `three_leaves_odd_L0` ·
`five_leaves_odd_multi` · `seven_leaves_odd_multi` · `eight_leaves_balanced` ·
`empty_files` · `duplicate_filenames` · `identical_path_twice` · `unicode_paths` ·
`case_only_difference` · `sort_boundary_chars` · `deep_nesting`

Notable passes:
- **Odd-node handling** — all four *promote* the odd last node (RFC 6962) rather than
  duplicating it. A promote/duplicate split would have changed 3 of 14 roots.
- **`case_only_difference`** (`A.txt` vs `a.txt`) — all four sort case-sensitively by UTF-8
  byte order. A case-insensitive sort anywhere would reorder leaves and change the root.
- **`sort_boundary_chars`** — `-` `.` `/` `0` straddle the `0x00` separator; all agree.
- **`empty_tree`** — all four return `e3b0c442…` = SHA-256(""), the RFC convention.

**Item 7 is proven by execution, not inspection.** All four declare `LEAF_PREFIX = 0x00` /
`INTERNAL_PREFIX = 0x01` (`server/merkle.py:41-42`, `sdk-python/_merkle.py:48-49`,
`sdk-node/dist/merkle.js:29-30`, `web/folder.js:9-10`) — and any mismatch would have
produced different roots on every non-empty vector.

---

## Items 1 & 2 — the two known defects, current state

### Defect 1 (uppercase) — **already fixed on master; and the brief describes it backwards**

The brief states: *"A hex digest supplied in uppercase can be reported as valid when it should
not be."* That is not what the defect was, and the distinction changes the fix.

- The **engine already normalizes the supplied side**: `server/engine.py:352` —
  `result["supplied_hash"] = hash_hex.strip().lower()`. Supplied-uppercase has always been
  handled correctly.
- The real D1 was on the **stored** side: `verifier-js` lowercased the *receipt's* hash before
  comparing, so a hand-edited receipt with an uppercase digest returned `ok: true` while canon
  said no match.
- **Current state:** `verifier-js/orphograph_verify.js:161` now reads
  `String(receipt.hash_hex || "")` — no `.toLowerCase()`, no alias fields. Line 184 lowercases
  only the computed side. That is canon behaviour. Confirmed by execution in the differential
  harness: `stored_UPPERCASE` → `INVALID`.

**Residual (MED):** `verify_hash_against_receipt` normalizes but does not *validate* the
supplied digest. Malformed input (truncated, non-hex, empty) silently becomes a mismatch
rather than an error, so a parse failure is indistinguishable from a genuine non-match. The
Field Kit semantics — strict `^[0-9a-f]{64}$` after normalization, raise on malformed —
are the correct remediation and would also fix `0x_prefixed`, which the engine currently
treats as a mismatch (the Field Kit strips the prefix).

### Defect 2 (custom excludes) — **mitigated, NOT solved. This is the real remaining gap.**

`sdk-python/orphograph/__init__.py:83` now accepts an `exclude` kwarg, and line ~103 passes
it to `MerkleTree.from_folder(root, exclude=exclude)`. That removes the permanent
false-negative *if the caller supplies the right list*.

**But the manifest does not persist the exclude patterns.** `MerkleTree.manifest()`
(`server/merkle.py:282-288`) emits only `algorithm`, `version`, `root_hex`, `leaves`. There
is no `exclude` field, in the manifest or anywhere in `server/engine.py` / `server/app.py`.

The code's own comment concedes the design: *"Must mirror whatever exclude list the folder
was ANCHORED with"* — i.e. correctness depends on the caller's memory, months later,
possibly a third party who never ran the capture.

Reproduced in the harness: `custom_excludes_forgotten` → `INVALID` (root mismatch) on both
`sdk-python.merkle` and `server.merkle`.

**Proposed fix (Field Kit semantics):** persist the effective exclude patterns inside the
manifest at capture time, and have verification read them **from the manifest**, never from
caller arguments. The manifest becomes authoritative for its own scope. This is strictly
better than the kwarg and eliminates the caller-memory requirement.

**Note:** production currently has **0 folder anchors** (see 2.3), so no live receipt is
affected. Fix before folder anchoring is promoted, not after.

---

## Customer impact — the highest-value question · **Answer: no user could have been given a wrong answer**

Stated in one of the three permitted forms, with citations.

**Defect 1 could not have produced a false positive for any real user:**
1. `server/engine.py:116` normalizes and strictly validates at *write* time —
   `hash_hex = hash_hex.strip().lower()` then `_is_hex(hash_hex, 64)`, raising
   `ValueError` otherwise. A service-issued receipt cannot hold an uppercase or malformed hash.
2. Confirmed empirically against production: **0 of 214 receipts have an uppercase stored
   hash.**
3. The supplied side is normalized at `engine.py:352`.
4. D1 was therefore only reachable via an out-of-band-edited receipt JSON verified through
   `verifier-js` — an adversarial artifact the user brought themselves, not one the office issued.

**Defect 2 could not have produced a false negative for any real user:**
- Production has **0 folder-type anchors** across all 214 receipts. The folder verification
  path has never been exercised against a real receipt.

**No notification to customers is warranted.** No draft written.

---

## Differential harness — `tools/audit/differential/`

`run_differential.py` — 25 cases across three surfaces. **Exit 0, 0 safety violations,
2 genuine disagreements.**

The safety gate is: exit 1 if any *attesting* implementation returns VALID for input that must
not validate. A false negative is a bug; a false positive is a notary telling someone a
document is attested when it is not.

**A note on this harness's own trustworthiness.** Its first run reported 2 safety violations
and 23/25 disagreements. Both were artifacts of the harness, not the product:
- The `anchor_write_guard` probe answers "could this value enter the ledger?", which is not
  "does this attest?" — folding it into the gate produced false alarms. It now reports
  `REACHABLE`/`UNREACHABLE` and is excluded from the gate.
- Disagreement was computed across implementations covering *different surfaces*, flagging
  23/25 rows as noise.
- A `sdk-node.fromHex` adapter returned ERROR for every input including valid hex, because
  `fromHex` is module-private (`sdk-node/dist/merkle.js:56`, declared `function fromHex`, never
  exported). Reporting that would have been a fabricated finding; it is now `ABSENT` with the
  reason recorded.

The two surviving disagreements are real and both LOW — the D6 error-surface split:

| case | `engine.stored` | `verifier-js` |
|---|---|---|
| `stored_missing` | ERROR "corrupt receipt" | INVALID "file not attested" |
| `stored_alias_only` | ERROR "corrupt receipt" | INVALID "file not attested" |

Same safety class, misleading diagnosis: the receipt is broken, the file may be fine. This
is the same defect class as P2 item "verifier failure messaging must distinguish not-yet-anchored
/ altered / wrong file / malformed receipt."

Also confirmed fixed by execution: `stored_alias_only` → `INVALID` (D5, alias fields, gone).

---

## Unmerged work

`origin/fix/verifier-minor-drifts` is **NOT merged** into master. Per
`AUDIT_VERIFIER_DRIFT_2026_07_12.md` it carries the D3, D4 and D7 fixes (sdk-node
`verifyFolder` fallback removal, strict per-pair hex regex, aligned missing-file error
surface). All MED or LOW. Merging is a deploy — see the deploy warning below.

---

## Public exposure — internal pages are live · **HIGH**

Verified live, following redirects:

| URL | Final status |
|---|---|
| `https://orphograph.com/_mockups/B_broadsheet` | **200** |
| `https://orphograph.com/index-legacy` | **200** |

`web/**` ships wholesale, so internal design mockups and a stale legacy homepage are publicly
reachable and crawlable, carrying claim language that differs from the canonical pages
(e.g. `web/index-legacy.html:418` discusses "guarantee admissibility";
`web/_mockups/B_broadsheet.html:101` carries its own legal framing).

Proposed fix: exclude `web/_mockups/` and `*-legacy.html` from the deploy artifact, or serve
404 for those prefixes. Verify with a live fetch after.

---

## Claim-surface findings (partial — sweep incomplete, see Coverage)

| Sev | file:line | text | why |
|---|---|---|---|
| HIGH | `web/lp/index.html:60` | "proof of authorship" | Authorship is explicitly outside the claim ceiling. **Live** — confirmed on `/lp/c2pa-alternative`. |
| HIGH | `web/lp/c2pa-alternative.html:12` | og:description "proof of authorship" | Same; propagates to social cards. |
| MED | `web/index.html:113` | `aria-label="What this guarantees"` | "Guarantee" framing on the homepage. |
| MED | `web/index.html:494` | "On the guarantee." | Same. |
| MED | `web/index-legacy.html:418` | "guarantee admissibility" | On a publicly reachable stale page. |
| LOW | `web/gift.html:58` | "guarantees as a self-buy" | Refund-scope wording, not a proof claim. |

**Correctly worded, do not change** — `web/writers.html:181`: *"Evidence of process, not proof
of authorship… It does not, by itself, identify the human who typed."* This is the model
phrasing; propagate it to the `/lp/` pages above.

**Absolute phrasing:** the `never` hits sampled (`web/certificate.html:36`,
`web/dataset-provenance.html:8,10,26,54,77`, `web/buy.html:87`, `web/about-the-office.html:49`)
all describe a **mechanism** — "the file never leaves your device" is a factual statement about
data flow, not an outcome promise. Not flagged. `web/badge-demo.html:102` likewise.

**Forecast framing:** `web/roadmap.html` uses "anticipated" throughout, but about *roadmap
items*, not about what the product does — not a doctrine violation.
`web/lp/wedding-photographer-proof.html:137` ("It signals…") needs a read in context.

---

## Security posture (partial)

- **Single bind:** `server/app.py:4513` — `ThreadingHTTPServer((HOST, PORT), Handler)` with
  `HOST = os.environ.get("HOST", "127.0.0.1")` (`server/app.py:86`). Localhost by default,
  env-overridable.
- **Tension to resolve, not a defect:** the "no public APIs / no open ports" rule cannot apply
  literally to a live public website on Fly, which must bind `0.0.0.0` behind the proxy. The
  rule as written should be scoped to *local daemons*; the Fly app is intentionally public.
  Flagging so the rule and reality are reconciled explicitly rather than silently.

---

## ⚠ Deploy warning that governs everything below

`CI_DEPLOY_ENABLED=true` and `FLY_API_TOKEN` are set on `Orphograph/Orphograph`, and
`.github/workflows/deploy.yml` fires on push to `master`. **Merging any PR to master
auto-deploys to production.** The comment in `deploy.yml` claiming deploy "is intentionally
not automated" is stale and wrong. Re-verified 2026-07-25.

Premortem required before any merge touching money paths.

---

## Item 16 — Reconcile receipts against payments · **No one paid and got nothing**

Checked against production records, not assumed. Note the local working tree's
`credit_ledger.jsonl` / `btc_orders.jsonl` / `manual_fulfillment_queue.jsonl` **do not exist
in production** — the real payment surface is `subscriptions.jsonl`,
`stripe_processed_events.jsonl`, `stripe_customer_emails.jsonl`, `api_keys.jsonl`.

**There is exactly one real paying customer**, and they were fulfilled:

| Fact | Value |
|---|---|
| Stripe customer / subscription | `cus_UXWbj0VwjAHCce` / `sub_1TYRYd9ThkMusxkbJILSm8iC` |
| Created | 2026-05-18T13:50:13Z, `customer.subscription.created` |
| Email | `criptopitirre@gmail.com` |
| API key issued | 2026-05-19T04:53:32Z — **fulfilled** |
| Receipts anchored under that subscription | 2 (`source: sub:a3527526436a5f1b`) |
| **Paid but no claim code / key** | **none** |

Receipt sources across all 214: `free` 212, `sub:a3527526436a5f1b` 2.

### HIGH — subscription state is written once and never updated

The stored record says `status: "active"` with `current_period_end: 1781790607` =
**2026-06-18T13:50:07Z — 38 days ago.** No renewal, cancellation, or payment-failure event
has been recorded since 2026-05-18.

Consequences, both bad:
- If the customer renewed, the system does not know it.
- If they lapsed or their card failed, **entitlement is still being served off a stale
  `active` flag.**

`stripe_processed_events.jsonl` holds only 2 events, both from 2026-05-18 — so no
subscription-lifecycle webhook (`customer.subscription.updated` / `.deleted` /
`invoice.payment_failed`) has been processed in over two months. Either they are not
subscribed to in the Stripe endpoint config, or they are arriving and not persisted.

Proposed fix: subscribe to and persist the lifecycle events; derive entitlement from
`current_period_end` vs now, never from a stored `status` string alone. Same class of defect
as the receipt `status: partial` bug — a state field written once and trusted forever.

### Item 11 — webhook idempotency: mechanism present

`data/stripe_processed_events.jsonl` keys on `event_id` with a stored `result`, which is a
replay-guard ledger. It exists and is written. **Not yet verified:** that the handler
consults it *before* acting (read-path check pending), and there is no equivalent ledger for
the crypto rail.

### Also observed

All 214 receipts carry `owner_id: None` — receipts are not linked to customer accounts;
attribution runs through `source` only. That makes per-customer reconciliation harder than it
needs to be, and would block a "show me my receipts" view. MED.

---

## Money paths — items 9–15, 17

### Item 9 — BTC amount bug · **HIGH** · `server/btc_price.py:191-196`

Not an undercharge. A **random ±$5 swing**, with a systematic overcharge bias.

```python
suffix = int(suffix) % 10000
base = (sats // 10000) * 10000     # <-- FLOOR discards up to 9,999 sats
sats = base + suffix
```

The unique-amount suffix **floors** to the nearest 10,000 sats, throwing away up to 9,999,
then adds a suffix that `server/app.py:2102` supplies as `secrets.randbelow(10000)` — random.
The customer is charged a uniformly random amount in a 10,000-sat band around a floored base.

Live path is `POST` → `server/app.py:2097` with `usd_amount = 19.0` **hardcoded** (only the
Writer Pack is purchasable by direct BTC; the comment notes tiers are "not built yet").
Routes are live: `/api/btc/price`, `/api/btc/qr.svg`, `/api/btc-order/`, `/api/btc/claim`.

Quantified at $60,000/BTC:

| SKU | true sats | charged range | worst undercharge | worst overcharge |
|---|---|---|---|---|
| Writer Pack $19 (live) | 31,667 | 30,000–39,999 | **−$1.00 (5.3%)** | +$5.00 |
| Pack of 50 $29 (if wired) | 48,333 | 40,000–49,999 | **−$5.00 (17.2%)** | +$1.00 |
| Standing Order $9 (if wired) | 15,000 | 10,000–19,999 | **−$3.00 (33.3%)** | +$3.00 |

For the live $19 SKU the expected charge is ~35,000 sats vs 31,667 true — customers are on
average **overcharged ~$2**, and undercharged in roughly the lowest 17% of the suffix range.
The customer-fairness problem is larger than the revenue leak here.

**Fix:** round **up** to the next boundary so the charge can never fall below true price:
`base = -(-sats // 10000) * 10000`. Guarantees `charged >= true`, caps overcharge at 9,999
sats, and preserves the uniqueness property the settle worker relies on.

### Item 10 — Stripe triad · **PASS**

All deployed on Fly (`flyctl secrets list`): `STRIPE_WEBHOOK_SECRET`, `RESEND_API_KEY`,
`STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_PRICE_PACK`, `STRIPE_PRICE_PACK50`,
`STRIPE_PRICE_SUB`, `STRIPE_PACK_URL`, `STRIPE_PACK50_URL`, `STRIPE_PERSONAL_MONTHLY_URL`,
`NOWPAYMENTS_API_KEY`, `NOWPAYMENTS_IPN_SECRET`, `ORPHO_FROM_EMAIL`.

**Memory correction:** the note that "Pack of 50 has no card SKU, crypto-only" is **stale** —
`STRIPE_PRICE_PACK50` and `STRIPE_PACK50_URL` are both deployed, and `/api/config` serves a
live `pack50_url`.

### Item 11 — Webhook idempotency · **PASS on both rails**

- Stripe: `_has_been_processed(event_id)` (`server/stripe_webhook.py:123`) guards at entry
  (`:171`) returning `{"ok": True, "duplicate": …}`; `_mark_processed` (`:140`) appends to
  `data/stripe_processed_events.jsonl`.
- Crypto: separate `nowpayments_processed_events.jsonl` with **both** a threading lock and a
  cross-process fcntl sentinel (`server/nowpayments_webhook.py:54-68`) — stronger than the
  Stripe side.

Residual, self-documented at `stripe_webhook.py:166-169`: a two-machine interleave could mint
twice in a tiny window. Currently moot — one Fly machine is running. LOW.

### Item 12 — Payment succeeds, email fails · **MED — safe by design, silent in practice**

Better than the brief assumed ("currently unknown"). The ordering is correct:

1. Credits are **minted first**, inside the lock (`_decide_and_mint_locked`).
2. The email is attempted **after** the lock releases, so a slow Resend retry cannot
   head-of-line-block other machines' IPNs (`nowpayments_webhook.py:188-196`).
3. A self-serve recovery path exists: `/recover` (`server/app.py:3322-3467`) looks up the
   **existing** claim code from the credits ledger by session id and re-sends it —
   *"NEVER mints a new code"* (`:3330`). No double-grant risk.

**So no one can pay and lose their entitlement.** The credits exist in the ledger either way.

**The gap:** `sent` is captured and written to stderr (`stripe_webhook.py:410-412`,
`nowpayments_webhook.py:197-200`) and then **never acted on**. No retry, no dead-letter queue,
no alert. `stripe_webhook.py:401-407` states the intended fallback outright — *"Founder can
recover by querying the credits ledger… and re-sending manually"* — but nothing notifies the
founder that it is needed. Fly log retention is short, so the signal expires unread.

The customer only recovers if they notice no email arrived **and** find `/recover`.

**Fix:** on `sent == False`, append to a dead-letter file and fire the existing notifier.

### Item 13 — BTC exact-amount matching vs price moves

Two distinct rails, and they behave differently:

- **NOWPayments invoice rail** — `create_invoice` sends `price_amount` in **USD**
  (`server/nowpayments_api.py:160`), so the processor owns the conversion and rate window.
  Not exposed to local price drift.
- **Direct-BTC rail** — sats are fixed at quote time and "the settle worker matches by exact
  amount" (`server/app.py:2100-2101`). Locking sats at quote is the *correct* design for
  exact-amount matching; the exposure is quote validity, bounded by the existing
  `com.orphograph.expire` job. Not re-verified this pass — listed under Coverage.

### Item 14 — Pricing consistency · **Canonical set confirmed**

Writer Pack $19 / Pack of 50 $29 / Standing Order $9-mo appear consistently in
`server/public_config.py:60,65`, `server/affiliate.py:5-6`, `server/newsletter.py:267,271`.
No contamination found in the server tree this pass (the earlier "3x-contaminated" finding
appears remediated). One placeholder remains: `server/analytics.py:166` hardcodes
`$9/mo per active subscription` for MRR — its own comment says "use actual plan amounts in
production." LOW, internal metric only.

### Item 15 — Annual subscription · **not offered; nothing broken**

`server/public_config.py:69` defaults `PERSONAL_ANNUAL_USD` to **60**, but
`STRIPE_PERSONAL_ANNUAL_URL` is **not** among the deployed secrets, and live `/api/config`
returns `"personal_annual_url": ""`. Per `public_config.py:154` an empty URL means "not
offered", so it is hidden rather than rendered as a dead button. Same for
`creator_monthly_url`. The $60/yr figure is unused config — the pricing decision is still
open, but no customer can hit it. **LOW.**

### Item 17 — Checkout health guard · **already committed**

Commits `1bf1bc4`, `3948739` ("flag placeholder Stripe URLs so dead card checkout can't ship
silently", PR #13) and `524fa6e`. Nothing uncommitted in the working tree. No work at risk.

---

## Coverage — what is NOT done

Stated plainly rather than implied complete:

- **BTC quote-expiry window (item 13, second half)** — the `com.orphograph.expire` job exists
  but its actual TTL and behaviour on an expired-but-paid order were not exercised.
- **Sanctions / OFAC / export exposure (P0 legal)** — not assessed. Requires legal review, not
  a code audit; flagged as out of scope for this pass rather than silently skipped.
- **GitHub suspension resolution evidence (P1)** — not verified.
- **Backup/recovery for OTS receipts and order records (P1)** — not verified.
- **Alerting on checkout failure (P1)** — confirmed absent (item 12); no alerting exists to
  audit.
- **OpenTimestamps calendar-downtime behaviour (P1)** — not exercised.
- **Claim sweep incomplete.** The table above is from targeted greps, not the exhaustive
  page-by-page inventory. Five delegated audit agents died on repeated API connection errors,
  so the breadth-first sweep was not completed.
- **Entity/legal insertion points (2.5) not enumerated.** "the office" appears across at least
  12 files including `web/terms.html`; the exact ordered list for a mechanical find-and-replace
  is not yet produced.
- **Secrets scan of full git history not run.**
- **Issuer-independent verification procedure (item 8)** not written or executed.
- **Regression tests (item 5)** exist as harness cases (`stored_UPPERCASE`,
  `custom_excludes_forgotten`) but are not yet pinned into the repo's pytest suite.

---

## Ordered remediation list

| # | Sev | Fix | Touches |
|---|---|---|---|
| 0 | HIGH | **OPEN — founder action.** Stripe dashboard: subscribe the webhook endpoint to `customer.subscription.updated` / `.deleted` / `invoice.payment_*`. Handler already covers them (`stripe_webhook.py:175`); the events are not arriving. Consequence is "you cannot tell if the subscriber renewed" — NOT lost entitlement (see Corrections). | Stripe endpoint config |
| 0b | ~~HIGH~~ | **WITHDRAWN — was false.** `subscriptions.is_active()` already gates on `current_period_end > now`; verified returning False for the expired subscriber. | — |
| 0c | HIGH | **DONE** `02f6720` — BTC tag is additive and price-aware; undercharge now arithmetically impossible. | `server/btc_price.py` |
| 1 | ~~HIGH~~ | **RE-SCOPED, DONE** `38ffb8c` — `status` was accurate, not stale. Added `bitcoin_attested` so integrators get the answer they need. | `server/engine.py` |
| 2 | HIGH | **DONE** `77084b0` — route guard 404s all three mockups + `index-legacy`; verified against a running server. | `server/app.py` |
| 3 | HIGH | **DONE** `38ffb8c` — now "timestamped record"; guard test blocks re-introduction. | `web/lp/*` |
| 4 | MED | **DONE** `891e950` (Wedge 01) — manifest carries `scope` with the effective patterns; sdk-python reads them from the manifest. VERSION not bumped; root unchanged. | `server/merkle.py`, SDKs |
| 5 | MED | **PARTIAL** `7e421fa` — added `supplied_hash_valid` so a parse failure is distinguishable. Raising + `0x`-stripping deliberately NOT done: pinned by `verifier_vectors.json` v05 and the conformance target for all four implementations; that is a spec change, not a one-file edit. | `server/engine.py` |
| 6 | MED | **DONE (partly withdrawn)** `38ffb8c` — removed the absolute "can't be forged or revoked" and the aria-label. Line 494 "On the guarantee" left intact: read in context it is well-written ("structural, not promissory"). | `web/index.html` |
| 7 | LOW | **DONE** `7e421fa` — malformed receipt no longer blames the file. Verdict unchanged. | `verifier-js/orphograph_verify.js` |
| 8 | LOW | Merge `fix/verifier-minor-drifts` (D3/D4/D7) — premortem first, it deploys | `sdk-node`, `sdk-python` |
| 9 | — | **DONE** — regressions pinned across `test_btc_*`, `test_manifest_scope`, `test_attestation_and_claims`, `test_dispute_bundle_contents`, `test_private_pages_not_public`. 1152 tests pass. | `tests/` |

---

## git status — zero application files modified

```
?? scripts/cf_purge.sh          <- pre-existing, not mine
?? tools/audit/                 <- this audit's harnesses
?? tools/gate_read.py           <- pre-existing, not mine
?? tools/test_gate_read.py      <- pre-existing, not mine
```

Branch `feat/lp-csp-cleanup`, 63 commits behind `origin/master`. No tracked file is modified;
the only additions are `tools/audit/` and this document. `scripts/cf_purge.sh`,
`tools/gate_read.py` and `tools/test_gate_read.py` were already untracked before this session.

## Reproduce

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 tools/audit/differential/run_differential.py   # exit 0
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 tools/audit/differential/parity.py             # exit 0
```
