---
title: "Building an Evidence Chain for AI Scraping Disputes: A Practical Playbook"
slug: "evidence-chain-ai-scraping-disputes"
date: "2026-05-15"
author: "Orphograph"
description: "Step-by-step playbook for photographers, illustrators, and writers to build a defensible evidence chain against AI training scrapers — timestamps, opt-outs, contemporaneous records."
canonical_url: "https://orphograph.com/blog/evidence-chain-ai-scraping-disputes"
tags: ["ai-scraping", "evidence", "playbook", "photographers", "copyright", "disputes"]
---

# Building an Evidence Chain for AI Scraping Disputes: A Practical Playbook

A children's-book illustrator in Edinburgh notices that a new generative
AI service produces images in a style that looks distressingly like hers.
The line weight, the watercolor texture, the muted palette — it's all
there. She wants to do something about it. She finds the LAION opt-out
form. She finds the company's opt-out portal. She also finds a class
action against the same company. The lawyer for the plaintiffs asks for
evidence.

She has the original Procreate files. She has dated Instagram posts. She
has client invoices. None of that, individually, is bulletproof. Together,
woven into a chain, it is. This is the playbook for assembling that
chain — what to collect, in what order, and how to bind it together so
each link reinforces the others.

This is written for individual creators, not law firms. It is not legal
advice. Use it as a checklist for building the evidence base you'd hand
to a lawyer if you needed one.

## The structure of a defensible evidence chain

A strong evidence chain answers four questions, independently and with
overlap:

1. **What is the work?** Specific files, with specific cryptographic
   fingerprints, identified unambiguously.
2. **When did it exist?** Earliest provable date for each file.
3. **Who made it?** Authorship attribution with corroborating records.
4. **What was done with it?** Publication, distribution, scraping
   exposure, opt-out history.

No single piece of evidence answers all four. The chain answers them
collectively, with multiple independent sources for each question. An
adversary attacking the chain has to refute each link — and the links
should come from sources the adversary cannot tamper with.

## Link 1: The cryptographic fingerprint

Compute the SHA-256 hash of every master file you care about. RAWs,
full-resolution exports, layered source files (PSD, Procreate, AI, SVG),
and any final delivery formats. Save the hashes in a list keyed by
filename and creation date.

On macOS/Linux:
```
shasum -a 256 /path/to/file
```
On Windows:
```
certutil -hashfile C:\path\to\file SHA256
```

A SHA-256 hash is 64 hex characters. It uniquely identifies a file —
change one bit, get a completely different hash. This is the atomic unit
of digital evidence. Without it, you can't unambiguously point to *which
file* you're claiming was created when.

## Link 2: The Bitcoin timestamp

For each master file, anchor the SHA-256 hash to the Bitcoin blockchain
via OpenTimestamps. This produces a `.ots` proof file (or a hosted
receipt JSON with embedded proofs) showing your hash was committed to a
Bitcoin block at a specific time.

This is the only step that establishes *cryptographic* proof of when the
file existed. Every other piece of evidence is either reputational
(notary, certificate authority) or self-reported (EXIF, file system
dates, cloud-backup timestamps). The Bitcoin timestamp is the
unforgeable anchor.

Anchor early and anchor often:
- Anchor each master file on the day you finish it.
- Anchor major revisions separately (the v1, v2, v3 chain is itself
  evidence).
- Anchor *now* anything older that you haven't anchored yet — that
  locks in today as the latest possible "existed by" date, even if you
  can't push it earlier.

Marginal cost: effectively zero (OpenTimestamps batches hashes for free;
hosted services charge a few dollars or less per receipt).

## Link 3: EXIF and embedded metadata

EXIF is editable, which is why it isn't sufficient on its own. But it's
useful as a *consistency check* against the cryptographic timestamp. If
your EXIF says 2023-08-04 and your earliest Bitcoin anchor is 2023-08-05,
those agree. If they disagree, the cryptographic date is the one that
stands.

For each file in the chain, archive:
- Full EXIF (`exiftool -G -a file.jpg`).
- IPTC/XMP fields (caption, byline, copyright notice).
- Any embedded color profile or capture-device fingerprint.
- C2PA manifest if present.

Archive these as plain-text dumps alongside the files. The
preservation of the metadata, even though it's individually editable,
gives a future fact-finder material to cross-check.

## Link 4: Contemporaneous records

These are the pieces of evidence created *at or near the time of
creation*, by independent systems, that an adversary cannot retroactively
modify.

Strong contemporaneous records include:

- **Cloud backup logs.** Dropbox, iCloud, Google Drive, Backblaze
  upload timestamps for the file. These are recorded by third-party
  systems with their own audit logs.
- **Email attachments.** An email you sent on date X with the file
  attached, retained by both the sender's and recipient's mail
  providers. Subpoenable, durable.
- **Social media posts.** Instagram, Mastodon, personal blog posts with
  the file or a preview. Platform timestamps are independently
  recorded. Internet Archive captures of those posts are even stronger.
- **Client invoices and contracts.** "Delivery of files for project X,
  dated Y." Cross-references the file to a commercial transaction.
