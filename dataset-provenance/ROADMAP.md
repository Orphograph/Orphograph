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

## Next (top = highest leverage)
- [ ] **#3 Permanent sample folder receipt + live demo link** — commit a non-pruning folder-receipt fixture (like `web/sample/` for single files) so the landing page + README can link a live `/certificate/<id>` example without the free-tier prune trap. Wire the link in once it exists.
- [ ] **#4 `provenance.py verify --receipt <id>`** — verify a local bundle against the *live* anchored manifest (`/api/verify_folder/<id>`), no local certificate file needed. Closes the verify loop for a bundle that's already anchored.
- [ ] **#5 Dataset badge + per-certificate OG image** — reuse `badge_svg`/`og_svg` to emit an embeddable "dataset anchored · N files" badge and a richer share-card for `/certificate/<id>`.
- [ ] **#6 Homepage discoverability** — surface `/dataset-provenance` from the homepage (footer + a folder-anchor section link).
- [ ] **#7 CLI PDF export** — `provenance.py` writes a PDF certificate (or wires the hosted `/certificate/<id>?print=1` path), so the artifact is one file to hand an auditor.
- [ ] **#8 Accessibility + print QA pass** — cert + landing pages: aria/focus/contrast, and verify the certificate prints to a clean one-document PDF.
- [ ] **#9 Blog post** — "Prove what was in your training set, and when" via the existing blog system (SEO content + inbound links).
- [ ] **#10 MCP tool** — expose dataset anchoring/verification through the existing MCP server so agents can anchor a dataset in-pipeline.

## Rules the loop follows
- One focused improvement per iteration; full test suite must stay green.
- Each iteration runs a real smoke (HTTP probe / hash-match / render) before commit.
- Pushes go through `~/.claude/orpho_paced_push.py` (≤1 per ~15min, never master).
- New ideas that surface get appended here, not silently dropped.
- Stop when every box is checked; leave the PR for founder review/merge.
