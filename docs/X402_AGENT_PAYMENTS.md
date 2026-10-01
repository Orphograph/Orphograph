# x402 — pay-per-anchor for agents (USDC on Base)

An AI agent with no account pays cents in USDC for exactly one anchor: the
x402 protocol (coinbase/x402 reference implementation), plain HTTP and
JSON, no invoice round-trip. See `server/x402.py` for the implementation
and `tests/test_x402_payment_flow.py` for the proof.

**Not the same rail as L402** (`docs/LIGHTNING_L402.md`): L402 pays in
Bitcoin sats over Lightning; x402 pays in USDC on Base. Both sit on the
SAME endpoint (`POST /api/anchor`). **L402 was retired on 2026-09-28**; x402
was not. While L402 is retired, a request carrying an
`Authorization: L402 ...` credential is answered 410 before anything else
on it is read, including an x402 payment header: the x402 payment is not
verified, not settled and not charged, and the 410 body says so. Without an
L402 credential the order is pack token → x402 — the first credential
present wins, so a request is never charged twice.

## Protocol

    POST /api/anchor (past free tier, x402 armed)
                                  → 402 PaymentRequired (JSON body: price,
                                    asset, network, pay-to address)
    agent signs an EIP-3009 authorization for that exact amount
    POST /api/anchor
      PAYMENT-SIGNATURE: <base64 PaymentPayload>   (or legacy X-PAYMENT)
                                  → 200 receipt, PAYMENT-RESPONSE header
                                    carries the settlement

