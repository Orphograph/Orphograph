---
title: "OpenTimestamps vs Traditional Notary Services: Which Proves What"
slug: "opentimestamps-vs-notary"
date: "2026-05-15"
author: "Orphograph"
description: "Honest comparison of OpenTimestamps and traditional notary services — cost, scope, jurisdiction, and what each one actually proves about a file."
canonical_url: "https://orphograph.com/blog/opentimestamps-vs-notary"
tags: ["opentimestamps", "notary", "comparison", "proof-of-existence", "timestamping"]
---

# OpenTimestamps vs Traditional Notary Services: Which Proves What

A freelance journalist in Manila finishes a draft on an explosive source
document at 11pm. She wants a timestamp on the file *now*, before she sends
the first email to a fact-checker, so the chain of custody is clean. The
nearest notary opens at 9am the next morning, charges $50–$150, and would
require her to print the document and physically attend. By the time the
notary's stamp goes on paper, the digital file has lived eleven hours in
a state she can't independently prove.

This is one of the most common cases where OpenTimestamps and a traditional
notary diverge. They're often described as substitutes. They mostly are
not. Each one proves a different thing.

## What a traditional notary actually does

A notary public is a state-appointed officer who performs *notarial acts*.
The two most common are acknowledgments (the signer appeared, identified
themselves, and signed willingly) and jurats (the signer swore under oath
that the contents are true). For documents, what gets notarized is usually
a signature on a paper — not the content. The notary verifies your
identity, watches you sign, and stamps the paper with their seal.

The legal weight is real, but narrower than most people assume:

- The notary proves **someone with your ID appeared on a date** and
  signed the specific document in front of them.
- It does **not** prove the contents are true.
- It does **not** prove an electronic file is identical to the one
  notarized — unless you used a remote online notary (RON) with file
  hashing, which is jurisdiction-dependent and not universal.
- Notarial acts are recognized in a specific jurisdiction. International
  recognition often requires an apostille (Hague Convention) or further
  consular legalization.

A notary's value is reputational and procedural: they're a trusted human
witness, accountable to their state, with a paper trail in their journal.
Courts have several centuries of practice interpreting notarial acts.

## What OpenTimestamps actually does

OpenTimestamps (OTS) is a free, open protocol that takes the SHA-256 hash
of a file, batches it with thousands of other hashes into a Merkle tree,
and writes the tree's root into a Bitcoin transaction. Each user gets a
proof file (`.ots`) showing the chain from their hash up to the Bitcoin
transaction. Anyone with the file, the `.ots` proof, and access to a
Bitcoin node (or a public block explorer) can verify the hash existed by
the time of the Bitcoin block.

What OTS proves:

- A specific 32-byte hash existed at or before a specific Bitcoin block.
- That hash uniquely corresponds to a specific file. Change one bit, and
  the hash no longer matches.
- The proof is verifiable without trusting OTS itself, the calendar
  servers, or any timestamping vendor. Only Bitcoin's consensus matters.

What OTS does not prove:

- Who created the file.
- Who submitted the hash.
- That the file is true, accurate, original, or authorized.
- That you have any rights to the content.

OTS is a clock. The notary is a witness. Those are different evidentiary
primitives.

## Side-by-side comparison

| Property | Notary public | OpenTimestamps |
|---|---|---|
| Proves identity of signer | Yes (with ID check) | No |
| Proves file existed by date | Indirectly, via signed paper | Yes, cryptographically |
| Proves file is unchanged | No (paper-based) | Yes (any change = new hash) |
| Cost per document | $5–$50 typical, RON $25–$80 | Effectively $0 (batched) |
| Time to complete | Minutes to days | ~10–60 minutes to Bitcoin confirmation |
| Jurisdiction | State-specific; apostille for international | Universal; verifiable anywhere with internet |
| Requires trusting a third party | Yes (the notary, their state) | No, after verification |
| Survives the issuer disappearing | Yes (state journal) | Yes (Bitcoin chain) |
| Recognized in court | Centuries of precedent | Limited, jurisdiction-dependent |
| Recognized by AI opt-out registries | Rarely | Increasingly common |
| Bulk capable | No (one at a time) | Yes (thousands per second) |

## When a notary is the right tool

- You're signing a contract and need an enforceable acknowledgment of
  identity and willingness.
- The other party requires notarization (real estate, certain affidavits,
  some immigration documents).
- Jurisdiction-specific formalities demand it (deeds, wills in many US
  states, sworn statements).
- You need an officer of the state to verify identity, not just timestamp
  a file.

For these, OpenTimestamps is not a substitute. It does not verify
identity. It does not administer oaths. A judge in a probate dispute
will not accept "I have an OTS proof" in lieu of a witnessed signature.

## When OpenTimestamps is the right tool

- You want to prove a digital file existed at a specific date.
- You're a photographer, writer, designer, or developer worried about
  later disputes over creation date.
- You're building evidence for AI-training opt-out, copyright timeline,
  or prior-art claims.
- You have hundreds or thousands of files to timestamp — notarization
  doesn't scale, OTS does.
- You need the proof to be verifiable by anyone, anywhere, decades from
  now, without trusting any company to still exist.

For these, a notary is overkill, slow, expensive, and often legally
mismatched. A notary timestamps a *signature*, not a *file*.

## The hybrid case

The most defensible posture for high-stakes work is to use both.
Notarize a *cover sheet* that lists the SHA-256 hashes of your files and
references the OTS receipt IDs. The notary's stamp now binds your
identity and signature to a specific set of hashes; the OTS receipt
binds those hashes to a Bitcoin-confirmed point in time. Together:

- Notary establishes: "This person signed, on this date, a document
  listing these hashes."
- OTS establishes: "These hashes existed by this Bitcoin block."

That combination gives you authorship attestation + cryptographic
existence proof, with two independent, durable trust anchors. It costs
$25–$80 in notary fees (typically once per batch of files), plus zero
incremental for the OTS receipts.

## The honest framing

OpenTimestamps is not a replacement for notarization. It is a different
evidentiary tool that fills a gap notaries cannot — proving an
electronic file existed at a specific time, at internet scale, for
effectively zero marginal cost, verifiable by anyone forever without
trusting any institution.

If your use case is *"prove this digital file existed by date X"*, OTS is
the right primitive. If your use case is *"prove someone with this
identity signed this document"*, you need a notary. If your use case is
*"prove both, defensibly"*, use both.

Orphograph is a hosted layer on top of OpenTimestamps — it hashes your
files in the browser (the bytes never leave your machine), submits the
hash to five independent OTS calendars, and packages the resulting proof
into a single receipt JSON plus per-calendar `.ots` files. You can verify
any receipt with the [standalone verifier](https://orphograph.com/docs/api.html)
or the [verify page](https://orphograph.com/verify/) — no Orphograph
account required, ever.
