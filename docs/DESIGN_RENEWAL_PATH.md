# DESIGN: Renewal Path (Re-Anchoring for Cryptographic Obsolescence)

Status: DESIGN ONLY — no implementation in this cycle. No code was changed to
produce this document.
Scope: strictly additive. No existing endpoint, receipt field, manifest field,
`.ots` byte, or verifier behavior changes meaning. **An issued receipt's meaning
must never change**, and every rule below is written to make that structurally
true rather than merely intended.

Language discipline (binding on all copy derived from this doc):

* **Tamper-evident, not tamper-proof.** Renewal makes a later alteration
  detectable; it does not make alteration impossible.
* No court-admissibility or legal-evidentiary-weight framing. This document
  describes what technical standards specify and what code would do.
* No authorship, AI-detection, or content-authenticity claims. Renewal
  preserves an existence-and-order commitment; it never widens what that
  commitment says.
* No "quantum-proof" / "quantum-immune" phrasing. Per `RFC 4998` §7, renewal
  preserves evidence **conditionally**, and the conditional must travel with
  the claim every time it is made.
* A receipt with no renewal record is **not** weaker than it was the day it was
  issued. Copy may never imply that un-renewed receipts are degraded.

Companion documents: `docs/QUANTUM_EXPOSURE_AUDIT.md` (risk item 14 is the gap
this design closes), `docs/DESIGN_EDIT_LINEAGE.md` (the machinery §2.2 assesses
for reuse), `docs/VERIFIER_SPEC.md`, `docs/THREAT-MODEL.md`.

---

## 0. Grounding — what exists today (verified against the code)

All statements below were read from the tree on **2026-08-05**. Where a fact is
taken from the brief rather than verified here, it is labelled as such.

| Component | File:line | Facts used |
|---|---|---|
| Anchor submission | `server/engine.py:33-35,52-75` | `OTS_TAG_SHA256 = b"\x08"`. `_submit` posts exactly 32 bytes; `_build_ots` frames `MAGIC ‖ VERSION ‖ 0x08 ‖ hash_bytes ‖ calendar_body`. **Only SHA-256 leaves the process.** |
| Calendars | `server/engine.py:37-43` | Five endpoints: `a.pool.opentimestamps.org`, `b.pool.opentimestamps.org`, `alice.btc.calendar.opentimestamps.org`, `finney.calendar.eternitywall.com`, `btc.calendar.catallaxy.com`. Three distinct operators, one chain. |
| Receipt record | `server/engine.py:382-451` | Fields written at anchor time: `receipt_id, created_at, hash_hex, sha512_hex, client_label, source, private, owner_id, attestation, c2pa_manifest_hash, metadata, calendars_ok, calendars_total, successes, failures`; plus `zk_provenance`, `hardware_attestation`, `lineage` when present (shape-stability rule: written only when set). |
| `sha512_hex` today | `server/engine.py:283,297-310,386`; `server/app.py:2236,2265-2266` | Optional keyword; validated for shape only; **client-supplied, never independently derived, never anchored.** `app.py` silently drops it when it is not a string. Confirmed by `docs/QUANTUM_EXPOSURE_AUDIT.md` §2.1. |
| Folder anchors | `server/merkle.py:37,98-111` | `ALGORITHM = "orphograph-merkle-v1-rfc6962"`, `VERSION = 1`. Leaf `SHA-256(0x00 ‖ rel_path ‖ 0x00 ‖ file_sha256)`; internal `SHA-256(0x01 ‖ L ‖ R)`; lone node promoted (no CVE-2012-2459 duplication). Folder receipts carry **no** `sha512_hex` at all — the anchored value is a root, not a file digest. |
| Reserved-leaf lineage | `server/engine.py:106,123-132,135-226,229-277` | `RESERVED_PARENT_PATH = ".orphograph/parent"`. `derive_lineage_from_manifest` re-derives the leaf and re-folds the tree via `merkle.MerkleTree.from_manifest`; hints are never authoritative. `attach_lineage` **rewrites `receipt.json`** post-anchor (`:271-272`). |
| Post-anchor receipt mutation | `server/upgrade_worker.py:252-287` | Rewrites `receipt.json`, adding/overwriting `status`, `btc_pinned_at`, `pinned_count`, `pinned_total`, `upgrade_attempts`, `upgrade_stalls`, `upgrade_frozen`, `upgrade_frozen_at`, `upgrade_frozen_reason`; `_send_pin_email_if_needed` (`:183,190`) additionally sets `pin_email_sent_at` and `integration_email_sent_at`. **`receipt.json` is a mutable file.** |
| Post-anchor `.ots` mutation | `server/upgrade_worker.py:241` | `ots_path.write_bytes(new_blob)` — the `.ots` blobs are **rewritten in place** as pending attestations complete. `.ots` bytes are mutable by design. |
| `.ots` discovery, server | `server/engine.py:784` | `verify_receipt` does `receipt_dir.glob("*.ots")` — **non-recursive** — and marks any `.ots` whose embedded 32 bytes ≠ `hash_hex` as `ok: false`, lowering `calendars_ok`. |
| `.ots` discovery, offline | `dist/orphograph-verify/verify_lineage.py:151-181` | `_ots_static_check` does `link_dir.glob("*.ots")` — **non-recursive** — and returns `ok=False` on any `.ots` whose embedded hash ≠ the receipt's `hash_hex`. |
| Export bundle | `server/receipt_export.py:48-58` | `export_zip` packages `receipt.json`, `manifest.json` (when present), and `receipt_dir.glob("*.ots")` — **non-recursive**. This zip is what customers actually hold. |
| Offline verifier | `dist/orphograph-verify/verify.py:99-234` | `file` mode walks an inclusion proof; `folder` mode re-derives the root with `merkle.MerkleTree.from_folder` (a **filesystem walk**, not a manifest re-fold). `_ots_subcheck` (`:65-96`) substring-matches `root_hex` in the `ots` client output and does not gate on `returncode` (classical defect, logged in audit §8.4). |
| Issued receipts | brief | ~245 receipts issued to date. **Not verified in this tree** — the local development `receipts/` directory holds 11. Every count in §6 is therefore stated as "the issued set", not as a verified number. |

