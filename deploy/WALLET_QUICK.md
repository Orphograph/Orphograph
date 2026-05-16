# Bitcoin receive address — fastest path

You need one `bc1q...` address that you control the private key for.
Server only sees the address. Even a total server compromise can't
move the funds — that's the whole point of the receive-only model.

This doc is the "I need an address in the next 10 minutes" version.
The full operator-grade guide is at `deploy/BTC_OPERATOR.md`.

---

## TL;DR — pick one

| Wallet | Platform | Time | Trust level |
|---|---|---|---|
| **Phoenix Wallet** | iOS / Android | 3 min | Software, open source, non-custodial ✓ |
| **BlueWallet** | iOS / Android | 3 min | Software, open source, non-custodial ✓ |
| **Sparrow Wallet** | Mac / Win / Linux | 10 min | Software, open source, non-custodial, more features ✓ |
| **Coldcard Mk4** | Hardware | days (ships from Canada) | Pure-paranoia, air-gapped, Bitcoin-only ✓✓ |
| **Trezor Safe 3** | Hardware | days (ships from EU) | Open firmware, ubiquitous ✓✓ |
| **Ledger Nano S+** | Hardware | days, or same-day at electronics stores | Proprietary secure element, popular ✓✓ |

**Today, no hardware:** Phoenix or BlueWallet. 3 minutes each.
Launch with this, migrate to hardware when it arrives.

---

## Path 1 — Phoenix Wallet (recommended for launch-today)

Phoenix is a Lightning-first wallet that also handles on-chain
beautifully. Open source, non-custodial, made by ACINQ.

1. iPhone: App Store. Android: Play Store / F-Droid.
   Search "Phoenix Wallet" by ACINQ.
2. Open the app. Create new wallet. Write down the 12-word seed
   on **paper, twice, store in two physically separate places**.
3. Settings → Wallet → "Show wallet seed" → confirm you have the
   24-word backup correctly recorded.
4. Receive (the arrow icon) → toggle to "On-chain (Bitcoin)" →
   you'll see a `bc1q...` address.
5. **Generate a fresh address** — never reuse for production receipts
   (privacy). Phoenix auto-rotates; the address shown when you tap
   Receive is the latest unused one.
6. Copy it.
7. `fly secrets set BTC_RECEIVE_ADDRESS=bc1q...COPIED -a orphograph`

Total: 3-5 minutes including writing down the seed.

**Caveat:** Phoenix uses a single-address-rotation model on-chain.
The same `bc1q` address may be reused if you take many payments
before sweeping. For better privacy at scale, migrate to Sparrow
or a hardware wallet later.

---

## Path 2 — BlueWallet (mobile alternative)

Similar to Phoenix but with a slightly different UX. Also open
source, also non-custodial.

1. App Store / Play Store: "BlueWallet".
2. Open → "Add a wallet" → "Bitcoin" → "Create" → choose Type:
   "Segwit (native)" → write down the 12-word seed (twice, paper,
   two locations).
