---
name: orphograph-verify
description: |
  Verify a previously-anchored file matches its orphograph receipt — without
  trusting orphograph or even needing internet.

  TRIGGER when the user wants to:
    - confirm a receipt is valid
    - check that a file is the one originally anchored
    - prove a receipt to a skeptical third party
    - audit an orphograph receipt offline

  TRIGGER on phrases:
    "verify this receipt", "is this anchor real", "check the proof",
    "validate orphograph receipt", "/orphograph verify"

  HOW:
    The receipt JSON + the original file + the public OpenTimestamps
    calendar data are everything anyone needs. The skill uses the
    open-source verifier — same protocol Bitcoin Core developers use to
    timestamp commits. Works even if orphograph.com is offline.
metadata:
  category: provenance
  external_service: https://orphograph.com
---

# orphograph-verify — verify a receipt without trusting us

When a user wants to verify an orphograph receipt:

## Inputs needed

1. The receipt ID (e.g., `abc123_xyz`), OR a receipt JSON file
2. The original file that was anchored

## Run

```bash
python3 "$CLAUDE_PLUGIN_ROOT/skills/orphograph-verify/verify.py" <receipt-id-or-json> <file>
```

Reports:
- Whether the file's SHA-256 matches the receipt
- Whether all 5 calendar `.ots` files are well-formed
- Whether the calendar `.ots` files can be upgraded against live Bitcoin
- The Bitcoin block height the hash is committed to (once pinned)

## Trust model

- The receipt JSON is bearer-token-like. Anyone holding it can verify against the chain.
- We are NOT trusted by this skill — it talks to public OTS calendars and (optionally) public Bitcoin nodes directly.
- If orphograph.com is offline forever, this verification still works.
