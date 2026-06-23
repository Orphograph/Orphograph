---
name: adspirer-growth-brief
description: |
  Plan or execute Orphograph marketing work inside this repo without touching
  the existing Claude plugin files.

  TRIGGER when the user asks to:
    - improve conversion
    - prioritize experiments
    - build a marketing engineering backlog
    - decide what to ship next for acquisition, activation, or upsell

  HARD CONSTRAINTS:
    - Do not edit `.claude-plugin/` or `marketplace/orphograph-plugin/` unless
      the user explicitly requests those files.
    - Ground every claim in the current repo state; do not invent product
      capabilities, legal status, or pricing.
    - Prefer implementable work over abstract decks.
metadata:
  category: marketing
  product: orphograph
---

# adspirer-growth-brief

Use this skill when the user wants a concrete growth plan or wants Codex to
ship the highest-value marketing change next.

## Start here

Read the current product and marketing context before proposing changes:

- `README.md`
- `web/index.html`
- `outbox/FUNNEL_DIGEST_2026-05-24.md`
- `outbox/UPSELL_CTAS_2026-05-22.md`
- `outbox/LP_SCRUB_2026-05-24.md`
- `outbox/BLOG_CONTENT_AUDIT_2026-05-23.md`

## How to operate

1. Identify the KPI or funnel stage the user is implicitly targeting.
2. Inspect the existing copy, UX, and instrumentation in the repo.
3. Produce a short, ranked backlog with the smallest high-leverage changes
   first.
4. If the user expects implementation, make the changes directly in the
   relevant `web/`, `scripts/`, `server/`, `tests/`, `outbox/`, or `outreach/`
   paths.
5. State what was shipped, how it should be validated, and any remaining blind
   spots.

## Default priorities

- Remove friction between first visit and first anchor.
- Surface pricing and plan differences earlier when they are hidden by scroll.
- Tighten proof, privacy, and trust language without turning it into legalese.
- Prefer better instrumentation over hand-wavy opinions when the repo lacks
  evidence.

## Avoid

- Editing Claude plugin assets as part of marketing work.
- Rewriting the product narrative around unsupported promises.
- Generic startup advice that is not specific to Orphograph's current files.