- **Version control commits.** For digital work (illustrations,
  designs, drafts), a Git repo with the file committed and an
  associated GPG-signed commit, or pushed to a remote whose own logs
  record the push time.
- **Behind-the-scenes captures.** Process videos, in-progress screenshots,
  "shooting setup" photos. These often have their own EXIF and their own
  timestamps that contextualize the master file.

Aim for at least two independent contemporaneous records per major
work. They don't need to be timestamps in the cryptographic sense — they
need to be records from systems controlled by other parties (or at
least independent of the file itself).

## Link 5: Publication history and Internet Archive captures

For published work, the Internet Archive Wayback Machine is one of the
most useful public services in this category. Pages crawled and stored
by archive.org are dated by the archive's own servers, independent of
the publisher.

Steps:

1. For each work, identify the URLs where it was first published.
2. Check archive.org/web/ for existing snapshots of those URLs. Many
   personal sites are crawled occasionally without you doing anything.
3. If no snapshot exists, submit the URL via "Save Page Now" to create
   one. The captured snapshot becomes a third-party-dated record of
   the file appearing at that URL.
4. Record the snapshot URLs in your evidence inventory.

When the dispute arises, an Internet Archive snapshot showing your
photo on your personal site in 2023 is independent corroboration of your
publication timeline — corroboration the scraping operator cannot
retroactively modify.

## Link 6: Opt-out and exposure records

If you've opted out of AI training datasets, keep proof:

- LAION opt-out submission confirmations (the "Have I Been Trained?"
  site by Spawning at haveibeentrained.com lets you opt out of LAION
  and lists participating model providers).
- Common Crawl opt-out via robots.txt updates (and archive.org
  snapshots of your robots.txt file showing when the directives were
  added).
- Direct opt-out portal confirmations from OpenAI, Stability AI,
  Anthropic, Adobe Firefly, and others that publish portals.
- Email correspondence with model operators requesting removal.

These records establish that you *attempted* to opt out, and the date
of that attempt. They're useful in two ways: (a) showing you exercised
available rights, (b) bounding the time window during which any scraping
could be claimed as inadvertent.

## Link 7: Detection of unauthorized use

If you suspect a specific model has been trained on your work, evidence
of that suspicion needs to be captured carefully.

- **Reproduction tests.** Prompt the model with descriptions of your
  style or characters and record outputs. Capture timestamps, prompts,
  and screen recordings. Multiple reproductions strengthen the
  pattern.
- **Have I Been Trained?** Search the LAION dataset for your images
  directly (haveibeentrained.com supports image-similarity search). A
  hit there is strong direct evidence your work was in the training
  set.
- **Model card and disclosure statements.** Save dated copies of the
  model operator's own public statements about training data, cutoff
  dates, opt-out support. These can be screenshotted and themselves
  anchored.

## Binding the chain together

Once you have the individual links, bind them into a single artifact:

1. Create a manifest document (PDF or Markdown) listing every file,
   its SHA-256 hash, its Bitcoin timestamp receipt ID, every
   contemporaneous record, every opt-out submission, and every
   reproduction test.
2. Hash that manifest.
3. Anchor the manifest hash via OpenTimestamps. Now the *manifest
   itself* is dated to a Bitcoin block, and any later changes to it
   are detectable.
4. Consider notarizing a printed cover sheet of the manifest listing
   the SHA-256 hashes. This binds your legal identity to the manifest
   contents (see our [comparison of OpenTimestamps and notary
   services](https://orphograph.com/blog/opentimestamps-vs-notary)).
5. Store the manifest, the individual evidence files, and the receipts
   in at least two locations — one online (encrypted cloud), one
   offline (encrypted local drive or USB).

The manifest is the document you (or your lawyer) hand to opposing
counsel or to an opt-out registry's compliance team. It is the
*chain*, not any single link.

## What this playbook does not do

Be honest about the limits:

- It does not prevent scraping. It documents work in a way that
  makes scraping disputes defensible after the fact.
- It does not guarantee a legal outcome. Disputes are decided by
  judges, juries, and settlement negotiations, not by evidence
  artifacts alone.
- It cannot retroactively cover work you didn't document at the
  time. Anchor today and the chain starts today.
- It is not a substitute for a lawyer when a real dispute starts.
  It is the evidence base a lawyer will need.

The chain's strength is *multiple independent links*. No single piece
needs to be airtight. Each one being plausible, and all of them being
mutually consistent, is what's hard for an adversary to refute.

## Start the chain today

The most common failure mode is waiting until a dispute exists before
collecting evidence. By then, the contemporaneous records you didn't
preserve are often gone — cached pages purged, email accounts closed,
cloud-backup retention windows lapsed.

The minimum useful step right now: pick your ten most important works,
compute their SHA-256 hashes, and anchor them via OpenTimestamps. That
alone gives you a cryptographic date-of-existence floor for those files.
Build out the other links over the following weeks.

Orphograph hashes your files in the browser (the bytes never leave your
machine), anchors them to five OpenTimestamps calendars, and gives you a
receipt that verifies against Bitcoin forever — even if our service
disappears. Free for one file per month; see the
[verify page](https://orphograph.com/verify/) or
[the playbook landing](https://orphograph.com/lp/prove-photo-pre-ai.html)
to start your chain.
