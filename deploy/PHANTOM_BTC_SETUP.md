# Phantom Wallet — Per-Customer Address Setup

**Use case:** you keep using Phantom (your existing wallet) as the receive + spending wallet. The Phantom seed never leaves your phone. The server gets a list of fresh addresses you've pre-generated, and rotates through them so customers can't link payments to each other or back to your other on-chain activity.

This is the **Path 2** option from `server/btc_payments.py::address_for_order`. Preference order is xpub > pool > single — since Phantom doesn't expose an xpub for Bitcoin, pool is the right path for you.

---

## What you'll do (one-time, ~10 min)

1. Open Phantom on your phone → switch to **Bitcoin** chain.
2. Tap **Receive**. Phantom shows you a fresh BTC address (`bc1q...` or `bc1p...`).
3. Copy it.
4. Tap **Receive** again — Phantom generates a different address. Copy it.
5. Repeat until you have at least **20** addresses (50-100 is better for the launch window).
6. Paste them all, one per line, into `~/orphograph/data/btc_address_pool.txt`.
7. Set permissions: `chmod 600 ~/orphograph/data/btc_address_pool.txt`
8. Restart the server. Done.

The server now hands a fresh address to every customer order, in the order you pasted them. When the pool runs low (you'll see a warning in `/api/health`), you tap Receive a few more times and append.

---

## Pool file format

`~/orphograph/data/btc_address_pool.txt`:

```
# Phantom-generated BTC receive addresses for Orphograph.
# Generated 2026-05-14. Tap Receive in Phantom to add more.

bc1qclvjjmwmr294rydv4x0dc787nx9jd8j4ny4jaz
bc1q2nd0addressfromphantomtaptap...
bc1q3rd0addressfromphantomtaptap...
bc1q4th0addressfromphantomtaptap...
# add more as you exhaust the pool
```

Rules the parser enforces:

- One address per line
- Blank lines and `# comments` are ignored
- Must start with `bc1q` (segwit) or `bc1p` (taproot)
- Length 30-90 chars
- Lines that don't match are silently skipped (so a typo doesn't break the pool)

---

## Why not just export the xpub?

Phantom's BTC implementation, as of 2026, **does not expose an xpub**. The xpub would let the server derive an infinite stream of fresh addresses from your seed — but it would also let the server derive any address that seed has ever held, which is a different privacy property than just rotating receive addresses. The address pool gives you 95% of the privacy benefit with 0% of the requirement to migrate wallets.

If you ever switch to a wallet that exposes an xpub (Sparrow, BlueWallet, Coldcard, Specter, Blockstream Green, etc.), set `ORPHO_BTC_XPUB` and the server will prefer that path automatically. No code change needed.

---

## Verifying the pool is wired correctly

After pasting:

```bash
cd ~/orphograph
python3 -c "
import sys; sys.path.insert(0, 'server')
import btc_payments
print(f'pool size: {btc_payments.pool_size()}')
print(f'next 3 addresses for orders:')
for i in range(3):
    print(f'  {btc_payments.address_for_order(f\"test_{i}\")}')
"
```

Expected:
```
pool size: 50    (or however many you pasted)
next 3 addresses for orders:
  bc1q...        ← address #1 from your file
  bc1q...        ← address #2
  bc1q...        ← address #3
```

If pool size is 0 but you have content in the file, check the format (lines must start with `bc1q` / `bc1p`).

---

## Operational notes

**When the pool runs out:**
If you exhaust the pool, `address_for_order` wraps and starts reusing from index 0. This means address #1 starts collecting again — privacy degrades exactly as if you were using a single address for the wrapped-around customers.

The `/api/health` endpoint surfaces `btc_pool_remaining` (todo: wire into health.py). When it drops below 5, append more.

**Rotating the pool:**
Replace the file. The index counter in `btc_pool_index.txt` keeps incrementing — so after you replace a 50-address pool, the server's next address is `(old_index % new_pool_size)`. If you want clean rotation, also delete `btc_pool_index.txt`; server starts at 0.

**Mixing pools and single fallback:**
The `BTC_RECEIVE_ADDRESS` env var is the ultimate fallback. If you delete the pool file mid-run, orders fall back to `BTC_RECEIVE_ADDRESS`. Keep both configured so nothing breaks.

---

## What the server CAN do with these addresses

- Display them to a single customer at order time
- Watch mempool.space for incoming transactions to that address
- Match incoming sats amount to pending orders
- Log the address into the order ledger

## What the server CANNOT do

- Move funds. Never. The seed is on your phone in Phantom.
- Derive past addresses your wallet has used (no xpub, no chain code)
- Re-sign or re-anchor receipts (separate cryptographic property)

This is the right tradeoff: rare/never autonomous sweep, total safety from server compromise.

---

## Next: BTC → USD → bank → PayPal pipeline

See `deploy/BTC_PAYOUT_PIPELINE.md` (next doc). The summary: weekly you tap Send in Phantom to your Strike/Cash App account, Strike auto-converts to USD and ACHs to your bank, bank auto-forwards to PayPal on a standing transfer. Everything except the Phantom tap is autopilot. The Phantom tap is the security feature you don't want to remove.
