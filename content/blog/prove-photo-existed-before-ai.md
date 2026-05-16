---
title: How to prove your photo existed before AI scraped it (without a lawyer)
slug: prove-photo-existed-before-ai
date: 2026-05-12
author: Orphograph
summary: A practical guide for photographers who want timestamp-grade proof of when they made an image — using Bitcoin, no court appearance, no $400/hr attorney. Free path included.
tags: [photographers, ai-disputes, opentimestamps, provenance]
---

# How to prove your photo existed before AI scraped it (without a lawyer)

If you've ever uploaded a photo to Instagram, you've probably wondered:
what stops an AI training set from scraping it, then a year from now
someone claims your photo is the AI-generated one?

This isn't paranoid. It's the next phase of a problem that already
happened to musicians (sample disputes), writers (plagiarism), and
illustrators (style mimicking). Photographers are now the cohort
getting the most heat — Getty filed against Stability AI for 12
million scraped images, and every working photographer has a "is
that mine?" story brewing.

The standard answer is **EXIF metadata + your RAW files + cloud
backups**. That's three plates spinning, all of which can be edited,
deleted, or lost. None of them produce evidence a third party would
treat as definitive.

This post walks through the better answer: **cryptographic
timestamping** — a way to lock today's date to a file's contents
such that the only path to forge the claim is to re-mine the
entire Bitcoin blockchain. Which nobody will do for your wedding
shoot.

## The fundamental tool: OpenTimestamps

[OpenTimestamps](https://opentimestamps.org/) is a free, open
protocol that works like this:

1. You compute the SHA-256 hash of your file. (32 bytes; reveals
   nothing about the photo.)
2. You submit the hash to one of ~5 "calendar servers."
3. Each calendar batches many users' hashes into a single Merkle
   root and writes that root into a Bitcoin transaction
   (approximately once per hour).
4. You receive a small `.ots` proof file. The proof says:
   *"this hash is provably included in Bitcoin block N, mined
   at time T."*

Verification is the inverse: given your original file + the
`.ots` proof, anyone with a Bitcoin node (or just the public
chain) can confirm that the hash existed at time T. No trust in
any server is required at verification time.

This is the gold standard for proof-of-existence. It's free, it's
permissionless, and it doesn't go away if any single company
disappears.

## The catch with raw OpenTimestamps

OpenTimestamps is a brilliant protocol, but it's a CLI:

```bash
pip install opentimestamps-client
ots stamp photo.jpg
# ... wait ~1 hour for the BTC calendar to publish the block ...
ots upgrade photo.jpg.ots
ots verify photo.jpg.ots
```

If you live in `pip install`, you're done — go use it, and skip the
rest of this post. (Seriously. Stop reading and go run it.)

For everyone else: the CLI is a wall. You need Python, a working
build chain for `python-bitcoinlib`, comfort with the terminal, and
the discipline to safely store `.ots` files alongside your raw
photos. Most photographers won't do any of that. So most photos go
unanchored, and the AI-vs-real disputes happen with no usable
evidence on either side.

## A simpler path: drop the file in a browser

There's a small product called [Orphograph](https://orphograph.com)
(disclosure: I built it) that wraps OpenTimestamps with a
browser-based workflow:

1. Open the site, drag your file onto the drop zone.
2. The hash is computed locally in your browser using WebCrypto.
   The image bytes never reach our server.
3. You get a receipt — JSON file + 5 OpenTimestamps proofs —
   downloadable as a single bundle.
4. The block-pinning upgrade happens automatically server-side
   over the following hour; the receipt updates from "pending" to
   "block-pinned at block N."

You can verify the receipt later with [a 100-line Python file
on GitHub](https://github.com/orphograph/orphograph-verify) — no
Orphograph dependency. The receipt outlives our company by design.

Three pricing tiers: free (1 anchor/month), $7 Pack (10 anchors,
never expires, claim code by email), $5/mo Personal (unlimited).

## How to actually do this for your portfolio

Pick the approach that matches your comfort level.

### Path A — free, technical (use OpenTimestamps directly)

```bash
pip install opentimestamps-client
# anchor:
ots stamp my_photo.jpg
# returns my_photo.jpg.ots — save it next to the photo
# upgrade after 1 hour to get the BTC merkle proof:
ots upgrade my_photo.jpg.ots
# verify any time:
ots verify my_photo.jpg.ots
```

Cost: $0. Time: 5 minutes per photo plus 1 hour wait. Defensibility:
strongest. You're using the protocol directly.

### Path B — free, browser (drag-and-drop monthly)

Visit [orphograph.com](https://orphograph.com), drop your best
photo of the month into the box. Save the receipt JSON. Done.

Cost: $0/month forever for the first anchor. Time: 10 seconds.
Defensibility: same as Path A, with a slight UX trade — the
receipt has to be re-downloaded if your browser clears
localStorage. Save the receipt to Dropbox / iCloud / your normal
photo backup.

### Path C — $7 for a batch (when you have 10 photos at once)

Same as Path B but pay $7 and skip the rate limit. The claim code
is delivered by email and lets you anchor 10 files in one
sitting. The Stripe receipt is your invoice; the email gets the
claim code.

Defensibility: identical to A/B (same OTS proofs).

### Path D — $5/mo (you anchor 50+ files per month)

If you batch-anchor a wedding shoot or a fashion week catalog,
the Personal tier is the only one with unlimited anchors and
email delivery. It also gives you an account dashboard showing
your history — useful if you ever need to produce a
chronologically-ordered list of "what I anchored when."

## What this does NOT do

Be honest about the limits, because the AI disputes are going to
get litigated and you want to know what your receipts will and
won't prove.

A timestamped hash proves:
- **A specific byte sequence existed at time T.** ✅

A timestamped hash does NOT prove:
- That **you** authored it. (Anyone with the file can anchor it.
  If you publicly publish a photo, the AI could anchor it the
  same day you did.)
- That the file was **made by a camera** versus generated.
- That the file is **legally authentic** under eIDAS or any
  national qualified-trust-service-provider framework. For that,
  you need a notary, a qualified TSA, or both.

The right framing is: **anchor immediately at capture time, when
the only person who has the file is you.** That's the temporal
moat the AI can't compete with — they need to scrape the file
first, and you've already locked the timestamp before they have
access.

In practice this means: anchor RAW files before posting JPEGs.
Anchor on the camera's SD-card directly if you can. Don't wait
until after a viral upload.

## What to do this week

If you're a working photographer, here's the minimum-effort path:

1. Pick your most distinctive image from the last week — the one
   you'd most regret losing the "I shot this" argument over.
2. Anchor it. Free, takes 10 seconds.
3. Save the receipt JSON to wherever you keep your photo backups.
4. If anyone ever disputes the date — show them the receipt and
   the open-source verifier link. Let the math speak.

## Further reading

- OpenTimestamps protocol: [opentimestamps.org](https://opentimestamps.org/)
- Orphograph open-source verifier: [github.com/orphograph/orphograph-verify](https://github.com/orphograph/orphograph-verify)
- eIDAS-grade timestamps (when you need legally binding): your
  national qualified-trust-service-provider list. EU = check the
  European Trust List. UK = ICO trust list. US = there is no
  federal QTSP regime; consult a digital evidence attorney.

If you've used timestamping in any actual dispute, I'd love to
hear what happened. The clearer the use cases, the better tools
get built for the next round.

---

*Orphograph is a $7-and-up Bitcoin timestamping service for
photographers. Open source verifier, no file uploads, your
receipt verifies forever. [Try the sample receipt without
signing up.](https://orphograph.com/#sample)*
