---
description: Unit economics check — anchoring costs, margins, break-even
---

Read CLAUDE.md and inspect `server/engine.py` (look at the
`stamp_to_calendars` / OTS submission path).

Verify and report:

1. **Anchoring strategy in code:** Confirm we are using OpenTimestamps
   calendars (which batch on our behalf) rather than broadcasting our own
   per-file Bitcoin transactions. Quote the exact code path. Per-file BTC
   tx would be a P0 emergency; OTS calendars are free and batched.

2. **Marginal cost per receipt:** Calculate based on current code:
   - On-chain BTC tx fee: $0 (calendars pay it from their aggregated batches)
   - HTTP outbound: 5 POSTs of 32 bytes each = negligible
   - Disk: ~1KB receipt.json + ~1KB × 5 .ots = ~6KB per receipt
   - Server compute: <10ms per anchor request
   Estimate total marginal cost in dollars (likely <$0.001 per receipt).

3. **Gross margin per tier:**
   - Free tier (1/mo): cost ~$0.005, revenue $0 → loss leader (acceptable)
   - $7 one-shot for 10: cost ~$0.05, Stripe fee $0.50, net $6.45 → 92% margin
   - $5/mo unlimited (assume 30 receipts avg): cost ~$0.15, Stripe $0.45,
     net $4.40 → 88% margin
   - $19/mo creator: similar margin, higher AOV
   Show the math.

4. **Break-even volumes:** Fixed costs estimated:
   - Domain: $12/yr
   - VPS or Fly.io: ~$5/mo
   - Email (transactional): $0–10/mo
   - Stripe account: free
   Total fixed: ~$15/mo. How many paying users to cover?

5. **Fee-spike risk:** Since we use OTS calendars (free), Bitcoin fee
   spikes do NOT affect our margin directly. They could affect calendar
   reliability (calendars may delay their batch submissions during
   congestion). Mitigation: 5 independent calendars, so single-calendar
   delays don't break receipts.

6. **Code-level recommendations:** Specific functions to add/change to
   protect margin. Consider:
   - Per-IP rate limiting in `server/app.py` to prevent free-tier abuse
   - Receipt expiry / cleanup for free-tier (keep paid forever, free 30 days)
   - Batch local writes if anchor volume ever exceeds ~10/sec

Save to `docs/audits/economics-$(date +%Y-%m-%d).md`.
