# BTC Receive + Cold-Storage Pipeline — stays in BTC, never converts

**The accumulation goal:** 5 BTC. Every USD conversion gives away upside. So the pipeline below keeps funds in BTC end-to-end. The only "fiat" decision is the day you choose to spend some BTC for life things — and that's an ad-hoc, infrequent action, not a recurring one.

**The autonomy promise:** everything is autopilot *except* one ~10-second tap on Phantom roughly weekly. That tap is your security feature.

---

## Architecture (BTC-only, no fiat hop)

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. CUSTOMER PAYS                                                 │
│    Customer sees fresh bc1q address from pool → sends sats       │
│    → confirmed on mempool.space → server marks order settled     │
│    → server sends receipt email automatically (Resend)           │
│    → funds accumulate in Phantom (your phone)                    │
└──────────────────────────────────────────────────────────────────┘
                              ↓ AUTONOMOUS ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. SERVER PINGS WHEN HOT-WALLET BALANCE > THRESHOLD              │
│    payout_monitor.py polls mempool.space for all pool addresses  │
│    Daily launchd cron checks: hot_balance ≥ ORPHO_SWEEP_THRESHOLD│
│    (default 500,000 sats = ~$300 at $60k/BTC)                    │
│    → Telegram ping: "📥 Phantom holds X sats — sweep to cold"    │
└──────────────────────────────────────────────────────────────────┘
                              ↓ YOUR ONE TAP ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. YOU SWEEP PHANTOM → COLD ADDRESS (~10 seconds)                │
│    Open Phantom → BTC → Send → paste your COLD address           │
│    → enter amount (typically "send all" minus a small buffer)    │
│    → Face ID → confirmed.                                        │
│    Phantom now holds only what's needed for the next ~week of    │
│    customer payments matching against. The bulk lives cold.      │
└──────────────────────────────────────────────────────────────────┘
                              ↓ ACCUMULATING ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. COLD WALLET HOLDS THE STACK                                   │
│    Cold = whatever the highest-security wallet you choose:       │
│    • Coldcard (recommended — air-gapped, BIP-39, $150)           │
│    • Trezor Safe 3 (open-source, $80)                            │
│    • Paper wallet (free; one-time generation, never online)      │
│    • A secondary Phantom on a device you NEVER use for browsing  │
│    No software ever sees the cold seed. Spending requires the    │
│    cold device, intentionally.                                   │
└──────────────────────────────────────────────────────────────────┘
                              ↓ DONE ↓
                Stack growing toward 5 BTC. No fiat path.
```

---

## Two distinct decisions, decoupled

| Decision | Cadence | Trigger |
|---|---|---|
| **Move from hot (Phantom) to cold (Coldcard)** | Weekly-ish | Server pings when threshold hit. ~10 sec tap. |
| **Sell some BTC for USD** (rare, your call) | Whenever you choose | Manual ad-hoc. Open Phantom → swap → withdraw. Or use Strike/Cash App for that specific sale. |

Decoupling these two decisions is the whole point. The pipeline keeps you accumulating; you separately and intentionally choose if and when to convert. No drip-conversion that bleeds the stack.

---

## Choosing your cold address

| Option | Cost | Privacy | Recovery friction |
|---|---|---|---|
| **Coldcard Mk4** | $150 | Highest (air-gapped, never online) | 12-24-word seed; metal backup essential |
| **Trezor Safe 3** | $80 | High | Same |
| **Paper wallet** (one-time generated offline) | $0 | High but fragile (single physical artifact) | None except keeping the paper safe |
| **Secondary Phantom on a "never-browse" phone** | $0 | Medium (still software wallet) | Phantom restore from 12-word phrase |
| **Just Phantom (no cold sweep at all)** | $0 | Lowest — single hot wallet | Phantom restore |

**Recommendation for solo bootstrapped:** start with Phantom-only until accumulated balance > $1000. At that point, buy a Coldcard ($150 is ~1% of the stack at that point — defensible insurance). Sweep weekly to the Coldcard's first address. Sleep better.

**Recommendation for "right now, no hardware":** create a NEW Phantom account on a separate iCloud/device you don't browse on (an old iPhone in a drawer works). Use that as cold. Sweep to its address. Total cost: $0. Risk: still a software wallet, so a sophisticated phone compromise is theoretically possible — but vastly safer than a hot wallet you carry daily.

---

## Setup checklist (one-time, ~20 min)

### A. Generate the cold address

Whichever option above you choose, **generate the address now** and verify the seed backup:

```
1. Power up the device (or open the new Phantom account).
2. Write down the 12-24 word seed phrase. METAL preferable, paper minimum.
3. Generate the first receive address. Should start with bc1q (segwit)
   or bc1p (taproot). Copy it.
4. Verify the address is correct: send $1 from Phantom to it. Confirm.
   You've now proven the address is yours and the cold device receives.
