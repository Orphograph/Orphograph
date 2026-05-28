---
title: Reading an .ots file by hand — a forensic walkthrough
slug: reading-ots-file-by-hand
date: 2026-05-14
author: Orphograph
summary: A byte-by-byte walkthrough of what's actually inside an OpenTimestamps proof file. Useful if you're an investigator, a journalist verifying a tip, or anyone who wants to confirm the math is real and not just a logo.
tags: [opentimestamps, forensics, cryptography]
---

# Reading an .ots file by hand — a forensic walkthrough

You can verify an OpenTimestamps proof file with the official CLI
in one command. But if you've ever wanted to look inside an .ots
file and confirm the math is real — not just a logo on a website
that claims to verify it — this post walks through the bytes one
at a time.

Useful if you're:

- A journalist verifying a tipster's anchored evidence
- A forensic analyst on a disputed-image case
- A photographer who wants to see exactly what you're paying for
- An auditor confirming Orphograph's claims aren't marketing
- Curious about how Bitcoin-anchored timestamping actually works

We'll dissect the sample receipt that ships with our open-source
verifier. You can follow along by downloading it:

```bash
curl -O https://orphograph.com/verify/examples/sample/a.ots
```

That's a real .ots file from a real anchor we made on 2026-05-12.
Let's see what's in it.

## The file format

