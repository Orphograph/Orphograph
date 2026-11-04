# x402 on Base mainnet — what the switch needs, in order

Status (2026-11-04): **not switched.** Production runs x402 on Base Sepolia, the
test network. The code below is ready for mainnet and refuses to arm a
half-finished switch. Nothing changes in production until the operator sets the
secrets in step 3.

## Why the CDP facilitator

The public `x402.org` facilitator is testnet-only. The Coinbase Developer Platform
(CDP) facilitator at `https://api.cdp.coinbase.com/platform/v2/x402`:

- settles USDC on Base mainnet (`eip155:8453`);
- screens every payment's payer and recipient against OFAC sanctions lists and
  risk signals, at verify and again at settle, and declines flagged payments
  (CDP x402 FAQ, read 2026-11-04);
- indexes the endpoint in its public discovery catalog (the "Bazaar") after a
  real payment goes through it. The 402 challenge already carries the discovery
  metadata (`extensions.bazaar`) that the catalog reads.

It authenticates each call with a 120-second JWT signed by an Ed25519 CDP API
key. `server/cdp_auth.py` produces that JWT with the standard library only.
`tests/test_x402_mainnet_switch.py` checks it against RFC 8032's test vectors
and against OpenSSL's independent verifier.

## What refuses to arm

`x402.config_problems()` must be empty or the rail stays off (`/api/health`
shows `x402.armed: false` and a `config_problems` count). It is not empty when:

- `ORPHO_X402_NETWORK` is mainnet but the facilitator is still `x402.org`;
- the asset is the USDC of the other network;
- the facilitator is CDP but `ORPHO_X402_CDP_KEY_ID` / `ORPHO_X402_CDP_KEY_SECRET`
  are missing or the secret is not a 64-byte Ed25519 key;
- the network is unknown or the facilitator URL is not https;
- `ORPHO_X402_PAY_TO_ADDRESS` is not `0x` plus 40 hex digits, is mixed-case
  but fails its EIP-55 checksum (a mistyped character), or is the zero address
  or a USDC token contract. Paste the address exactly as the wallet shows it;
  an all-lowercase address is accepted but carries no typo protection.

To see the reasons on the machine: `python3 -c "import x402; print(x402.config_problems())"`
from `server/`.

## Steps

1. **Receiving address.** Pick a Base address you control, preferably on a
   hardware wallet. Send it 0.05 USDC from another wallet first and confirm
   it on BaseScan. The server only needs the public address; no key for it
   ever goes on the server.
2. **CDP key.** In the CDP portal, create a project and a Secret API key with
   the **Ed25519** algorithm. Keep the key id and the secret.
3. **Secrets** (one command, values typed by the operator, never pasted into chat):
   `fly secrets set -a orphograph ORPHO_X402_NETWORK=eip155:8453 ORPHO_X402_FACILITATOR_URL=https://api.cdp.coinbase.com/platform/v2/x402 ORPHO_X402_PAY_TO_ADDRESS=<address> ORPHO_X402_CDP_KEY_ID=<id> ORPHO_X402_CDP_KEY_SECRET=<secret>`
   (`ORPHO_X402_ASSET` can stay unset: it follows the network.)
4. **Check it armed:** `curl -s https://orphograph.com/api/health` shows
   `"x402": {"armed": true, "network": "eip155:8453", "config_problems": 0}`.
   If it shows `armed: false`, read the reasons on the machine (above).
5. **Canary payment.** From a separate wallet holding a little USDC on Base,
   exhaust the free tier and pay one anchor with an x402 client. Expect a 200
   with `x402_settled: true`; see the transfer on BaseScan. This is also the call
   that gets the endpoint indexed.
6. **Docs, same day:** change `web/llms.txt`, `web/openapi.json` and
   `docs/X402_AGENT_PAYMENTS.md` from "Base Sepolia, the test network" to Base
   mainnet in one PR. `tests/test_openapi_contract.py` pins this wording.
7. **Roll back** at any point: `fly secrets unset -a orphograph ORPHO_X402_NETWORK ORPHO_X402_FACILITATOR_URL ORPHO_X402_CDP_KEY_ID ORPHO_X402_CDP_KEY_SECRET`
   returns the rail to the test network.

## Paying into Stripe (optional, off by default)

The pay-to address can be a Stripe crypto deposit address, so x402 payments
land in the Stripe balance. Stripe books each one once the server reports its
on-chain transaction: after every successful settle, and only after its charge
row is in `x402_ledger.jsonl`, the server creates a PaymentIntent with the
`crypto` payment method in `transaction_verification` mode
(`stripe_api.record_x402_payment`, Stripe-Version `2026-05-27.preview`, the
x402 payment identifier as Idempotency-Key). Source:
https://docs.stripe.com/payments/machine/x402 (read 2026-11-04). The request
fields follow Stripe's sample, stripe-samples/machine-payments (MIT).

1. **Enable the payment method.** Dashboard → Settings → Payment methods →
   request **Stablecoins and Crypto**. It shows Pending until Stripe approves it.
   (US businesses outside New York; others email machine-payments@stripe.com.)
2. **Create the Base deposit address** (the docs' "Create a Stripe deposit
   address" step): `POST /v1/crypto/deposit_addresses` with `network=base` and
   header `Stripe-Version: 2026-05-27.preview`, using the live secret key, in
   the operator's own terminal. Keep the returned address. For a test run, do
   the same in a Stripe sandbox with Base Sepolia (`network=base` there too).
3. **Restricted key.** The server's `STRIPE_SECRET_KEY` needs **PaymentIntents:
   Write** for this call, on top of what checkout already uses.
4. **Secrets**, staged so they apply together on the next deploy (values typed
   by the operator, never pasted into chat):
   `fly secrets set --stage -a orphograph ORPHO_X402_STRIPE_DEPOSIT_ADDRESS=<address> ORPHO_X402_PAY_TO_ADDRESS=<same address> ORPHO_X402_STRIPE_RECORD=1`
   Staged secrets take effect with the next deploy.
5. **Check:** `/api/health` shows `"stripe_record": true` and
   `"stripe_pending": 0` in the `x402` block, `config_problems: 0`. After the
   canary payment, the PaymentIntent appears under Payments in the Dashboard.

What refuses to arm (added to `config_problems()`, rail stays off): recording
on with no deposit address; a pay-to address that is not the deposit address
(compared case-insensitively); a network other than Base mainnet or Base
Sepolia; no `STRIPE_SECRET_KEY`; `ORPHO_X402_STRIPE_RECORD` set to anything but
`1` or `0`.

Recording never blocks or fails the paid anchor: the money has already moved.
It runs on a background thread. Each payment is written to
`x402_stripe_pending.jsonl` (next to the ledger) before the Stripe call and
removed when Stripe answers success; a failure logs its error category only and
stays pending. Pending rows are re-sent at boot with the same Idempotency-Key,
so a retry cannot create a second PaymentIntent. `stripe_pending` in
`/api/health` counts them. Turn it off with
`fly secrets unset -a orphograph ORPHO_X402_STRIPE_RECORD` (the pay-to address
stays the deposit address until changed).

## Key handling

The CDP key authenticates this server to CDP. It cannot move funds: payments
are signed by the payer and settle to the address in step 1. If it leaks,
revoke it in the CDP portal, create a new one and repeat step 3. The secret is
never logged; error messages name the env var, not its value.
