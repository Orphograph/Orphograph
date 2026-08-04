# Lightning L402 — pay-per-anchor for agents

An AI agent with no account pays sats for exactly one anchor. This is the
agent-pays loop: no signup, no card, no stored identity — a payment IS the
authorization, and the resulting receipt verifies independently forever.

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

## Arming it (founder steps — until then every path 503s/429s exactly as before)
1. Create a provider account: LNbits instance (self-hosted or hosted) or
   OpenNode.
2. `fly secrets set ORPHO_LN_BACKEND=lnbits ORPHO_LN_LNBITS_URL=… ORPHO_LN_LNBITS_KEY=…`
   (or `ORPHO_LN_BACKEND=opennode ORPHO_LN_OPENNODE_KEY=…`)
3. Optional: `ORPHO_LN_PRICE_SATS` (default 100), `ORPHO_LN_MACAROON_TTL`
   (default 3600s).
4. Verify: `curl -X POST https://orphograph.com/api/ln/quote` returns an
   invoice; pay it; anchor with the credential; confirm the receipt's
   `source` starts with `ln:`.

## Test posture
tests/test_lightning_l402.py drives the REAL HTTP handler with the mock
backend (mock refuses to load unless ORPHO_LN_ALLOW_MOCK=1, so production
can never fake settlement). Covered: 402 challenge shape, paid anchor,
replay rejection, unpaid rejection, tampered macaroon, unconfigured
fallback to the classic 429.
