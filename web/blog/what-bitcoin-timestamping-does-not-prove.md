---
title: "What Bitcoin Timestamping Does NOT Prove"
slug: "what-bitcoin-timestamping-does-not-prove"
date: "2026-05-15"
author: "Orphograph"
description: "An honest list of the things Bitcoin-anchored proof-of-existence does not establish — authorship, truth, ownership, legal admissibility — and why understanding the limits matters."
canonical_url: "https://orphograph.com/blog/what-bitcoin-timestamping-does-not-prove"
tags: ["bitcoin", "timestamping", "limitations", "proof-of-existence", "honest"]
---

# What Bitcoin Timestamping Does NOT Prove

A documentary filmmaker in Buenos Aires gets into a heated thread on a
photography forum about AI-trained image generators. He drops a Bitcoin
timestamp receipt for one of his hero shots, expecting that to settle the
question of whether the image is "his." Another forum member, who happens
to be a paralegal, replies: *"That proves the file existed by January 2024.
It does not prove you took the photo. It does not prove you own it. And it
is not, in itself, court-admissible evidence."*

He is right. And anyone selling Bitcoin timestamping who fails to say so
plainly is overselling.

Orphograph runs on OpenTimestamps, which writes hashes into the Bitcoin
blockchain. That cryptography is genuinely powerful for what it actually
does. But marketing copy from this corner of the industry routinely
overstates what it accomplishes. Here is an honest list of the things a
Bitcoin timestamp does not prove — and why understanding the limits is
the difference between defensible evidence and a confident-sounding
mistake.

## 1. It does not prove authorship

The hash in a Bitcoin block proves a 32-byte fingerprint existed at a
specific point in time. It says nothing about *who* submitted the hash or
*who* created the file. Anyone who has the file can compute its hash and
submit it. If you steal a photo and timestamp it before the original
photographer does, you have a timestamp — not authorship.

Authorship requires a separate evidence chain: original raw files,
metadata patterns, behind-the-scenes captures, contemporaneous emails or
social posts referencing the work, client invoices, drafts that show
progression. A Bitcoin timestamp is one *node* in that chain. It is not
the chain itself.

## 2. It does not prove ownership

Ownership is a legal status, not a cryptographic one. Even if you can
prove you created a work, copyright assignment, work-for-hire clauses,
employment contracts, marriage-asset rules, and licensing agreements all
affect who owns the rights. A Bitcoin timestamp says nothing about any of
that. The timestamp survives a copyright dispute exactly as well as any
other piece of factual evidence about the file's existence — which is to
say, it is useful but not dispositive.

## 3. It does not prove truth

A timestamped document is not a true document. You can write a complete
fabrication, hash it, anchor the hash to Bitcoin, and now you have proof
that *this exact false statement* existed by a specific date. That can be
useful (you can later prove you said the false thing first, e.g. for
defamation defense or to show the evolution of a claim), but it is the
opposite of evidence of truth. A timestamp is content-neutral.

## 4. It does not prove the shutter clicked when EXIF says it did

For photos, this is the failure mode that comes up most. EXIF metadata —
including `DateTimeOriginal`, GPS coordinates, camera body, lens — is
trivially editable. A Bitcoin timestamp on a JPEG with EXIF saying 2018
only proves the file (including its possibly-forged 2018 EXIF) existed
by the timestamp date. It does not validate the EXIF claims.

To strengthen EXIF claims, you need either (a) capture-time timestamping
done on-device the moment the shutter fires, or (b) corroborating evidence
from a separate system: a cloud upload log, a GPS app's location history,
contemporaneous emails with the file attached, etc.

## 5. It is not "court-admissible" by default

This phrase appears in too much timestamping marketing, including from
vendors who should know better. *Admissibility is a judge's call in a
specific case in a specific jurisdiction.* A Bitcoin timestamp is a piece
of technical evidence. Whether a court admits it depends on:

- The rules of evidence in that jurisdiction (FRE in US federal court,
  state rules elsewhere, civil-law presumptions in other countries).
- Whether the proponent can authenticate it — usually requires an
  expert witness or stipulation.
- Whether the case is the kind where digital chain-of-custody matters.
- Whether the judge has seen Bitcoin timestamps before.

