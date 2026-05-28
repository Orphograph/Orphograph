# Bitcoin payment operator guide

This doc covers everything the founder needs to set up the
receive-only Bitcoin payment flow such that **the wallet cannot
be hacked through the server**. The contract: the private key for
your receive address never touches anything connected to the
internet, ever. Server compromise → attacker reads public data,
can't move funds.

If a section here doesn't make sense after reading it twice, stop
and re-read before going live. This is the money path.

---

## The model in one sentence

**You generate the receive address on a hardware wallet, give the
server only the public address, and the server watches the chain
to credit purchases.** No private key on the server. No xpub on the
server. No seed phrase anywhere connected to the internet.

## What the server holds, and what it can do with it

| What the server has | What that lets an attacker do if compromised |
|---|---|
| The public BTC address | Read the address's transaction history (it's public on-chain anyway). **Cannot spend.** |
| Pending order records (amount_sats + email + order_id) | See who paid what; correlate sat amounts to customers. **Cannot spend.** |
| Claim codes minted | Issue future claim codes; but those need a paid order they don't have. **Cannot spend.** |
| The settlement worker code | Cause incorrect credit (mint claim codes for un-paid orders). Audit log catches this. **Cannot spend.** |

The key word in every row: **cannot spend.** That's the whole
point. Funds in the receive wallet are reachable only by the
hardware device with the private key. Even a total server takeover
doesn't expose them.

## Step-by-step setup

### 1. Get a hardware wallet (if you don't have one)

Pick one. Order list reflects rough trust + ease:

- **Coldcard Mk4** (~$150) — air-gapped, microSD-based PSBT,
  Bitcoin-only, open source. The pure-paranoia choice.
- **Trezor Safe 3** (~$80) — open firmware, USB-only.
- **Ledger Nano S Plus / X** ($80–$160) — proprietary secure
  element, ubiquitous, ledger had a 2020 customer data leak so
  pick a non-billing-name address if you mind that.
- **SeedSigner** (DIY, <$50) — air-gapped, scan QR, open source,
  build it yourself on a Raspberry Pi Zero.

Initialize it offline. Write the 24-word seed phrase on two metal
backups (paper burns; ink fades). Store the two backups in two
physically separate locations. **The seed phrase is the money.**
Anyone with the seed has the wallet. Lose the seed → lose the
funds. Leak the seed → lose the funds.

### 2. Generate the receive address

Use the wallet's UI (or a companion app like Sparrow Wallet or
Specter Desktop on an air-gapped machine) to:

1. Create a new account if your wallet supports labeled accounts.
   Name it `orphograph-pack` or similar — keeps Orphograph revenue
   physically separated from personal sats.
2. Generate a fresh **native SegWit (bc1q…)** receive address. The
   `bc1q` prefix is "P2WPKH" — lowest fees, widest wallet
   compatibility.
