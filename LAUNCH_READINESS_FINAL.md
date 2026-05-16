# Orphograph Launch Readiness — Final Sanity Sweep

**Date:** 2026-05-15
**Sweep range:** commits `21812b4..HEAD` (13 commits)

## Checklist

### 1. Test suite green
- [x] **PASS** — `329 passed in 18.80s` (target: 329+)

### 2. Python compile clean
- [x] **PASS** — `py_compile server/*.py scripts/*.py` produced zero output.

### 3. No secrets in git
- [x] **PASS** — Only matches are in `scripts/launch.sh`, `scripts/stripe_bootstrap.sh`, `scripts/stripe_listen.sh` and they are **placeholder strings / shell prompts / docstring patterns** (`sk_live_xxx`, the literal string `whsec_...`, regex prefixes `sk_live_*)`). No actual key material. Treated as PASS.

### 4. No Hydroboro lineage in product code
- [x] **PASS** — All grep hits are in `deploy/*.md` (planning/runbook docs) and `scripts/publish_safety_check.sh` (the *deny-phrase scanner itself*, which legitimately contains the regex). **Zero** matches in `server/`, `web/` (excluding blog/lp), or `dist/`.

### 5. Stripe webhook fails closed
- [x] **PASS** — `server/app.py:1734-1746` confirms: if `STRIPE_WEBHOOK_SECRET` is unset and `ALLOW_UNSIGNED_WEBHOOK_PROBE` is false → returns **503**. The 200 path is dev-only and gated by an explicit env opt-in (`ORPHO_ALLOW_UNSIGNED_WEBHOOK_PROBE`, which CLAUDE.md says must not be set on Fly). Production posture is correct.

### 6. Privacy invariants intact
- [x] `truncate_ip` — `server/app.py:58, 348, 359` (imported from rate_limit, used on peer address)
- [x] `email_id` — `server/auth.py:82` (HMAC-keyed function defined)
- [x] `__Host-` prefix — `server/auth.py:270-281`, `server/app.py:364`
- [x] `Content-Security-Policy` — `server/app.py:126`

### 7. No tracked data/ runtime files
- [x] **PASS** — `git ls-files data/ | grep -v '\.gitkeep$'` returned empty.

### 8. CLAUDE.md kill-criteria sanity (diff 21812b4..HEAD)
- [x] **No file uploads.** Only `arrayBuffer` uses in the diff are:
  - `fetch(url).arrayBuffer()` for **remote URL hashing** (downloads bytes to hash client-side, no POST back)
  - `slice.arrayBuffer()` for **local EXIF parsing** in the browser
  No code path POSTs file bytes to our server.
- [x] **No per-receipt BTC fee.** No matches for `raw_tx`, `broadcast`, or `bitcoin rpc` in the diff. Anchoring stays via OTS calendar batching (principle #2 intact).
- [x] **"Court-admissible" / "legally binding" copy.** All hits are in **disclaimer/negation contexts**:
  - `server/receipt_export.py:89` — `"Is not court-admissible legal evidence"` (disclaimer)
  - `web/compare.html:127` — comparison column showing what *competitors* claim
  - `web/about.html:113` — `"We don't claim 'legally binding' or 'court-admissible.'"`
  - Blog posts under `web/blog/` — explicitly debunking the phrase
  No affirmative legal-evidence claims found.

### 9. Final commit history sane
- [x] **PASS** — 13 commits, all conventional-commit prefixed (`feat:`, `fix:`, `security:`, `docs:`, `test:`, `security+fix:`, `test+ux:`). None are `WIP`, `test commit`, or placeholders. Most recent: `4fc0e5b test+ux: Public config, receipt export, subscription inheritance + EXIF error surfacing`.

### 10. Smoke test the static probe
- [x] **PASS** — `python3 scripts/all_endpoints_probe.py --help` printed full help text, no syntax error.

---

## Summary

**10 / 10 checks PASS. Launch-ready from a code-integrity standpoint.**

The 13-commit run from `21812b4..HEAD` does not violate any of the six non-negotiable principles in `CLAUDE.md`:
1. Files never touch the server — confirmed (only remote-URL hashing and local EXIF reads in the diff).
2. Anchoring stays batched / free — no per-receipt BTC broadcast paths added.
3. Receipts verify without us — no changes to `verify_cli.py` semantics.
4. Y3-band override is in force — feature breadth expansion (export, gifting, teams, extension, Lightroom plugin) is on-policy.
5. Honest copy — every "court-admissible" / "legally binding" hit is a disclaimer or competitor-comparison.
6. Zero Hydroboro lineage in product code — confirmed; planning docs in `deploy/` reference Hydroboro in roadmap/lineage-firewall context only, which is the intended use.

**Remaining external (non-code) gates per memory index** (`project_orphograph_launch_state.md`): GitHub push, Stripe live keys, Resend live keys, Fly deploy, founder interviews, Show HN.

**No code-side blockers identified.**