Three grounded observations drive the whole design:

1. **Only SHA-256 reaches Bitcoin.** `_submit` sends 32 bytes; `OTS_TAG_SHA256`
   is the only tag emitted. Any second algorithm can therefore only ever be
   committed *via* SHA-256, never *instead of* it. §3 states the ceiling this
   creates and does not pretend past it.
2. **`receipt.json` and `.ots` are mutable files.** A renewal record that
   commits to their bytes would be silently voided by the next background
   `upgrade_worker` run, with no human action and no error. §2.3 is written
   around this.
3. **Every `.ots` glob in the system is non-recursive and fails closed on a
   hash mismatch.** Dropping a renewal `.ots` beside the originals would make
   receipts that verify today start reporting failures. §2.5 makes the
   subdirectory a hard rule with that as the stated reason.

---

## 1. Threat model, stated narrowly

Renewal is a process control, not a cryptographic primitive. It is worth being
exact about the small set of things it buys.

### 1.1 What renewal protects against

**(a) A future *collision* break of SHA-256 — the MD5/SHA-1 failure mode.**

This is a classical structural-cryptanalysis risk, not a quantum one. Per the
research file §3.5 and NIST IR 8547 ipd Table 7, SHA-256 collision resistance
is ~2¹²⁸ classically and is not meaningfully reduced by quantum attack. The
risk is that a *mathematical* break arrives, the way it did for its
predecessors.

Renewal helps in one specific, mechanical way. At renewal time T, while
SHA-256 is still sound, the service commits — into Bitcoin — a record whose
*content* includes digests of the same material under **different algorithms**
(SHA-512, SHA3-256). After a later SHA-256 break at time T′ > T, those
second-algorithm digests are still usable, because they were fixed before the
break and their fixing is itself attested by a pre-break block. This is exactly
RFC 4998's logic, and it is the entire mechanism.

Note carefully what carries the weight: **the algorithm diversity lives in the
committed content, not in the transport.** The outer commitment is still
SHA-256, because that is all OTS and Bitcoin offer. Renewal does not make the
transport diverse and no service-layer design can.

**(b) Decay or loss of the anchoring authority.**

Calendar operators can disappear, and a pending attestation can fail to
complete. `upgrade_worker`'s stall/freeze accounting (`:267-280`) exists
because this already happens. A renewal anchors the same evidence again
through whichever calendars are alive at renewal time, so the evidence does
not depend on any single operator surviving indefinitely.

**(c) Obsolescence of the optional proof layers.**

Ed25519 manifest signatures, P-256 hardware attestations, and the
Groth16/BN254 layer are Shor-vulnerable (audit §4, B1/B2/B4). Renewal cannot
repair any of them. What it *can* do is freeze the fact that a given proof
block existed, in that exact form, before the break — which is narrow but is
strictly more than nothing.

**(d) Silent drift in the service's own records.**

A renewal record commits to a canonical, enumerated subset of the receipt
(§2.3). Once anchored, any later edit to those fields becomes detectable by
anyone holding the renewal record — including an edit made by the office.
That is a real self-binding property and it is the one most useful to a
customer today, independent of any cryptographic timeline.

### 1.2 What renewal does NOT protect against

Stated plainly, because these are the places the argument can be oversold.

**It cannot repair a break that has already happened.** RFC 4998 §7:
*"Cryptographic algorithms and parameters that are used within Archive
Timestamps must be secure at the time of generation."* §1.2:
*"**Before** the cryptographic algorithms used within the Archive Timestamp
become weak … Archive Timestamps have to be renewed."* §5: *"**Prior to** such
an event…"* Every operative sentence in the standard runs in one direction.
After a break, anyone can manufacture the thing being renewed, so a
post-break renewal attests nothing. **A renewal that runs late is a renewal
that did not run.**

**It cannot defend against a SHA-256 *second-preimage* break.** Bitcoin's
header chain, its transaction Merkle tree, the OTS commitment ops, and the
outer commitment of every renewal record are all SHA-256. If second-preimage
resistance falls, the entire construction falls with it, and there is no
service-layer mitigation. This cannot be solved at our layer; the honest
partial mitigation is (a) above, which addresses the *collision* failure mode —
the one the literature considers likely to arrive first (research §3.5, §5.5).

**It cannot retroactively add a second-algorithm digest of a customer's file.**
The office never sees file bytes, by design. For a receipt already issued,
no record we can construct alone can contain a genuine SHA-512 *of the file*.
In RFC 4998 terms: **Timestamp Renewal is available retroactively; Hash-Tree
Renewal is not**, because Hash-Tree Renewal *"requires all evidence data [to]
be accessed"* (§1.2). Closing that gap for the back catalog requires the
customer to re-participate with their original bytes. §6.4 designs that as an
opt-in path and does not pretend it is automatic.

**It cannot make a receipt say more than it said.** A renewed receipt still
attests exactly one thing: content with this fingerprint existed no later than
the recorded Bitcoin block. Renewal preserves scope; it never widens it.

**It does not protect the file.** Renewal preserves a commitment. If the
customer loses the bytes, there is nothing to check the commitment against.

**It says nothing about legal or evidentiary weight**, in any forum, and no
copy derived from this document may imply otherwise.

**Out of scope entirely:** Bitcoin's own economic security (research §4.2 item
6), TLS in transit (audit B6), and the treasury/payment path (audit B5).

### 1.3 The honest summary sentence

> A timestamp made before a break preserves what it attested, provided the
> renewal schedule stays ahead of the break — and no schedule can be
> guaranteed to, because cryptanalytic progress is not predictable.

Both halves must always travel together. The second half is not a hedge added
for modesty; it is RFC 4998 §7 and it is the reason the mechanism is a process
rather than a guarantee.

---

## 2. The receipt-side design

### 2.1 Goal

For an issued receipt R, produce an artifact chain RR₁, RR₂, … such that a
holder of R plus the chain can check, fully offline:

* each RRₙ commits to a canonical, enumerated core of R **and** to RRₙ₋₁;
* each RRₙ's own SHA-256 is the value embedded in RRₙ's `.ots` files;
* each RRₙ carries digests of that core under SHA-512 and SHA3-256;

