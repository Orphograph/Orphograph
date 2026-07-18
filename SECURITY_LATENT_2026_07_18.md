# Latent Security Items — Assessment & Remediation (2026-07-18)

Follow-up to the 2026-06-22 security review, which flagged two items as
non-gating. Both are assessed here; the safe/additive subset is fixed on
branch `fix/funnel-conversion-2026-06-15`; the rest is founder-gated.

---

## Item 1 — Founder auth token in localStorage

### What the token is

`ORPHO_FOUNDER_TOKEN` is a **static shared secret** set as a server env var
and compared (constant-time) against the `X-Orpho-Founder` request header.
It is not a session: no expiry, no per-device identity, no entry in the
session ledger (`auth_sessions.jsonl`).

Client-side it is persisted under the localStorage key
`orpho_founder_token` by:

- `web/account.js:356` (setup comment) and `web/account.js:737-743`
  (`setupFounderPanel` reads it and calls `/api/founder/payout-status`)
- `web/founder/admin.html:335,368,373`
- `web/founder/metrics.html:359-369`
- `web/founder/support.html:457,597-599`
- `web/founder/funnel.html:203` reads it from an input field only
  (does NOT persist — the one founder page that already had it right)

### Privileges if stolen

All `/api/founder/*` GET endpoints (`server/app.py:1312-1338` dispatch):
hot BTC wallet balance + sweep status, revenue metrics, per-customer
lookup by email (purchase history, subscription state), admin toggles
view, morning summary, funnel analytics. Read-only — there are **no
POST/DELETE founder endpoints** — so theft leaks sensitive business and
customer data but cannot mutate state or move funds.

### XSS blast radius under the strict CSP

CSP (`server/app.py:240-244`, applied to every response):
`default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors
'none'; …` — no `unsafe-inline`, no `unsafe-eval`. Verified live on
orphograph.com 2026-07-18.

- Injected inline-script XSS (reflected/stored) **cannot execute**; token
  theft via classic XSS is impractical.
- Residual theft paths: a compromised same-origin JS file (deploy/supply
  chain), a malicious browser extension, or physical/shared-machine access
  to the founder's browser profile. localStorage never expires, so any
  single exposure is durable until the env var is rotated.
- Side effect of the same CSP: the inline `<script>` blocks on
  `founder/admin.html`, `metrics.html`, `support.html`, `funnel.html` are
  themselves blocked in production, so those dashboards are currently
  non-functional; the live localStorage exposure today is via
  `account.js` (external file, CSP-allowed) after a manual console `setItem`.

### Session-ledger interaction

`/api/me/logout-all` (dispatch `server/app.py:1456`; rate-limited since
commit `03514ed`, `server/app.py:3388`) revokes **customer sessions only**. It does
not — and cannot — revoke the founder token, because the token is not a
session. Revocation today = rotate `ORPHO_FOUNDER_TOKEN` on the server
(fly secrets set) and re-enter it in the browser.

### Fixed now (safe, additive, zero change for the legitimate founder)

Brute-force hardening of the server-side gate — before this change an
attacker could guess `X-Orpho-Founder` values without bound:

- `server/rate_limit.py:128` — new `TokenBucket.peek(key)`: read-only
  quota check, consumes nothing.
- `server/app.py:185-194` — `_founder_fail_limiter`: 20 failed attempts
  per truncated client IP, refilling over 1 hour, in-memory.
- `server/app.py:509` — `Handler._founder_authorized()`: single shared
  gate replacing six duplicated per-endpoint copies
  (`server/app.py:2179,2198,2212,2434,2455,2551`). Failures-only
  semantics: a correct token never consumes quota; a failed compare
  consumes one; an empty bucket refuses **without comparing**, still
  answering the same 404 — endpoint-hiding behavior is unchanged and no
  response ever reveals that a lockout exists.

Tests: `tests/test_founder_token_bruteforce.py` (lockout engages at
capacity, success never consumes, lockout spans all founder endpoints,
token-unset stays 404) and `tests/test_rate_limit.py` (peek semantics).

### Founder-gated (needs a product decision — not changed)

