---
title: How to prove a photo existed before an AI model was released
slug: how-to-prove-photo-existed-before-ai-model-released
date: 2026-05-17
canonical: https://orphograph.com/blog/how-to-prove-photo-existed-before-ai-model-released/
author: Orphograph
summary: A step-by-step walkthrough for photographers who want a Bitcoin-anchored receipt that locks the date of an image before any 2026 AI model was trained on it.
description: Step-by-step walkthrough to prove a photo existed before AI training. Bitcoin-anchored timestamp, free tier, ten-minute setup.
tags: [photographers, ai-disputes, opentimestamps, provenance]
---

# How to prove a photo existed before an AI model was released

By the middle of 2026 most working photographers have seen the same
thing happen at least once: an image gets posted, weeks later a
similar one shows up in an AI-generated set, and the argument over
who shot what starts. The dispute is almost always settled by
whichever party has the better paper trail — not the better
argument.

This post is a practical walkthrough for locking that paper trail
to a moment in time, before any 2026 AI model finishes training on
your work. The technique is called cryptographic timestamping, and
it produces a receipt that a third party can check against the
public Bitcoin chain without trusting you, without trusting the
service that issued the receipt, and without involving a lawyer.

## What "prove it existed" actually means

A timestamp does not prove you authored a file. It proves a
specific byte sequence existed by a specific moment. That is a
narrower claim than "I made this," but it is the one claim that
matters in an AI-training dispute.

The dispute usually breaks down to one question: did the disputed
file exist before the model's training data cutoff? If your receipt
points to a Bitcoin block mined before that cutoff, the model could
not have been trained on a future version of the file. The argument
collapses into a date check.

For 2026 disputes the relevant cutoffs are public. Most major
foundation models published in 2026 used training data with cutoffs
between mid-2024 and early 2026. If your receipt is older than the
cutoff, the model could not have produced the original.

## What you need before you start

You need three things:

1. The original file. RAW is ideal. JPEG works. The file's bytes
   must not change after the anchor — re-saving in Lightroom
   produces a new file with a different hash.
2. A way to compute a SHA-256 hash. Every modern browser has this
   built in via WebCrypto. You do not need to install anything.
3. A place to save the receipt. Anywhere your normal photo backup
   lives is fine: Dropbox, iCloud, an external drive.

You do not need a Bitcoin wallet. You do not need to pay a miner.
You are not putting money on chain. You are submitting a 32-byte
fingerprint of your file to a calendar server that batches many
users' fingerprints into one Bitcoin transaction.

## Step 1: hash the file

Open Orphograph in a browser. Drag the file onto the drop zone.
The hash is computed locally — the file's bytes never leave your
machine. The page shows two hashes: SHA-256 and SHA-512. Both are
included in the receipt so that if SHA-256 is ever broken, the
SHA-512 sibling still verifies independently.

If you want to do this without any service, the command-line path
is `shasum -a 256 my_photo.jpg`. You will see the same 64-character
hex string the browser produced. Save that string somewhere. The
hash itself reveals nothing about the file — it is a one-way
fingerprint.

## Step 2: submit to a calendar

The browser sends the hash (not the file) to five OpenTimestamps
calendar servers. Each calendar is run by a different operator.
They each batch your hash with other users' hashes into a Merkle
tree and write the tree's root into a Bitcoin transaction within
the next hour.

You get back a receipt bundle: a JSON file plus five `.ots` proof
files, one per calendar. The bundle downloads as a single zip.

Save the zip to your photo backup right now. If you lose the
receipt, the anchor still exists on Bitcoin, but you have no way to
prove which block contains your hash. The receipt is the index.

## Step 3: wait for the block

Within roughly one hour the calendar's Merkle root is included in a
Bitcoin block. The receipt's status updates from "pending" to
"block-pinned at block N." From this point forward the proof is
self-contained: anyone with a Bitcoin node can verify that your
hash is committed to block N, mined at a specific timestamp.

You can see what a finished receipt looks like at
[https://orphograph.com/r/o3WGD22T4UwqfCrb](https://orphograph.com/r/o3WGD22T4UwqfCrb)
— the genesis receipt, public, pinned to a real Bitcoin block.

## Step 4: verify it yourself

This is the step most photographers skip, and it is the most
important one. A receipt you have never verified is a receipt you
do not actually trust. Run the verification once, on your own
machine, so you know what the output looks like:

```bash
pip install opentimestamps-client
ots verify my_photo.jpg.ots
```

The verifier walks the Merkle path inside the `.ots` file, locates
the Bitcoin transaction, fetches the block header, and confirms
that the block's timestamp is what the receipt claims. If anything
fails, you see the failure here, not later in a dispute.

The verifier does not depend on Orphograph. It does not depend on
the calendar that issued the proof. It depends only on a Bitcoin
node and the file. This is why the receipt outlives any single
service.

## Step 5: anchor at capture time, not at publication time

Here is the part that determines whether your receipts actually
help in a dispute. The cryptography proves a file existed by a
block's timestamp. It does not prove you, specifically, were the
first to anchor it.

If you post a photo on Instagram on Tuesday and anchor it on
Thursday, anyone who downloaded the photo on Wednesday could have
anchored it before you. Both timestamps are equally valid. The
cryptography cannot distinguish.

The defense is to anchor the file before publication, while the
file is still only on your camera and your laptop. The gap between
"file exists privately" and "file exists publicly" is the only
window where your anchor is provably first. Anchor inside that
window.

For working photographers this means: anchor RAW files before you
post JPEGs. Anchor a fashion week shoot's selects before they ship
to the publication. Anchor portraits before you deliver them.

## What this does not do

Be honest with yourself about the limits.

A timestamped hash proves:

- A specific byte sequence existed at or before a Bitcoin block's
  timestamp.

A timestamped hash does not prove:

- That you authored the file.
- That the file was produced by a camera rather than generated.
- That you own the rights to the subject.
- That the file is legally authentic under any qualified
  trust-service-provider framework.

For full legal authentication you need a qualified TSA (in the EU)
or a notary (in jurisdictions that recognize digital notarial
acts). Orphograph is not either of those. It is a strong,
permissionless, mathematical anchor to a specific date — which is
the piece most photographers are missing.

## What to do this week

If you do nothing else, do this:

1. Pick the three photos from the last month you would most regret
   losing the "I shot this" argument over.
2. Anchor each one. The free tier covers three anchors per
   24 hours, which is exactly enough.
3. Save the receipt zips to your normal photo backup.
4. Run `ots verify` on one of them, just so you know the
   verification works on your machine.

The whole thing takes about ten minutes. The receipt is good for
the working life of Bitcoin, which is the longest-running
proof-of-existence anchor available in 2026.

Start with the free tier at
[https://orphograph.com](https://orphograph.com).

---

*Orphograph is a Bitcoin file-timestamping service. Three free
anchors every 24 hours, $29 for a 10-anchor pack, $9/mo for
unlimited. Open-source verifier. Receipts verify against any
Bitcoin node without our servers.*