and such that **R itself is byte-for-byte untouched** — no field added, no
field changed, no `.ots` rewritten, no manifest edited.

### 2.2 Is the existing edit-lineage machinery reusable?

**Partially. The pattern yes; the three named functions no.** Three separate
answers, because conflating them is how this goes wrong.

**Reusable — the leaf-folding *pattern*.** `merkle._leaf_hash(rel_path,
file_digest)` accepts any 32-byte digest, so any 32-byte value can be folded
into the anchored root at a reserved path with **no `merkle.py` change and no
algorithm-tag bump**. `.orphograph/parent` proves this works. The same trick is
the right shape for a *future folder* anchor carrying a second-algorithm
digest (§3.3), and it is the only mechanism in the codebase that puts an
extra commitment **inside** the anchored 32 bytes.

**Not reusable — `derive_lineage_from_manifest`.** It requires a manifest
(`engine.py:164-166` raises on a missing `leaves` list). **Single-file anchors
have no manifest at all** — `anchor_hash` takes a bare hex string. Roughly the
whole issued set would be out of reach. Making it reachable would mean
promoting single-file anchors to synthetic two-leaf manifests, which changes
`hash_hex` from "your file's SHA-256" to "a Merkle root". That is a change in
the meaning of the anchored value and it is forbidden by this design's scope.
**This is the discriminator**: renewal must work for receipts that have no
manifest, therefore it cannot be manifest-shaped.

**Not reusable, and actively an anti-pattern — `attach_lineage`.** Its whole
job is a post-anchor **rewrite of `receipt.json`** (`engine.py:271-272`). That
is precisely the operation renewal must never perform. A renewal record's value
comes from the target being stable; a design that rewrites the target to point
at the record destroys the record's own commitment.

**An existing limitation renewal must not compound.** `verify.py folder`
re-derives the root with `MerkleTree.from_folder` — a filesystem walk — while
lineage validation uses `from_manifest`, a re-fold of declared leaves. A
synthetic reserved leaf has no file on disk, so **`folder --dir` mode already
disagrees with the manifest root for any lineage manifest**. Adding a second
synthetic reserved leaf (§3.3) doubles that surface. Any phase that adds one
must ship the `from_folder` reconciliation (skip-or-inject reserved paths) in
the same phase, not later. Named here so it is not quietly inherited.

**Verdict: build a separate, self-describing renewal record. Reuse the
leaf-folding idea for future folder anchors only; reuse neither lineage
function.**

### 2.3 The renewal record

A renewal record is a standalone JSON object. It is never merged into
`receipt.json`.

```
{
  "kind": "orphograph-renewal-v1",
  "sequence": 1,
  "renewed_at": "2026-08-05T00:00:00+00:00",

  "target": {
    "receipt_id": "<rid>",
    "anchored_digest_hex": "<the receipt's hash_hex — immutable>",
    "core_sha256":   "<64 hex>",
    "core_sha512":   "<128 hex>",
    "core_sha3_256": "<64 hex>",
    "manifest_sha256": "<64 hex or null>"
  },

  "prev_renewal_sha256": null,          // or the SHA-256 of RR(n-1)

  "batch": {                             // present only for batched renewals
    "root_hex": "<64 hex>",
    "algorithm": "orphograph-merkle-v1-rfc6962",
    "leaf_path": "renewal/<rid>/001",
    "proof": [["L","<64 hex>"], ["R","<64 hex>"]]
  }
}
```

**`core_*` — what exactly is being committed to.** Not the bytes of
`receipt.json`. Those are mutable: `upgrade_worker.py:252-280` writes `status`,
`btc_pinned_at`, `pinned_count`, `pinned_total`, `upgrade_attempts`,
`upgrade_stalls`, `upgrade_frozen`, `upgrade_frozen_at`,
`upgrade_frozen_reason`; `:183,190` write `pin_email_sent_at` and
`integration_email_sent_at`; `attach_lineage` writes `lineage`. A byte-hash of
`receipt.json` would be voided by the next worker run, silently, with no error
and no human in the loop.

Instead, `receipt_core` is an **explicit allow-list**, serialized as canonical
JSON (UTF-8, keys sorted, no insignificant whitespace, no NaN/Infinity), over
exactly these anchor-time fields:

```
receipt_id, created_at, hash_hex, sha512_hex, client_label, source,
private, owner_id, attestation, c2pa_manifest_hash, metadata,
calendars_ok, calendars_total, successes, failures,
zk_provenance, hardware_attestation      (each included only when present)
```

Deliberately **excluded**: every field in the mutation list above, and
`lineage` — because `attach_lineage` writes it after `anchor_hash` returns, so
it is not an anchor-time field on the single-hash path.

**Absent vs. present-but-null must be specified per field, or two
implementations will disagree.** The two are not interchangeable: `sha512_hex`
is written as an explicit `null` on every single-file receipt
(`engine.py:386` assigns it unconditionally), while `zk_provenance` and
`hardware_attestation` are **omitted entirely** when absent
(`engine.py:422-433`, the shape-stability rule). A strict canonical serializer
produces different bytes for `{"sha512_hex": null}` and `{}`, so the allow-list
must classify every field rather than leave it to the implementer:

| Class | Fields | Canonicalization rule |
|---|---|---|
| **Always present, may be `null`** | `receipt_id`, `created_at`, `hash_hex`, `sha512_hex`, `client_label`, `source`, `private`, `owner_id`, `attestation`, `c2pa_manifest_hash`, `metadata`, `calendars_ok`, `calendars_total`, `successes`, `failures` | Emit the key always. A missing key on disk is a **malformed receipt** — fail, never substitute a default. |
| **Omitted when absent** | `zk_provenance`, `hardware_attestation` | Emit the key only if present in `receipt.json`. Never emit `null` for these. |

Without this table, `verify_renewal.py` implementations diverge on the very
common case of a receipt with no ZK block, and the divergence is silent — both
sides compute a valid-looking digest and simply disagree. This is the single
detail most likely to make Phase 1 unverifiable in practice, so it belongs in
the spec rather than in the code.

