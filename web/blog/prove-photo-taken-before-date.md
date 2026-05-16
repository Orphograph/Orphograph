---
title: "How to Prove a Photo Was Taken Before a Specific Date"
slug: "prove-photo-taken-before-date"
date: "2026-05-15"
author: "Orphograph"
description: "A practical guide for photographers needing to prove a photo existed before a date — for AI-training disputes, copyright timelines, and authorship claims."
canonical_url: "https://orphograph.com/blog/prove-photo-taken-before-date"
tags: ["photography", "ai-disputes", "proof-of-existence", "timestamping", "evidence"]
---

# How to Prove a Photo Was Taken Before a Specific Date

A wildlife photographer posts a coyote shot from 2019 to her portfolio. Two
years later, an AI-image-generation service trained on a scraped dataset
starts producing eerily similar coyote compositions. She wants to participate
in a class-action — or at least submit her work to an opt-out registry — and
the lawyers ask the obvious question: *can you prove this photo existed before
the training cutoff?*

EXIF metadata says 2019. EXIF is also trivially editable in any image editor.
A Lightroom catalog says 2019. So does the dropbox modified-date. None of
those independently prove anything to an adversary who assumes you might have
backdated the file last week.

This is the specific problem proof-of-existence timestamping solves — and
where its limits start. Here is what actually works, and what does not.

## What "before a date" really means in disputes

Three different questions get collapsed into one:

1. **Did this exact file exist before date X?** Provable with a cryptographic
   timestamp anchored to an immutable public ledger.
2. **Was this photograph taken with a camera before date X?** Not provable
   from the file alone. EXIF can be forged; sensor pattern noise (PRNU) can
   sometimes corroborate a camera body but not a date.
3. **Did you create this work before date X?** Authorship is a separate
   evidentiary chain — usually a mix of timestamped existence, original raw
   files, behind-the-scenes captures, and contemporaneous mentions
   (emails, social posts).

Most AI-dataset opt-out and training-data-disclosure regimes (the EU AI Act
provisions starting 2026, the various US state proposals, the LAION and
Common Crawl opt-out portals) only require question #1: did the asset exist
before the training cutoff. Establishing #1 cheaply and verifiably is the
goal. The other questions need different evidence.

## Why Bitcoin works for "before a date"

The Bitcoin blockchain produces a new block roughly every ten minutes. Each
block contains the hash of the previous block, so rewriting a block from
years ago would require redoing the proof-of-work for every block since —
billions of dollars of electricity and hardware. In practical terms, a hash
that appears in a Bitcoin block from 2024 cannot have been inserted later.

If you can show that a SHA-256 hash of your photo was committed inside a
Bitcoin block at block height 880,000 (mined January 2025), then your photo,
*bit for bit identical to the file you're presenting now*, existed at that
moment. Change a single pixel and the SHA-256 hash changes, and your proof
no longer applies to the modified file. That's the whole game.

You do not need to put your photo on the blockchain. You only need to put
its 32-byte hash there — and you don't even need to put each hash there
directly. The OpenTimestamps protocol batches thousands of users' hashes
into a single Merkle tree, writes only the tree root to Bitcoin, and gives
each user a small proof file (`.ots`) showing how their hash chains up to
that root. The marginal cost per file is effectively zero.

## The minimum workflow

To make a photo defensible against future "you backdated this" challenges,
do the following on the day you want the timestamp to start:

1. **Export the master file you care about.** Usually the original RAW
   (`.CR3`, `.NEF`, `.ARW`, `.DNG`) plus a TIFF or full-quality JPEG
   sidecar. Hash both. Future you will be grateful you kept the RAW.
2. **Compute the SHA-256 hash of each file.** On macOS / Linux:
   `shasum -a 256 photo.cr3`. On Windows: `certutil -hashfile photo.cr3
   SHA256`. A timestamping service does this for you in the browser.
