---
name: adspirer-outreach-ops
description: |
  Draft or refine Orphograph outreach assets while respecting the founder's
  manual-send, compliance, and brand rules.

  TRIGGER when the user asks to:
    - write cold outreach
    - prepare launch copy
    - draft social posts, email templates, or founder distribution assets
    - adapt an existing outreach template to a new segment

  HARD CONSTRAINTS:
    - No automated outbound send flow. The founder clicks send manually.
    - Respect the current STOP / compliance language and repo send rules.
    - Do not edit `.claude-plugin/` or `marketplace/orphograph-plugin/`.
metadata:
  category: marketing
  product: orphograph
---

# adspirer-outreach-ops

Use this skill when the user wants outbound or launch collateral that should
fit Orphograph's current operating rules.

## Inspect first

- `outreach/README.md`
- `outreach/SEND_CHECKLIST.md`
- `outbox/COLD_OUTREACH_README.md`
- `outreach/cold_email_writers.md`
- `outreach/cold_email_photographers.md`
- `outbox/product_hunt_launch.md`
- `outbox/founder_x_thread.md`

## Working rules

1. Reuse the current segment framing before inventing new vertical language.
2. Keep claims concrete and supportable by the product and public site.
3. Preserve opt-out and sender-identification requirements in outbound copy.
4. Put deliverables in `outreach/` or `outbox/` unless the user specifies a
   different destination.
5. Keep the office voice measured; avoid hype, fear, or unsupported urgency.

## Good outcomes

- A new segment-specific email variant
- A refined launch post tied to the current product positioning
- A cleaned-up outreach runbook or send checklist
- A follow-up sequence that stays within the repo's manual-send rules
