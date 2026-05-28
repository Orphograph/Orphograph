---
title: OpenTimestamps for non-developers — a 10-minute walkthrough
slug: opentimestamps-for-non-developers
date: 2026-05-13
author: Orphograph
summary: A practical explainer of what OpenTimestamps is, why it works, and how to use it without ever opening a terminal. Bitcoin without the gas fees, timestamping without the CLI.
tags: [opentimestamps, bitcoin, non-developers, photographers]
---

# OpenTimestamps for non-developers — a 10-minute walkthrough

OpenTimestamps is the most useful thing in the Bitcoin ecosystem
that you've probably never heard of. It's a free, open protocol
that lets anyone prove "this file existed at this exact time" by
anchoring a fingerprint to the Bitcoin blockchain — and the only
thing it costs is the 10 seconds it takes to use it.

This post explains how it works without making you install Python.
If you finish reading this, you'll understand:

- What "timestamping" actually means in a cryptographic sense
- Why anchoring to Bitcoin is the gold standard for proof-of-existence
- Why OpenTimestamps doesn't cost gas fees the way you'd expect
- The three layers (yours, the calendar's, Bitcoin's) and what each
  one does
- How to use it today, without a CLI

## What a timestamp actually is

When most people say "timestamp," they mean "the metadata field on
a file that says when it was created." That's not really a
timestamp — it's a self-attestation, and self-attestations can be
changed by anyone with editor access.

A cryptographic timestamp is different: it's a signed promise from
a party with no incentive to lie that the data they saw existed at
or before a specific time. Specifically:

1. You take a file (any file: photo, contract, video).
2. You compute a **hash** of it — a fixed-length fingerprint that
   uniquely identifies the file's contents. Any change to the file
   produces a totally different hash.
3. You submit the hash (not the file — just the 32-byte
   fingerprint) to a trusted timestamping authority.
4. The authority writes the hash into a record that is itself
   reliably dated, then signs the package.

Anyone with the original file + the timestamp can later prove the
file existed by:

- Re-computing the hash of the file
- Confirming the hash matches what was timestamped
- Confirming the timestamp authority's record is intact

If the file changes by even one bit, the hash changes, and the
timestamp no longer matches. **You cannot edit a timestamped file
without invalidating the timestamp.** That's the magic.

## Why Bitcoin makes this stronger

The traditional model of cryptographic timestamping uses **Qualified
Trust Service Providers** (QTSPs) — government-accredited entities
in Europe (under eIDAS) and similar bodies elsewhere. They sign
timestamps with private keys backed by hardware security modules.
That's strong against most threats, but it has two limits:

1. You're trusting one company. If they're hacked, bribed, or shut
   down, your timestamps become questionable.
2. They charge per timestamp (~$0.10–$2.00 each, depending on
   volume).

The Bitcoin model fixes both:

1. **Trustless verification.** A Bitcoin timestamp is "signed" not
   by any one party but by the entire proof-of-work of Bitcoin's
   chain. Forging a fake timestamp would require an attacker to
   re-mine years of blocks faster than the entire global mining
   network produced them — economically impossible for any
   foreseeable adversary.
2. **No per-record cost.** Here's the trick that makes this scale:
   OpenTimestamps doesn't write each user's hash directly into
   Bitcoin. Instead, it batches them.

## How batching works (the part that costs nothing)

If you've used Ethereum, you know "anchoring data on-chain" usually
costs gas — $1 to $30 per transaction, depending on network
congestion. So how does OpenTimestamps anchor your hash without
charging you anything?

The protocol uses a tree structure called a **Merkle tree**.

Imagine you and 999 other people all want to timestamp a file at
roughly the same time. A "calendar server" collects all 1,000 of
your hashes. It hashes them pairwise, then hashes those pairs, then
hashes those pairs again — building up a tree until only one hash
remains at the top: the **Merkle root**.

The calendar then writes JUST that single 32-byte Merkle root into
a single Bitcoin transaction.

The single transaction proves the existence of ALL 1,000 hashes,
because each individual hash is reachable by walking up the tree.

Your "proof" file — the `.ots` file you save with your original —
contains the steps from your specific hash up the tree to the
Merkle root, plus the Bitcoin transaction ID. To verify, anyone
can:

1. Re-compute your hash from your original file
2. Walk the tree up to the Merkle root using the steps in your
   `.ots`
3. Check the Bitcoin transaction containing that root

The verifier needs no special account, no API key, no permission.
Just a copy of the Bitcoin chain (which is fully public) and the
`.ots` file you saved.

Marginal cost to OpenTimestamps: one shared Bitcoin transaction
amortized across thousands of users, so effectively zero per record.
Marginal cost to you: zero.

## The three layers

OpenTimestamps has three layers, each doing one specific job:

### Layer 1 — Your file

