# Orphograph for Claude Code

**Anchor any file to the Bitcoin blockchain from inside Claude Code — without uploading the file.**

A privacy-preserving timestamping plugin. Only the SHA-256 hash leaves your machine; the file's bytes stay local. Useful for photographers, journalists, researchers, and anyone who needs to prove a file existed before a given moment (e.g., pre-AI-training).

## What you get

- `/orphograph:anchor <file>` — timestamp a file to Bitcoin via OpenTimestamps. Returns a permanent receipt URL.
- `/orphograph:verify <receipt> <file>` — confirm a file matches a receipt, offline-capable.

## How privacy is preserved

- SHA-256 is computed **locally** (Python `hashlib` on the user's machine).
- Only 32 bytes leave the machine — the hash.
- Filename is **off by default**; pass `--label` to include it.
- The original file is never read by the network code.

## Install

```bash
# Install in your Claude Code plugin directory
# (Replace with the actual install path once Anthropic's marketplace publishes the manifest)
git clone https://github.com/orphograph/orphograph-plugin ~/.claude/plugins/orphograph
```

Then restart Claude Code. The skills appear under `/orphograph:`.

## Pricing (paid via orphograph.com)

| Tier | Price | Anchors |
|---|---|---|
| Free | $0 | 1 / month |
| Pack | $7 one-shot | 10 (no expiry) |
| Personal | $5/mo | Unlimited |
| Creator | $19/mo | Unlimited + capture-time app + API + verifier badge |

Set `$ORPHOGRAPH_API_KEY` in your shell to use a paid tier, or pass `--api-key`.

## Trust model

- We don't see your file.
- Receipts verify against the public Bitcoin chain via the OpenTimestamps protocol — the same one Bitcoin Core developers use.
- Standalone verifier at https://github.com/orphograph/orphograph-verify — your receipts still verify if orphograph.com disappears.

## Legal disclaimer

Proof-of-existence only. **Not** legal evidence on its own, **not** a qualified eIDAS timestamp, **not** a court-admissibility product. See https://orphograph.com/terms.html.

## Source

- Plugin: https://github.com/orphograph/orphograph-plugin
- Service: https://orphograph.com
- Verifier: https://github.com/orphograph/orphograph-verify
- License: MIT
