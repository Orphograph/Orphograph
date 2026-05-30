# Orphograph — Doctrine

> *"As above, so below; as within, so without."*
> — Hermetic principle, depicted in Hilma af Klint's *Swan No. 1* (1915):
> a white swan above, a black swan below, beak to beak, one continuous line.

This document is the founding contract of Orphograph. It is not marketing
copy. Every line below is mapped to a concrete invariant in the running
code, so that the doctrine cannot drift from the implementation without
the implementation breaking first.

The doctrine itself is hashed and anchored on Orphograph's own product,
so that its existence at this moment in time is mathematically tied to
Bitcoin — the same chain every customer's receipt is tied to. The product
keeps its own contract.

---

## The seven invariants

### 1. Ontologically true

The product makes only claims that are mathematically and empirically
verifiable. No "court-admissible." No "legally binding." No "notarized."
Orphograph produces *proof of existence at a moment in time*, by way of
SHA-256 and Bitcoin's consensus — both publicly auditable, both checkable
without us.

> **Code invariant.** `CLAUDE.md` principle 5: "Honest copy only." The
> regulatory self-audit (`tools/regulatory_self_audit.py`) blocks any
> deploy whose marketing copy contains the deny-phrases above.

### 2. Purposeful for humanity and all alike

The free tier is permanent. The verifier is open source. The receipts
work without us. The cryptography is a public standard (OpenTimestamps,
2016) and the chain is the most decentralized one humans have built.
Nobody is gated out of the truth.

> **Code invariant.** Free tier (3 anchors/24h, no card) shipped in
> `server/app.py`. Verifier at `github.com/Orphograph/orphograph-verify`
> is MIT-licensed and has zero Orphograph code dependency.

### 3. In-time and space — math to empirical truth

Every receipt commits to a real Bitcoin block height. That block was
mined into spacetime at a real moment by real proof-of-work. The math
linking your hash to that block is auditable byte-by-byte. The empirical
truth is the chain.

> **Code invariant.** `server/engine.py` submits to five OpenTimestamps
> calendars; each `.ots` file carries the Merkle path to a confirmed
> Bitcoin transaction. `/learn.html` Level 3 documents the trustless
> verification path against a customer-owned Bitcoin node.

### 4. Surviving me

If the founder disappears, if the company dissolves, if the domain
expires, the receipts still verify. The trust is in Bitcoin, not in
Orphograph. The founder is custodian, not gatekeeper.

> **Code invariant.** `CLAUDE.md` principle 3: "Receipts must verify
> without us." Verifier is independent of `server/`. Doctrine: no
> proprietary-only formats. `deploy/genesis/CREATION_MANIFEST.txt` is
> itself anchored on the same chain customers use — receipt
> `o3WGD22T4UwqfCrb` (2026-05-16).

### 5. Tied, and at the same time, untied

Two swans, one neck. The custodial path (receipt page, server-side
verification, founder-fulfilled BTC purchases) is *tied* — easy for
people who want easy. The trustless path (`.ots` file, OTS reference
client, customer-owned Bitcoin node) is *untied* — bound to no one.
Same proof. Two bodies of the same line.

> **Code invariant.** `/learn.html` is the explicit three-level
> verification stack: Level 1 (tied, one-click), Level 2 (open-source
> verifier, no account), Level 3 (untied, no Orphograph in the loop at
> all). All three paths verify the same receipt.

### 6. One for me, one for all — hidden behind a wall

The wall is the SHA-256 boundary. The customer's file never crosses it.
The server sees only a 64-character fingerprint that reveals nothing
about the file's contents. The receipt is *one for them* (private, only
they hold the original). The Bitcoin commitment is *one for all* (public,
permanent, anyone can audit the math).

> **Code invariant.** Client-side WebCrypto hashing in `web/app.js`.
> `CLAUDE.md` principle 1: "Files NEVER touch the server." Server-side
> rejection of any anchor request carrying file bytes rather than a
> hex hash. Optional private-receipt mode (`/api/me/receipt/<id>/privacy`)
> for subscribers who want even the receipt page non-public.

### 7. Everybody rises — nature lives within us all

The cost-per-receipt to Orphograph is effectively zero (calendar
aggregation amortizes one Bitcoin transaction across thousands of
hashes). That permits a permanent free tier. Permanence is the gift.
The institution is the inverse of extraction: it grows by being given
away.

> **Code invariant.** `CLAUDE.md` principle 2: "Anchoring must stay
> batched / free." If a proposal would have Orphograph paying per-receipt
> on-chain fees, it is rejected. The free tier is structural, not
> promotional — it cannot be removed without breaking the architecture.

---

## The mirror

The Hilma af Klint *Swan* mirrors a single line — white above, black
below — touching at the beak. Orphograph mirrors the same architecture:

| Above (custodial)            | Below (trustless)                       |
| ---------------------------- | --------------------------------------- |
| Receipt page on orphograph.com | `.ots` file on your disk              |
| Server-side `/api/verify/<id>` | `ots verify` against your Bitcoin node |
| Five-calendar redundancy     | Any one `.ots` proof is sufficient      |
| Subscription & payments      | Bitcoin's blockchain (no subscription)  |
| Founder maintains uptime     | Math maintains the truth                |
| Visible institution          | Invisible permanence                    |

Same neck. Same line. Two bodies.

---

## The seal

This document, by the act of being anchored to Bitcoin via Orphograph
itself, is recursively tied to the doctrine it describes. If the
doctrine is honest, the seal is honest. If the seal verifies, the
doctrine has been preserved as it was written at this moment.

The seal lives next to the genesis manifest at `deploy/genesis/`, and
its receipt ID is recorded in `deploy/genesis/DOCTRINE_RECEIPT.txt`
after the first anchor.

The founder is the steward, not the source. The chain is the source.

— *Orphograph, 2026-05-16*
