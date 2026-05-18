# HMAC / Secrets Git-History Audit — 2026-05-18

Premortem item: **B-8** (LAUNCH_CHECKLIST.md §"One-time pre-push scrub" + §"Pattern A pseudonymous").

Auditor: read-only Claude subagent. No history-mutating commands executed.

---

## Verdict

**No leak. Git history is clean.** Pattern A (the `rm -rf .git && git init` clean-slate burn at LAUNCH_CHECKLIST.md:184-188) was executed. The `git filter-branch` path at line 167 was therefore moot — the entire pre-launch dev history including any accidentally committed secrets was destroyed before the first `git push` to `github.com/Orphograph/Orphograph`.

Do NOT re-audit this unless something resurrects pre-burn history.

---

## Evidence

| Check | Result |
|---|---|
| Total commits on all refs | **22** |
| Earliest commit | `6c18d58 release: 0.1.0 — empirical notary, Bitcoin-anchored` |
| Author identity on earliest 5 commits | `Orphograph <orphograph@users.noreply.github.com>` (uniform) |
| `git cat-file -t b0009bf` (the pre-burn commit cited in checklist:162) | `fatal: Not a valid object name b0009bf` — gone |
| Remote | `origin = https://github.com/Orphograph/Orphograph.git` |
| Tracked files matching `data/` | **0** (`.gitignore` line 1: `data/`) |

### Per-file history scan (`git log --all --full-history --oneline -- <path>`)

| Path | Commits returned | Status |
|---|---|---|
| `data/.hmac_secret` | (empty) | Never tracked in current history |
| `data/auth_sessions.jsonl` | (empty) | Never tracked in current history |
| `data/auth_tokens.jsonl` | (empty) | Never tracked in current history |
| `data/btc_address.txt` | (empty) | Never tracked in current history |
| `data/cold_wallet_address.txt` | (empty) | Never tracked in current history |

All five queries returned zero commits. Combined with the 22-commit ceiling, the uniform pseudonym authorship from commit #1, and the missing pre-burn SHA `b0009bf`, this is consistent with — and only with — a clean `git init` immediately before the public push. There is no shadow history on `refs/original/`, `refs/stash`, or any other ref reachable via `--all`.

Current working-tree `data/` directory contains the live secret files locally (auth_sessions.jsonl, auth_tokens.jsonl, btc_address.txt, cold_wallet_address.txt, plus ledger / events / receipts), but `.gitignore` rule `data/` keeps every one of them out of the index. `git ls-files data/` returns nothing.

---

## Remediation

**None required.** No secret has been published to `github.com/Orphograph/Orphograph`.

Recommendation: tick the two unchecked boxes at LAUNCH_CHECKLIST.md lines 160 and 180 as **DONE via Pattern A**, with a one-line note pointing to this audit, so the next reviewer doesn't redo the work.

---

## What to do IF this audit ever flips (template for future use)

If a future run of `git log --all --full-history -- data/.hmac_secret` returns any SHA, the secret is in the GitHub object database forever and must be treated as compromised. The founder would then need to, **in this order**:

1. **Rotate first, scrub second** (history rewrite buys nothing if attackers already cloned):
   - Generate a fresh HMAC key on the Fly host: SSH in and `rm data/.hmac_secret`; the next boot writes a new random key. All issued auth tokens will be invalidated — expected.
   - Rotate the BTC payout address: generate new cold-wallet address in the founder's hardware wallet; update `data/cold_wallet_address.txt` on the server only.
   - Invalidate `data/auth_sessions.jsonl` and `data/auth_tokens.jsonl`: `rm` on server; users get logged out (acceptable for a v0.1).
2. **Rewrite GitHub history** (run locally, NOT inside this audit subagent):
   ```
   git filter-repo --invert-paths \
     --path data/.hmac_secret \
     --path data/auth_sessions.jsonl \
     --path data/auth_tokens.jsonl \
     --path data/btc_address.txt \
     --path data/cold_wallet_address.txt
   git push origin --force --all
   git push origin --force --tags
   ```
   `git filter-repo` is the modern replacement for `filter-branch`; install via `brew install git-filter-repo`.
3. **Purge GitHub-side caches**: open a GitHub support ticket asking them to expire the cached objects on `github.com/Orphograph/Orphograph` (force-push alone leaves dangling objects accessible by SHA for ~90 days).
4. **Re-audit** by re-running every command in this file and confirming all five `git log` checks remain empty.

These commands are documented here only as a future-proof runbook. **Do not execute today** — there is nothing to remediate.
