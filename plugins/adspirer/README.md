# Adspirer for Codex

`Adspirer` is a Codex-native plugin for marketing work inside the `orphograph`
repo.

It is intentionally separate from the Claude marketplace files under
`.claude-plugin/` and `marketplace/orphograph-plugin/`. Its job is to help
Codex work on landing pages, funnel instrumentation, and outreach material
without rewriting the Claude plugin surface.

## Skills

- `adspirer-growth-brief` — prioritize marketing engineering work for
  Orphograph using the current repo state.
- `adspirer-web-funnel` — improve landing pages, pricing surfaces, funnel
  tracking, and conversion UX.
- `adspirer-outreach-ops` — draft or refine outreach assets while respecting
  the founder's send and compliance rules.

## Install in Codex

From the repo root:

```bash
codex plugin marketplace add .
codex plugin add adspirer@orphograph
```

If the `orphograph` Codex marketplace is already configured, only the second
command is needed.

## Guardrails

- Do not edit `.claude-plugin/` or `marketplace/orphograph-plugin/` unless the
  user explicitly asks for it.
- Reuse claims already supported by `README.md`, `web/`, `outbox/`, and
  `outreach/`.
- Keep privacy, provenance, and legal wording consistent with the existing
  product surfaces.
