---
description: Pricing model audit and recommended tier structure
---

Read CLAUDE.md and any pricing-related code (none yet — flag that as a gap).

Produce a pricing analysis covering:

1. **Current structure assessment:** Is $0 / $7-one-shot-for-10 / $5-mo /
   $19-mo coherent? What signals does it send to the buyer? Tension between
   one-shot impulse buyers and subscription LTV.

2. **Competitor benchmarks:** WordProof (€10–40/mo), OriginStamp (B2B
   bespoke), ScoreDetect (~$12/mo), Bernstein (B2B enterprise). Where do
   we fit? Note OpenTimestamps is $0 and is our biggest pricing pressure.

3. **One-shot vs. subscription LTV math:** Model both for the photographer
   persona. Which produces better 12-month revenue at realistic conversion
   (assume 2% free→paid)?

4. **Recommended tier structure:** Specific tiers with names, prices,
   features, rationale. Include a free tier, an impulse-buy tier, a
   personal subscription, and a creator/prosumer subscription.

5. **Recurring-revenue mechanics:** What product changes turn one-time
   hashing into a subscription? (Folder monitoring, re-verification badges,
   API access, team plans, embeddable verifier widget.)

6. **Payment friction:** Stripe is industry standard but takes 2.9% + 30¢
   which destroys margin on a $7 sale. Compare BTCPay (no fee, self-hosted)
   vs NOWPayments (~0.5% per swap) vs Lightning (millisat fees, sub-$5
   crypto becomes viable). Should we drop on-chain BTC entirely for small
   amounts?

7. **Price-test plan:** Three A/B tests to run in the first 90 days
   post-launch (e.g. $5 vs $7 one-shot, free-tier 1/mo vs 3/mo,
   $5-mo vs $9-mo personal).

Save to `docs/audits/pricing-$(date +%Y-%m-%d).md`.