```

### B. Wire the address into the server

Append it to a new config file the monitor reads:

```bash
mkdir -p ~/orphograph/data
echo "bc1q...YOUR_COLD_ADDRESS_HERE" > ~/orphograph/data/cold_wallet_address.txt
chmod 600 ~/orphograph/data/cold_wallet_address.txt
```

The server uses this address only for the daily summary (so the ping can say "ready to sweep $X to your cold wallet"). The server NEVER moves funds to it — only Phantom can do that, via your tap.

### C. Set the sweep threshold

```bash
# Add to .env.local
echo 'ORPHO_SWEEP_THRESHOLD_SATS=500000' >> ~/orphograph/.env.local  # ~$300 @ $60k/BTC
```

Lower threshold = more frequent pings, more frequent taps. Higher threshold = larger hot-wallet exposure between sweeps.

### D. Install the monitor (planned — see TODO)

```bash
# Once payout_monitor.py is built:
cp ~/orphograph/scripts/monitor.plist ~/Library/LaunchAgents/com.orphograph.payout-monitor.plist
launchctl load ~/Library/LaunchAgents/com.orphograph.payout-monitor.plist
```

Daily at 9am ET, monitor polls mempool.space, computes hot balance, pings via Telegram if threshold hit.

---

## Cost summary

| Step | BTC fee | USD-equivalent (@ $60k/BTC) |
|---|---|---|
| Phantom → Cold (on-chain tx) | ~5,000-50,000 sats | $3-30 depending on mempool |
| Server-side monitoring | $0 | $0 |
| **Total per sweep** | **5k-50k sats** | **$3-30** |

For a weekly $300 sweep: $3-30 in fees, so 1-10% — high if the mempool is congested. Mitigations: batch sweeps less often (bi-weekly when fees are high), or use Lightning where supported. Phantom has Lightning support on some chains — for BTC specifically, on-chain is the path today.

---

## What the server CAN do (autonomous)

- Track every customer payment to a pool address
- Mirror incoming-sats running totals via mempool.space polling
- Send Telegram pings when the hot balance crosses your threshold
- Email a weekly summary of revenue + sweep recommendations
- Log all of the above to `data/payout_log.jsonl` for accounting + tax prep

## What the server CANNOT do (by design)

- Move funds. Ever.
- Sign a sweep transaction
- Access either Phantom's seed or your cold device's seed

This is the property we don't give up. A total server compromise leaves the attacker with: a log of which addresses received what amounts, no spending authority over any of them. Worst case they can deface the site; they cannot steal a single sat.

---

## Failure modes + recovery

| Scenario | What happens | Recovery |
|---|---|---|
| Phantom seed lost / phone destroyed | Funds in Phantom are gone | Restore Phantom from your 12-word phrase backup (you HAVE backed this up, right?) |
| Cold device lost / destroyed | Funds in cold are gone | Restore cold device from its 12-word phrase backup (DO this today — metal backup, fireproof safe) |
| Server compromised | Attacker sees addresses + ledger, cannot move funds | Rebuild server from `~/orphograph/` git, fresh Fly deploy, rotate API keys |
| mempool.space goes offline | Monitor can't see payments | Falls back to blockstream.info (TODO: add fallback in payout_monitor.py) |
| Pool exhausted | Customers reuse old addresses, privacy degrades | Server logs warning at `/api/health`; you generate more in Phantom |

---

## When to convert SOME BTC to USD (life decisions, not part of the pipeline)

Decoupled from this pipeline. Triggered by needs, not schedule:

1. Tax obligation due (April + Oct in US — quarterly estimates if MRR is large)
2. Operating costs need funding (Fly bill, etc. — but these are <$10/mo, so trivial)
3. Personal cash flow event (rent, emergency, etc.)
4. Strategic rebalance (BTC has appreciated significantly + you want to lock in)

**How to do it when the moment comes:**

```
Phantom → Send → to a Strike or Cash App BTC address (one-time setup)
↓
Strike auto-converts → USD in Strike balance
↓
Strike → ACH → your bank
↓
(optional) bank → PayPal
```

Same Strike account as the original "PayPal pipeline" idea — just used as an ad-hoc sell venue, not a recurring sweep destination. Total time: ~3 days end-to-end. Spread: 0.30%.

But this is **your call, your timing, not the server's**. The server doesn't sell BTC. The server stacks BTC.

---

## What I'll build next if you want

The one missing piece: `server/payout_monitor.py` + the Telegram ping. Currently a TODO. Components:

1. `mempool_watcher.py` — polls mempool.space `/api/address/<addr>` for each pool address; persists running totals to `data/incoming_ledger.jsonl`
2. `payout_monitor.py` — reads incoming_ledger.jsonl + Phantom-sent ledger; computes hot balance; if ≥ threshold, sends Telegram via `~/.claude/notifier.py`
3. `scripts/monitor.plist` — launchd cron, daily 9am ET
4. `/api/me/payout-status` — JSON for the founder's account page: hot balance, lifetime received, last sweep, recommended sweep amount

Say "build payout monitor" and I'll ship it as the next track. Until then, you can sanity-check Phantom's balance manually whenever you remember — the rest of the pipeline (receive + receipt + email) is fully running already.