Allow-list, not deny-list, and the reason matters: under a deny-list, a new
anchor-time field added next year would silently change every future core
digest and split the corpus. Under an allow-list it is simply not covered until
`kind` is bumped to `orphograph-renewal-v2`, which names its own field list.
The cost is real and should be stated: a future evidence-bearing field is not
protected by v1 renewals. That is the correct direction to fail.

**`anchored_digest_hex`** is `hash_hex`, which is immutable and is the value
embedded in every `.ots`. It is the stable join between the renewal record and
the original attestation.

**`manifest_sha256`** covers folder receipts. `manifest.json` is written once
by `_handle_anchor_folder` and is not rewritten afterwards — but that is
currently an accident of the code, not an enforced invariant. Adopting this
field means adding a regression test asserting `manifest.json` is
write-once. If that invariant is not adopted, the field must be `null`.

**`.ots` bytes are deliberately not committed to.** `upgrade_worker.py:241`
rewrites them in place as pending attestations complete. Committing to them
would guarantee the renewal record breaks. `anchored_digest_hex` covers what
those blobs are *for*.

**The chain.** `prev_renewal_sha256` is the SHA-256 of the canonical bytes of
RRₙ₋₁, so the sequence is a hash chain terminating at the original receipt
core. `sequence` is an ordering hint only; the chain is the authority, and a
verifier must recompute it rather than trust the integer.

**Anchoring an RR.** `commitment = SHA-256(canonical_bytes(RR))`, submitted
through the ordinary `anchor_hash` path — same five calendars, same 32-byte
envelope, same `.ots` framing. No protocol change, no new tag, no new endpoint
semantics.

### 2.4 Batch renewal — why this is cheap

Renewing the issued set one anchor at a time would be one OTS submission per
receipt. It does not need to be.

Build one RFC 6962 tree — the existing `merkle.py`, unchanged — whose leaves
are the per-receipt renewal records, at reserved paths `renewal/<rid>/<seq>`.
Anchor the single root. Each receipt then gets its own renewal record plus an
inclusion proof into that root.

Cost: **one anchor for the entire corpus**, per cycle. This is what makes an
annual cadence (§5) essentially free, and it is why Phase 1 is the cheapest
honest win rather than a large project. The batch tree is built from declared
leaves, so `from_manifest` verifies it and the `from_folder` limitation in §2.2
does not apply — nothing is being walked on disk.

### 2.5 Where renewal artifacts live — a hard rule

**All renewal artifacts go under `receipts/<rid>/renewal/`. A renewal `.ots`
must never be written into `receipts/<rid>/`.**

This is not tidiness. Verified above:

* `server/engine.py:784` — `verify_receipt` globs `*.ots` non-recursively and
  marks any blob whose embedded 32 bytes ≠ `hash_hex` as `ok: false`, which
  **lowers the reported `calendars_ok`**.
* `dist/orphograph-verify/verify_lineage.py:157-181` — `_ots_static_check`
  globs `*.ots` non-recursively and returns `ok=False` on the same mismatch.

A renewal `.ots` embeds the renewal commitment, not `hash_hex`, by
construction. Placed in the receipt directory root it would make **receipts
that verify cleanly today start reporting failures** — a worse violation of
additive-only than shipping nothing. Because both globs are non-recursive, a
`renewal/` subdirectory is invisible to them, so existing verification is
provably unaffected.

Proposed layout:

```
receipts/<rid>/
  receipt.json                      # untouched, forever
  manifest.json                     # untouched, forever (folder anchors)
  a.ots  b.ots  alice.ots  …        # untouched by renewal
  renewal/
    001.json                        # RR sequence 1
    001.a.ots  001.b.ots  …         # its own attestations
    002.json  002.*.ots             # RR sequence 2
```

### 2.6 ASCII — one receipt, two renewal cycles

```
  ORIGINAL (2026-05) ────────────────────────────────────────────────┐
    receipt.json  →  receipt_core  →  core_sha256                    │
                      hash_hex ─────────────────────────┐            │
                                                        │            │
                     32 bytes ─→ OTS ─→ Bitcoin block B0│            │
                                                        │            │
  RENEWAL 1 (2027-01)                                   │            │
    RR1 = { target: { hash_hex ←──────────────────────┘              │
                      core_sha256, core_sha512, core_sha3_256 ←──────┘
                    },
            prev_renewal_sha256: null }
    SHA-256(RR1) ─→ OTS ─→ Bitcoin block B1        (B1 > B0)

  RENEWAL 2 (2028-01)
    RR2 = { target: { …same core digests… },
            prev_renewal_sha256: SHA-256(RR1) ──────→ binds to RR1 }
    SHA-256(RR2) ─→ OTS ─→ Bitcoin block B2        (B2 > B1)
```

Read the chain right-to-left: RR2 proves RR1's exact bytes existed by B2; RR1
proves the receipt core's SHA-512 and SHA3-256 were fixed by B1. If SHA-256
collision resistance falls at some later date, those SHA-512 and SHA3-256
values were committed before it, and the commitment is attested by a block that
predates the break.

### 2.7 What a green renewal chain establishes

* That the receipt's enumerated core fields have not changed since the earliest
  renewal in the chain — **including by the office**.
* That digests of that core under SHA-512 and SHA3-256 were fixed no later than
  the earliest renewal's Bitcoin block.
* That each renewal record in the chain existed by its own block, in the order
  the chain declares.
* That the original attestation's `hash_hex` is the one the chain refers to.

### 2.8 What a green renewal chain does NOT establish

This section is load-bearing and must appear verbatim in verifier output and in
any derived copy.

* **Not** that the customer's file matches anything. The office never had the
  bytes. File-side verification is unchanged and remains `verify.py`'s job.
* **Not** a second-algorithm digest **of the file** for receipts issued before
  §3 ships. `core_sha512` is a digest of *receipt content*, not of the file.
  For a receipt whose `sha512_hex` was absent — including every folder anchor
  (audit §2.1) — renewal adds no file-side algorithm diversity at all.
* **Not** protection against a SHA-256 second-preimage break. The outer
  commitment is SHA-256.