Server checks, in order: parse the header → look up what this
authorization's nonce has already bought (hex case ignored: `0xAABB..` and
`0xaabb..` are one nonce) → if it already bought an anchor, 401 and nothing
else runs → otherwise VERIFY with the facilitator (cheap, no chain write) →
SETTLE for real, still before any anchor work, and record the charge →
claim the nonce atomically (our own single-use lock, the same one
`lightning.claim()` gives L402 — verify() alone is not single-use) → do the
actual anchor. A payment that has not settled buys nothing: a refused
settlement is a 402 with no receipt anywhere, an unreachable facilitator a
503 with no charge — only an audit row saying the settlement call got no
answer, which never blocks a resubmission of the identical request. If the
network then refuses that resubmission as "used", the 402 says an earlier
attempt existed (`x402_earlier_attempt: true`) instead of claiming nothing
was ever charged — whether that refusal comes at settle() or already at
verify() — and a plain refusal speaks only for itself ("this request moved
no money"), never for calls the office may not have on record. The payer's
wallet is the arbiter, and support has the row.

One more office-side failure is answered by name: a settlement that
succeeded but whose ledger row could not be written is a 503 carrying
`x402_transaction` (the on-chain transaction id — the customer's proof) and
no anchor; the charge cannot be held or redeemed without its row, so the
hint says to contact support with that id rather than resubmit. A paid 200
always carries `x402_settled: true`.

Redemption is bound to the REQUEST that paid, not to anything on-chain.
Once a payment settles, the whole signed payload is public: the
facilitator settles with EIP-3009 `transferWithAuthorization(from, to,
value, validAfter, validBefore, nonce, signature)`, and every one of those
fields is in the transaction's calldata. So the charge row carries a
digest of the request body the payer sent (canonical JSON, key order
ignored; only the digest is stored), and a held payment is redeemed — and
the replay 401 names the receipt a payment bought — only for the identical
request. Anything else presenting that payment's payload is answered like
a replay (401, no receipt named). The most anyone who rebuilds the payload
from the chain can do with a held payment is make the exact anchor its
payer asked for: same hash, same label, same metadata. A held payment
cannot be redeemed for a different hash; that is the price of the binding.

The body is not secret either, once a receipt is shared: a non-private
receipt serves its hash, label and metadata publicly, and the held 200
hands out that receipt's URL. So the binding does not rest on anyone being
unable to rebuild the body. It rests on what a redemption can produce:
the exact anchor the payer asked for, filed under whoever the charge was
made for. When the payer is a subscriber, the charge row records their
account identity (an HMAC id, never an address; nobody else's account is
linked to a payment), and a held redemption files a private receipt under
that identity, never under whoever presents the request. The redeemed
receipt's email and `anchor.created` webhook go only to that identity when
it presents the request itself (signed in, subscribed), or to a
`notify_email` carried in the paid body; otherwise nobody is mailed, and
the payer's replay of the identical request names the receipt. A held
private payment also stays redeemable after the payer's subscription
lapses: it was authorised as private when it was charged. If the office
cannot read its payment records during that redemption, the answer is the
same 503 as everywhere else ("resubmit the identical request shortly"),
never the private-receipt refusal.

Accepted residual, stated rather than hidden: anyone who can rebuild both
the payload (from the chain) and the body (from a shared receipt) can
confirm which receipt a payment bought, because the replay 401 names it to
the identical request. No public route finds a receipt by its hash (every
receipt route takes a receipt id), so this needs a receipt that was
already shared, and it links a payment to a receipt the viewer can already
see.

(An earlier version of this rail, never deployed, bound redemption to a
digest of the signature and authorization on the premise that only the
payer holds the signature. That premise is false for this scheme, for the
reason above; a review reproduced an observer redeeming a held payment for
their own hash from calldata alone. The payload digest is still stored —
it tells two signed payloads sharing a nonce apart — but it binds nothing.)

A crash between the claim and delivery used to leave the claim standing,
which reads as delivered: the charged payment answered 401 for ever. Now
any failure inside the anchor releases the claim (the charge stays held),
and at boot — when no anchor can be in flight, given ONE server process
per data directory, which is the production shape — every standing claim
without a delivery row is released. One bounded consequence, stated
rather than hidden: if the anchor succeeded and the delivery row itself
could not be written (the ledger file unwritable seconds after it took the
charge row), the next boot releases that claim too, and the identical
request can redeem it for a second anchor. The cost is one anchor's price,
to the payer who did pay, on a disk that is already failing.

The first cut settled AFTER the anchor, so that nothing would be charged
for an anchor that never happened. That handed out real receipts on
verify() alone — a settle() failure afterwards could not take the 200 back
— and, with the nonce compared as a string, a re-cased resubmission of a
spent payload bought a second one. The fairness that order was after is
kept the other way round: a settled payment whose anchor then fails (a
total calendar outage, 0 of 5 accepted, no Bitcoin commitment) is HELD,
not lost. That 200 says `x402_payment_held: true` and carries no
PAYMENT-RESPONSE header; the identical request (same payload, same body),
resubmitted once calendars answer, goes straight to another anchor attempt on the money already
collected — no second settle() — and after that it is a replay like any
other. Both facts live on disk: the charge in `x402_ledger.jsonl`, the
delivery as the claim in `x402_claimed.jsonl`.

## Custody posture (stated plainly)

Inbound payments only. Verification and settlement happen at the
FACILITATOR — the public `x402.org` testnet facilitator by default, or a
self-hosted / Coinbase-hosted one via `ORPHO_X402_FACILITATOR_URL`.
This module never holds a wallet key and never signs anything; signing is
entirely the agent's own client-side responsibility (the `x402` reference
package, or any x402-compliant client).

## Arming it (founder steps — until then every path falls through to the
## classic 429, exactly as before; L402 is retired and issues no challenge)

Unarmed, the server does not read the payment header at all: no
facilitator is called and no x402 ledger is touched (pinned by
`test_an_unarmed_rail_ignores_a_payment_header_and_calls_no_facilitator`).
Disarming a rail that holds settled-but-undelivered (HELD) payments
therefore leaves them unredeemable until it is armed again; check
`x402_ledger.jsonl` for held charges before removing the pay-to address.

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
   L402's own arming step (5) treats its public copy (L402 is retired; its
   copy now only says so).

## What is NOT built yet (say so; do not imply otherwise)

- A mainnet facilitator auth path (see step 5 above).
- Sanctions screening on payer addresses — testnet does not need it;
  mainnet needs a founder decision on whether the chosen facilitator does
  it or whether this module must.
- A demo wallet / client: this document and `server/x402.py` cover the
  SERVER accepting a payment, not an agent that signs one. The `x402`
  reference package (PyPI, extras `evm,httpx`) is the intended client for
  that half.
