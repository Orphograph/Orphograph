# ADR 0003 — Use the product on itself for IP protection

**Status:** Accepted
**Date:** 2026-05-18

## Context

The trademark and patent system requires evidence of priority. The
USPTO accepts dated documents, the courts accept witnessed
declarations, and the AIA grace-period rules permit a 12-month window
from public disclosure to file. All of these are stronger when the
date of disclosure is itself unfalsifiable.

The founder cannot afford USPTO filings at present. The brand,
however, is in commercial use today and the protocol is publicly
disclosed today. Both have priority dates of today. The question is:
how is that priority recorded so that it remains useful nine, twelve,
or twenty-four months from now when filings become possible?

## Decision

Every legally-relevant artifact is anchored using the office's own
notary protocol on or near the day of first commercial use or
disclosure.

This includes:
- The MIT LICENSE
- The MCP server source
- The MCP README and landing page
- The architecture publication (`/web/method/architecture.html`)
- The brand assets (seal, favicon, wordmark, OG card)
- The comparison page against C2PA
- Outbound drafts (HN launch, Anthropic outreach) — drafted dates
  themselves are evidentiary
- The launch record narrative document

Each anchor produces a receipt that commits to the Bitcoin chain
within approximately one hour. The receipt list is preserved in
`outbox/LAUNCH_RECEIPTS.json` and the narrative index in
`outbox/LAUNCH_RECORD_2026-05-18.md`.

## Consequences

**Positive.**
- Trademark priority date (first commercial use) is documented with a
  Bitcoin-anchored timestamp that any later opposer cannot dispute.
- Patent grace-period priority (under the AIA, 12 months from public
  disclosure) is documented identically.
- Copyright authorship date on each artifact is recorded the same way.
- The office uses its own product. The dogfooding is the strongest
  trust signal available.

**Negative.**
- None substantial.

## Re-anchoring policy

A weekly cron re-anchors:
- `git rev-parse HEAD`
- The LICENSE file
- The current versions of the brand assets
- The architecture publication

Each run produces a fresh receipt; the receipt history accumulates
into a Bitcoin-anchored evidence chain of continuous authorship.

The cron script is at `scripts/weekly_anchor.py`. The launchd plist
template is at `scripts/com.orphograph.weekly_anchor.plist.template`.
