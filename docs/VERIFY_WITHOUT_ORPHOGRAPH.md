# Verify an Orphograph receipt without trusting Orphograph

This is the trustless path. It shows an independent reviewer — an editor, a
lawyer, an auditor, a skeptic — how to confirm that a file existed before a
specific Bitcoin block **without running any Orphograph code and without
contacting orphograph.com.** If Orphograph disappeared tomorrow, every receipt
it ever issued still verifies by this procedure.

The claim Orphograph makes is deliberately narrow: *this exact byte sequence
existed at or before time T.* It does **not** assert authorship, human-vs-AI
origin, or legal authenticity. This guide verifies exactly that narrow claim,
against Bitcoin's chain, using only open tools.

---

## What you have

An Orphograph receipt is just files — no proprietary format:

- `receipt.json` — the anchored SHA-256 (and a SHA-512 sibling), a timestamp, and metadata.
- five `.ots` files (`a.ots`, `alice.ots`, `b.ots`, `btc.ots`, `finney.ots`) — standard
  [OpenTimestamps](https://opentimestamps.org/) proofs, one per calendar.

Get the bundle by either:

```bash
# from the service (or any mirror of it)
curl -O https://orphograph.com/api/receipt/<RECEIPT_ID>.zip && unzip <RECEIPT_ID>.zip
```

or straight off an Orphograph USB drive: `.orphograph/receipts/<RECEIPT_ID>/`.

You also need the **original file** if you want to prove *this file* (not just
"some file") produced the receipt.

---

## Three levels of trust

### Level 1 — read the receipt page (trusts Orphograph to display honestly)
Open `https://orphograph.com/r/<RECEIPT_ID>`. Fine for everyday use; it trusts us
to show the truth. The next two levels do not.

### Level 2 — the bundled MIT verifier, offline (trusts open code you can read)
A ~100-line stdlib Python file, no dependencies, no network. It re-hashes your
file, confirms the hash matches the receipt, and checks every `.ots` is
well-formed and commits the same hash:

```bash
python3 server/verify_cli.py <RECEIPT_ID>/receipt.json --file path/to/original-file
```

Expect: `file matches: YES`, `sha-512 match: YES`, and all five `.ots` `[OK]`.
This trusts only code you can read — not Orphograph's servers. (Tamper with the
file by one byte and `file matches` flips to `NO`.)

### Level 3 — the official OpenTimestamps client, against Bitcoin (trusts only Bitcoin + audited OSS)
This is the strongest level. It uses the **upstream** OpenTimestamps reference
client — software maintained by the OpenTimestamps project, not by Orphograph —
to verify the `.ots` proofs directly against the Bitcoin blockchain. No
Orphograph code is involved at all.

```bash
pip install opentimestamps-client          # the upstream reference client

# 1. Upgrade the calendar-pending proofs to full Bitcoin attestations.
#    (Fresh receipts are calendar-committed; ~1 hour later the calendars
#     have folded the commitment into a Bitcoin transaction. `ots upgrade`
#     fetches that block attestation and rewrites the .ots in place.)
ots upgrade <RECEIPT_ID>/*.ots

# 2. Verify a proof against the ORIGINAL file, directly against Bitcoin.
ots verify -f path/to/original-file <RECEIPT_ID>/btc.ots
```

`ots verify` re-hashes your file, walks the proof's Merkle path to the Bitcoin
block header, and reports the block height and the UTC time of that block:

```
Success! Bitcoin block <N> attests existence as of <UTC timestamp>
```

For the strongest possible assurance, run `ots verify` against **your own
Bitcoin node** (`ots --bitcoin-node ... verify ...`) so you trust no third party
for the block data either — only Bitcoin's consensus.

What each level trusts:

| Level | Trusts | Needs Orphograph? |
|---|---|---|
| 1 | Orphograph to display honestly | yes |
| 2 | open MIT code you can read | no |
| 3 | Bitcoin consensus + audited OpenTimestamps OSS | **no** |

---

## What "verified" proves — and does not

- **Proves:** the file's exact bytes existed at or before the Bitcoin block the
  proof attests. The block time is a conservative upper bound on existence
  (Bitcoin timestamps are monotonic and economically expensive to forge).
- **Does not prove:** who created the file, whether a human or a model produced
  it, or that it is legally authentic. It is proof-of-existence-in-time, nothing
  more. Pair it with a qualified trust-service-provider timestamp if you need a
  legally binding (e.g. eIDAS) one; Orphograph is complementary to, not a
  replacement for, those.

---

## If you only remember one command

```bash
pip install opentimestamps-client && ots upgrade <id>/*.ots && ots verify -f <file> <id>/btc.ots
```

That single line takes a receipt and an original file and confirms, against
Bitcoin, the moment the file is proven to have existed — with zero dependence on
Orphograph.