Some EU jurisdictions recognize qualified timestamp authorities under
eIDAS Regulation 910/2014 with specific legal presumptions. OpenTimestamps
is **not** a qualified timestamp authority. It is an open protocol with no
EU qualification status. It can still be entered as evidence and explained
by an expert — but anyone marketing OTS-based proofs as "legally binding"
or "qualified eIDAS timestamps" is misstating the regulatory status.

For US courts, federal evidence rules (FRE 901, 902) require
authentication of digital records. A Bitcoin timestamp can be
authenticated — Bitcoin's transaction history is a public, immutable
record — but it requires a competent witness who can explain SHA-256,
Merkle trees, and proof-of-work to the trier of fact.

## 6. It does not retroactively cover the past

If you anchor a file today, you have proven it existed today. You have
*not* proven it existed yesterday, last year, or before some AI
model's training cutoff in 2023. The earliest date you can claim is the
date of the Bitcoin block your hash chains up to.

This is the most painful limit for photographers and writers who didn't
timestamp older work. There is no way to retroactively put a 2018 hash
into a 2018 Bitcoin block. Anyone who claims they can offer "backdated"
or "retroactive" timestamps is selling fraud.

The honest workflow: anchor everything you have *now*, then keep anchoring
new work as it's created. Going forward, your evidence improves
monotonically. For older work, the timestamp from today plus other
contemporaneous evidence (cloud backup logs, email attachments, social
posts) is what you have.

## 7. It does not stop scraping or copying

A timestamp is passive evidence. It does not encrypt the file, watermark
it, prevent it from being downloaded, or block AI training scrapers. It
is the receipt, not the lock. If someone scrapes your portfolio site,
trains a model, and produces derivative work, the timestamp is useful
*after* the fact, in disputes or opt-out claims. It does not prevent the
initial harm.

If you want preventative measures, you need a different layer: robots.txt
plus active scraper-blocking, adversarial watermarks (Glaze, Nightshade,
PhotoGuard), opt-out registries with major dataset operators (LAION,
Common Crawl), and platform-level controls.

## 8. It does not prove integrity of the surrounding context

A hash matches a specific sequence of bytes. If your photo includes EXIF
metadata, the hash includes the EXIF. If you re-export the same image
with one EXIF field changed, the hash changes, and the original timestamp
no longer applies to the new file. This means:

- Recompressing a JPEG breaks the proof.
- Stripping EXIF for web publication breaks the proof.
- Cropping, color-grading, watermarking — all produce a new file with a
  new hash. The original timestamp doesn't transfer.

Best practice: keep the original master file (the one you hashed)
unchanged in cold storage. Publish derivatives, but archive the master
plus the receipt forever.

## 9. It does not prove the file is unique

Two different photographers can independently photograph the same
landmark at similar angles. Both can timestamp their files. Both
timestamps are valid. Neither proves "I am the first person to have ever
captured this scene." A timestamp is per-file, not per-idea.

For ideas, processes, and inventions, the right primitive is a patent or
a defensive publication — not a Bitcoin timestamp.

## 10. It does not handle key compromise on your end

If someone gains access to your file storage and silently exfiltrates a
file before you anchor it, your future timestamp is still valid for the
file — but the adversary now has a copy they can timestamp first. The
timestamp does not detect this kind of pre-existing exposure.

This is why workflows matter. Anchor at capture time (or as close as
possible). Anchor before publishing. Anchor before sharing with
collaborators. The closer to the moment of creation, the smaller the
window for someone else to claim first-anchor.

## Why these limits are the feature

A tool that's clear about what it doesn't do is more trustworthy than a
tool that promises everything. Cryptographic proof-of-existence is a
*narrow* primitive done *well*. It proves one specific thing — a file's
existence by a specific Bitcoin block — and it does so cheaply, durably,
and verifiably by anyone without trusting the issuer.

Stack it with the other tools (notarization, EXIF, contemporaneous
records, opt-out registries, watermarking) and you build defensible
evidence chains. Treat it as a one-shot legal silver bullet and you
will be embarrassed in a dispute.

Orphograph sells proof-of-existence. We do not sell legal evidence,
court admissibility, authorship attestation, or any other thing the
underlying cryptography cannot deliver. The
[verify page](https://orphograph.com/verify/) lets anyone validate any
receipt against the Bitcoin chain without trusting us. That is what
"honest infrastructure" looks like.
