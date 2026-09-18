---
title: Why Orphograph uses five OpenTimestamps calendars instead of one
slug: why-5-opentimestamps-calendars-not-1
date: 2026-05-17
canonical: https://orphograph.com/blog/why-5-opentimestamps-calendars-not-1/
author: Orphograph
summary: Submitting a hash to five OpenTimestamps calendar servers removes any single point of failure. The redundancy argument, explained in full.
description: Why submitting to five OpenTimestamps calendars instead of one matters — separate operators, no single point of failure, receipt survives outages.
tags: [opentimestamps, redundancy, infrastructure, reliability]
---

# Why Orphograph uses five OpenTimestamps calendars instead of one

When Orphograph anchors a file, the browser submits the file's
hash to five different OpenTimestamps calendar servers, not one.
The receipt you download contains five `.ots` proof files, one per
calendar. This is more bytes, more network calls, and more moving
parts than the minimum required to produce a working anchor.

The reason is straightforward: one calendar is a single point of
failure, and a timestamping service whose receipts can stop
verifying because one server went away is not actually a
timestamping service. This post explains what a calendar does, why
running five of them matters, and what redundancy actually buys
you in practice.

## What a calendar server does

A calendar server is the piece of OpenTimestamps infrastructure
that sits between a file's hash and the Bitcoin chain. The job is
narrow:

1. Receive submitted hashes from many users.
2. Batch them into a Merkle tree.
3. Write the tree's root into a Bitcoin transaction approximately
   once per hour.
4. Hold on to the per-user Merkle paths so that any user can later
   retrieve the proof linking their hash to the on-chain root.

A calendar does not custody any user's file. It does not require
identity. It does not authenticate users. It is a stateless batching
service in front of a Bitcoin write.

The calendar's value is operational. It runs the equipment that
submits to Bitcoin so that individual users do not have to. It
keeps the historical Merkle paths so that anyone can come back
years later, supply a hash, and receive the chain of proofs back
to the on-chain root.

Run by more than one operator. The public calendars are
`alice.btc.calendar` and `bob.btc.calendar`, both on the
OpenTimestamps project's own domain, plus
`finney.calendar.eternitywall.com` and `btc.calendar.catallaxy.com`.
The `a.pool` and `b.pool` addresses are aggregators: front doors
that batch requests into alice and bob. That is four calendars under
three separately run domains.

## What goes wrong with one calendar

Now imagine a service that submits each user's hash to exactly one
calendar. That receipt has a small list of failure modes that
collectively form a single point of failure.

The calendar goes offline temporarily. Submission fails. The user
gets no receipt. This happens regularly. Calendars are run by
volunteers and small organizations. Maintenance windows, hardware
failures, and brief outages are normal.

The calendar goes offline permanently. The historical Merkle paths
are lost. Even though the on-chain Merkle root is still on
Bitcoin, the path connecting the user's hash to that root lives on
the calendar's server. Without the path, the proof does not
verify. The on-chain anchor is still there but it is unreachable.

The calendar is compromised. An attacker takes control of the
server and starts issuing fake `.ots` files. Without comparing
against an independent calendar, a downstream verifier has no
external check on the issued proof.

The calendar operator stops maintaining the service. The most
common ending. Volunteer-run infrastructure gets neglected. The
domain expires. The host migrates. Whatever the reason, three
years from now the URL stops resolving.

Each of these is a survivable event for the Bitcoin chain — the
chain does not care that a calendar went away. But it is not
survivable for the user whose receipt only points to that
calendar's Merkle path.

## How five calendars fix this

Orphograph submits each hash to five servers: the two aggregators,
plus alice, finney and catallaxy directly. The receipt carries five
proof files that lead to four distinct calendars, each with its own
on-chain anchor. The `a.pool` proof and the alice proof end in the
same alice transaction. The receipt verifies if any one of the five
still verifies.

The math is simple. If each calendar has a 95% probability of
being available three years from now — a reasonable working
estimate based on the last decade of OpenTimestamps operation —
then the probability that all four calendars fail simultaneously is
roughly 0.05 to the fourth power, or about one in 160,000. That
assumes they fail independently. Alice and bob share an operator, so
a cautious reader should count three: about one in 8,000. Either
way, the probability that at least one survives is far higher than
with a single calendar, where it is 95%.

