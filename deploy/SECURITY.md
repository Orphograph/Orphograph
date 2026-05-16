# Security posture

This document mirrors the runtime defenses live in production, so
auditors and operators have a single reference. Last updated 2026-05-12
after the post-launch hardening pass.

## Transport

- All public traffic forced to HTTPS at the Fly edge (`fly.toml: force_https = true`).
- HSTS: `max-age=31536000; includeSubDomains` set on every response.
- TLS terminated by Fly; the app server never sees plaintext on public
  paths. Loopback dev binds to `127.0.0.1` (`HOST=127.0.0.1` default).

## Content Security Policy

```
default-src 'self';
script-src  'self';
style-src   'self';
img-src     'self' data:;
connect-src 'self';
frame-ancestors 'none';
base-uri    'self';
form-action 'self';
```

No third-party scripts, fonts, analytics, or trackers — the site loads
only what we serve. The only `data:` exception is for inline SVG image
data we may use in the future; no current asset relies on it.

The CSP is audited via `tests/test_ui.py::test_landing_does_not_load_third_party_scripts`,
which fails if a non-self `<script>` tag is added to any HTML file.

## Other response headers

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`

## Authentication / identity

There is no password and no account by default.

- **Free tier:** stateless. IP-prefix rate limit (10 anchors/hour/`/24`).
- **Pack tier:** bearer token (`pk_<24 url-safe chars>`) delivered by
  email. The token IS the credential; anyone with it can spend the
  credits. Tokens arrive via `#pack=` URL fragment (never transmitted
  to the server, so no log exposure) and persist in browser
  `localStorage` only.

## Webhook signature verification

`POST /api/stripe/webhook` requires a valid `Stripe-Signature` header
per Stripe's documented HMAC-SHA256 scheme. Implementation in
`server/stripe_webhook.py`:

- Tolerance: 300s (rejects timestamps that drift further).
- Multi-`v1=` support for key rotation: accepts if any presented
  signature matches.
- `hmac.compare_digest` for timing-safe equality.
- Idempotency: every successfully-handled event ID is appended to
  `stripe_processed_events.jsonl`; duplicate deliveries no-op.

## Data integrity

- Append-only JSONL ledgers for receipts, credits, processed Stripe
  events, OTS upgrades, expiry events. Each is locked via
  `fcntl.flock` across processes (multi-machine safe).
- Cross-process consume-credit atomicity is verified by
  `tests/test_credits.py::test_concurrent_processes_cannot_double_spend`.

## Cryptographic dependencies

- SHA-256 for the Bitcoin anchor (OTS protocol requirement).
- SHA-512 sibling witness stored in every receipt (quantum hedge —
  see "Quantum protection" section of the FAQ).
- HMAC-SHA256 for Stripe webhook signatures.
- `secrets.token_urlsafe(12)` for receipt IDs (96 bits, brute-force
  infeasible).
- `secrets.token_urlsafe(24)` planned for magic-link auth tokens.

## Rate limits

- `/api/anchor`: 10/hour per IP-prefix (`/24` for IPv4, `/48` for IPv6).
  Bypassable only with a valid Pack token, which consumes a credit.
- `/api/stripe/webhook`: limited only by Content-Length cap (256KB).
  Defended by signature verification — invalid sigs return 400 fast.
- Static files: served by `http.server` with no rate limit; Fly's
  edge handles abuse.

In-memory token-bucket state resets on process restart. A persistent
snapshot is planned (task 18 / `rate_limit_state.json`).

## Logging

- IPs are truncated to `/24` (IPv4) or `/48` (IPv6) before being written
  to stderr access logs. Full IPs are never persisted.
- Pack tokens arrive via URL fragment and are never logged.
- Email addresses are persisted only in the credit ledger and Stripe
  webhook events, for receipt delivery and customer support.

## Container

- `python:3.11-slim` base.
- Non-root user `orpho` (UID 10001).
- Stdlib-only Python — no third-party dependencies and no `pip install`
  during build (eliminates supply-chain risk for the runtime).
- Source tree mounted read-only at `/app`; data writes go to
  `/app/data` (Fly persistent volume).

## Threat model: what we do not defend

- **Phishing of Pack claim codes.** If an attacker convinces a buyer
  to forward their activation email, the attacker can spend the credits.
  Mitigation: the email body explicitly warns "without the claim code
  there is no way to recover the pack."
- **Browser XSS in user-controlled `client_label`.** Currently mitigated
  by `textContent` rendering in all UI paths plus CSP, but if a future
  edit uses `innerHTML` with a label, the existing CSP `script-src 'self'`
  still blocks inline script execution. A pre-commit hook flags
  `innerHTML =` patterns to keep this discipline.
- **Bitcoin protocol failure or 51% attack.** Out of scope; we anchor to
  whatever chain has the most cumulative work. If Bitcoin is abandoned
  by the world, future receipts would need to migrate to whatever
  succeeds it. Receipts already in past blocks remain verifiable as
  long as those blocks remain in the longest chain.
- **Quantum break of SHA-256 strong enough to find preimages.** The
  SHA-512 sibling in every receipt is the hedge; if SHA-256 is broken
  but SHA-512 is not, the file→receipt binding still holds.
