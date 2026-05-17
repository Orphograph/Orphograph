---
title: Why a Bitcoin block height is the strongest proof of existence in 2026
slug: bitcoin-block-height-as-source-of-truth
date: 2026-05-17
canonical: https://orphograph.com/blog/bitcoin-block-height-as-source-of-truth/
author: Orphograph
summary: A Bitcoin block height has a sixteen-year track record without a reorganization past about six blocks. That track record is what makes it a useful anchor.
description: Why a Bitcoin block height is the strongest existence-proof anchor in 2026. Sixteen-year track record, attack costs, what would have to fail.
tags: [bitcoin, proof-of-existence, timestamps, security]
---

# Why a Bitcoin block height is the strongest proof of existence in 2026

When you anchor a file's hash to a Bitcoin block, the receipt
references a specific block height — for example, "this file's
hash is committed to the Merkle root inside block 892,341, mined
at 2026-05-17 14:00 UTC."

That block height is the load-bearing piece of the proof.
Everything else in the receipt — the Merkle path, the calendar
servers, the issuing service — is auxiliary. The block height is
the part that anyone can independently check against a public
record sixteen years long.

This post explains why a block height is currently the strongest
existence-proof anchor available, what its actual track record
looks like, and what would have to be true for a Bitcoin-anchored
timestamp to fail. The goal is to give you enough information to
decide for yourself whether you trust it.

## What a block height represents

A Bitcoin block is a batch of transactions, sealed with a header
that contains:

- The hash of the previous block.
- A Merkle root summarizing every transaction in the block.
- A timestamp set by the miner, validated by the network to be
  within roughly two hours of consensus time.
- A nonce, found by trial and error, that makes the block's own
  hash satisfy a target difficulty.

The block height is just the position of that block in the chain.
Block 0 is the genesis block, mined in January 2009. Block 892,341
is the 892,341st block in the chain. Each block can only have one
predecessor, and the predecessor's hash is baked into the
successor's header. Changing block N requires changing every block
from N onward, because each header is cryptographically bound to
the one before it.

This is the structural property that makes a block height useful
as a timestamp anchor. The block exists at exactly one point in a
totally ordered sequence, and that ordering is enforced by hashes,
not by any operator's policy.

## The sixteen-year track record

As of May 2026, Bitcoin has been producing blocks continuously for
just over sixteen years. The chain has never been rewritten beyond
about six blocks deep — and even those shallow reorganizations
have been rare events caused by near-simultaneous block discovery,
not by attack.

A few specific facts worth sitting with:

- The deepest reorganization in Bitcoin's history was a 53-block
  rollback in March 2013, caused by a bug in the database backend
  of an earlier client version. That bug was patched within hours.
  No subsequent reorganization has come close.
- Since 2013, no reorganization has exceeded six blocks. The
  rolling average is below one block.
- The chain has survived multiple hard forks, several exchange
  failures, two halvings, and a period in 2024 when the network's
  hash rate briefly dropped due to mining-equipment relocations.
  In each case the chain kept producing blocks.
- The cumulative proof-of-work in the chain — the sum of all the
  computational effort the network has expended — increases
  monotonically. Reversing any historical block requires redoing
  every block of work since.

For a timestamp anchor, this matters because the question is not
"can Bitcoin be attacked in theory" but "has it been attacked in
practice, over sixteen years, with billions of dollars in
incentives, ever." The answer is no. Not even close.

## What a six-block confirmation means in practice

Most timestamping services, including Orphograph, treat a block as
"deeply confirmed" once six additional blocks have been mined on
top of it. That is roughly one hour of additional work.

The convention exists because of probability. The chance of a block
being rolled back declines exponentially with each additional
block of work on top of it. After six blocks, the probability of a
rollback is, in practice, indistinguishable from zero — given
sixteen years of empirical data showing no rollback at that depth.

This is also why timestamping receipts say "block-pinned after one
hour." That hour is the six-block confirmation window. After it,
the block is treated as permanent.

