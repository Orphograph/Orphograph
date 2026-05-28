---
title: How Bitcoin merkle roots make file timestamps un-falsifiable
slug: bitcoin-merkle-roots-unforgeable-timestamps
date: 2026-05-13
author: Orphograph
summary: A technical walkthrough of why a timestamp anchored to Bitcoin is fundamentally different from any conventional database record. With the math, but without the lecture.
tags: [bitcoin, merkle-trees, cryptography, opentimestamps]
---

# How Bitcoin merkle roots make file timestamps un-falsifiable

When you read "this file is timestamped on Bitcoin," you might
picture a database somewhere with a row that says
`('file_abc123', '2026-05-13T14:00:00Z')`. That's not what's
happening. The actual mechanism is structurally different from a
database, and the difference is the entire point.

This post walks through the cryptography that makes a Bitcoin-
anchored timestamp un-falsifiable, the specific attacks an
adversary would have to mount to break it, and why those attacks
fail in practice.

## The setup

You have a file. Call it `photo.jpg`. You want to be able to prove,
later, that this exact file existed at or before some specific date
— let's say **2026-05-13 at 14:00 UTC**.

You can prove this by anchoring a fingerprint of the file (its
hash) to a record system that:

1. Cannot be edited after the fact.
2. Is replicated widely enough that you can't selectively reach
   all copies.
3. Is time-stamped in a way that doesn't depend on any one party
   telling the truth.

Bitcoin's chain satisfies all three by construction. Here's how.

## Step 1 — Hashing the file

You compute SHA-256 of `photo.jpg`. SHA-256 takes any input and
produces a 32-byte (256-bit) output:

```
photo.jpg  →  SHA-256  →  7accf9e90453280e6fb081fd9d83dfb1eeef3bd64e0d680826989ba79bccac88
```

Two properties of SHA-256 are doing all the work here:

**Preimage resistance.** Given the 32-byte output, finding ANY
input that produces it is computationally infeasible. You'd need
roughly 2^256 ≈ 10^77 attempts. The number of atoms in the
observable universe is around 10^80, for scale.

**Collision resistance.** Finding any two inputs that produce the
SAME output is also infeasible. The birthday attack reduces this
to ~2^128 attempts, which is still astronomical.

In practical terms: if I tell you a SHA-256 hash, you can't find a
matching file by guessing, and you can't construct a different
file that matches the same hash. The hash is a unique fingerprint.

## Step 2 — Building the Merkle tree

OpenTimestamps doesn't write your hash directly into Bitcoin —
that would be expensive. Instead, a calendar server batches your
hash with many others and builds a **Merkle tree**.

A Merkle tree is recursive pairwise hashing. Suppose 8 users
submit hashes A, B, C, D, E, F, G, H:

```
                    ROOT  (= hash of LM + NO)
                   /     \
              LM            NO
            /    \        /    \
          IJ      KL    MN      OP
         /  \    /  \  /  \    /  \
        A    B  C    D E    F  G    H

  IJ = SHA-256(A || B)
  KL = SHA-256(C || D)
  ...
  LM = SHA-256(IJ || KL)
  NO = SHA-256(MN || OP)
  ROOT = SHA-256(LM || NO)
```

Each leaf (A, B, ..., H) is one user's file hash. Each node is the
hash of its two children. The **root** is one 32-byte value that
cryptographically depends on every leaf.

Three useful properties:

1. **Tamper evidence.** Change any leaf, and the root changes.
   You cannot edit any user's hash without recomputing every
   ancestor up to the root.

2. **Logarithmic proof size.** To prove that your hash A is in
   this tree, you only need the "sibling" path: B, KL, NO. With
   those four values plus your original A, anyone can recompute
   ROOT. Proof size grows as log₂(N) — for 1 million leaves,
   that's just 20 sibling hashes.

3. **No need to reveal other users' data.** The verifier sees only
   your hash + the sibling path. They never learn what files other
   users anchored.

## Step 3 — Anchoring the root in Bitcoin

The calendar takes the root of the tree (one 32-byte value) and
writes it into a Bitcoin transaction. Specifically: it embeds the
root in an `OP_RETURN` output, which is a Bitcoin output type
explicitly designed for committing arbitrary data without making
it spendable.

