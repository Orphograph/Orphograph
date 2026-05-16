---
title: Why your domain dying shouldn't kill your timestamp receipt
slug: why-domain-dying-doesnt-kill-your-timestamp
date: 2026-05-14
author: Orphograph
summary: Most SaaS receipts are worthless when the company disappears. A Bitcoin-anchored timestamp is structurally different — your proof outlives the issuer by design. Here's how, and what to actually save.
tags: [opentimestamps, durability, photographers]
---

# Why your domain dying shouldn't kill your timestamp receipt

The number-one question every working photographer asks before
paying anything for a timestamping service: **what happens to my
proof if your company disappears?**

This is a reasonable thing to ask. The graveyard of
crypto/blockchain notarization services is large enough to be a
genre of its own:

- **Proof of Existence (poex.io)** — abandoned, SSL expired 2021
- **Stampery** — failed / pivoted
- **Po.et** — pivoted, then folded
- **OriginStamp** — still operating but pivoted B2B-enterprise in
  May 2025, abandoning the consumer tier

If you anchored 500 photos through one of those services before it
disappeared, what would you have? Probably a forgotten password to a
defunct login page and a vague memory that you'd "done something."

This post explains why Orphograph receipts are structurally
different — your proof works without us, by construction — and
exactly what files you need to save to make that promise real.

## The two kinds of timestamping services

There are two architectural shapes a timestamping service can take.
One survives the company's death; the other doesn't.

### Type A — proprietary verifier (don't trust these for the long haul)

The service issues a "receipt" that's basically a database record
keyed to your account. Verification means logging back into their
site and clicking "verify." If the site is down, your receipt is
worthless.

These services usually have nice UX and slick marketing. They sell
trust, not durability.

### Type B — open-standard receipts (these survive)

The service is a wrapper around an open standard. Their receipts
are not their property — they're standardized files anyone can
verify against the underlying chain or protocol. If the wrapper
dies, the receipts still work.

Orphograph is type B by design. So is raw OpenTimestamps. So,
historically, was OriginStamp's consumer tier (though they've
since gone enterprise-only). WordProof is partial — their
receipts include their chain's data but verification requires
their tools.

## How an Orphograph receipt survives us

When you anchor a file with Orphograph, you get back:

1. **A receipt JSON file** (~1KB) — contains the SHA-256 hash of
   your file, the timestamp, and pointers to the OpenTimestamps
   calendar servers that received it.
2. **Five `.ots` proof files** (~200–500 bytes each) — one per
   OpenTimestamps calendar. Each contains the cryptographic path
   from your hash up to a Bitcoin transaction.
3. **A SHA-512 sibling witness** in the receipt (quantum hedge —
   we wrote about that in [the quantum-protection blog post]).

Total bundle: ~3KB. Save it next to your photo. That's it.

To verify five years from now, you need three things — none of
which depend on Orphograph the company still existing:

1. **The original file** (you have it).
2. **The receipt + .ots files** (you saved them).
3. **A copy of our open-source verifier** — available at
   [orphograph.com/verify/](/verify/) right now (MIT, ~100 lines
   of Python, save a copy locally if you're paranoid).

Plus optionally, the public Bitcoin blockchain, which is
replicated across tens of thousands of independent nodes worldwide
and isn't going anywhere.

That's the contract. The math, not our company, is the trust.

## What to actually save

Here's the minimum-paranoid backup posture:

### Tier 1 — what you save next to every photo (zero effort)

Just save the receipt JSON next to your photo, like an `.xmp`
sidecar. The five `.ots` files are tiny; tar them up alongside.

```
my_photo.jpg
my_photo.jpg.orpho.json     ← the receipt
my_photo.jpg.orpho.tar      ← the 5 .ots files
```

Most photographers already do something like this for RAW + JPG
pairs. Add `.orpho.json` to the workflow and you're done. Our
folder-watcher CLI does this automatically.

### Tier 2 — what you save in case of zombie internet (low effort)

Once, save a copy of the verifier somewhere you control:

```bash
curl -O https://orphograph.com/verify/orphograph-verify-0.1.tar.gz
```

Throw it on your existing photo backup drive. The tarball is 5KB.
If Orphograph goes away, the verifier is there. If the verifier
binary on our site changes versions, your saved copy still
verifies your old receipts (the protocol is stable).

### Tier 3 — what you save if you're really worried (moderate effort)

Run [the OpenTimestamps client](https://opentimestamps.org/)
yourself to "upgrade" your .ots files into full Bitcoin merkle
proofs. This bakes the entire Bitcoin transaction proof into the
.ots file, so verification becomes:

1. Re-hash the original file
2. Walk the merkle path inside the .ots to a Bitcoin block hash
3. Verify the block hash against the public chain

No company involved. No website needed. Pure cryptography against
a public ledger.

```bash
pip install opentimestamps-client
ots upgrade my_photo.jpg.orpho.tar/*.ots
ots verify my_photo.jpg.orpho.tar/*.ots
```

If you're anchoring evidence-grade work (legal disputes, IP
litigation), do this for every important receipt within a week of
anchoring. The .ots upgrade locks the proof to a specific Bitcoin
block, after which no future events can affect it.

### Tier 4 — what you save if you're a working journalist or whistleblower (high effort)

For the highest-paranoia use cases:

1. Upgrade the .ots files (tier 3).
2. Anchor your full archive to multiple chains (BTC + ETH + ICP
   via different services) so a Bitcoin-specific failure doesn't
   take you down.
3. Store the receipts on physically distributed media (a USB stick
   in a safe deposit box, a copy at a trusted family member's
   house, an offline-capable hard drive in your studio).
4. Anchor the receipts themselves periodically (Orphograph can
   anchor any file — including a .tar.gz of your other receipts).

This is overkill for 99% of photographers. It's the right move if
you're documenting human-rights abuses or sitting on a
Pulitzer-shaped story.

## What can actually still kill your receipt

To be honest about the threat model:

| Threat | What it kills | What survives |
|---|---|---|
| Orphograph (the company) shuts down | Our website. Our `/api/verify/` shortcut. | **Your receipt + the OSS verifier still work.** |
| OpenTimestamps calendar servers all shut down | The "upgrade" path for new anchors made after the shutdown. | **Already-upgraded .ots files survive.** Once a proof is locked to a Bitcoin block, no calendar is needed for verification. |
| Bitcoin is abandoned / 51%-attacked / forked into oblivion | Trust in the chain itself. | **Already-mined historical blocks remain in the chain's PoW history.** Even a future fork would have to rewrite millennia of accumulated work to fake a historical anchor. |
| You lose your receipt files | Your proof. There's no recovery. | Nothing — this is the one failure mode you control. **Back up your receipts the way you back up your photos.** |
| Your hard drive dies | Same as above. | Same. |

The one threat we can't insulate you from: losing the receipt
file. The cryptography is durable; user error is the limit.

## Why we wrote this

Most SaaS services tell you "you can trust us" and hope the
question of long-term durability never comes up. Photographers
*especially* think in decade-long horizons — your archive is
forever, and a proof tied to a specific company's continued
existence is structurally incompatible with that.

We built Orphograph because the protocol (OpenTimestamps + Bitcoin)
already solves this. We're just a wrapper that makes the protocol
accessible without `pip install`. The receipts you get from us are
the same shape OpenTimestamps would issue directly — we don't add
proprietary fields, we don't watermark them, we don't reserve the
right to invalidate them.

If we're around in 10 years: great. If we're not: your receipts
still work.

That's the whole pitch.

---

*Anchor your first file at [orphograph.com](/). 1 free per month
forever. $7 for a 10-pack. Open-source verifier at
[/verify/](/verify/) so your receipts outlive us, by design.*
