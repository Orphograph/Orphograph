---
name: adspirer-web-funnel
description: |
  Improve Orphograph landing pages, pricing surfaces, and funnel
  instrumentation from inside Codex while preserving the current visual and
  trust language.

  TRIGGER when the user asks to:
    - improve a landing page or pricing page
    - add or fix conversion tracking
    - reduce funnel drop-off
    - rewrite hero copy, CTA copy, or upsell sections

  HARD CONSTRAINTS:
    - Keep the existing institutional / serif visual language unless the user
      asks for a redesign.
    - Preserve the supported privacy and provenance claims already present in
      the repo.
    - Do not edit `.claude-plugin/` or `marketplace/orphograph-plugin/`.
metadata:
  category: marketing
  product: orphograph
---

# adspirer-web-funnel

Use this skill for web conversion work that Codex can directly implement.

## Inspect first

- `web/index.html`
- `web/index.css`
- `web/buy.html`
- `web/buy.css`
- `web/landing.js`
- `web/app.js`
- `outbox/FUNNEL_DIGEST_2026-05-24.md`
- `outbox/WEB_WIRING_2026-05-22.md`

## Working rules

1. Keep the product promise precise:
   file bytes stay local; only hashes leave the machine.
2. Keep legal posture precise:
   proof-of-existence is not the same as court admissibility or a qualified
   timestamp.
3. When editing copy, prefer clearer framing over louder framing.
4. When editing UI, preserve the established Orphograph design language unless
   the user asks for a broader visual departure.
5. When the repo lacks instrumentation for a hypothesis, add the tracking
   before claiming a result.

## Typical outputs

- Hero, CTA, pricing, or FAQ copy changes
- Funnel event wiring or cleanup
- New trust, proof, or upsell modules in `web/`
- Validation notes tied to concrete files and events