## Why a block height beats other anchors

Compare a Bitcoin block height to the alternatives:

A timestamp on a private database depends on the operator not
editing the row. The operator can edit the row at any time. The
row is gone the day the company shuts down.

A timestamp from a qualified trust service provider is stronger,
because the TSA is regulated and audited. It still depends on the
TSA's signing key not being compromised and on the TSA continuing
to exist. Qualified TSAs have had keys revoked and providers have
wound down.

A timestamp on a private chain is the database, with extra steps.
The validators run the show. If they agree to rewrite history,
history is rewritten.

A Bitcoin block height depends on no entity having enough
sustained hash rate to rewrite the chain back to your block. That
trust is calibrated by sixteen years of empirical data and by an
attack cost currently in the billions of dollars per day.

None of these are absolute. The question is which one has the
strongest track record in 2026. The answer, by every available
measure, is Bitcoin.

## What would have to be true for a Bitcoin timestamp to fail

Be honest about the failure modes. A Bitcoin-anchored timestamp
fails only if at least one of the following is true:

A sustained 51% attack succeeds against the chain. An attacker
must control more than half of Bitcoin's global hash rate
continuously, from the block of your anchor forward, until the
attacker's alternative chain is longer than the honest chain. The
attacker must also keep winning every new block forever. As of
2026, the cost of doing this for one day is estimated at roughly
$20–40M. For one year, it exceeds the GDP of most countries. The
attacker also gets no reward — the attempt destroys the value of
the asset being attacked.

A SHA-256 collision is found. No public SHA-256 collision has
ever been found. Best known academic attacks require roughly
2^126 operations. If SHA-256 is ever broken, the impact would be
a once-in-a-generation cryptographic event affecting essentially
the entire internet. Orphograph receipts include a SHA-512
sibling hash that would still verify in that scenario.

The entire Bitcoin network agrees to a coordinated rollback. The
closest historical precedent is the March 2013 rollback, caused
by a software bug, lasting hours, reverting 24 blocks. Nothing of
comparable scope has happened since.

Bitcoin ceases to exist. The historical chain is currently stored
on tens of thousands of independent nodes worldwide, including in
academic archives. Total disappearance is implausible on any
timeline shorter than decades.

If any one of these happens, your timestamp is at risk. If none of
them happens, your timestamp is good for the working life of
Bitcoin.

## What this means for a receipt

Practically, the block height in your receipt is the part you
should focus on when checking the proof. The verification step is
straightforward:

1. Look up the block at that height on any public block explorer,
   or on your own Bitcoin node.
2. Confirm the block's timestamp matches what the receipt claims.
3. Confirm the Merkle root in the block's header matches the one
   the receipt's path computes to.
4. Confirm the file's hash leads, via the Merkle path, to that
   root.

If all four match, the file existed by the block's timestamp. The
service that issued the receipt is no longer in the trust chain.
The calendar that batched the submission is no longer in the trust
chain. The only trust required is in the Bitcoin chain itself, and
that trust is what the sixteen-year track record is calibrating.

## The practical takeaway

A Bitcoin block height is not magic. It is a specific position in
a specific chain that has, over sixteen years and through
substantial financial incentive to break it, never been
successfully rewritten beyond a handful of blocks.

That track record is what you are relying on when you anchor a
file to Bitcoin. The cryptography matters, but the empirical
record is the part that converts a theoretical guarantee into a
useful one. In 2026 there is no other anchor with comparable
properties.

If the receipt format outlives the service that issued it — which
Orphograph's does, by design — then the only question that matters
for the next decade is whether the Bitcoin chain continues. So far
it has, every ten minutes, for sixteen years.

---

*Orphograph is a Bitcoin file-timestamping service. Three free
anchors every 24 hours, $29 for a 10-anchor pack, $9/mo for
unlimited. Open-source verifier. Receipts verify against any
Bitcoin node without our servers.*
