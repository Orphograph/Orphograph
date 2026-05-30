# Publishing the Orphograph Claude Code plugin

> **⚠️ SUPERSEDED (2026-05-30).** This runbook described publishing the plugin
> to a *separate* `orphograph/orphograph-plugin` repo. That approach is retired.
> The plugin now ships **inside the main repo** (`Orphograph/Orphograph`), which
> doubles as a Claude Code marketplace via the root `.claude-plugin/marketplace.json`.
> No separate repo, no `gh repo create`. Current end-user install:
>
> ```
> /plugin marketplace add https://github.com/Orphograph/Orphograph
> /plugin install orphograph@orphograph
> ```
>
> See `marketplace/orphograph-plugin/README.md` for the canonical install
> instructions. The historical steps below are kept for reference only — the
> `git init` / separate-repo / `git clone <plugin-repo>` parts no longer apply.

**Outcome (historical):** the plugin at `marketplace/orphograph-plugin/` is
installable by any Claude Code user in two commands.

This doc is **founder-action**. I can't run `gh` against your account.

---

## 1. Pre-flight (one minute)

Make sure the local plugin tree compiles and the scripts run.

```bash
cd ~/orphograph

# Syntax check
python3 -m py_compile marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py
python3 -m py_compile marketplace/orphograph-plugin/skills/orphograph-verify/verify.py

# Smoke test the anchor flow against the live local server
echo "hello world" > /tmp/anchor-smoke.txt
python3 marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py \
  /tmp/anchor-smoke.txt --endpoint http://127.0.0.1:8989 --json
```

Expected: JSON with `"ok": true`, a `receipt_id`, and `calendars_ok` between 1 and 5.

---

## 2. Publish in 4 commands (three minutes)

```bash
cd ~/orphograph/marketplace/orphograph-plugin

# Initialize the plugin repo
git init -b main
git add .
git commit -m "Orphograph plugin v0.1 — Bitcoin-anchored file timestamping for Claude Code"

# Create the GitHub repo + push (uses your existing gh auth)
gh repo create orphograph/orphograph-plugin \
  --public \
  --description "Anchor files to Bitcoin from inside Claude Code. Privacy-by-construction — files never upload, only their SHA-256." \
  --homepage "https://orphograph.com" \
  --source=. \
  --remote=origin \
  --push
```

If `orphograph/` org doesn't exist yet, replace with `gh repo create <your-username>/orphograph-plugin ...`.

---

## 3. Add install instructions to the public README

The README already has a section. After the repo is live, edit:

```bash
cd ~/orphograph/marketplace/orphograph-plugin
# Edit README.md: replace the install placeholder with the verified URL
# Then push
git add README.md
git commit -m "docs: pin install URL"
git push
```

End-user install becomes:

```bash
# Clone the plugin into Claude Code's plugin directory
git clone https://github.com/orphograph/orphograph-plugin ~/.claude/plugins/orphograph

# Restart Claude Code. Skills appear under /orphograph:
```

---

## 4. Submit to Anthropic's plugin discovery (when published)

As of May 2026, Anthropic's plugin marketplace flow is:

1. The `.claude-plugin/plugin.json` manifest must be at the repo root (it is).
2. The repo must be public on GitHub (it will be after step 2).
3. Submit the repo URL to https://claude.com/code/plugins/submit (if open) or via a PR to the community plugin index repository if Anthropic maintains one.

Check both at publish time — the discovery surface evolves. If neither is live yet, distribution is still effective via the install command above + the AI-transparency article (`deploy/ARTICLE_WRITTEN_BY_AN_AI.md`).

---

## 5. Announce (after the repo is live)

Three places, in order of leverage:

1. **The AI-transparency article** (`deploy/ARTICLE_WRITTEN_BY_AN_AI.md`) — mentions the plugin near the end. Publishing the article *with* a working plugin link is the right sequencing.

2. **r/ClaudeAI subreddit + r/AIToolDeveloperSupport** — Anthropic plugin community lives there. Post format: "[Plugin] Orphograph — anchor files to Bitcoin from inside Claude Code, files never upload."

3. **X / Twitter thread tagging @AnthropicAI** with the install one-liner as tweet 1, privacy architecture as tweet 2, free tier offer as tweet 3.

---

## 6. Verify the plugin loads correctly

After installation in any Claude Code session:

```
/help
```

Should list `/orphograph:anchor` and `/orphograph:verify` under user-invocable skills.

Try:
```
/orphograph:anchor /path/to/any/file.txt
```

Expected: anchor.py runs, prints the receipt URL, calendars_ok = 5/5.

---

## 7. Rollback (if something breaks publicly)

```bash
cd ~/orphograph/marketplace/orphograph-plugin
gh release delete <bad-version> --yes
git revert HEAD
git push
```

Or — for a hard takedown:

```bash
gh repo edit orphograph/orphograph-plugin --visibility private
```

Visibility flip is reversible. Deletion is not. Default to flip-to-private over delete.

---

## What this gets you

**Distribution channel #1.** Every Claude Code user who installs the plugin becomes a potential orphograph.com customer (free anchor on first run; pays for a Pack when they exceed the free tier). The "I'm an AI tool that helps with proof-of-existence" framing rides directly on the AI-transparency narrative from the article.

**Marketing surface.** Every time the skill renders its description in someone's `/help`, your brand appears. Skill descriptions are durable advertising — they live in the user's environment until they uninstall.

**Credibility flywheel.** Open-source plugin + open-source verifier + closed-source service. The trust chain is `we don't see your file → here's the code that proves it → here's the chain that anchors it`. Each layer is third-party-checkable. This is exactly the trust posture the AI-transparency article argues for.