OpenTimestamps files are binary. They follow a documented format
([spec on github.com/opentimestamps](https://github.com/opentimestamps/python-opentimestamps))
that's deliberately simple — three sections:

1. **Magic header** (15 bytes) — identifies the file as an OTS proof
2. **The hash being attested** (SHA-256, 32 bytes)
3. **The merkle path** from that hash up to a Bitcoin transaction
   (variable length; depends on tree depth)

The first two are trivial to read. The third is where the
cryptographic magic happens.

## Section 1 — the magic header

Open the file in `xxd` or any hex viewer:

```bash
xxd a.ots | head -1
```

The first 18 bytes are:

```
00 4f 70 65 6e 54 69 6d 65 73 74 61 6d 70 73 00 00 50
```

Decode that as ASCII:

```
.OpenTimestamps..P
```

Specifically:

- Byte 0: `\x00` (null byte — explicit "this is a binary file" marker)
- Bytes 1–14: `OpenTimestamps` (literal ASCII)
- Bytes 15–16: `\x00\x00` (separator)
- Byte 17: `P` (start of "Proof" string)

The full magic constant is:

```
\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94
```

That last 8-byte sequence (`\xbf\x89...`) is a version + format
identifier — it tells parsers exactly which OTS spec version this
file conforms to. As of 2026, there's only one version in the
wild.

If a file claims to be an .ots proof but doesn't start with this
exact sequence, it isn't one. Our open-source verifier checks for
exactly this magic before doing anything else.

## Section 2 — the hash being attested

Byte 25 of the file is the SHA-256 tag (`\x08`), followed by 32
bytes — the actual SHA-256 hash of the file you anchored.

For the sample receipt, that hash is:

```
7accf9e90453280e6fb081fd9d83dfb1eeef3bd64e0d680826989ba79bccac88
```

You can verify by computing the SHA-256 of the sample's original
file:

```bash
curl -O https://orphograph.com/verify/examples/sample/sample.txt
shasum -a 256 sample.txt
# 7accf9e90453280e6fb081fd9d83dfb1eeef3bd64e0d680826989ba79bccac88
```

That's a real hash of a real file matching what's embedded in the
.ots proof. No mystery.

This is the **input** to the OTS proof. The .ots file's job is to
prove that this 32-byte fingerprint existed in Bitcoin's blockchain
at a specific time.

## Section 3 — the merkle path

Everything after byte 57 is the merkle path. This is where the
proof structure lives.

Each step in the path is one of:

- **SHA-256 op** (`\x08`) — "hash whatever's accumulated so far with SHA-256"
- **Append** (`\xf0 <length> <bytes>`) — "concatenate these bytes to what we have"
- **Prepend** (`\xf1 <length> <bytes>`) — "stick these bytes BEFORE what we have"
- **Calendar attestation** (`\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e <length> <calendar-URL>`) — "this hash got submitted to this calendar; ask them for the upgraded proof"
- **Bitcoin block attestation** (`\x05\x88\x96\x0d\x73\xd7\x19\x01 <varint-block-height>`) — "this hash is incorporated in the Bitcoin block at height N"

Walking the path means: start with your file's hash, follow each
op, accumulating values, until you arrive at a Bitcoin block
header's merkle root.

For our sample file, the calendar attestation reads:

```
.-https://alice.btc.calendar.opentimestamps.org
```

That's saying: "I submitted this hash to alice's calendar. If
you want the full Bitcoin merkle proof, ask that calendar for
the upgraded version."

When the calendar batches your hash with thousands of others into
a Bitcoin transaction (which it does roughly once per hour), the
upgraded .ots file gets extended with:

1. The merkle path WITHIN the calendar's batch (from your hash
   to their root)
2. The Bitcoin transaction ID containing that root
3. The Bitcoin block height where that transaction landed
4. The merkle path WITHIN that block (from the calendar's root
   transaction up to the block header's merkle root field)

After that "upgrade" runs, the .ots file is self-sufficient — no
calendar is needed to verify, just the public Bitcoin chain.

You can trigger the upgrade yourself:

```bash
pip install opentimestamps-client
ots upgrade a.ots
```

Then `xxd a.ots | tail` will show the new bytes that got appended.

## Section 4 — what the math actually proves

Once an .ots file has been upgraded to include the Bitcoin block
attestation, here's what its math proves:

Given:
- The file you originally anchored (you have this)
- The .ots proof file (you have this)
- The public Bitcoin blockchain (anyone has this)

You can recompute, deterministically:

1. SHA-256(your file) → must equal the hash in the .ots
2. Walking the merkle path in the .ots → must equal the merkle
   root of the Bitcoin block at the specified height
3. The Bitcoin block at that height → must be in the longest
   chain (you can check this against any Bitcoin node, including
   public block explorers like [mempool.space](https://mempool.space))

If all three are true, the proof holds. The file existed at the
timestamp of that Bitcoin block. To forge the proof, an attacker
would need to:

- Find a SHA-256 collision (preimage: 2^128 operations under
  Grover's algorithm; classical: not done in public)
- AND rewrite the Bitcoin block that contains the attestation
- AND rewrite every Bitcoin block after it to maintain the
  longest-chain property

The economics of that attack are well-documented. As of 2026,
attacking even a single Bitcoin block from years ago would
require sustained 51% hash-rate dominance for the entire interval
since — measured in tens of billions of dollars of mining
infrastructure running flat-out for years.

That's the security backing every .ots proof.

## Reading our sample, end-to-end

For the sample file we shipped:

1. **Magic header** ✓ matches the OTS magic constant
2. **Hash** ✓ matches SHA-256 of `sample.txt`
3. **Merkle path** ends with a calendar attestation (the sample
   was anchored less than an hour before this post was written;
   the BTC upgrade hadn't completed yet at write-time)
4. **After `ots upgrade`** ✓ the file gains the full Bitcoin
   block attestation

Run our verifier:

```bash
python3 verify.py examples/sample/receipt.json --file examples/sample/sample.txt
```

You should see five `[OK]` lines for the five calendars, plus a
"file hash matches" line. The verifier is 100 lines of Python; if
you don't trust our claims, **read the verifier source code**
(`verify.py` in the same tarball).

## Why this matters

Most digital-evidence systems sell trust. Orphograph sells
math-you-can-verify. You don't have to take our word for anything
— including the claim that we're not lying to you. The .ots file
is auditable, the verifier is open source, the protocol is public,
and the underlying Bitcoin chain is replicated across the
internet.

If something on the verification path ever stops adding up — your
hash, the merkle path, the Bitcoin chain — the cryptographic
contract is broken and we want to hear about it. Our security
disclosure address is in the README; PGP keys available on
request.

For 99% of users, "click verify" is enough. For the 1% who need
to confirm the math is real, this post is a starting point.
Welcome to the rabbit hole.

---

*Anchor a file at [orphograph.com](/). Verify any receipt with
the open-source verifier at [/verify/](/verify/) — no Orphograph
account, no Python install required if you use the bundled
sample's pre-upgraded receipts. $19 for a Writer Pack of 10, free for one
per month.*
