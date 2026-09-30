# x402 — pay-per-anchor for agents (USDC on Base)

An AI agent with no account pays cents in USDC for exactly one anchor: the
x402 protocol (coinbase/x402 reference implementation), plain HTTP and
JSON, no invoice round-trip. See `server/x402.py` for the implementation
and `tests/test_x402_payment_flow.py` for the proof.

**Not the same rail as L402** (`docs/LIGHTNING_L402.md`): L402 pays in
Bitcoin sats over Lightning; x402 pays in USDC on Base. Both sit on the
SAME endpoint (`POST /api/anchor`), checked in order pack token → L402 →
x402 — the first credential present wins, so a request is never charged
twice.

## Protocol

    POST /api/anchor (past free tier, x402 armed)
                                  → 402 PaymentRequired (JSON body: price,
                                    asset, network, pay-to address)
    agent signs an EIP-3009 authorization for that exact amount
    POST /api/anchor
      PAYMENT-SIGNATURE: <base64 PaymentPayload>   (or legacy X-PAYMENT)
                                  → 200 receipt, PAYMENT-RESPONSE header
                                    carries the settlement

Server checks, in order: parse the header → VERIFY with the facilitator
(cheap, no chain write) → claim the authorization's nonce atomically (our
own single-use lock — verify() alone is not single-use, and relying on the
chain to catch a replay after the fact is exactly the race L402's own
`lightning.claim()` was built to close) → do the actual anchor → only THEN
settle for real. An on-chain USDC transfer cannot be refunded the way a
Pack credit or an L402 credential can, so nothing is charged for an anchor
that never happened, and a total-calendar-outage anchor (worthless, no
Bitcoin commitment) is never charged for either — the claim is released so
the same signature can be retried.

## Custody posture (stated plainly)

Inbound payments only. Verification and settlement happen at the
FACILITATOR — the public `x402.org` testnet facilitator by default, or a
self-hosted / Coinbase-hosted one via `ORPHO_X402_FACILITATOR_URL`.
This module never holds a wallet key and never signs anything; signing is
entirely the agent's own client-side responsibility (the `x402` reference
package, or any x402-compliant client).

## Arming it (founder steps — until then every path falls through to L402,
## then to the classic 429, exactly as before)

1. **Testnet only until mainnet is explicitly approved.** Create a Base
   Sepolia address to receive payments (a fresh EOA is enough; no ETH is
   needed to RECEIVE — the facilitator pays gas to settle).
2. `fly secrets set ORPHO_X402_PAY_TO_ADDRESS=0x…`
3. Optional: `ORPHO_X402_PRICE_CENTS` (default 5), `ORPHO_X402_NETWORK`
   (default `eip155:84532`, Base Sepolia), `ORPHO_X402_ASSET` (default the
   Base Sepolia USDC contract, confirmed against Circle's own docs:
   `0x036CbD53842c5426634e7929541eC2318f3dCF7e`), `ORPHO_X402_FACILITATOR_URL`
   (default `https://x402.org/facilitator`, free and public).
4. Verify: hit `/api/anchor` past the free tier with no payment header,
   confirm the 402 body's `accepts[0].payTo` matches the address above;
   sign a real testnet authorization (the `x402` PyPI package's client
   does this) and confirm the receipt's `source` starts with `x402:`.
5. Mainnet, later, needs explicit founder approval:
   `ORPHO_X402_NETWORK=eip155:8453`, `ORPHO_X402_ASSET` = the mainnet USDC
   contract (`0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`), and a mainnet
   facilitator (Coinbase-hosted, needs a CDP API key — not yet wired into
   `server/x402.py`'s auth path; the module currently assumes an
   unauthenticated facilitator, which is correct for the public testnet
   one and will need a small addition for a keyed mainnet facilitator).
6. Flip the public copy in the same PR as arming — the homepage, pricing
   page and API docs should say agents can pay in USDC, matching how
   L402's own arming step (5) treats its public copy.

## What is NOT built yet (say so; do not imply otherwise)

- A mainnet facilitator auth path (see step 5 above).
- Sanctions screening on payer addresses — testnet does not need it;
  mainnet needs a founder decision on whether the chosen facilitator does
  it or whether this module must.
- A demo wallet / client: this document and `server/x402.py` cover the
  SERVER accepting a payment, not an agent that signs one. The `x402`
  reference package (PyPI, extras `evm,httpx`) is the intended client for
  that half.