3. Tap the wallet → "Receive" → you get a `bc1q...` address.
4. Generate a fresh address for every receipt if you want privacy
   (BlueWallet supports this — there's a refresh button).
5. Copy + paste into the `fly secrets set` command.

Total: 3-5 minutes.

---

## Path 3 — Sparrow Wallet (recommended end state, no hardware yet)

Best feature set for someone running a Bitcoin-receiving business.
Coin control, address labels, transaction history, integrated
with hardware wallets when you get one.

1. Download from https://sparrowwallet.com — verify the
   PGP signature if you're paranoid (recommended for production).
2. macOS: drag to Applications. First launch — right-click → Open
   to bypass Gatekeeper (signed app, but unfamiliar developer).
3. File → New Wallet → name it `orphograph-pack` → Wallet Type:
   Single Signature → Native SegWit (P2WPKH) → Mnemonic (12 words).
4. Click "Generate" → **write down the 12 words on metal backup
   plate** (Sparrow even has a `Print on metal backup` link).
   Two physically separate metal copies are the gold standard.
5. Set a wallet password (separate from the seed — protects
   against quick visual snooping if someone opens Sparrow).
6. Receive tab → first address is `bc1q...`. Copy.
7. `fly secrets set BTC_RECEIVE_ADDRESS=bc1q...COPIED -a orphograph`

Sparrow generates fresh receive addresses automatically — every
time you receive a payment, the next one is unused. Built-in
privacy.

Total: 10-15 minutes including the metal backup.

---

## Path 4 — Hardware wallet (when it arrives)

Order today, set up when it arrives. Until then, run Phoenix or
Sparrow.

### Coldcard Mk4 ($150, coinkite.com)

Air-gapped via microSD card PSBT. Bitcoin-only firmware. Most
paranoid choice. Ships from Canada — usually 3-7 days.

### Trezor Safe 3 ($80, trezor.io)

Open firmware. USB-only. Easy beginner UX. Ships from EU.

### Ledger Nano S Plus ($80, ledger.com)

Proprietary secure element. Best wallet-app coverage. Past
customer-data breach (2020) is non-fatal because they only leaked
buyer info, never private keys. If you mind, order with a separate
billing address.

### SeedSigner (DIY, <$50)

If you're handy: build it on a Raspberry Pi Zero with a stock
firmware. Air-gapped, scan QR codes. Highest trust-floor.

### Migrate when hardware arrives

1. Set up the hardware wallet, generate a fresh address (different
   seed from your software wallet).
2. Update Fly: `fly secrets set BTC_RECEIVE_ADDRESS=bc1q...NEW`.
3. From your software wallet, sweep accumulated sats to a new
   address on the hardware wallet.
4. The software wallet's old receive address will keep collecting
   any leftover in-flight payments — orphograph.com's logic handles
   the old address gracefully (each order records the address at
   creation time).

---

## What NOT to use

❌ **Coinbase / Binance / Kraken / Cash App / any exchange address.**
These are CUSTODIAL. The exchange controls the private key. Your
"receive address" is the exchange's address with a memo tag for
your account. Defeats the entire "server can't be hacked" model
because the exchange's compliance team CAN freeze the funds.

❌ **Wallet of Satoshi / Strike / any Lightning-only custodial app.**
Same problem.

❌ **A paper wallet from bitaddress.org or any random web tool.**
You don't know where the entropy came from. Hardcoded seeds and
SQL injection have leaked many "paper wallets" historically. Use
a real wallet app.

❌ **An address from someone else's wallet.**
Only you have the keys, only you receive the funds. If you don't
control the keys, you don't own the Bitcoin.

---

## Sanity-check before pasting into Fly

The address you're about to paste should:

- ✅ Start with `bc1q` (native SegWit) or `bc1p` (Taproot). Both work.
- ✅ Be exactly 42 characters (bc1q) or 62 characters (bc1p).
- ✅ Be lowercase (Fly is case-sensitive on env vars).
- ✅ Have been generated INSIDE the wallet — never copied from a
  blog post, support email, or chat message (those are scams).
- ✅ Be a FRESH address (not previously used) for privacy.

Quick sanity check:
```bash
ADDR="bc1q...YOURADDRESS"
echo "$ADDR" | grep -E '^bc1q[a-z0-9]{38}$' || echo "FAIL: not a native SegWit address"
```

---

## After you set the address

The orphograph.com BTC checkout activates the next time the
settle worker fires (every 5 minutes on Fly cron). Test with a
tiny self-payment:

1. Visit https://orphograph.com on your phone.
2. Click "Pay with Bitcoin", enter your own email.
3. Open the buy page — copy the exact sat amount + bc1q address.
4. From a DIFFERENT wallet (or sweep from somewhere), send exactly
   that amount.
5. Wait ~10 min for 1 Bitcoin block confirmation.
6. Email arrives with claim code.
7. Activation link auto-stashes the claim code in your browser.
8. Drop another file to anchor — it consumes from your pack.

Once you see the loop close end-to-end, you're done. Real
customers will follow the same path.
