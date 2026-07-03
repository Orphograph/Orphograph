---
title: How to prove what was in your training set — and when
slug: prove-what-was-in-your-training-set
date: 2026-06-30
canonical: https://orphograph.com/blog/prove-what-was-in-your-training-set/
author: Orphograph
summary: Audits, customer due-diligence, and the EU AI Act all ask the same question of a dataset — what exactly was in it, and by when? A spreadsheet someone can edit later is not an answer. Here is how to make the answer cryptographic, dated, and independently checkable, without the data ever leaving your environment.
description: How to prove what was in a machine-learning training set and when, using a Bitcoin-anchored Merkle receipt over the data, its licenses, and its acquisition log. What it proves, what it does not, and how to do it.
tags: [dataset-provenance, machine-learning, data-governance, merkle, bitcoin, proof-of-existence]
---

# How to prove what was in your training set — and when

Sooner or later, someone asks a hard question about a dataset you
trained on: *what exactly was in it, and by when?* An auditor asks it.
A customer's security team asks it during due-diligence. A regulator
asks it under the EU AI Act's data-governance expectations. And in a
copyright or consent dispute, opposing counsel asks it under oath.

The usual answer is a spreadsheet, a `README`, or a folder someone
swears hasn't changed. None of those are evidence. A spreadsheet can be
edited after the fact. A file's modification date can be set to anything.
A cloud bucket's "last modified" is controlled by whoever owns the
bucket. When the question actually matters, "trust our records" is not a
position you want to be defending.

## Make the answer cryptographic instead

There is a cheap, durable alternative: bind the dataset — together with
the documents that say where it came from — into a single fingerprint and
anchor that fingerprint to a public, tamper-evident record.

Concretely: take the **data**, the **license and consent documents**, and
the **acquisition log** that records where each source came from and under
what terms. Hash every file locally and combine the hashes into one
[Merkle tree](/blog/bitcoin-merkle-roots-unforgeable-timestamps). The
single root of that tree is a 64-character value that could only have come
from those exact bytes. Anchor that root to the Bitcoin blockchain, whose
block dates no one can forge or back-date, and you have a dated,
independent record that the whole bundle existed in that exact form by
that moment.

The dataset itself never leaves your environment. Only the fingerprints —
the manifest of paths and digests and the root — are anchored. An
air-gapped workflow anchors nothing at all and keeps the proof entirely
in-house.

## What a dataset receipt proves

- **The set existed in this exact form by the anchored date.** Every file
  — the data, the licenses, the acquisition log — is committed to one
  Bitcoin-anchored root.
- **Nothing has changed since.** Relabel one image, swap a license, edit
  the log, and the root changes. If the root still matches, the bundle is
  byte-for-byte what was anchored.
- **Each file is independently verifiable.** A Merkle inclusion proof lets
  anyone confirm one specific file belonged to the certified set without
  seeing — or trusting you about — any other file in it.

## What it does *not* prove

Honesty about scope is the whole point; a provenance tool that overclaims
is a liability.

- It does **not** prove the data was lawfully sourced, licensed, or owned.
  A receipt records that bytes existed at a moment, not the legal right to
  them.
- It does **not** prove the acquisition log is truthful. The log is
  anchored as written — its accuracy is a separate question.
- It does **not** prove authorship. A fingerprint binds a file to a date,
  not to an author.

It is corroborating evidence of **integrity and time** — strongest when the
acquisition log it anchors is itself honest and complete.

## How to do it

You can anchor a dataset bundle as a step in a training or release
pipeline. The output is a hosted, shareable **provenance certificate** —
the summary, the honest scope above, your license and acquisition-log
documents, the full file manifest, and a verifier where an auditor can
drop any file and confirm in their own browser that it belongs to the set.
A live example is here:
[a sample dataset certificate](/certificate/DatasetProvenanceSample).

The format is open. The Bitcoin anchor uses
[OpenTimestamps](/blog/opentimestamps-for-non-developers), and any receipt
verifies with the public, MIT-licensed
[verifier](/verify/) — no account, no dependency on us. If this office
disappeared tomorrow, the anchor and the manifest are all anyone needs.

## Where it matters

The same artifact answers several questions at once: it supports an **EU AI
Act** data-governance record, it gives a customer's security team something
checkable during **due-diligence**, and in a **copyright or consent
dispute** it converts "we had this before your registration" from a
credibility argument into a fingerprint-and-block-height check.

If you train on data, this is a cheap habit with a long tail of value.
See [how dataset provenance works](/dataset-provenance), then anchor your
first dataset — the data stays on your machine; only the proof leaves.
