# Dataset-provenance — continuous upgrade roadmap

The seed was one thing (a hosted per-folder provenance certificate). Each
shipped item surfaces the next. This file is the live backlog + state for the
autonomous upgrade loop. The loop: pick the top unchecked item → build →
verify (full suite green + a smoke) → commit to `feat/dataset-provenance-certificate`
→ enqueue the paced pusher → check it off here. **Never push master. PRs only.**

## Shipped
- [x] **#0 Seed** — hosted `/certificate/<id>` view + reference CLI (`provenance.py`). _(commit 01a90b6, PR #61)_
- [x] **#1 Drag-and-drop file checker** — drop a file on the certificate; it's hashed locally and matched against the manifest (full Merkle proof when the path is visible). _(commit b2173bf)_
- [x] **#2 `/dataset-provenance` explainer** — public indexable landing page + sitemap + schema.org. _(commit f2d51fa)_
- [x] **#3 Permanent sample folder receipt + live demo link** — `web/sample-folder/` (real Bitcoin-pinned `.ots` for the deterministic `sample-dataset` root) seeded on boot via `_seed_sample_folder_receipt()`; live at `/certificate/DatasetProvenanceSample`, linked from the landing page + README. Surfaced & shipped the **`paths_public`** opt-in (a public folder receipt renders the full manifest instead of redacting paths).

## Next (top = highest leverage)
- [x] **#4 `provenance.py verify --receipt <id>`** — verify a local bundle against the *live* anchored receipt (fetches only the root via `/api/receipt/<id>`; the bundle never leaves). Mutually exclusive with `--cert`, supports `--file` inclusion + `--api`. Smoke covers match/tamper/unknown/both-flags. _(commit pending)_
- [x] **#5 Embeddable dataset badge** — `badge_svg` is now folder-aware (subtitle "dataset · N files · anchored to Bitcoin"; links to `/certificate/<id>` instead of `/r/`), and the certificate page gained a badge-embed block (preview + copy-paste HTML). Single-file badge output unchanged. _(per-certificate OG image split to #16.)_
- [x] **#6 Homepage discoverability** — `/dataset-provenance` now links from the homepage footer ("Made for" column) and contextually from the folder-anchor block ("Anchoring a dataset? →"). Both verified live; suite unchanged (HTML-only).
- [x] **#7 CLI PDF export** — `anchor --pdf` emits `certificate.pdf` via a portable, stdlib-only monospaced→PDF generator (`_text_to_pdf`, base-14 Courier, US-Letter, multi-page) — no cupsfilter/wkhtmltopdf dependency. Verified `file` parses it (correct page counts) and pagination works. _(commit pending)_
- [x] **#8 Accessibility + print QA pass** — certificate page: `scope="col"` on manifest headers, `role="status" aria-live="polite"` on the drop-check + inclusion result regions, `role="alert"` on the error line, and a scoped muted-contrast lift to WCAG AA (#6b665d, ~5.5:1; global token untouched). Landing page already clean (headings hierarchy, lang, no images). Print QA by CSS inspection — no-print covers header/CTAs/share/tools and the manifest expands in print (a headless-browser print can't run in this env). certificate.css → v3. _(commit pending)_
- [x] **#9 Blog post** — `content/blog/prove-what-was-in-your-training-set.md` (markdown post, bare-slug index entry at `/blog/prove-what-was-in-your-training-set`). Honest scope, inbound links to /dataset-provenance + the live sample cert + /verify. Auto-appears in list_posts → dynamic sitemap + atom/rss. predeploy #11 unaffected (bare slug, not .html). _(commit pending)_
- [ ] **#10 MCP tool** — expose dataset anchoring/verification through the existing MCP server so agents can anchor a dataset in-pipeline.
- [ ] **#11 `paths_public` end-to-end** — let owners opt into a public manifest at anchor time: a `--public-paths` flag on `provenance.py` + the folder-anchor UI, plumbed into the stored receipt. (The flag + server honoring it shipped with #3; this wires the producer side + a "paths published by owner" note on the certificate page.)
- [ ] **#12 Certificate page polish for the sample** — when `paths_public`, show a small "the owner has published file paths" affordance; ensure the redacted vs public states both read clearly.
- [ ] **#13 `verify` per-file diff on mismatch** — when the root differs, fetch the public manifest (or use the local cert) and report WHICH files were added/removed/changed, not just "root MISMATCH". Far more actionable for an auditor.
- [ ] **#14 `verify --json`** — machine-readable verdict (root, per-check pass/fail, exit code) so the gate plugs into CI dashboards.
- [ ] **#20 Dataset-provenance blog series** — 2–3 more targeted posts for SEO depth (EU AI Act data-governance records; copyright/consent disputes over training data; provenance as a CI gate), each cross-linking the landing page + live sample.
- [ ] **#19 Site-wide muted-text contrast (founder design call)** — the global `--muted` (#837e75) is ~3.9:1 on cream, failing WCAG AA for small text site-wide. The cert page is now scoped-fixed; a global token bump touches every page's look, so it's a founder design decision — assess/propose, don't unilaterally change.
- [ ] **#18 Richer PDF certificate** — add a letterhead/seal header (and optionally the certified/does-not-certify scope styling) to `_text_to_pdf`, or offer the hosted browser-rendered `/certificate/<id>?print=1` as the styled alternative for users who want the full design.
- [ ] **#17 Dataset-provenance homepage callout** — a small visual section (not just footer/contextual links) near the folder-anchor block that names the use-case and links to `/dataset-provenance`, so it's discoverable above the fold.
- [ ] **#16 Per-certificate dynamic OG share-card** — a route rendering "Dataset Provenance Certificate · <name> · N files" for `/certificate/<id>`. CAVEAT: `og_svg.render_og` emits SVG and many social unfurlers (X, LinkedIn, iMessage) don't render SVG OG images — assess a PNG/raster path first; skip if it needs a heavy rasterizer dependency.

## Rules the loop follows
- One focused improvement per iteration; full test suite must stay green.
- Each iteration runs a real smoke (HTTP probe / hash-match / render) before commit.
- Pushes go through `~/.claude/orpho_paced_push.py` (≤1 per ~15min, never master).
- New ideas that surface get appended here, not silently dropped.
- Stop when every box is checked; leave the PR for founder review/merge.
