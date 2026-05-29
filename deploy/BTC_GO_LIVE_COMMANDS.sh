#!/usr/bin/env bash
# BTC payout go-live — ready-to-paste commands.
# Pick exactly ONE of options A / B / C. Replace the placeholder with your real value.
# Nothing in this file runs automatically; copy the block you want into your shell.
#
# Background: /api/health currently reports
#   "payout":     { "configured": false, "address_pool_size": 0, "xpub_set": false }
#   "btc_oracle": { "available": false, "source": "none" }
# Both flip to true after the steps below.

set -euo pipefail
APP=orphograph

# ─── OPTION A: single receive address (simplest) ────────────────────────────
# Trade-off: address reused across all customers — fine at low volume,
# weakens on-chain privacy at >10 tx / week.
#
# fly secrets set BTC_RECEIVE_ADDRESS=bc1qREPLACE_ME -a "$APP"

# ─── OPTION B: xpub (privacy-preserving, hardware-wallet recommended) ──────
# Trade-off: requires exporting an extended public key from Coldcard / Trezor /
# Sparrow. Server derives a fresh address per order; private key never leaves
# the wallet.
#
# fly secrets set ORPHO_BTC_XPUB=xpubREPLACE_ME -a "$APP"

# ─── OPTION C: Phantom address pool (no xpub needed) ───────────────────────
# Trade-off: must pre-generate addresses and refill before exhaustion.
# Step 1 — in Phantom, tap "Receive" 20-100 times, copy each new address.
# Step 2 — paste one per line below, save, then upload to Fly volume.
#
# cat > data/btc_address_pool.txt <<'POOL'
# bc1qREPLACE_ME_1
# bc1qREPLACE_ME_2
# bc1qREPLACE_ME_3
# # … etc, one per line
# POOL
# fly ssh sftp shell -a "$APP" <<'SFTP'
# put data/btc_address_pool.txt /app/data/btc_address_pool.txt
# SFTP

# ─── ORACLE: no command needed ─────────────────────────────────────────────
# btc_oracle.available flips to true on the first cache poll, which happens
# automatically when an order is created (mempool.space → Coinbase → Kraken,
# all public APIs, no secrets).
# To force-warm the cache without a real order:
#   curl -s https://orphograph.com/api/btc/price-debug > /dev/null

# ─── VERIFY ────────────────────────────────────────────────────────────────
# After running your chosen option, wait ~30s for the app to roll, then:
#
# curl -s https://orphograph.com/api/health | python3 -m json.tool \
#   | grep -E '"(available|configured|xpub_set|address_pool_size|source|usd_per_btc)"'
#
# Expected post-config:
#   "available": true,  "source": "mempool|coinbase|kraken",  "usd_per_btc": <non-zero>
#   "configured": true, "xpub_set": true (B) or false (A/C),  "address_pool_size": <N>
