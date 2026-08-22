# Recipient test: agent action record

## Static handoff specimen

- Claimed action text: `sent invoice #42`
- UTF-8 bytes include no trailing newline: recipient must confirm the exact byte convention.
- SHA-256: `<REPLACE_WITH_SHA256>`
- Receipt ID and proof: `<REPLACE_WITH_SUCCESSFUL_RECEIPT_AND_OTS_PROOF>`
- Workflow record: `<REPLACE_WITH_AGENT_RUN_ID_AND_LOCAL_RECEIPTS_JSONL_EXCERPT>`

This file is a template, not evidence that the action occurred.

## Exact proof statement

If the digest matches and the retained OpenTimestamps proof verifies, the
receipt proves that the exact supplied action-text bytes existed no later than
the attesting Bitcoin block. The local JSONL record can show what the CLI wrote
and when it wrote it, subject to the trust placed in that local record.

## Exact non-proof statement

The receipt does **not** prove that an invoice was sent, that invoice 42 exists,
that an agent performed the action, who controlled the agent or signing key,
that the action was authorized, successful, unique, complete, or correct, or
that the local timestamp is a Bitcoin-confirmed event time.

## Acceptance interview script

1. Ask: “What exact bytes are committed, including the newline convention?”
   Accept only an answer that recomputes the digest from the supplied bytes.
2. Ask: “What is the strongest supported time statement?” Accept: “The bytes
   existed no later than the attesting Bitcoin block,” after proof verification.
3. Ask: “Does this show invoice 42 was sent?” Required answer: “No; it commits
   only to a statement that says it was sent.”
4. Ask: “Who performed or authorized the action?” Required answer: “The receipt
   does not establish either fact.”
5. Ask the recipient to identify the receipt/proof status. Fail the test if a
   placeholder, pending calendar response, or local timestamp is called a
   Bitcoin-confirmed receipt.

Acceptance requires all five answers and a recorded digest result. Record the
interview separately; this template does not claim that acceptance occurred.