This is the entire redundancy argument. Separate operators mean
separate failure modes. Failures must coincide for
the receipt to break. Coincident failures of unrelated
infrastructure are rare enough to ignore.

The cost is small. Each calendar accepts the hash for free.
Submission is five HTTP POSTs instead of one. The receipt is a few
kilobytes larger. There is no per-anchor pricing difference.

## What "independent" actually means here

Redundancy only works where the calendars are genuinely independent.
Five servers run by one operator in one data center is not
redundancy — a single power outage takes all of them down.

So here is the plain count. Of the five servers Orphograph submits
to, two are aggregators for alice and bob, which sit on the
OpenTimestamps project's own domain. Finney runs under
eternitywall.com and the fifth calendar under catallaxy.com. That is
four calendars and three separately run domains, not five of each.
For every proof to be lost, all three would have to fail together,
before the proofs were upgraded. Once a proof is upgraded it carries
its full path to a Bitcoin block and no longer needs its calendar at
all.

Separate operators also mean separate trust assumptions. If one
calendar issues a fake proof, the others will not match. A verifier
can compare across calendars and notice the inconsistency
immediately. With one calendar, there is no cross-check.

## The point you can check yourself

If you want to confirm that the redundancy is real, the receipt
shows it. The receipt zip from any Orphograph anchor contains five
separate `.ots` files: `a.ots`, `b.ots`, `alice.ots`, `finney.ots`
and `btc.ots`. Run `ots info` on each. A pending proof names the
calendar behind it; an upgraded proof shows the Bitcoin transaction
it ends in. `a.ots` and `alice.ots` name the same calendar and end
in the same Bitcoin transaction; the other three each end in their
own. You can verify each one with the OpenTimestamps command-line
tool:

```bash
ots verify -f my_photo.jpg alice.ots
ots verify -f my_photo.jpg b.ots
ots verify -f my_photo.jpg finney.ots
ots verify -f my_photo.jpg a.ots
ots verify -f my_photo.jpg btc.ots
```

Each verification is a confirmation against Bitcoin, and the five
proofs end in four different transactions. If two of those succeed,
the proof is already redundantly anchored.

For a receipt to fail verification entirely, all five would have
to fail simultaneously. That is the threshold Orphograph is
designed against.

## Why this matters for long-lived receipts

A timestamping receipt is supposed to be good for the working life
of Bitcoin — decades, by current estimates. The companies running
calendars are not all going to outlive that period. The companies
running timestamping services certainly are not. Orphograph, like
every software company, may not exist in twenty years.

The receipt's job is to survive all of them. The way it does that
is by being self-contained: the original file, plus the receipt
zip, plus a Bitcoin node, plus the open-source verifier, is
everything required to confirm the anchor. Nothing in that chain
depends on Orphograph still being online. Nothing depends on any
specific calendar still being online — as long as at least one is.

Five calendars is the practical engineering answer to "what would
need to be true for this receipt to still work in 2046?" One
calendar surviving is enough. Four calendars give you three
redundant ways for that to happen.

## The trade-off

The cost of five-calendar fan-out is not zero. The office makes five
submissions instead of one, the receipt is roughly five times
larger, and there is more code to test. The Orphograph receipt
format adds about 4 KB per anchor for the additional `.ots` files.

In exchange, the receipt has no single point of failure on the
calendar side. The redundancy is the entire reason for the
overhead. For a service whose receipts are supposed to verify
indefinitely, that trade-off is the right one.

There is no version of the product where you only get one
calendar's proof. The five-calendar receipt is the only receipt
Orphograph issues.

*Corrected 2026-09-18. An earlier version of this post counted five
calendars and five operators, and said each proof ended in its own
Bitcoin transaction. Two of the five servers are aggregators for
alice and bob, so the five proofs reach four calendars. The text
above carries the corrected count; the address of the post is
unchanged.*

---

*Orphograph is a Bitcoin file-timestamping service. Three free
anchors every 24 hours, $29 for a 10-anchor pack, $9/mo for
unlimited. Five calendar servers, four distinct calendars, no single point of
failure. Receipts verify against any
Bitcoin node without our servers.*
