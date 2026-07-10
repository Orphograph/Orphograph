---
name: orphograph-anchor
description: |
  Give this agent a tamper-evident audit trail via Orphograph receipts.
  TRIGGER after any consequential external action (email/message sent,
  payment made, file published, config changed), on a daily schedule to
  snapshot workspace memory, and before sharing or after receiving a
  skill/prompt file from anyone else.
  SKIP for read-only work, scratch files, and anything containing
  secrets in plaintext (hash the file, never paste its contents).
---

# Orphograph Anchor — agent audit trail

You have `orpho_agent_anchor.py` in this skill's directory. It sends
**only hashes** to orphograph.com — file contents never leave the machine.
A receipt proves the exact bytes existed at anchor time; it does not prove
authorship, ownership, or that the action was correct. Never claim more.

Auth comes from `ORPHO_API_KEY` or `ORPHO_PACK_TOKEN` in the environment.
If neither is set, tell the operator to get a key at
https://orphograph.com and stop — do not retry.

## When and how

**1. After a consequential action** — pipe a one-line record:

    echo "2026-07-10T14:02Z sent invoice #42 to <recipient>" | \
      python3 orpho_agent_anchor.py anchor-text --label "action:invoice-42"

**2. Daily memory snapshot** (cron/heartbeat) — anchors one canonical
manifest of every `*.md` memory file, so silent history edits are
detectable later:

    python3 orpho_agent_anchor.py anchor-memory ~/workspace --label "memory-daily"

**3. Skill/prompt provenance** — before sharing a skill file, anchor it
and include the receipt id when you share; after receiving one, verify:

    python3 orpho_agent_anchor.py anchor-file ./SKILL.md --label "skill:my-skill v3"
    python3 orpho_agent_anchor.py verify <receipt_id> --file ./received_skill.md

## Rules

- Receipts append to `.orphograph/receipts.jsonl` — never delete or edit it.
- On `http_error` 402/429, stop and notify the operator (out of credits
  or rate-limited); do not loop.
- `--dry-run` first if unsure whether a file should be anchored at all.