1. **Move founder auth off localStorage entirely.** The right shape is a
   server-issued founder session (httpOnly, Secure, SameSite=Strict
   cookie) with expiry, joining the session ledger so `logout-all`-style
   revocation works. That is a coordinated backend+frontend change (login
   flow for the founder pages, cookie plumbing, CSRF posture) and changes
   how the founder signs in — product decision.
2. **Interim option if (1) is deferred:** switch the founder pages to
   sessionStorage or an expiring wrapper. Cheaper, but still script-readable
   and it changes founder workflow (re-paste per browser session) — gated.
3. **Founder dashboards are CSP-broken** (inline scripts). Externalizing
   those scripts would make the pages work again — but that *widens* the
   localStorage exposure this item is about, so it should be done together
   with (1), not before it.

---

## Item 2 — pay-btc.js third-party hosts

### Exposure as found

`web/pay-btc.js` (loaded by `web/pay/btc.html:224`) referenced two
third-party hosts directly from the browser. There were **no third-party
`<script>` tags** (so SRI was never applicable):

1. `https://mempool.space/api/v1/prices` — `fetch()` price oracle
   (pay-btc.js, old line 36). Compromise/MITM of the oracle could skew
   the displayed BTC amount → under/over-payment.
2. `https://api.qrserver.com/v1/create-qr-code/…` — the **payment QR was
   rendered by a third party** as a remote `<img>` (old line 58). This
   was the serious one: a compromised QR host can serve a scannable QR
   encoding a *different address*. Users scan QRs; few compare against
   the on-page address text. That is a direct payment-redirection vector.

### What the CSP already did

`connect-src 'self'` blocked the oracle fetch and `img-src 'self' data:`
blocked the QR image — **neither host was whitelisted**, so the live
exposure was already zero. The cost: the page has been shipping degraded
(price stuck on "loading…", QR image broken). The latent risk was that a
future "fix the pay page" change would whitelist `api.qrserver.com` in
the CSP and open the redirection vector for real.

### Fixed now (same-origin replacement; CSP untouched)

- `server/app.py:899` — `GET /api/btc/price` → `{"usd": <float>}`,
  proxying the existing server-side multi-oracle 60s cache
  (`server/btc_price.py`; mempool.space → coinbase → kraken fallback
  already lives server-side). 503 when no oracle is reachable, matching
  the order-creation path.
- `server/app.py:911` — `GET /api/btc/qr.svg?sats=N` → server-rendered
  BIP-21 QR via the existing stdlib `server/qrcode_svg.py` (same engine
  as `/api/btc-order/<id>/qr.svg`). The address is pinned to the
  server-side constant `PAY_BTC_ADDRESS` (`server/app.py:196-204`,
  env-overridable) — nothing in the request can change the destination;
  only the amount travels, and it is bounded to
  `546..5,000,000` sats. Same privacy contract as the sibling endpoint:
  no label, no email, no order id in the QR payload.
- `web/pay-btc.js:40` fetches `/api/btc/price`; `web/pay-btc.js:64` sets
  the QR to `/api/btc/qr.svg?sats=…`. Zero third-party hosts remain in
  the page; the strict CSP holds with zero exceptions, and the page's
  price + QR actually work again.

Tests: `tests/test_pay_btc_same_origin.py` — price proxy (cache hit and
all-oracles-down 503), QR bounds/type validation, byte-identical-SVG
proof that the QR encodes only the server-side address, boundary
acceptance, and a regression guard asserting `pay-btc.js` and
`pay/btc.html` contain no third-party references (so a reintroduction
fails CI before anyone is tempted to relax the CSP for it).

### Founder-gated / follow-ups

- None required for security. Optional product follow-up: the QR label
  ("Orphograph Pack") was dropped to match the sibling endpoint's
  payload contract; restore only if wallet-side labeling matters.
- `web/privacy.html:67` says the site uses exactly one localStorage
  entry (`orpho_pack_token`); `writers.js` sessions and the founder
  token predate that sentence. Copy accuracy fix — bundle with Item 1's
  product decision.

---

## Verification

- Full suite: `875 passed` (0 failed, includes the regulated-term gate)
  via `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q`
  on 2026-07-18.
- New coverage: 16 tests across `test_pay_btc_same_origin.py`,
  `test_founder_token_bruteforce.py`, `test_rate_limit.py` additions.
- Live CSP header re-verified against orphograph.com before analysis.
