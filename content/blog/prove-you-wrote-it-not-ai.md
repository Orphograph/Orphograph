---
title: How to prove you wrote it — not an AI
slug: prove-you-wrote-it-not-ai
date: 2026-05-17
canonical: https://orphograph.com/blog/prove-you-wrote-it-not-ai/
summary: A practical, no-software walkthrough for writers accused of using AI. Save the file. Anchor its fingerprint. The Bitcoin block height is your alibi.
description: Practical guide for writers, students, and journalists who need to prove a draft is theirs and pre-existed any AI assistance claim.
tags: [writers, ai, attribution, proof-of-existence]
---

# How to prove you wrote it — not an AI

Writers — novelists, students, journalists, copywriters, ghostwriters,
academics — are now routinely accused of having used AI to produce text.
The accusation lands whether or not it's true. Editors, professors,
publishers, and platforms all run AI-detection tools, and those tools
are wrong often enough that the accusation often arrives at honest
people first.

The fix isn't a better detector. There isn't one. Detection tools
hallucinate at scale and contradict each other. The fix is going to
the opposite end of the problem: **proving the file existed before
the accusation could have been made.** If you can show a draft of
the same text existed on disk on day X, anchored to a public ledger
that pre-dates the publication or submission, the detection question
becomes moot. Whether the words look "AI-like" stops mattering. They
existed before the comparison could have been made.

This guide is for any writer who wants a 60-second inoculation against
that conversation.

## What you need

Nothing to install. A browser. A file.

The file can be your manuscript draft, a research note, a chapter
outline, an email, a screenshot of your Scrivener window, a recorded
voice memo, a Word document — any byte sequence. Orphograph treats
everything as raw bytes. It does not read your prose.

## What you do

Three steps:

1. **Save** your draft as a file on your machine. Date the filename
   if you want (the date is for you; the proof doesn't depend on it).
2. **Drop the file** at <https://orphograph.com>. The browser computes
   a SHA-256 fingerprint locally; the file's contents never leave your
   machine. Only the 64-character fingerprint is submitted.
3. **Save the receipt.** You'll get a 16-character receipt ID, a URL
   like `https://orphograph.com/r/o3WGD22T4UwqfCrb`, and five small
   `.ots` proof files. Stash them next to the manuscript in your
   normal backup folder.

That's the whole inoculation. The receipt's hash gets committed to a
Bitcoin block within about an hour. From then on, the receipt is
evidence that the file existed by no later than that block's mined
timestamp.

## What the receipt actually proves

It proves one specific, narrow fact: **a file with this exact SHA-256
fingerprint existed on or before Bitcoin block N.**

That is enough for the AI-suspicion conversation, because the workflow
is:

- You get accused on, say, October 14
- You produce the receipt showing the file existed on, say, June 3
- Anyone with the original file can rehash it and confirm the receipt
  matches
- Anyone with a Bitcoin node can verify the block was mined June 3
- The accuser now has to argue you predicted on June 3 exactly which
  AI-generated text would best match a future accusation, which is
  absurd

The receipt is not authorship in any legal sense. It is a date of
record. For the most common version of this argument — "you wrote
this with ChatGPT after I saw it" — date of record is the entire
ballgame.

## Three workflows for different kinds of writers

**Novelists and long-form authors.** Anchor the draft at the end of
each writing day. A weekly or chapter-end cadence is fine if daily is
too much friction. Keep the receipts in the same folder as the
manuscript backups. Five years from now when a "you used AI to write
that" question arrives — even via casual social media accusation, not
a formal challenge — you have a stack of timestamped artifacts that
trace the actual writing process.

**Students writing essays.** Anchor the file when you first finish a
draft, and again when you submit. If the assignment is contested
later, the receipt set tells the story: here is the draft from
Wednesday night, here is the version I submitted Thursday morning,
here is what the rubric was on Tuesday. Two anchors. Free tier.

**Journalists and academics.** Anchor source documents (recordings,
PDF leaks, datasets) the moment you receive them. Anchor your own
manuscripts at finalization. The receipt set forms a paper trail that
a fact-checker, editor, or court can verify without trusting you or
the publication.

## What it cannot do

- It cannot prove you are the author. Anyone with the file can anchor
  it. Authorship is established through other means (handwritten
  notes, document version history in editing software, witnessing,
  prior correspondence). The timestamp is necessary, not sufficient.
- It cannot prove the file was unedited between draft and submission.
  Each anchor pins one specific byte sequence. Edit one comma, and the
  fingerprint changes — that's a different file with its own receipt
  needed.
- It cannot prove the prose is human-written. Nothing can prove that.
  But it can prove the prose existed before the AI tool being blamed
  was capable of generating it, which is usually the underlying
  question.

## The cost

The free tier covers three anchors every twenty-four hours, perpetual.
Most writers will never need more than that — a chapter-end anchor is
infrequent. The Pack of Fifty is $29, one-time, no subscription;
fifty anchors covers a typical book project. The Standing Order
($9 / month, cancel anytime) is for working writers who anchor
multiple times per day.

The receipts verify against the Bitcoin blockchain regardless of which
tier produced them. The math is identical. Pricing only affects how
many you can produce.

## What survives the service

Every receipt this office issues can be verified later using
[OpenTimestamps](https://opentimestamps.org), the open standard
Orphograph uses underneath, against any copy of the Bitcoin chain.
If Orphograph closes tomorrow, the receipts you hold today still
work. The instrument is in your hands, not on our server.

## Try it

Open <https://orphograph.com>. Drop a draft. Save the receipt. You
now have a defensible date of record for that file. If the
AI-suspicion conversation ever finds you, the receipt is what you
hand over.
