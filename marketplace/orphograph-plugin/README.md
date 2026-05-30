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

The plugin ships inside the public Orphograph repo, which doubles as a Claude
Code plugin marketplace. Two commands:

```
/plugin marketplace add https://github.com/Orphograph/Orphograph
/plugin install orphograph@orphograph
```

The first registers the marketplace from GitHub; the second installs the plugin
(`orphograph` = the plugin name, `@orphograph` = the marketplace name). Verify
with `/plugin list`. The skills then appear under `/orphograph:` — no restart
needed.

> The `https://` URL works everywhere. The shorthand
> `/plugin marketplace add Orphograph/Orphograph` also works but resolves to an
> SSH clone (`git@github.com:…`), so it needs GitHub SSH keys configured — use
> the `https://` form above if you're not sure.

Prefer not to use the marketplace? Clone the repo and point Claude Code at the
plugin subdirectory:

```bash
git clone https://github.com/Orphograph/Orphograph ~/src/orphograph
# then: /plugin marketplace add ~/src/orphograph
```

## Pricing (paid via orphograph.com)

| Tier | Price | Anchors |
|---|---|---|
| Free | $0 | 3 / 24h |
| Writer Pack | $19 one-shot | 10 (no expiry) |
| Standing Order | $9/mo | Unlimited |
| Creator | $19/mo | Unlimited + capture-time app + API + verifier badge |

Set `$ORPHOGRAPH_API_KEY` in your shell to use a paid tier, or pass `--api-key`.

## Trust model

- We don't see your file.
- Receipts verify against the public Bitcoin chain via the OpenTimestamps protocol — the same one Bitcoin Core developers use.
- Standalone MIT verifier ships in the repo (`server/verify_cli.py`) — your receipts still verify if orphograph.com disappears.

## Legal disclaimer

Proof-of-existence only. **Not** legal evidence on its own, **not** a qualified eIDAS timestamp, **not** a court-admissibility product. See https://orphograph.com/terms.html.

## Source

- Plugin + marketplace: https://github.com/Orphograph/Orphograph (this repo, under `marketplace/orphograph-plugin/`)
- Service: https://orphograph.com
- Verifier: the MIT verifier ships in the repo at `server/verify_cli.py`
- License: MIT
