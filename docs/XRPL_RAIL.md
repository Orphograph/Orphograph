# XRPL payment rail (RLUSD, receive-only)

Status (2026-11-17): **built and off.** The rail ships disarmed. Nothing
happens until the founder sets `ORPHO_XRPL_ADDRESS`. Code is in
`server/xrpl_pay.py`. Tests are in `tests/test_xrpl_pay.py` (in process) and
`tests/test_xrpl_pay_server.py` (a real server process). Both run against
loopback stand-ins for XRPL nodes and never touch the real ledger.

A buyer pays for a credit pack in RLUSD, Ripple's USD stablecoin on the XRP
Ledger. The rail is the same purchase as the NOWPayments crypto checkout
(same pack ladder, same claim-code email, same credit ledger). It differs in
one way: Orphograph reads the payment off the public ledger itself, so no
processor sits in the middle.

XRPL is a **payment rail only**. Receipts are anchored to Bitcoin and nothing
else. The XRPL anchor experiment in `tools/xrpl_anchor/` stays testnet-only and
is not connected to this rail.

## How a purchase works

1. `POST /api/xrpl/invoice` with `{"plan": "writer_pack"|"pack_50", "email": "..."}`
   (JSON only, the same content-type gate as every other API POST). The answer
   carries the merchant address, a fresh 32-bit destination tag, and the amount:
   the pack's USD price in RLUSD, 1:1, with no FX oracle ($5 → 5 RLUSD,
   $20 → 20 RLUSD). The invoice expires after 60 minutes.
2. The buyer sends RLUSD from any XRPL wallet to that address **with that tag**.
3. The buyer's page polls `GET /api/xrpl/invoice/<id>`. Each poll runs at most
   one bounded ledger check per invoice every 10 seconds. A HEAD request never
   runs one.
4. Once the payment is confirmed, a claim code is minted and emailed, exactly
   as for a NOWPayments order. The status route never returns the email or the
   claim code. Its access-log line shows `/api/xrpl/invoice/{id}`, not the id.

### What counts as paid

Only a transaction that meets every one of these:

- it is in a **validated** ledger;
- it is a `Payment` whose `Destination` is our address and whose
  `DestinationTag` is the invoice's tag;
- `meta.TransactionResult` is `tesSUCCESS` (so `tecPATH_DRY` and every other
  `tec` result is rejected);
- `meta.delivered_amount`, **never** `Amount`/`DeliverMax`, is RLUSD, with the
  configured currency code and issuer both matching exactly, and its value is
  at least the quote. A partial payment can carry a huge `Amount` and deliver
  almost nothing, which is why `Amount` is never read;
- it closed after the invoice was created and before it expired.

| What arrived | Invoice state | Credit |
|---|---|---|
| RLUSD ≥ quote, in time | `paid` | once |
| RLUSD < quote | `underpaid`, manual review | none (a later full payment still credits) |
| Full payment after expiry | `late_payment`, manual review | none |
| XRP, another token, an impostor issuer | unchanged, manual review | none |
| A second payment after the credit | unchanged, manual review | none |
| Not validated yet | unchanged | checked again next poll |
| Any node failure | unchanged; `verification: "unavailable"` | none |

Each transaction hash is processed once across every invoice. A hash that was
already counted is never looked at again, so a replayed hash cannot pay twice
or pay a second invoice. Crediting follows the NOWPayments webhook: a thread
lock plus a cross-process file lock, a per-invoice mint marker, a per-hash
marker, and a check of the credit ledger itself (source
`xrpl:<tx hash>:<invoice id>`). If a crash cuts in between, the next poll
repairs the records without minting a second pack.

Manual review means the founder reconciles by hand: refund the payment, or
top it up to a full pack. Invoices flagged for review show
`"manual_review": true` in the status reply and in `data/xrpl_invoices.jsonl`.

### Trusting the nodes

Public XRPL nodes are run by other people and any one of them could lie. The
rail does what the testnet verifier does (`tools/xrpl_anchor/xrpl_verify.py`):

- One node's `account_tx` finds candidate payments. Each candidate is then
  re-read with `tx` on **every** configured node. Nothing is decided unless
  `ORPHO_XRPL_MIN_AGREE` distinct hosts (default **2**) give the same answer:
  same verdict, hash, ledger, close time, amount and ledger hash.
- If any node returns a different transaction or a different answer, the
  check is UNAVAILABLE and nothing changes. Too few confirmations is also
  UNAVAILABLE, never a pass.