3. **Anchor the hash via OpenTimestamps.** Either run the OTS CLI yourself
   (free, requires running a tool and saving the `.ots` file) or use a
   hosted service like [Orphograph](https://orphograph.com) that handles
   submission to multiple calendars and gives you a receipt JSON.
4. **Wait for Bitcoin confirmation.** Calendars batch and write the Merkle
   root to Bitcoin roughly once an hour. Your proof becomes fully verifiable
   against the blockchain after the next block is mined (~10–60 minutes
   typical, sometimes a few hours).
5. **Store the original file, the hash, and the receipt together.** A
   timestamp is useless without the file it points to. Back up all three to
   at least two locations. Cold storage is fine; receipts are tiny (under
   5 KB each).

## What about EXIF "Date Taken"?

EXIF `DateTimeOriginal` is a hint, not evidence. Anyone who has ever shot
with the wrong camera clock knows it can be off by years. Any image editor
can rewrite it. A determined adversary in a dispute will (correctly) point
this out.

That said, EXIF is still useful as *corroborating* evidence. If your hash
was anchored on 2024-03-15 and the EXIF says 2024-03-14 18:42, those two
sources telling the same story is stronger than either alone. The hash
proves the file existed by 2024-03-15. The EXIF, while editable, is at
least *consistent* with the timestamp.

The reverse case is the giveaway: if your EXIF says 2019 but your earliest
hash anchor is 2024, all you've proven is that the file existed in 2024.
That may still be enough for an AI-training cutoff (most major models had
cutoffs in 2023 or earlier), but be honest about what the evidence shows.

## What this approach does NOT do

This is the part most marketing pages skip. A Bitcoin-anchored hash:

- **Does not prove you took the photo.** It proves a file existed. Authorship
  is a separate argument.
- **Does not prove where or when the shutter fired.** Only that the file
  existed by the timestamp.
- **Does not prevent scraping.** It's an evidence layer, not a defense layer.
- **Is not "court-admissible" by default.** Admissibility depends on
  jurisdiction, the case, the judge, and how you authenticate the
  evidence on the stand. A Bitcoin timestamp is *technical proof of
  existence by date*, not a notarial act.
- **Does not retroactively cover work you anchored late.** If you start
  anchoring today, today is your earliest provable date for those files.
  There is no way to prove a 2018 photo existed in 2018 if you didn't
  timestamp it back then.

The last point is the painful one. Photographers who didn't timestamp old
work cannot retroactively get a 2018 anchor for it. The best you can do for
older work is anchor it now (so it's at least defensible from today
forward) and rely on whatever other contemporaneous evidence exists —
hard-drive forensics, cloud-backup metadata, old emails attaching the file,
client invoices, social posts.

## Practical scenarios

**Scenario A: A new AI service launches in 2026 and you want to opt out of
its next training cycle.** Anchor your portfolio now. Going forward, you
can prove any of those files existed before the next training run. The 2026
hash is sufficient evidence for any model trained after that date.

**Scenario B: You're working on a project you plan to publish in six
months.** Anchor each milestone — first edit, color grade, final master.
You build a *chain* of timestamps that documents the evolution of the work,
which is harder to dispute than a single end-state hash.

**Scenario C: You suspect a specific image in your portfolio is in a
training dataset.** Anchor it now to lock in today as the latest possible
"existed by" date, then check it against the LAION and Common Crawl opt-out
tools using the original URL where it was published. The timestamp + the
URL crawl date together are stronger than either alone.

## The honest pitch

Cryptographic proof-of-existence is cheap (a few dollars or free), fast
(under an hour to full Bitcoin confirmation), and verifiable by anyone
without trusting the service that issued the receipt. For photographers
trying to participate in opt-out registries or future class actions, that's
enough — *if you start now*.

Orphograph hashes your files in the browser (your photos never upload), submits
the hash to five independent OpenTimestamps calendars, and gives you a
receipt you can verify against the public Bitcoin chain forever, even if
our service disappears. Free for one file per month. See the
[verify page](https://orphograph.com/verify/) or the
[photographer-specific landing](https://orphograph.com/lp/prove-photo-pre-ai.html)
to start.