* **Not** an upgrade of the original claim. Existence-by-a-block, still.
* **Not** repair of any broken optional layer (signature, attestation, SNARK).
* **Not** independence of authority. Every renewal in the chain anchors to the
  same chain the original did. See §4.
* **Not** anything about legal or evidentiary weight.
* **Not** proof that renewal ran *before* any particular cryptanalytic event —
  only that it ran before the block it names. Whether that was early enough is
  a question the chain cannot answer.

---

## 3. Algorithm agility — actually anchoring a second digest

### 3.1 The ceiling, stated first

OTS defines exactly four hash ops: `OpSHA1 0x02`, `OpRIPEMD160 0x03`,
`OpSHA256 0x08`, `OpKECCAK256 0x67` (research §5.4). There is no SHA-512 op and
no NIST SHA-3 op. Unknown tags raise `DeserializationError` — **old verifiers
hard-fail on new ops rather than skipping them**, so adding one is a hard fork
of the proof format, and project leadership has explicitly declined further
hash functions. Bitcoin itself is SHA-256d.

**Consequence, stated precisely:** a second algorithm can only ever be
committed *via* SHA-256. That defends against a SHA-256 **collision /
chosen-prefix** break — an attacker must now produce a substitute matching both
digests simultaneously — and it does **not** defend against a SHA-256
**second-preimage** break, because the outer commitment, the OTS ops, and the
chain are all SHA-256. Nothing at the anchoring layer can, and no amount of
design effort changes that. This is a real ceiling and the copy must sit under
it.

The collision case is nonetheless the one worth defending, and the reason is in
the research: for a self-serve service where the anchorer chooses the anchored
document, a chosen-prefix collision *is* a live attack (research §5.5's
correction to the SHA-1 argument), so collision resistance is the binding
property for us.

### 3.2 Forward path, single-file anchors — the digest-set commitment

Today `sha512_hex` sits in `receipt.json` beside the anchor, protected only by
the receipt store. Two ways to move it inside a Bitcoin commitment:

