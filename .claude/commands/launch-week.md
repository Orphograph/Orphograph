---
description: Generate the next 7-day launch task list, ranked by ROI
---

Read CLAUDE.md and the most recent audit in `docs/audits/`. If none exists,
say so and recommend running `/audit` first.

Produce a 7-day task list for the founder (human-only) and a parallel
7-day task list for Claude Code to execute autonomously where safe.

Format:

## Founder tasks (human-only)
For each: title, time estimate, why it matters, success metric.
Focus on: customer conversations, content creation, outreach, decisions
only the founder can make (brand name, domain purchase, Stripe activation,
legal entity, persona interviews).

## Claude Code tasks (code-only)
For each: title, files affected, acceptance criteria, test plan.
Focus on: bug fixes, feature implementation from approved decisions,
test coverage, refactors with measurable wins.
Reject anything that conflicts with the non-negotiable principles in
CLAUDE.md.

## What we are NOT doing this week
List feature requests / shiny objects explicitly deferred, with the
threshold that would unlock them later (e.g. "branded PDF receipts —
deferred until 10 paying customers per Principle #4").

Save to `docs/weeks/week-$(date +%Y-%m-%d).md`.