The transaction gets included in a Bitcoin block. The block has
its own properties:

- **A timestamp** (the block header's `timestamp` field) — set by
  the miner, validated by the network to be within ~2 hours of
  network-consensus time.
- **Proof-of-work.** The block hash must satisfy a target difficulty
  — the miner must have done roughly 2^N hash computations to
  produce it, where N is whatever difficulty the network requires.

Once your transaction is in a block and the block has accumulated
6 subsequent blocks (~1 hour), the block is considered "deeply
confirmed." From this point forward:

- The block exists in tens of thousands of independent Bitcoin
  node copies worldwide.
- Replacing it would require an attacker to:
  1. Rewrite that block (cost: the proof-of-work the original
     miner already did).
  2. Rewrite EVERY subsequent block on top of it (cost: ALL the
     proof-of-work since then, faster than the rest of the
     mining network can produce new blocks).

That second condition is "winning a 51% attack" — controlling a
majority of Bitcoin's global hash rate sustained for the entire
period since your anchor. As of 2026, that costs roughly billions
of dollars per day to attempt. And critically, **the attacker has
to attempt it FROM the point of your anchor onward, indefinitely,
to keep the false history alive.**

## What the .ots proof actually contains

Your `.ots` proof is, in essence, the breadcrumbs from your hash
to the Bitcoin block header. It contains:

1. The OpenTimestamps file format header (magic bytes + version).
2. Your original hash (so the verifier knows what to expect).
3. The Merkle path from your hash to the root (the sibling
   hashes at each level of the tree).
4. The Bitcoin transaction containing the root.
5. The Bitcoin block header containing that transaction.
6. (Optional) The Merkle path within Bitcoin's own block-internal
   Merkle tree, from the transaction to the block header's
   `merkle_root` field.

A verifier checks:

1. Does my file hash match the leaf in the `.ots`?
2. Walking the sibling path, do I arrive at the Merkle root the
   calendar claimed?
3. Is that Merkle root present in the Bitcoin transaction the
   `.ots` references?
4. Is that transaction in the Bitcoin block the `.ots` references?
5. Is that block actually part of the longest Bitcoin chain?

If all five are yes, the file's existence by the block's timestamp
is proven. No trust in the calendar, in OpenTimestamps, in the
service you used — only in the math + the public Bitcoin chain.

## Attack scenarios — what would an adversary have to do?

Let's enumerate every way someone might try to forge a timestamp,
and what stops each one.

### Attack 1: edit the file after anchoring

Adversary modifies the original file to say something different.

**Why it fails.** The hash changes. The new hash doesn't match the
leaf in the `.ots` proof. Verification fails at step 1.

### Attack 2: substitute a different `.ots` proof

Adversary tries to produce a fake `.ots` that "matches" their
modified file by claiming a different leaf hash.

**Why it fails.** The fake leaf hash doesn't lead, via the sibling
path, to the same Merkle root the calendar published. They'd need
to either (a) find a SHA-256 collision (infeasible), or (b)
manufacture a whole alternative Merkle tree (infeasible without
also manufacturing the Bitcoin transaction containing its root).

### Attack 3: produce a fake Bitcoin transaction

Adversary signs a Bitcoin transaction that has their fake Merkle
root in it.

**Why it fails.** Anyone signing a Bitcoin transaction must own
the funds being spent (or have a relationship with someone who
does). The transaction must be relayed by Bitcoin nodes and
accepted into a block. To get it INTO a block, a miner must include
it. Miners include only transactions they see and that pay fees.
Your fake transaction would have to win this race against legitimate
transactions — possible, but doesn't help, because:

### Attack 4: get the fake transaction into a block

Adversary gets their fake transaction into a current Bitcoin block.

**Why it fails — partly.** This is possible! You can write
`OP_RETURN` data into Bitcoin freely. The constraint is that the
block's TIMESTAMP is set when the block is mined. So your fake
"proof" would say the file existed by, say, 2026-05-13 — but the
block was mined in 2026-05-13. You can't backdate the block. You
can only produce a proof valid from the block's timestamp forward,
which is the present. Anyone claiming "I had this file before
2026-05-13" would still need to anchor BEFORE 2026-05-13 — they
can't fabricate the anchor retroactively.

### Attack 5: rewrite Bitcoin's chain

Adversary wants to make their proof claim "this file existed since
2024" by retroactively inserting a transaction into a 2024 block.

**Why it fails — definitively.** Rewriting a 2-year-old Bitcoin
block requires re-mining 2 years of subsequent blocks faster than
the global mining network is producing new blocks. The 2-year
backlog represents roughly 10^28 hash operations of cumulative
work. Even controlling the entire current Bitcoin hash rate, an
attacker would need 2 years of sustained 51% attack to catch up
to "now," and at that point they'd have to keep winning every new
block forever to prevent honest miners from out-running them. The
estimated cost: tens of billions of dollars in mining capex with
no exit strategy. Nation-state-scale, and even then, dubious.

This is the property that distinguishes Bitcoin from a database.
A database can be retroactively edited by anyone with admin access.
Bitcoin's chain cannot be retroactively edited by anyone, including
its core developers, including the entire mining cartel acting in
unison, because every Bitcoin node validates the chain
independently.

### Attack 6: bribe / coerce a calendar operator

Adversary pays a calendar operator to issue a fake `.ots` claiming
a file was anchored a year earlier than it was.

**Why it fails.** The calendar's `.ots` is only valid if it leads
to a Merkle root that actually appears in a Bitcoin transaction.
The calendar can issue any `.ots` they want, but unless the
corresponding Merkle root is in a real, properly-dated Bitcoin
block, the verifier rejects it.

### Attack 7: race condition — generate a colliding file

Adversary finds a different file that produces the same SHA-256
hash as your `photo.jpg`, so they can claim YOUR proof actually
proves THEIR file existed.

**Why it fails.** This is a SHA-256 collision attack. As of 2026,
no public SHA-256 collision has ever been found, despite ~$100M+
in academic and adversarial research. Best known attacks require
roughly 2^126 operations, which exceeds the lifetime computational
budget of the entire planet. **Note:** if SHA-256 is ever broken,
your Orphograph receipts also include a SHA-512 sibling witness —
the file would need to produce a collision in BOTH hashes
simultaneously, which is structurally harder.

### Attack 8: capture-time substitution

Adversary anchors a file at the same moment as the legitimate
creator and later claims they were first.

**Why it fails — only with discipline.** This isn't a cryptographic
failure; it's a behavioral one. If you publicly publish a photo
and someone immediately anchors it before you do, both timestamps
are equally valid. The cryptography can't distinguish between you
and them. The practical defense is: anchor at capture time, when
only you have the file. The "I anchored before publication" gap
is the moat.

## What this is NOT

For all the cryptographic beauty, let's be clear about the limits.

A Bitcoin-anchored timestamp proves:
- **A specific byte sequence existed at or before a specific block's timestamp.** ✓

It does NOT prove:
- That you authored or own the file.
- That the file was made by a camera vs. an AI.
- Any specific legal qualification (eIDAS qualified status, etc.)
  unless an additional qualified trust service provider has also
  signed.
- That you'll be able to find the original file later if you lose
  it.

Treat it as "strong evidence the file existed by date T," and you
have the right mental model.

## Why this matters now

For most of the last decade, the answer to "did this photo
actually exist before AI training cutoff X?" was "let me check the
EXIF data and pray." That answer is collapsing under the weight of
edit-history software, deepfake suspicion, and increasingly
sophisticated AI-generated imagery.

Bitcoin-anchored timestamping gives anyone — creator, journalist,
photographer, whistleblower, accuser, defendant — a permissionless
way to lock a date to a file's contents using mathematics that
cost the entire mining network billions of dollars per day to
defeat.

It is not perfect. But it is structurally honest in a way that
self-attested timestamps, traditional digital signatures, and
proprietary "verified" badges are not. The proof outlives the
issuer. The verifier doesn't need permission. The math is the
trust.

If you make things that might one day be disputed, anchor them.

---

*Orphograph is a $19-and-up Bitcoin timestamping service for
photographers + creators. Open source verifier, no file uploads,
your receipt verifies forever. [Try the sample receipt without
signing up.](https://orphograph.com/#sample)*
