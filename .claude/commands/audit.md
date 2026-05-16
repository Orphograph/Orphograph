---
description: Run the full 12-section forensic product audit
---

You are running a forensic product audit. Read CLAUDE.md for full project
context, then inspect the actual codebase (`server/`, `web/`), the landing
page (`web/index.html`), and any recent changes. Do not make assumptions
about features — verify them in the code.

Produce a report covering ALL 12 sections below. Be brutal, specific, and
actionable. Write the report to `docs/audits/audit-$(date +%Y-%m-%d).md`.

## Section 1: What I Actually Have
Inventory every feature, page, flow, and capability. Distinguish "fully
built," "partial," and "claimed but not in code." Identify actual
architecture. Flag anything claimed (privacy, differentiation) that isn't
proven in the code.

## Section 2: What's Missing or Broken
What's missing for a credible launch? For third-party receipt value
(identity binding, independent verifiability, durability)? What's fragile?
What's confusing? What do competitors do that we don't? What would
embarrass us in front of a sophisticated buyer (IP attorney, forensic
auditor, journalist, photographer)?

## Section 3: What Can Be Extracted for Immediate Revenue
Of what's built, what's saleable today? Commodity vs. differentiated?
Best-fit use cases given current state. Buyer segments reachable in 30
days without new building. Minimum viable landing-page narrative.

## Section 4: Pricing Audit
Is the planned $0 / $7-for-10 / $5-mo / $19-mo tier structure coherent?
Compare to WordProof, OriginStamp, ScoreDetect, Bernstein. Best model:
one-shot, credit pack, subscription, freemium, usage? Specific recommended
tiers with rationale. Path to recurring revenue.

## Section 5: Unit Economics
Cost per receipt at current architecture. Anchoring is via OTS (free) —
confirm in code. Stripe/crypto processing impact. Hosting at scale.
Gross margin per tier. Break-even volume.

## Section 6: Landing Page / Onboarding Audit
Is the first 5 seconds clear about who and why? Line-by-line copy rewrites
of `web/index.html`. Missing trust signals (sample receipt, verifier link,
founder identity, social proof). Conversion-killers. Friction in
"drop file → first hash → paid" flow. FAQ gaps.

## Section 7: Differentiation vs. Free Competitors
OpenTimestamps is free. Why pay us? List every plausible differentiator.
Real vs. marketing fluff. The single sharpest wedge for the landing page.

## Section 8: Buyer Segment Match
Rate 1-10 fit for: crypto-curious consumers, photographers/illustrators
(AI scraping fear), indie musicians, freelance designers, journalists,
solo IP attorneys, SMB compliance, notaries, genealogists, whistleblowers.
For top 2: exact landing-page H1 + 3-bullet value prop.

## Section 9: Zero-Budget Distribution Plan
12-week plan: HN Show HN draft + timing, Product Hunt prep, Reddit subs +
cadence, niche communities, cold outreach targets, SEO content plan with
10 article titles, GitHub open-source strategy (the verify_cli.py is the
trust artifact), Twitter build-in-public (only if worth it).

## Section 10: Revenue Projections
Three 12-month MRR projections (conservative / moderate / optimistic).
Monthly numbers with explicit traffic, conversion, and pricing assumptions.

## Section 11: Top 10 Things to Do This Week
Ranked by ROI. Each: action, time required, expected impact, success metric.

## Section 12: Kill Criteria
MRR thresholds at month 3/6/9/12 below which I reduce time invested.
Metrics that signal pivot to B2B. Signals to shut down paid tier.
Weekly hour ceiling at each stage.

## Rules
- No padding, no AI-disclaimers, no hedging.
- Use real dollar figures, percentages, timelines.
- If something is bad, say so plainly.
- If you can't verify from the code, list it as a focused question at the
  end — do not invent.