- The public-node policy from `tools/xrpl_anchor/NODES.md` applies:
  - One honest User-Agent (`orphograph-xrpl-pay/0 (+https://github.com/Orphograph/Orphograph)`)
    that no env var can change.
  - A refusal blocks that node until the next restart: HTTP 401, 403 or 429,
    `slowDown` or `tooBusy`, or a reply that mentions a rate limit. There is no
    retry and no rotation.
  - Redirects are never followed. Every request has a 10-second timeout, and
    replies are capped at 2 MB.
  - Traffic stays light: an `account_tx` scan reads at most 3 pages of 100.

The anchor tool is not in the production image (the Dockerfile ships
`server/` only), and it has to stay a standalone script. So the rail
restates its rules, and `tests/test_xrpl_pay.py::TestNodePolicy` fails if
the refusal codes, timeout, body cap or host deduplication drift from the
tool's.

## Arming it (founder actions)

1. **Own an XRPL account.** Use a wallet you control, such as Xaman, Ledger
   or a self-custody app. Fund it with the XRP reserve. Only its public
   address (`r...`) goes to the server. **Never put a seed or secret key on
   the server.** The rail has no code that could use one, and the repo's
   secret scan rejects XRPL seed shapes.
2. **Set an RLUSD trust line** on that account. You can't receive RLUSD
   without one. Take the RLUSD issuer address from Ripple's own RLUSD page
   and check it against a second source. The server does not hardcode it.
3. **Pick two XRPL node operators.** The default node list is
   `https://xrplcluster.com/` alone, and the default quorum is two, so a
   default configuration stays off on purpose. Before you add a node, read its
   terms and record them (see the node-table rule in
   `tools/xrpl_anchor/NODES.md`). Two hostnames from the same operator, such as
   `xrplcluster.com` and `xrpl.ws`, don't count as independent. Ripple's public
   servers say they are "not for sustained or business use". To run on a
   single node instead, set `ORPHO_XRPL_MIN_AGREE=1` deliberately; health then
   shows `min_agree: 1`.
4. **Set the Fly secrets** (all public values; secrets is just where config lives):

   ```sh
   fly secrets set \
     ORPHO_XRPL_ADDRESS=r...your-address... \
     ORPHO_XRPL_RLUSD_ISSUER=r...rlusd-issuer... \
     ORPHO_XRPL_RPC=https://node-one.example/,https://node-two.example/
   # optional:
   #   ORPHO_XRPL_RLUSD_CURRENCY  40-hex code; default is "RLUSD" in hex,
   #                              524C555344000000000000000000000000000000
   #   ORPHO_XRPL_MIN_AGREE       default 2
   ```

5. **Read it back.** `/api/health` should show
   `"xrpl": {"armed": true, "config_problems": 0, ...}`. If `config_problems`
   isn't 0, run `python3 -c "import xrpl_pay; print(xrpl_pay.config_problems())"`
   in `server/` on the machine. The output names env vars, never values.
6. **Buy one Writer Pack yourself** with a small real RLUSD payment and watch it
   credit before telling anyone the rail exists. No page links to it yet.

The rail refuses to arm, staying 404 with `armed: false` and the problem
counted, when the address or issuer is not a valid classic address
(alphabet, length, checksum), is a black-hole account, or is the same as the
other one; when the currency code is not 40 hex characters or is a standard
(`00...`) code; when a node URL is not https (plain http is allowed only to
loopback) or carries credentials; or when there are fewer distinct node hosts
than `ORPHO_XRPL_MIN_AGREE`.

To disarm, unset `ORPHO_XRPL_ADDRESS`. Both routes then answer 404, the same
as any unknown path. `ORPHO_DISABLE_CHECKOUT=1` stops new invoices on every
rail.

## Legal coherence (design facts, not legal advice)

These are statements about how the system is built. They are not a legal
opinion, and a lawyer should confirm what they mean in each jurisdiction.

- **A merchant taking payment for a service.** Orphograph sells credit packs
  for its own anchoring service. A payment in RLUSD is the price of that
  service, the same as a card or NOWPayments payment.
- **Non-custodial.** The server knows only a public address and cannot sign,
  send or move funds. It holds no customer balance in any crypto asset. What a
  buyer gets is service credits in Orphograph's own ledger, redeemable only
  for anchoring. They are not money, not transferable value, and not a claim
  on any crypto asset.
- **No token is issued.** Orphograph does not create, issue or redeem any
  token, IOU or trust line. RLUSD is issued by its own issuer. Orphograph only
  receives it.
- **Bitcoin is the anchor.** XRPL never anchors a production receipt. It is a
  way to pay, nothing more.
- **Refunds follow the other rails.** The terms of service apply: a full
  refund of unused credits within 7 days, partially used packs pro-rata at
  Orphograph's discretion. Refunds of crypto payments, and of underpaid, late
  or wrong-asset payments, are sent by hand from the founder's own wallet. The
  server cannot send them.