This stays on your machine. You compute its SHA-256 hash and that's
the only thing that leaves your computer.

### Layer 2 — Calendar servers

These are the OpenTimestamps community-run servers (5 of them by
default — `a.pool`, `b.pool`, `alice`, `finney`, `btc.catallaxy`).
Each calendar:

- Receives your hash
- Adds it to a Merkle tree alongside many other users' hashes
- Once per hour, writes the tree's root into a Bitcoin transaction
- Returns to you a small proof file (the `.ots`) that includes the
  steps from your hash to the Merkle root, plus the Bitcoin tx ID

Calendars are run by independent operators with no financial
relationship to OpenTimestamps. The protocol uses 5 of them
simultaneously so a single calendar going down doesn't lose your
proof.

### Layer 3 — Bitcoin

Bitcoin's job here is the simplest: it remembers things. Each block
written contains all the transactions from the past 10 minutes,
including any OpenTimestamps Merkle roots. Once a block is mined
into the chain and has accumulated enough subsequent blocks (called
"confirmations"), the data inside is for all practical purposes
immutable.

This is the part that gives OpenTimestamps its strength. The
calendar operators could go offline, get hacked, or lie — but the
hash is already in Bitcoin's chain, which is replicated across
tens of thousands of independent nodes globally.

## What this looks like in practice

If you're comfortable in a terminal, here's the whole protocol in
four commands:

```bash
# Install the official Python client:
pip install opentimestamps-client

# Anchor a file:
ots stamp my_photo.jpg
# → produces my_photo.jpg.ots (the proof file)

# Wait ~1 hour for the calendar to publish its Bitcoin tx.

# Upgrade the .ots to include the full Bitcoin merkle proof:
ots upgrade my_photo.jpg.ots

# Verify any time, even years later:
ots verify my_photo.jpg.ots
```

The `.ots` file is tiny (~200–500 bytes), and once upgraded, can be
verified offline against any Bitcoin node.

## What if you don't want to install Python?

The protocol is great. The CLI is a wall.

A handful of services wrap OpenTimestamps with a browser-based UI:

- **[Orphograph](https://orphograph.com)** — drop a file in your
  browser, get a receipt back. The file never uploads (hashing
  happens in WebCrypto). $19 for 10 anchors, $9/mo for unlimited.
  Open-source verifier on GitHub means your receipts work even if
  the service disappears. (Disclosure: I built this.)
- Other browser-based wrappers exist too — ranging from WordPress
  plugins to enterprise-oriented platforms. Which one fits depends on
  whether you want a simple individual tool or a team/enterprise setup.

If you're a photographer worried about AI training scraping your
work and you want a way to prove "I took this on October 4" without
installing anything, the Orphograph free tier or $19 Writer Pack is the
shortest path. If you want zero dependency on any single company,
use OpenTimestamps directly via the CLI.

## Common questions

### Does the timestamp prove I authored the file?

No. It proves the file existed by a certain time. If you publicly
post a photo and someone immediately anchors it before you do, both
of you can prove "this file existed by Day 1." The trick is to
anchor at capture time, before anyone else has access — that's the
temporal moat.

### What if Bitcoin disappears?

Then a lot of things break. But also: your proof relies on the
historical chain, not on Bitcoin continuing into the future.
Existing blocks are baked-in PoW; even if Bitcoin's mining halts
forever tomorrow, your already-anchored proofs remain verifiable.

### Is this admissible in court?

It's strong cryptographic evidence of proof-of-existence. It is
NOT a qualified eIDAS timestamp. Whether a specific court accepts
it depends on the case, the jurisdiction, and the lawyer. Treat it
as "strong evidence the file existed by time T" not as a notary
replacement.

### How long does the proof last?

Until Bitcoin's chain is rewritten or abandoned. Practical answer:
indefinitely. Earliest OpenTimestamps anchors are now 8+ years old
and still verify.

### Can I anchor a 4GB video?

Yes — the protocol hashes the file with SHA-256, and SHA-256 takes
the same fixed time regardless of input size. Only the 32-byte hash
goes to the calendar.

## Why this matters

We're entering an era where the question "did a human or an AI
make this?" is going to be litigated millions of times — in
courtrooms, on Twitter, in copyright registries, in dating apps.

The clearest answer any creator can give is **"I made this, and I
locked the date to Bitcoin's chain before any AI saw it."** That's
the entire OpenTimestamps thesis in one sentence.

It's free. It's permissionless. It doesn't require trust. And
unlike most things in crypto, it's been quietly working in
production since 2017.

Anchor your work.

---

*Orphograph is a $19-and-up Bitcoin timestamping service for
photographers + creators. Open source verifier, no file uploads,
your receipt verifies forever. [Try the sample receipt without
signing up.](https://orphograph.com/#sample)*