3. Copy the address. Verify it on the hardware device's screen
   (don't trust the host computer to display the right thing).

You can also use **Taproot (bc1p…)** if your wallet supports it —
slightly better privacy long-term, slightly less wallet
compatibility today. Either works.

### 3. Set the address on Fly

```bash
fly secrets set BTC_RECEIVE_ADDRESS=bc1qXXXXXXXXXXXXXXXXXXXXX -a orphograph
```

That's the only Bitcoin-related secret. There's no key, no xpub,
no seed phrase being set. The server just gets a public address.

Verify on the running server:

```bash
fly ssh console --command "env | grep BTC_"
```

### 4. Schedule the settlement worker

The settle worker polls mempool.space every 5 minutes for
incoming payments to the address. Once a payment confirms (1+
block confirmations), the worker mints a claim code and emails
the buyer.

Set it on Fly machines cron:

```bash
fly machines run \
  --schedule "every-5-minutes" \
  --command "python3 scripts/btc_settle.py" \
  --env "ORPHO_DATA_DIR=/app/data" \
  --vm-memory 256 \
  -a orphograph .
```

Verify it's running:

```bash
fly logs -a orphograph | grep btc_settle
# you should see "[btc_settle]" lines every 5 minutes when there are pending orders
```

### 5. Test with a tiny order

Place a real $19 order through the public site (it's BTC, you'll
pay yourself):

1. Click "Pay with Bitcoin" on the landing.
2. Enter your email.
3. The buy page shows the address + an exact sat amount.
4. Send exactly that many sats from any wallet (could be a
   different wallet from the receive one).
5. Wait 10–15 min for 1 confirmation.
6. Settle worker fires → claim code email arrives.
7. Sats land in your hardware wallet, visible in the wallet's
   transaction history.

That's the end-to-end test.

## Threat model in detail

Going through every conceivable attack and why it doesn't work:

### Threat 1 — Full server compromise (RCE / breach)

Attacker gets root on the Fly machine.

What they can do:
- Read the public address (already public)
- Read pending order amounts + emails (low-value)
- Read past settlements
- Tamper with the running server's code

What they CANNOT do:
- Move any of the funds in your hardware wallet
- Forge a settlement (claim codes are minted server-side but they
  have no value to the attacker who can already mint anything)

Recovery: redeploy the server from clean source. Funds untouched.

### Threat 2 — `BTC_RECEIVE_ADDRESS` env var swap

Attacker with Fly account access changes the env var to their
address.

What that does:
- Future customer orders get displayed with the attacker's
  address. Customers pay the attacker.
- Existing pending orders are NOT changed — `btc_payments.py`
  records the address at order creation time.
- Already-confirmed payments are NOT touched.

Defenses already in place:
- The env var change is logged in Fly's audit log. If you have MFA
  on the Fly account, only you can change it.
- The settle worker explicitly compares the address on each pending
  order to the current env var; if they diverge, the worker logs
  a loud warning and skips that order rather than crediting from
  the wrong chain history.

Recovery: change the env var back, alert any affected customers
(they paid the attacker, not you — refund-or-resend at your
discretion), file a complaint with Fly if you suspect insider
abuse.

### Threat 3 — Settle-worker tampering

Attacker modifies `scripts/btc_settle.py` to credit fake
settlements (mint claim codes for orders that weren't paid).

What that does:
- Generates claim codes that are spendable on anchors.
- Does NOT move any funds.

Damage cap: each fake settlement = 10 free anchors. At $19 implied
value, the attacker's marginal "gain" is $19 worth of OpenTimestamps
anchors. Calendar load is negligible. **Not worth attacking for.**

Defenses:
- All settle events are append-only in `btc_orders.jsonl`. You can
  audit by comparing `settled` events to the actual chain (each
  has `tx_hash`). Cross-check periodically.
- Standard server hardening covers this (code-signing, deploy-only
  via Fly, no SSH access to non-founders).

### Threat 4 — Phishing customers with a fake address

Attacker MITMs the customer's connection or replaces the address
on the public site via XSS / CSP bypass / DNS hijack.

What they could do:
- Make customers send funds to the attacker's address.

Defenses:
- HTTPS forced at the Fly edge → cert pinning isn't strictly
  enforced by browsers but HSTS prevents downgrade.
- CSP `default-src 'self'` blocks injected scripts that would
  rewrite the address client-side.
- No `innerHTML` is used to render the address — `textContent`
  only — so any injected payload would have to control the JS
  itself, which CSP blocks.
- Domain registrar should have transfer lock + registrar lock
  enabled (separate threat: DNS hijack).

Recovery: if the address on the live site is wrong, suspend the
service immediately, audit the deploy, do a redeploy with a verified
copy.

### Threat 5 — Customer disputes "I paid but didn't get my code"

Not really a hack — but worth covering. The settle worker is
deterministic on chain data: if a payment of the exact expected
amount confirmed and lands at the receive address, the credit
fires. If the customer claims they paid but no settlement
occurred:

1. Ask for the tx hash from their wallet.
2. Look up the tx in mempool.space.
3. Check: did it actually pay the right address? Was the amount
   exactly right? Did it confirm?
4. If they sent the wrong amount or to a wrong address, that's
   their problem (the address + amount are clearly shown). You can
   issue a one-time goodwill claim code or refund.
5. If they paid correctly and the worker missed it: there's a
   real bug. File an incident in `docs/incidents/<date>_btc.md`
   and fix.

## Operational rhythm

### Daily

- Settle worker runs every 5 minutes (automated).
- Customer support inbox checks for "I paid but…" tickets.

### Weekly

- Sweep accumulated sats from the hot-receive address to cold
  storage. Why: even though the hot address only has a public
  presence on our server, leaving large balances in any single
  on-chain address invites correlation analysis. After every $200
  or so, sweep:
  1. On your hardware wallet, sign a tx sending the balance to a
     cold-storage address you control (different seed, different
     device ideally).
  2. Broadcast. Done.
- Optionally rotate the receive address: generate a new bc1q
  address on the hardware wallet, update Fly:
  ```bash
  fly secrets set BTC_RECEIVE_ADDRESS=bc1qNEW -a orphograph
  ```
  Wait ~12 hours for any in-flight orders against the old address
  to confirm (the worker still has the old address in their order
  records and will keep checking). After that, orders generated
  against the new address only.

### Quarterly

- Reconcile: download `btc_orders.jsonl` from Fly volume, compare
  every `settled` event's tx_hash to mempool.space, confirm
  amounts match. Spot-check 10% of records.
- Verify the hardware wallet's seed backups are still readable +
  in the right places.

## Recovery if everything goes wrong

If the server is gone, the data is gone, you've lost the orders
ledger, customers complain — the hardware wallet still has the
funds. The seed phrase + the hardware device recover everything.
The orphograph.com receive flow is reconstructible from chain
history (you can see every payment that arrived).

The whole point of this model: the worst day on the server is
"customers got confused, refunds and apologies." Not "I lost
everything."

## What this doc deliberately doesn't cover

- Multi-sig setups (2-of-3 hardware wallets) — overkill until
  monthly revenue justifies the operational complexity.
- Lightning Network — different model (channel state, custodial
  options, watchtowers). Defer until Pack proves out on-chain.
- Coinjoin / Wabisabi for privacy — not relevant for receive-only.
- Stablecoin / USDC payments — different chain, different model,
  different operator doc.

Roadmap: add Lightning + multi-sig when monthly revenue passes
$1,000. See `deploy/MARKET_ROADMAP.md`.
