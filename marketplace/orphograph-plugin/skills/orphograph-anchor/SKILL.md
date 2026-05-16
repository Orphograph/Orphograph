---
name: orphograph-anchor
description: |
  Anchor a file (or any hash) to the Bitcoin blockchain via OpenTimestamps,
  producing a verifiable proof-of-existence receipt.

  TRIGGER when the user asks to:
    - prove a file existed before a date
    - timestamp a file to the blockchain
    - create a pre-AI-era proof of authorship
    - notarize a photo, document, source-code release, or model weight
    - prove provenance for an image, video, audio, or text artifact
    - anchor a hash for later verification

  TRIGGER on phrases:
    "anchor this", "timestamp this file", "prove existence", "pre-AI proof",
    "blockchain notarize", "stamp this to bitcoin", "OpenTimestamps", "/orphograph"

  SKIP when:
    - the user only wants cryptographic hashing without timestamping (just run sha256sum)
    - the user is asking about legal evidence/court admissibility (this is proof-of-existence, NOT legal evidence)
    - the file contains material that should not be associated with a public hash (orphograph's hash is public-ish via the OTS calendars)

  HOW it works (privacy):
    - The file's bytes NEVER upload. Only its 32-byte SHA-256 leaves the
      user's machine. Reconstructing the file from the hash is computationally
      impossible.
    - The filename is optional — default off.
    - The hash is submitted to 5 OpenTimestamps calendars which batch many
      users' hashes into a Bitcoin transaction (~hourly). Marginal cost: ~$0.

  COSTS for the user:
    - Free tier: 1 anchor / month
    - $7 Pack: 10 anchors (no expiry)
    - $5/mo Personal: unlimited
    - The user pays at https://orphograph.com — we don't take credentials here.
metadata:
  category: provenance
  external_service: https://orphograph.com
---

# orphograph-anchor — Bitcoin-anchored timestamping

When a user asks to anchor / timestamp / prove the existence of a file,
follow this flow:

## Step 1 — Confirm what they want anchored

Ask which file or hash. Accept either:
- A file path (preferred) — the skill computes SHA-256 locally
- A pre-computed 64-char hex SHA-256 — useful if the file is on another
  machine or too large to read

## Step 2 — Run the anchor script

The skill ships with `anchor.py`. From the project's CWD:

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/orphograph-anchor/anchor.py" <file-or-hash> [--label "optional label"] [--api-key rk_...]
```

Behavior:
- If the argument is a file path → reads it and computes SHA-256 locally.
- If the argument is 64 hex chars → uses it directly as the hash.
- Includes `--label` only if the user explicitly provided one (privacy default).
- If `--api-key` is set or `$ORPHOGRAPH_API_KEY` is in env → uses paid tier;
  otherwise hits the free tier (1/month rate limit).

The script prints:
- The receipt ID
- The receipt URL (https://orphograph.com/r/<id>)
- The number of calendars that confirmed (5/5 is healthy)

## Step 3 — Report back to the user

Tell them:
1. The receipt URL — they should bookmark it
2. To save the receipt JSON via `curl https://orphograph.com/api/receipt/<id> > receipt.json` so they can verify offline forever
3. That the Bitcoin block pin completes within ~1 hour and the receipt page will then link to mempool.space and blockstream.info

## What this skill is NOT

- Not a legal evidence service. Proof-of-existence ≠ legal admissibility.
- Not encrypting or storing the file. We don't see the file.
- Not exclusive to Orphograph — the same OpenTimestamps proof can be verified
  by any OTS client without us. The receipt JSON + verify_cli.py from
  github.com/orphograph/orphograph-verify is the trust artifact.