**Option A — change the anchored value.** Submit
`SHA-256(0x02 ‖ sha256 ‖ sha512)` instead of the file's SHA-256. This is the
audit's R2c. **Rejected for now.** It changes `hash_hex` from "your file's
SHA-256" to a derived value, which breaks the file→receipt binding that every
shipped verifier, the browser extension, the JS verifier, the MCP, and all
public copy rest on. It is a meaning change, and it forces a simultaneous
flag-day across surfaces we do not control (customers' pinned verifier copies).

**Option B — a second, parallel anchor. Recommended.** Leave `hash_hex`
exactly as it is. At anchor time, additionally build:

```
{ "kind": "orphograph-digestset-v1",
  "sha256": "<64 hex>", "sha512": "<128 hex>", "sha3_256": "<64 hex>" }
```

and anchor `SHA-256(canonical_bytes(...))` as its own OTS submission, stored in
`receipts/<rid>/renewal/digestset.json` + `.ots` under the §2.5 rule.

Properties: `hash_hex` unchanged; every existing verifier unaffected; a v1
verifier that has never heard of digest sets produces the identical result it
does today; and the SHA-512 is now inside a Bitcoin commitment rather than
beside one. To substitute a file an attacker must now collide SHA-256 **and**
SHA-512 **and** SHA3-256 on the same bytes.

Prerequisites, both already recommended by the audit and both cheap:

* **R2a** — make a *present-but-mismatched* `sha512_hex` fatal in every
  verifier. `server/verify_cli.py:68-77` is the **reference behavior for a
  present sibling**: it is gated on `if sibling:` (correctly skipped when the
  field is absent) and returns 3 on mismatch. The actual R2a work is bringing
  `dist/orphograph-verify/verify.py` and
  `verifier-js/orphograph_verify.js:191-200` to match — neither was read for
  this design; the audit reports both check "only when present", which needs
  confirming against a mismatch case, not just an absent one.
* **R2b** — require `sha512_hex` on new single-file anchors.
  `server/app.py:2265-2266` currently drops a non-string silently, which should
  become a 400. **Forward-only**; existing receipts stay valid, and the copy
  correction must land first (audit R1) so the site never claims coverage the
  corpus does not have.

Adding SHA3-256 costs nothing at ingest (it is another `hashlib` pass over
bytes the client already holds) and is worth doing at the same time, because it
is the only genuinely *different construction* in the set — SHA-256 and
SHA-512 share the Merkle–Damgård/SHA-2 design, so a structural break in one is
correlated with the other. SHA3-256 is a sponge. That is the diversity RFC 4998
§7 is actually asking for.

### 3.3 Forward path, folder anchors — materially more work

Folder receipts carry no file digest at all; `hash_hex` is a Merkle root. A
second-algorithm root needs a **parallel tree**:

```
leaf′     = SHA-512(0x00 ‖ rel_path ‖ 0x00 ‖ file_sha512)
internal′ = SHA-512(0x01 ‖ L ‖ R)
anchor      SHA-256(root512)         # the outer commitment is still SHA-256
algorithm   "orphograph-merkle-v1-rfc6962-sha512"   # a NEW tag, not v2 of the old
```

Scope of the change: `server/merkle.py`, the MCP's local re-implementation
(`mcp/orphograph_mcp.py:237,250,283`), `sdk-python/orphograph/_merkle.py`, the
vendored `dist/orphograph-verify/merkle.py`, and the client CLIs that build
manifests. The client must also compute a second digest per file — a second
full pass over every byte, which is a real cost for large corpora.

The cheaper interim, using §2.2's reusable pattern: a reserved leaf
`.orphograph/digestset` inside the existing SHA-256 tree, whose
`file_sha256_hex` is `SHA-256(canonical_bytes(digest_set_manifest))`. That puts
a second-algorithm commitment inside the anchored 32 bytes with **no
`merkle.py` change and no tag bump**. It inherits the `from_folder` limitation
named in §2.2 and must ship with the reconciliation.

### 3.4 Compatibility cost, stated explicitly

| Surface | Cost |
|---|---|
| Existing receipts | **Zero.** Nothing is added, removed, or rewritten. They gain no file-side algorithm diversity — see §2.8 — and that is a limitation, not a regression. |
| `hash_hex` semantics | **Unchanged** under Option B. This is the reason Option B is recommended over A. |
| Published verifier (`dist/orphograph-verify/verify.py`) | Unchanged for `file` and `folder` modes. A new `verify_renewal.py` sits beside it. Customers holding an old copy keep getting correct results on old receipts. |
| JS verifier / browser extension | Unchanged. Optionally learn the digest-set later. |
| `receipt_export.export_zip` | **Must change** or customers never receive renewal artifacts (§6.3). |
| New single-file anchors | `sha512_hex` becomes required (R2b). A breaking change **for new anchors only**, and it needs a deprecation window announced through the API changelog before it becomes a 400. |
| Folder anchors | Unchanged unless §3.3 ships. Then: a new algorithm tag, five re-implementations, and a second hash pass client-side. |

---

## 4. Authority redundancy — an honest assessment

### 4.1 Does five calendars satisfy RFC 4998 §7?

RFC 4998 §7: *"it is recommended to generate and manage at least two redundant
Evidence Records with ArchiveTimeStampSequences using **different hash
algorithms and different TSAs**."*

Today: `server/engine.py:37-43` lists five endpoints across **three distinct
operators** (the `opentimestamps.org` pool, `eternitywall.com`,
`catallaxy.com`), submitted in parallel (`:350-360`), each producing its own
`.ots`.

**On different hash algorithms: no.** Every path is SHA-256, end to end.

**On different authorities: partially, and the honest answer is "no" in the
sense the RFC means.** Three operators is genuine *operator* redundancy — it
survives one operator going offline, refusing service, or losing its
commitment. That is worth having and it works. But all five aggregate into the
same Bitcoin chain and reduce to the same SHA-256d assumption. In RFC 4998's
model the redundancy is meant to survive the failure of an *authority*; here
the authority is the chain, and the five calendars are five routes to one
authority. **If the chain is the failure mode, all five fail together.**

The correct public sentence is: *five calendars across three operators give
route and operator redundancy over a single chain.* Not "five independent
authorities". Anything stronger is inaccurate.

### 4.2 What a genuinely independent second authority looks like

Four candidates, assessed honestly.

**(a) A second proof-of-work chain with a different PoW hash.** OTS already
defines non-Bitcoin attestation types. Gains: an independent ledger with an
independent operator set, an independent failure mode, and — if the PoW hash
differs from SHA-256d — genuine diversity at the *chain* layer. Does not gain
diversity in the OTS commitment ops, which remain SHA-256. Honest verdict: the
cheapest real independence available, and the one worth prototyping first. It
does not remove the §3.1 ceiling.

**(b) An RFC 3161 TSA token.** A genuinely different trust root — a CA-issued
signing certificate rather than a chain — and a genuinely different failure
mode. But the token *is* a signature (RSA or ECDSA), so per research §7.4 and
ETSI TR 103 619 it is the **weaker** construction on a post-quantum horizon,
not the stronger one; NIST disallows those algorithms after 2035. Adding one
would add breadth today and a dated liability tomorrow. Honest verdict: worth
doing only if a specific acceptance room asks for RFC 3161 by name, and it must
never be described as strengthening the quantum posture. It does the opposite.

**(c) Wide publication of the batch root.** Publish each cycle's batch root
(§2.4) somewhere many independent parties observe and retain — a public
append-only log, a periodic digest that recipients keep. Cheap, hash-only, no
new cryptographic assumption. Its authority is *replicative* rather than
cryptographic: it converts "one chain says so" into "many independent parties
recorded this digest on this date". That is a different kind of evidence, and
weaker per-observer, but it fails independently of everything else in the
system, which is exactly the property RFC 4998 is reaching for.

**(d) A hash-based signature (SLH-DSA, FIPS 205) over each renewal record.**
Sometimes proposed as "authority redundancy". **It is not.** The signer would
be us, so it adds no independent authority — it adds a service attestation,
plus key-management burden. It is PQ-safe and introduces no assumption the
system does not already make (research §2.2), so it is defensible *later* as a
service-identity signal. It is not a second authority and must not be described
as one.

**Recommendation:** state §4.1's honest sentence in copy now (free). Prototype
(a) and (c) together in a later phase — they compose, since one batch root can
be anchored to a second chain and published simultaneously. Decline (b) as a
PQ measure. Defer (d).

---

## 5. When renewal must run

### 5.1 The three candidate policies

**Fixed cadence.** Renew everything on a schedule regardless of external
events. Pros: no judgment call, no monitoring dependency, demonstrably running,
and — because of §2.4's batching — approximately free. Cons: a break arriving
mid-cycle is not caught early; cadence alone adds no algorithm diversity beyond
what the record content carries.

**Event-driven.** Renew when a named cryptanalytic or standards milestone
fires. Pros: responsive, no work when nothing is happening. Cons: requires
monitoring and judgment, and is **late by construction** — Vigil et al. put it
directly (research §7.8): *"as cryptanalytic progress is hard to predict, it is
unclear whether such an assumption is justified."* An event-only policy is a
bet that we will see it coming.

**Hybrid. Recommended.** A fixed cadence as the floor, plus a named trigger
list that forces an out-of-cycle renewal within a stated SLA. The cadence is
what actually protects; the triggers are a backstop, not a substitute.

### 5.2 The proposed policy

**Floor: annual batch renewal**, one anchor for the whole corpus (§2.4), on a
fixed calendar date, run by an automated job whose success or failure is
visible. Annual is chosen because the cost is one anchor and the operational
risk of a longer interval is asymmetric — a missed year cannot be recovered
after a break.

**Triggers forcing out-of-cycle renewal.** Each is a named, observable
condition — not a judgment call.

| # | Observable condition | Action | SLA |
|---|---|---|---|
| **T1** | SHA-256 appears in a deprecation or disallowed table in any successor to NIST IR 8547 (verified absent as of the research file: IR 8547 ipd Tables 2, 4, 7 contain no SHA-2 entry). | Emergency batch renewal; open the §3 work if not already shipped. | 30 days |
| **T2** | A published collision attack on SHA-256 reduced to ≥40 of 64 rounds, **or** any published chosen-prefix attack on full SHA-256 below 2¹²⁸. | Emergency batch renewal; freeze new single-algorithm anchors pending §3. | 30 days |
| **T3** | A full-round SHA-256 collision published anywhere, chosen-prefix or not. | Immediate renewal of everything, then stop and reassess. **State plainly at this point that renewal no longer helps anything anchored after the break** (§1.2). | Immediate |
| **T4** | ETSI TS 119 312 assigns SHA-256 a sunset horizon, or an equivalent national scheme does. | Schedule renewal and the §3 migration inside the published horizon. | Per horizon |
| **T5** | Independent OTS calendar operators drop below two, **or** OTS adds a new hash op tag. | Renew through surviving calendars; reassess §4.2(a). | 90 days |
| **T6** | A CRQC milestone of any kind. | **Explicit NON-trigger for the hash layer.** | — |

T6 is in the table deliberately. NIST places SHA-256 preimage resistance in
post-quantum Category 5 and does not deprecate it anywhere (research §3.3), and
BHT offers no practical collision advantage (§3.4). Treating a quantum
headline as a hash-renewal trigger would be responding to the wrong risk, and
writing that down is part of the policy. Quantum milestones *are* triggers for
the optional signature layers — a separate track, per audit §7 R6.

**Two standing obligations regardless of triggers.** Publish the renewal
schedule and the trigger list, so the conditional in §1.3 is checkable by the
customer rather than asserted. And record every renewal cycle's outcome in a
durable, append-only log — a renewal programme whose failures are invisible is
not a programme.

---

## 6. Migration & compatibility

### 6.1 The issued set

Per the brief, roughly 245 receipts have been issued. That count is **not**
verified in this tree (§0). The design does not depend on the number: batching
(§2.4) makes the per-cycle cost one anchor whether the corpus is 245 or 245,000.

### 6.2 What changes for an issued receipt: nothing

Enumerated, because "nothing" is the load-bearing claim:

* `receipt.json` — not read for renewal beyond computing `receipt_core`, never
  written. No pointer field, no renewal flag, no version bump. Renewal
  artifacts are discovered by directory presence, the same way `.ots` files
  already are.
* `manifest.json` — never written.
* `*.ots` in the receipt root — never written, never moved, never re-globbed.
* `hash_hex` — unchanged, and its meaning is unchanged.
* `/api/verify/<rid>` — the existing response shape is unchanged. A `renewal`
  block may be **added** when present, under the same shape-stability rule
  `zk_provenance` and `hardware_attestation` already follow on the **surface**
  side (`verify_receipt`, `engine.py:820-826`; the corresponding **write**-side
  rule is at `engine.py:422-433`, cited in §0 as part of the record shape).
* Existing verifier copies in customers' hands — produce identical results.

**Does renewal change the meaning of an existing receipt? No.** The receipt
attests exactly what it attested. The renewal record is a separate, later
statement *about* the receipt, made by the service, with its own separate
attestation. A verifier must present it that way — never as an upgrade to the
receipt, and never in a way that makes an un-renewed receipt look deficient.

### 6.3 What must change on the service side

| Item | Why |
|---|---|
| `server/receipt_export.py:48-58` | The `*.ots` glob is non-recursive, so `renewal/` is invisible. **The export zip is what customers actually hold** — without this change, renewal exists only on our disk and is worthless to the customer. Highest-priority item in this table. |
| A standalone `verify_renewal.py` | Nothing in `dist/orphograph-verify/` can check a renewal chain. See §6.5. |
| `/api/verify/<rid>` | Additive `renewal` block. |
| Regression test: `receipts/<rid>/*.ots` glob | Assert no renewal `.ots` ever lands in the receipt root (§2.5). This is the rule most likely to be violated by a well-meaning later change. |
| Regression test: `manifest.json` write-once | Required before `manifest_sha256` (§2.3) can be trusted. |
| Renewal cycle log | Append-only, per §5.2. |

### 6.4 Opt-in or automatic?

**Both, split along the RFC 4998 line, and the split is not arbitrary.**

**Timestamp Renewal — automatic, service-side, no customer action.** It
operates on data the office already holds (`receipt_core`), cannot change the
customer's receipt, and cannot fail in a way that harms them. Withholding it
pending consent would mean most receipts never get renewed, which is the
failure mode the whole design exists to prevent. Customers should be *told*,
via the renewal-schedule publication in §5.2.

**Hash-Tree Renewal — strictly opt-in, and it requires the customer.** RFC 4998
§1.2: *"all evidence data must be accessed and timestamped."* We cannot access
it. For a receipt already issued, the only way to obtain a genuine
second-algorithm digest *of the file* is for the customer to re-hash their
original bytes and submit the digest set. Shape: a `/api/renew/<rid>` endpoint
accepting `{sha512_hex, sha3_256_hex}`, verified against the receipt's
`hash_hex` where the client also supplies a matching SHA-256, producing a
digest-set commitment anchored per §3.2 and linked into the renewal chain.

Two honest constraints on that path. The submitted digests are **client
asserted**, exactly as `sha512_hex` is today (audit §2.1) — the office is
attesting *that a claim was made at a time*, not that the claim is true, and
the copy must say so. And it can only be offered where the customer still holds
the bytes, which for older receipts many will not. **This cannot be fully
solved.** The partial mitigation is: offer it, be explicit about what it does
and does not establish, and make the automatic Timestamp Renewal the default so
that the receipts nobody re-participates in still get *something*.

### 6.5 What the offline verifier must learn

A new `dist/orphograph-verify/verify_renewal.py`, standalone, stdlib-only,
matching the house exit-code convention (0 OK / 2 args / 3 recomputation
failed / 4 OTS sub-check failed). It must:

1. Rebuild `receipt_core` from `receipt.json` using the **v1 allow-list**,
   canonicalize, and compute SHA-256/SHA-512/SHA3-256. Compare against the
   RR's `target.core_*` **verbatim** — no helpful lowercasing, per
   `docs/VERIFIER_SPEC.md` §4.2 / AUDIT_VERIFIER_DRIFT D1.
2. Confirm `target.anchored_digest_hex == receipt.hash_hex`.
3. Recompute `SHA-256(canonical_bytes(RR))` and confirm it equals the 32 bytes
   embedded in each `renewal/<seq>.*.ots` at
   `len(OTS_HEADER_MAGIC)+2 .. +34` — the same offset check
   `engine.verify_receipt` uses.
4. Walk `prev_renewal_sha256` backwards, recomputing each link. Fail on any
   break, any cycle, any `sequence` that disagrees with the recomputed order.
5. For batched RRs, verify the inclusion proof into `batch.root_hex` using the
   vendored `merkle.MerkleTree.verify_inclusion`, and confirm the batch root is
   the value embedded in the batch `.ots`.
6. Gate on the `ots` client's **`returncode`**, not a substring match. The
   existing `_ots_subcheck` (`verify.py:90-96`) does not, which is audit §8.4;
   the new verifier must not inherit that defect.
7. Print §2.8 verbatim, every run, alongside the result.
8. Report a receipt with no renewal directory as **"no renewal recorded"** —
   informational, exit 0. Absence of renewal is not a failure, and a verifier
   that says otherwise would retroactively downgrade every issued receipt.

---

## 7. Effort estimate and phased plan

Cheapest honest win first. Each phase stands alone and is shippable without the
next.

### Phase 0 — Doctrine and copy. Hours.

Land the honest sentences before building anything:

* Five calendars across three operators = route and operator redundancy over a
  single chain (§4.1). Not "independent authorities".
* Timestamps preserve evidence created before a break, **conditional on
  renewal**; that conditional travels with the claim (§1.3).
* Complete audit **R1** — the "every receipt carries a SHA-512 sibling" copy is
  false today, for reasons unrelated to quantum, and is the highest-priority
  correction in the audit.

Zero code risk. Removes a false claim shipping now. Prerequisite for R2b, since
requiring `sha512_hex` while the copy overstates existing coverage would be the
wrong order.

### Phase 1 — Batch Timestamp Renewal + verifier. 2–3 days. **Recommended first build.**

* Define `receipt_core` canonicalization and the v1 allow-list (§2.3).
* Batch-renewal job: build the tree over per-receipt RRs, anchor one root,
  write `receipts/<rid>/renewal/001.json` + inclusion proof + `.ots` (§2.4).
* `verify_renewal.py` per §6.5.
* Regression test asserting no renewal `.ots` reaches the receipt root (§2.5).

Why this is the cheapest honest win: it needs no protocol change, no OTS
change, no client change, no customer action, and **no file access**. It closes
audit risk item 14 — the absence of a renewal mechanism — for the entire issued
corpus at a cost of one anchor. It touches zero existing bytes, so the
additive-only guarantee is structural rather than aspirational.

Its limit, stated in the same breath: **the batch's outer commitment is still
SHA-256.** The algorithm diversity lives in the record's content, not in the
transport. Phase 1 makes the §7 argument honest to state; it does not make the
system hash-agile.

### Phase 2 — Surface and cadence. 2–3 days.

* `export_zip` includes `renewal/` (§6.3) — without this the customer never
  sees Phase 1's output.
* `/api/verify` additive `renewal` block.
* Annual cadence job + append-only cycle log (§5.2).
* Publish the schedule and the trigger table.

### Phase 3 — Forward algorithm agility, single-file. 1–2 weeks.

* Audit **R2a**: present-but-mismatched `sha512_hex` fatal in every verifier.
* Audit **R2b**: `sha512_hex` required on new single-file anchors, after a
  published deprecation window.
* Digest-set commitment anchored as a second OTS submission at ingest (§3.2),
  including SHA3-256 for construction diversity.

First phase where a second algorithm is genuinely inside a Bitcoin commitment.
Forward-only by construction.

### Phase 4 — Back-catalog Hash-Tree Renewal + folder trees. 2–4 weeks. Demand-gated.

* `/api/renew/<rid>` for customer-supplied digest sets (§6.4).
* Folder second-tree or the reserved-leaf interim (§3.3), including the
  `from_folder` reconciliation named in §2.2.

Build only against a named request. It is the most work, it depends on customer
participation we cannot compel, and Phases 1–3 already carry the argument.

### Phase 5 — Authority redundancy. Not scheduled.

Prototype a second-chain attestation and batch-root publication together
(§4.2 a + c). Decline RFC 3161 as a PQ measure. Revisit SLH-DSA only as a
service-identity signal, never as an authority.

### Not recommended

* Changing the anchored value to a combined digest (§3.2 Option A) — a meaning
  change across surfaces we do not control.
* Adding a hash op to OTS — a hard fork of the proof format that the upstream
  project has explicitly declined.
* Rebuilding the ZK layer on a hash-based proof system for post-quantum
  reasons. Largest possible effort, protects a claim the product deliberately
  does not make (audit §7).

---

## 8. Open questions

1. **`receipt_core` allow-list membership.** Should `failures` be included? It
   records calendars that declined at anchor time — evidentially interesting,
   but if it were ever backfilled the core digest would break. Leaning include,
   with a write-once invariant test.
2. **`manifest_sha256`.** Adopt the write-once invariant for `manifest.json`,
   or leave the field `null`? Adopting it is cheap now and expensive later.
3. **Cadence.** Annual is proposed. Is a shorter interval worth the operational
   noise given that batching makes the marginal anchor essentially free?
4. **T2's threshold.** "≥40 of 64 rounds" is a placeholder that needs a
   cryptographer's number, not an engineer's guess. It must be a published,
   checkable figure before the trigger table ships.
5. **Renewal of renewals.** Should RRs themselves be re-renewed indefinitely,
   or does the chain terminate at some depth? RFC 4998's model is indefinite;
   the storage cost is trivial; the verifier walk cost is linear.
6. **Should renewal cover private receipts?** They contain `owner_id`. The core
   digest reveals nothing (it is a digest), but the batch tree's leaf path
   `renewal/<rid>/<seq>` reveals that a receipt id exists. Leaning: use a
   blinded leaf path for private receipts.
7. **What does the receipt page show?** A renewal block risks implying that
   un-renewed receipts are lesser. Wording needs the same care §2.8 gets.
