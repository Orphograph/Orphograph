# Lightning L402 — pay-per-anchor for agents

## Retired 2026-09-28

Founder decision on "arm one crypto rail, or retire both": the Lightning
rail is retired until someone asks for it. It was never armed in production.
The code is fenced, not deleted. While `LIGHTNING_RETIRED = True` in
`server/lightning.py`:

- `lightning.configured()` is False whatever the environment holds, so a
  stray `ORPHO_LN_*` secret arms nothing;
- `POST /api/ln/quote` answers 410 with a JSON body that names card checkout;
- any anchor request (`/api/anchor`, `/api/anchor/batch`,
  `/api/anchor_folder`) carrying `Authorization: L402 ...` (any case)
  answers the same 410 before any anchor or payment work: no pack credit is
  spent, no x402 payment is verified or settled, the spent set and the
  macaroon secret are not read;
- no 402 L402 challenge is ever issued; past the free tier the answer is the
  plain 429 (or x402's 402 challenge once x402 is armed);
- `/api/health` reports `"lightning": {"rail": "retired", "configured": false}`.

Re-arming, exactly:

1. Set `LIGHTNING_RETIRED = False` in `server/lightning.py`.
2. Invert `tests/test_lightning_rail_is_retired.py` in the same PR: it
   fails by design once the constant is False. Its copy guard
   (`test_no_file_under_web_offers_lightning_payment`) becomes the list of
   surfaces to restore.
3. Then follow "Arming it" below, steps 1 to 5.

The armed code stays under test meanwhile: `tests/test_lightning_l402.py`,
`tests/test_l402_single_use.py` and `tests/test_payment_outbound_followups.py`
lift the fence on their own in-process module copy, and the Lightning cases in
`tests/test_payment_and_outbound_hardening.py` start their server through
`tests/_run_server.py --arm-lightning` (`_srv.server_processes(...,
arm_lightning=True)`). That flag is not an environment variable: production
starts `server/app.py` through `scripts/init_volume.sh`, and `tests/` is
neither copied into the image nor in its build context (`.dockerignore`), so
nothing set in production can lift the fence.

Everything below describes the rail as it was built, for the day it is
re-armed.

An AI agent with no account pays sats for exactly one anchor. This is the
agent-pays loop: no signup, no card, no stored identity — a payment IS the
authorization, and the resulting receipt verifies independently forever.

**Not the same rail as x402** (server/x402.py): L402 pays in Bitcoin sats
over Lightning; x402 pays in USDC on Base. Both sit on the SAME endpoint
(`POST /api/anchor`). Armed, they were checked in the order pack token,
L402, x402 — the first credential present wins, so a request never pays
twice. Retired, an L402 credential is answered 410 before the pack token or
the x402 header is even looked at, so a request carrying both an L402
credential and an x402 payment is refused and charged nothing; it is never
quietly paid by x402 instead. x402 itself is not retired
(docs/X402_AGENT_PAYMENTS.md). Copy that mentions one payment rail should
say so plainly rather than implying it is the only one.

## Protocol (standard L402 shape)

    POST /api/ln/quote            → 200 {invoice, macaroon, price_sats}
      — or —
    POST /api/anchor (past free tier, LN armed)
                                  → 402, WWW-Authenticate: L402 token=…, invoice=…
    pay invoice → preimage
    POST /api/anchor
      Authorization: L402 <macaroon>:<preimage_hex>
                                  → 200 receipt  (credential is single-use)

Server checks, in order: HMAC macaroon signature → expiry →
SHA256(preimage) == payment_hash → backend settlement truth → unspent.
The spend is marked only after a receipt exists with ≥1 calendar accepted
(a 0-calendar anchor leaves the credential unspent — same fairness as the
card-pack refund path).

## Custody posture (stated plainly)
Inbound payments only, custodied by the configured provider under the
founder's own account. Orphograph never holds Lightning keys and has no
code path that sends funds. No token, no yield — sats are a payment
method here, nothing else.

## Arming it (founder steps, after the re-arm steps at the top; retired, every path answers 410 or the plain 429)
1. Create a provider account: LNbits instance (self-hosted or hosted) or
   OpenNode.
2. `fly secrets set ORPHO_LN_BACKEND=lnbits ORPHO_LN_LNBITS_URL=… ORPHO_LN_LNBITS_KEY=…`
   (or `ORPHO_LN_BACKEND=opennode ORPHO_LN_OPENNODE_KEY=…`)
3. Optional: `ORPHO_LN_PRICE_SATS` (default 100), `ORPHO_LN_MACAROON_TTL`
   (default 3600s).
4. Verify: `curl -X POST https://orphograph.com/api/ln/quote` returns an
   invoice; pay it; anchor with the credential; confirm the receipt's
   `source` starts with `ln:`.
5. Flip the public copy in the same PR as arming. While retired, no public
   surface offers Lightning: the only mentions left say it is retired (the
   `/api/ln/quote` bullet in `web/llms.txt`, the "Retired: Lightning
   payments" section and the health example in `web/docs/api.html`). Once
   armed, that copy turns a working rail into one agents are told is gone.
   The list of surfaces is whatever
   `tests/test_lightning_rail_is_retired.py::test_no_file_under_web_offers_lightning_payment`
   scans (every file under `web/`, so new pages are included automatically);
   `grep -ril lightning web/` prints the same set. Update each surface and
   invert that test together. CI cannot do this for you: it never sees
   production secrets, so it cannot tell that the rail is armed.

## Test posture
tests/test_lightning_l402.py drives the REAL HTTP handler with the mock
backend (mock refuses to load unless ORPHO_LN_ALLOW_MOCK=1, so production
can never fake settlement), with the retirement fence lifted on its own
module copy. Covered: 402 challenge shape, paid anchor, replay rejection,
unpaid rejection, tampered macaroon, unconfigured fallback to the classic
429, and a really paid credential refused 410 and left unspent while
retired. tests/test_lightning_rail_is_retired.py covers the retired rail
through real server processes.
