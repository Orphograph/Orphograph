---
title: Digital notary vs cryptographic timestamp — what each one proves
slug: digital-notary-vs-cryptographic-timestamp
date: 2026-05-17
canonical: https://orphograph.com/blog/digital-notary-vs-cryptographic-timestamp/
author: Orphograph
summary: Notaries verify a person signed a document. Cryptographic timestamps verify a file's bytes existed by a date. Different jobs, different proofs, no overlap.
description: Digital notary vs cryptographic timestamp — what each actually proves, why they are not interchangeable, and which one your use case needs.
tags: [legal, notary, timestamps, evidence]
---

# Digital notary vs cryptographic timestamp — what each one proves

Both a digital notary and a cryptographic timestamp produce a piece
of evidence about a document. They are often confused, partly
because both use the word "verify" and partly because both produce
a receipt. They are not the same thing. They prove different
claims. A receipt from one is not a substitute for the other.

This post explains the difference in legal terms, in technical
terms, and in terms of which one you actually need for which
situation. It also makes one thing explicit up front: Orphograph is
a cryptographic timestamping service. It is not a notary public. It
does not perform notarial acts. It cannot replace a notary in any
jurisdiction.

## What a notary public actually does

A notary public is a state-appointed officer whose job is to
witness the signing of a document. The notary's role is narrow and
well-defined:

1. Verify the identity of the signer, usually by checking a
   government-issued ID.
2. Witness the signer apply their signature, in person or via an
   approved remote video session.
3. Apply the notarial seal and signature, attesting that the
   above happened.
4. Record the act in a journal that the notary is legally required
   to keep for years.

A digital or "remote online" notary does the same thing using a
video session and a digital seal instead of an ink stamp. The
underlying legal authority is identical. The notary is still acting
under state commission, still verifying identity, still witnessing
the signature.

What a notarized document proves:

- A specific person, with verified identity, signed this specific
  document on this specific date in the presence of an authorized
  notary.

What a notarized document does not prove:

- That the contents of the document are true.
- That the signer had the legal authority to sign.
- That the document existed before the notarization date in any
  form — the notary only attests to the moment of signing.

If you need a deed, a power of attorney, an affidavit, or any
document where the law requires sworn personal attestation, you
need a notary. There is no cryptographic substitute.

## What a cryptographic timestamp actually does

A cryptographic timestamp does not involve a person at all. It does
not verify identity. It does not witness a signing. It does one
thing: it proves that a specific sequence of bytes existed by a
specific moment in time.

The mechanism is mathematical:

1. You compute a hash of the file. The hash is a 32-byte
   fingerprint that uniquely identifies the file's contents.
2. The hash is anchored into a public, append-only record system
   that includes a verifiable timestamp. In Orphograph's case the
   record system is the Bitcoin chain, accessed via the
   OpenTimestamps protocol.
3. You receive a proof that links the file's hash to the moment
   the record was committed.

What a cryptographic timestamp proves:

- This specific byte sequence existed at or before this specific
  moment.

What a cryptographic timestamp does not prove:

- Who created the file.
- Who possesses the file now.
- Whether the file's contents are accurate, original, or owned by
  any particular party.
- That any person ever saw, signed, or agreed to the file.

The proof is about the bytes, not about the people. A file's hash
is the same whether you generated it, copied it from someone else,
or downloaded it from the open web. The timestamp says only that
those exact bytes were locked to that date.

## The two proofs side by side

If you imagine a contract dispute, the difference becomes obvious.

A notary's receipt answers: "Did Alice, identity verified, sign
this exact agreement on March 4?"

A cryptographic timestamp answers: "Did this exact agreement file
exist by March 4?"

In a fully evidenced case you might want both — the timestamp
proves the document was not edited after the signing, and the
notary proves the right person signed the version that the
timestamp locked. Each one is a different layer of the same record.
Neither one can do the other's job.

## Where the confusion comes from

The phrase "digital notary" gets used loosely. Some software
services call themselves notaries when they are actually
timestamping services. Some blockchain services claim to
"notarize" files when they are only hashing and anchoring them.
The legal term has a specific meaning that none of these services
satisfy.

A useful rule of thumb: if there is no human officer verifying
identity, it is not a notary act. If the service only takes a file
and returns a receipt, it is a timestamping service. The two are
not interchangeable, regardless of how the product page is worded.

Orphograph is in the second category. There is no human in the
loop. There is no identity verification. The service computes a
hash and anchors it. That is the entire mechanism. The receipt
proves a file existed by a date and nothing more.

## Which one your situation needs

Use a notary when:

- The law requires it. Real estate deeds, certain affidavits,
  certain powers of attorney, certain immigration forms — these
  require notarization by statute.
- You need to prove a specific person signed a specific document.
- You need a witness whose role the court already understands.

Use a cryptographic timestamp when:

- You need to prove a file existed before a specific date.
- You need a receipt that anyone can verify without contacting you
  or any service.
- You need an anchor that survives the service that issued it.
- The use case is about the file's content, not about who signed
  it.

Use both when:

- The dispute is likely to turn on both who signed and when the
  document existed. Notarize the signed document, then anchor the
  notarized PDF. The timestamp proves the notarized version was
  the one that existed on that date; the notarial act proves a
  verified person signed it.

## Legal weight in 2026

A notarized document carries a defined legal weight in every U.S.
state and in most jurisdictions worldwide. Courts know what to do
with it. Statutes spell out its effect.

A cryptographic timestamp carries less codified legal weight, but
it is increasingly accepted as evidence. In the EU, qualified
electronic timestamps under eIDAS have an explicit legal
presumption of accuracy. In the U.S., a Bitcoin-anchored timestamp
is treated as a form of self-authenticating digital evidence under
the Federal Rules of Evidence — admissible, but the weight is up
to the trier of fact.

What the court treats it as depends on the dispute. In an AI
training dispute, where the question is "did this file exist
before the training cutoff," a Bitcoin-anchored timestamp is often
the strongest available evidence because the alternative is
self-attested EXIF data, which is trivially editable. In a contract
dispute over who signed what, a timestamp adds little — the notary
is the load-bearing piece.

## What Orphograph is and is not

Orphograph is a cryptographic timestamping service. The browser
computes a hash, submits it to five OpenTimestamps calendar
servers, and returns a receipt that anchors to Bitcoin within an
hour. The receipt verifies against any Bitcoin node, with or
without Orphograph's servers.

Orphograph is not a notary public, not a qualified trust service
provider under eIDAS, and not a court-authorized digital signature
authority. It does not verify the identity of users. It does not
witness signatures. It does not retain a notarial journal. It is a
proof-of-existence service for files, and that is the entire scope.

If you need a notarial act, hire a notary. If you need a qualified
electronic timestamp under eIDAS, contact a qualified TSA in your
jurisdiction. If you need a strong, free or low-cost,
permissionless anchor of "this file existed by this date" — that
is the thing Orphograph does well.

---

*Orphograph is a Bitcoin file-timestamping service. Three free
anchors every 24 hours, $29 for a 10-anchor pack, $9/mo for
unlimited. Receipts verify against any Bitcoin node without our
servers. We are not a notary public.*
