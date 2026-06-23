# Log & Data Retention Policy — Orphograph

Status: ACTIVE (interim, 2026-06). Reflects production as deployed plus the
interim scrub (`scripts/interim_pii_scrub.py`). The frozen branch
`harden/pii-log-scrub-2026-06-06` supersedes the "interim" rows on deploy.

PII classes: **P0** none · **P1** pseudonymous (truncated IP, hashes, masked
email) · **P2** direct identifier (email address) · **P3** identifier + money
context (email tied to payments/claims).

## 1. Stream logs (stdout/stderr → Fly platform)

| Stream | Purpose | PII class (today) | Retention | Rule |
|---|---|---|---|---|
| HTTP access log (`Handler.log_message`) | request lines for ops/abuse | P2 — leaks `?e=<email>` on unsubscribe clicks and `/a/<token>` magic-link tokens | Fly platform buffer only (no log shipper configured): short, non-durable, ages out on its own — typically hours, not weeks | Fixed on branch deploy (`_scrub_log_pii` → `<redacted>`); IPs already truncated (/24, /48). No interim action possible or needed; do NOT attach a log shipper before the branch ships |
| `[btc_claim]` stderr line | founder visibility of BTC claims | P3 — full email + txid | same as above | Fixed on deploy (mask `f***@domain`, txid[:10]). No interim action |
| `[recover]` / `[nowpayments_webhook]` lines | payment-recovery audit | P1 — already `mask_email()` | same | none |

## 2. At-rest files on the Fly volume (`/app/data`)

### 2.1 THE BOOKS — never mutated, indefinite retention
`ledger.jsonl`, `credit_ledger.jsonl`, `subscriptions.jsonl`,
`stripe_customer_emails.jsonl`, `btc_orders.jsonl`, `btc_claims.jsonl`,
`receipts/`, `stripe_processed_events.jsonl`,
`nowpayments_processed_events.jsonl`, `manual_fulfillment_queue.jsonl`,
`anchors.jsonl`, `upgrade_log.jsonl`, `expiry_log.jsonl`, `affiliate_*.jsonl`.

- Purpose: append-only financial/anchor records (the money source of truth).
- PII class: P3 (several store the customer email today).
- Retention: indefinite (financial records, 7-year horizon).
- Rule: **no scrub, no rotation, no rewrite — ever.** The branch encrypts the
  email fields of *new* rows at rest (`enc:v1:`) once deployed with
  `ORPHO_EMAIL_ENC_KEY`. Legacy plaintext rows may be migrated only by a
  dedicated, founder-approved, post-deploy migration with verified backup —
  not by the interim scrub. Off-box backup via `orpho_ledger_backup.py`
  (encrypted, rotated) remains the canonical backup path.

### 2.2 Operational ledgers — deferred to branch deploy
`suppressions.jsonl`, `waitlist.jsonl`, `api_keys.jsonl`, `teams.jsonl`,
`team_invites.jsonl`, `webhooks.jsonl`, `referrals.jsonl`,
`onboarding_state.jsonl`, `gdpr_deletions.jsonl`, `auth_tokens.jsonl`.

- Purpose: marketing list, CAN-SPAM suppression, API-key↔email map, team
  membership, webhook registrations, referral dedup, drip state, GDPR
  tombstones, magic-link tokens.
- PII class: P2.
- Retention: life of the function (suppression list is retained indefinitely —
  it is itself a legal obligation).
- Rule: **must stay cleartext until the branch deploys** — the running (old)
  code matches these email fields by string equality (suppression checks,
  newsletter sends, key revocation, referral dedup, GDPR export/delete).
  Encrypting early would break those functions, including legally required
  ones. On deploy: new rows are encrypted automatically; extend
  `interim_pii_scrub.py`'s `SCRUB_NOW` table to migrate legacy rows.

### 2.3 Scrubbable now — covered by the interim scrub
| File | Purpose | PII class | Retention | Rule |
|---|---|---|---|---|
| `refund_requests.jsonl` | customer cancellation/refund feedback queue | P2 (email + free-text reason) | 12 months after resolution | Email field encrypted **now** by `interim_pii_scrub.py` (only production reader is a row count). Free-text `reason` is customer-supplied; review before any export |

### 2.4 Low-PII telemetry
| File | Purpose | PII class | Retention | Rule |
|---|---|---|---|---|
| `events.jsonl` | 4-event funnel analytics (`event`, `page`, truncated IP) | P1 | 13 months | No emails/UA/cookies by design (schema rejects extra keys). Rotate annually if size warrants |
| `payout_pings.jsonl`, `.cadence_last_run`, etc. | ops state | P0 | operational | none |

### 2.5 New stores introduced by the branch (post-deploy)
`unsub_tokens.jsonl` (opaque token → encrypted email map, keyed-hash index) and
`resend_suppressed_emails.jsonl` / `resend_processed_events.jsonl`
(bounce/complaint suppression): P1/P2, encrypted at rest from first write,
retained for the life of the function.

## 3. Third-party retention (noted, outside our control)
- **Fly platform logs**: short non-durable buffer; no shipper configured (keep
  it that way until the access-log scrub deploys).
- **Resend**: holds recipient addresses + delivery metadata per its own
  retention; transactional necessity.
- **Stripe / NOWPayments**: customer + payment data per their policies;
  required for the service.

## 4. Standing rules
1. New log lines carrying an email MUST use `auth.mask_email()`; new at-rest
   email fields MUST go through `email_crypto.encrypt()`.
2. No full IPs anywhere — `truncate_ip()` (/24 v4, /48 v6) is the floor.
3. Bearer material (claim codes `pk_…`, magic-link tokens, API keys) never
   appears in logs or URLs that reach logs (fragments/POST bodies only).
4. Backups of PII-bearing files (incl. the pre-scrub tar) are retained locally
   encrypted, deleted from the box once the scrub is verified, and never
   committed to git.
5. `ORPHO_EMAIL_ENC_KEY` lives only as a Fly secret + founder vault copy; on
   rotation the prior key moves to `ORPHO_EMAIL_ENC_KEYS_OLD` (decrypt-only).

## Addendum 2026-06-10 (main session) — production scrub execution record

Prod volume inspected (`fly ssh console`, `ls /app/data`):
- **`refund_requests.jsonl` does NOT exist on prod** — the single safely-scrubbable
  file has no rows yet. No plaintext refund PII at rest; the interim scrub is a
  no-op until the file first appears. `scripts/interim_pii_scrub.py` stays ready.
- **Prod-ahead files not visible to local-code analysis:** `auth_sessions.jsonl`
  (4 lines matching email pattern) and `auth_tokens.jsonl` (19 lines). Both are
  read by LIVE auth code (sessions / magic-link tokens) → same class as
  suppressions/waitlist: **defer encryption to the PII-branch deploy**; encrypting
  under running old code would break login. Counted only; values never displayed.
- **`ORPHO_EMAIL_ENC_KEY` pre-armed 2026-06-10:** generated, stored in login
  Keychain (service `ORPHO_EMAIL_ENC_KEY`) and set as a Fly secret. Inert to the
  running image; at-rest email encryption switches on automatically the moment
  `harden/pii-log-scrub-2026-06-06` deploys. Founder: copy key to password
  manager (second trust zone).
- Fly log stream retention note from the main analysis stands: leaked lines age
  out of the short non-durable buffer on their own; do not add a log shipper
  pre-deploy.
