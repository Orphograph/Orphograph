# The Orphograph Receipt: Construction, Redundancy, Verification, and Limits

Status: descriptive of the shipped implementation as of 2026-07-21.
Canon: `server/engine.py`, `server/merkle.py`, `server/upgrade_worker.py`,
`server/app.py`. Where this document and the code disagree, the code is
authoritative and this document is in error; the normative verification
algorithm is `docs/VERIFIER_SPEC.md`. Published test vectors accompany this
document at `docs/test-vectors/`.

A rendered copy of this document is served at `/method/whitepaper`.

---

## 1. The claim

An Orphograph receipt attests exactly one proposition:

> A byte sequence whose SHA-256 digest equals the receipt's recorded
> `hash_hex` existed no later than the time at which that digest was
> committed, through the OpenTimestamps protocol, to the Bitcoin chain.

Existence **by** a time. Nothing further. The office states the
non-claims with the same precision as the claim, because a receipt
misread as more than it is would be a defective instrument:

- **Not authorship.** The receipt records that a digest was submitted;
  it carries no evidence of who created the underlying bytes, who
  submitted them, or on whose behalf.
- **Not truth of content.** A photograph of an event that did not occur,
  a document containing false statements — each receives the same
  receipt as any other byte sequence. The receipt is mute on content.
- **Not originality or priority of creation.** The receipt proves the
  bytes existed by the anchor time. It does not prove the bytes did not
  exist earlier, elsewhere, or in other hands. Earlier copies, where
  they exist, are equally genuine.
- **Not lawful possession or capture.** The protocol makes no statement
  about how the bytes were obtained.
- **Not identity.** The office does not verify the identity of the
  submitter. An optional Ed25519 signature block on folder manifests
  (below, §2.4) binds a key, not a person; the linkage between a key
  and a real-world party is the customer's claim, not the office's.
- **Not exact wall-clock time of creation.** The receipt's time
  resolution is the time of calendar acceptance and, ultimately, the
  Bitcoin block that commits the digest. A file anchored a year after
  it was created proves existence at anchor time only.

The receipt's `created_at` field is the office's own UTC clock at
submission (ISO 8601). It is informational. The evidentiary time bound
is the Bitcoin block attestation carried in the `.ots` proof files, not
the office's clock.

Everything in this document is *tamper-evident* by construction; nothing
in it is tamper-proof. The distinction is load-bearing: the design
guarantees that alteration is detectable by an independent party, not
that alteration is impossible.

## 2. Construction

### 2.1 The file digest

The unit of attestation is the SHA-256 digest (FIPS 180-4) of the exact
byte sequence of the file. Hashing is performed on the customer's
machine — in the browser via the Web Cryptography API, or locally by the
SDKs and command-line tools, streaming in fixed-size chunks. The file
body is not transmitted; only the hex digest crosses the wire.

The anchoring path (`engine.anchor_hash`) canonicalises the digest
before accepting it: the supplied string is whitespace-stripped and
lowercased, then rejected unless it is exactly 64 characters drawn from
`0123456789abcdef`. A service-issued receipt therefore always stores
`hash_hex` as 64 lowercase hex characters. The same rule applies to the
optional SHA-512 sibling (`sha512_hex`, 128 lowercase hex characters),
a second digest of the same bytes recorded alongside the SHA-256.
The OpenTimestamps commitment covers the SHA-256 only; the sibling's
value is that a forged file-to-receipt binding must collide both
functions at once, and that under Grover-type quantum attack the
recorded pair retains approximately 2^128 (SHA-256) and 2^256 (SHA-512)
preimage work respectively.

### 2.2 Folder anchoring: the Merkle tree

A folder is committed as a single 32-byte root using a binary hash tree
in the style of RFC 6962 (Certificate Transparency; updated by RFC
9162), with one deliberate extension: each leaf binds the file's
relative path together with its content digest. As implemented in
`server/merkle.py` (algorithm tag `orphograph-merkle-v1-rfc6962`,
manifest `version: 1`):

- **Leaf.** `SHA-256( 0x00 || utf8(rel_path) || 0x00 || file_sha256 )`
  where `file_sha256` is the raw 32-byte content digest. The leading
  `0x00` is the RFC 6962 leaf domain-separation prefix; the interior
  `0x00` separates the path bytes from the digest bytes.
- **Internal node.** `SHA-256( 0x01 || left || right )`, with the
  `0x01` internal-node prefix. Both children are exactly 32 bytes.
- **Ordering.** Leaves are sorted by the UTF-8 byte order of the POSIX
  relative path (forward slashes; case preserved; no Unicode
  normalisation is performed — a documented v1 limitation).
- **Odd level.** When a level holds an odd number of nodes, the lone
  last node is **promoted** to the next level unchanged. It is never
  paired with itself and never duplicated; duplication reintroduces the
  second-preimage ambiguity catalogued as CVE-2012-2459.
- **Degenerate case.** A single-file folder yields `root == leaf`.
  Empty folders are rejected.
- **Enumeration.** Files are streamed through SHA-256 in 1 MiB chunks.
  Symbolic links are skipped, not followed, and are not recorded.
  Hidden dotfiles are included. A default exclusion list
  (`.DS_Store`, `Thumbs.db`, `desktop.ini`, `.git/*`, `node_modules/*`,
  `__pycache__/*`, `*.tmp`, `*.swp`, `*.swo`, `~$*`) removes operating-
  system and tooling detritus; a caller may replace it, and an empty
  list disables exclusion.

The consequence of path binding is intentional: renaming a file — even
byte-identical content — changes its leaf and therefore the root. Under
a folder receipt, the relative path is evidence, not decoration.
Customers for whom paths are sensitive may anchor under opaque labels
(for example, the file's own digest as its name); the protocol records
the naming policy the customer chose.

The customer's machine builds the tree and transmits only the
**manifest**: the algorithm tag, the version, the ordered leaves (each
`path`, `file_sha256_hex`, `leaf_hex`, `size_bytes`), and `root_hex`.
The server reconstructs every leaf and every internal node from the
manifest and refuses to anchor if the recomputed root does not equal the
manifest's `root_hex`. The root — a single 64-character digest — then
enters the same anchoring path as a single-file digest. The manifest is
persisted beside the receipt so that per-file inclusion proofs can be
served later; for a public folder receipt viewed by anyone other than
its owner, leaf paths are redacted by default (digests and sizes remain
visible) unless the owner opted to publish them.

An **inclusion proof** for one file is the list of sibling hashes from
its leaf to the root, each tagged `"L"` or `"R"` for the side on which
the sibling sits. A promoted node contributes no step, so a valid proof
may be shorter than ⌈log₂ N⌉. A third party holding the file, its
committed relative path, the proof, and the anchored root can confirm
membership without learning anything else about the set.

### 2.3 The OpenTimestamps commitment

The digest (file digest, or folder root) is submitted by HTTP POST to
the `/digest` endpoint of each of five OpenTimestamps calendars (§3).
Each calendar aggregates the digests submitted during an interval into
its own Merkle tree and commits that tree's root to the Bitcoin chain in
a single transaction; each submitter receives a per-digest proof path.

The office stores each calendar's response as an `.ots` file with the
following layout, fixed by `engine._build_ots`:

```
offset  0   31-byte OpenTimestamps header magic
            00 4F 70 65 6E 54 69 6D 65 73 74 61 6D 70 73 00 00
            50 72 6F 6F 66 00 BF 89 E2 E8 84 E8 92 94
offset 31   version byte 0x01
offset 32   hash-algorithm tag 0x08 (SHA-256)
offset 33   the 32-byte anchored digest, raw
offset 65+  the calendar's proof body
```

The proof body is a chain of commitment operations in the standard
OpenTimestamps encoding. The subset the office's own tooling walks
(`upgrade_worker._commitment_for_pending`) is: `0xF0` append *n* bytes,
`0xF1` prepend *n* bytes, `0x08` apply SHA-256. Executing the chain
from the anchored digest yields the running commitment; a
calendar-pending attestation (marker bytes
`00 83 DF E3 0D 2E F9 0C 8E`) at the end of the chain names the
calendar that holds the eventual Bitcoin attestation, and — once
upgraded — the chain terminates instead in a Bitcoin block attestation
that an OpenTimestamps client checks against the block's header. The
office does not modify the OTS format in any way.

### 2.4 Optional fields recorded at anchor time

The receipt may additionally record, verbatim and size-capped: a
`client_label` (≤200 characters); an `attestation` object restricted to
`claim`, `author`, `license`, `url`, `signed_at` (each ≤500 characters)
— a free-form statement whose *existence at anchor time* is itself
anchored, though its truth is not examined; a `metadata` object
restricted to an allowlist of file and EXIF fields (GPS is deliberately
not accepted); and a `c2pa_manifest_hash`, the SHA-256 of a C2PA
manifest the customer wishes the receipt to reference. Folder manifests
may carry an Ed25519 `signature` block; when present it must verify at
anchor time or the anchor is rejected, and the receipt records
`signature_verified` and the signer's key identifier.

## 3. Redundancy: five calendars, and the distinct states of a receipt

Each anchor is submitted, in parallel, to five independently operated
OpenTimestamps calendars:

1. `a.pool.opentimestamps.org`
2. `b.pool.opentimestamps.org`
3. `alice.btc.calendar.opentimestamps.org`
4. `finney.calendar.eternitywall.com`
5. `btc.calendar.catallaxy.com`

Each acceptance produces an independent `.ots` proof. The receipt
records `calendars_ok` (acceptances) out of `calendars_total` (five),
with per-calendar failures listed verbatim.

**Acceptance is not confirmation.** The two are kept as distinct
states, measured by distinct counters:

- **Acceptance at anchor time** — `calendars_ok`. The service's
  acceptance threshold is `MIN_CALENDARS_OK` (default 3, operator-
  configurable): a receipt issued with fewer acceptances is returned to
  the customer flagged `low_redundancy`. A receipt with zero
  acceptances holds no commitment path at all, can never upgrade, and
  is treated as worthless — a consumed paid credit is refunded, and the
  receipt is returned only for transparency.
- **Bitcoin-pin confirmation** — `pinned_count` out of `pinned_total`,
  maintained by the upgrade worker (`server/upgrade_worker.py`), which
  can be a strict subset of the acceptances.

A calendar acceptance means the digest is in the calendar's queue; the
calendar folds it into a Bitcoin transaction on the calendar's own
schedule, typically within about an hour. The upgrade worker
periodically re-queries each calendar — at the commitment digest reached
by walking the stored proof's operation chain to its pending-attestation
marker, which is not the customer's original digest — and splices the
upgraded proof body into the stored `.ots`. The receipt's `status`
field then takes exactly one of these values, as computed in
`upgrade_worker._upgrade_one`:

- **`pending`** — no calendar has yet returned an upgraded
  (Bitcoin-attested) proof. Every fresh receipt begins here.
- **`partial`** — at least one, but not all, of the accepted calendars'
  proofs have upgraded.
- **`pinned`** — every accepted calendar's proof has upgraded (and at
  least one exists). `btc_pinned_at` is set once, at the first
  transition to any pinned state, and is never rewritten.
- **`frozen`** (`upgrade_frozen: true`, alongside a `pending` or
  `partial` status) — after `MAX_UPGRADE_STALLS` consecutive polling
  runs with no forward progress (default 24), the worker stops
  re-querying a stuck receipt. Freezing is a polling-cadence decision
  only; it never alters proof bytes, and it may be cleared to resume.

One honesty note, stated in the worker's own source and repeated here:
the worker stores each calendar's latest blob and records the pin; it
does not itself parse the upgraded proof to independently confirm
Bitcoin inclusion. `status: "pinned"` is a server-side hint that the
proof is no longer calendar-pending. The authoritative check of
Bitcoin inclusion is independent verification (§4) with an
OpenTimestamps client against the chain — which requires no trust in
the office's reported status at all.

## 4. Verification

Verification is designed to be performed offline, with open-source
tools, without contacting the office. The materials required are the
original file (held by the customer), the receipt JSON with its `.ots`
proof files, and for folder claims the manifest and an inclusion proof.
The office's servers are deliberately absent from that list.

Three levels, in increasing independence:

1. **The receipt page** (`/r/<id>`) — convenience display; trusts the
   office to display honestly.
2. **The bundled MIT-licensed verifier** (`server/verify_cli.py`, ~100
   lines of standard-library Python; also shipped as a browser
   verifier) — re-hashes the file, compares against the receipt,
   checks each `.ots` file's header magic and embedded digest. Trusts
   only code the verifier can read.
3. **The upstream OpenTimestamps reference client** — `ots upgrade`
   then `ots verify` walks the proof to a Bitcoin block header and
   reports the block height and time. Trusts only Bitcoin and audited
   open-source software maintained outside the office.

What a verifier MUST check (the normative statement is
`docs/VERIFIER_SPEC.md`; the published vectors in `docs/test-vectors/`
pin the expected verdicts):

- **Digest recomputation.** SHA-256 the actual bytes; when the receipt
  carries `sha512_hex`, recompute SHA-512 as well, and treat a SHA-256
  match with a SHA-512 mismatch as a verification failure.
- **Strict canonical hex comparison.** The comparison against the
  receipt is a plain string comparison in which only the *supplied*
  side is whitespace-stripped and lowercased; the receipt's stored
  `hash_hex` is compared **verbatim**. Case-normalisation of the
  stored side is explicitly not performed. A receipt whose stored hash
  contains uppercase characters therefore matches no supplied digest —
  the service never writes uppercase, so such a receipt is by
  definition out-of-band-edited, and a verifier that "helpfully"
  lowercases both sides would accept exactly the tampered artifact a
  verifier exists to reject. The digest lives in `hash_hex` and
  nowhere else; alias fields must not be accepted. A stored hash of
  the wrong length or with non-hex characters renders the receipt
  corrupt, not merely mismatched.
- **`.ots` binding.** Each proof file must begin with the 31-byte
  header magic, and the 32 bytes at offset 33 must equal the decoded
  `hash_hex`. Truncated or wrong-magic files fail this check without
  raising.
- **Folder claims.** Recompute the leaf as
  `SHA-256(0x00 || path || 0x00 || file_digest)`, walk the proof
  (`"L"`: sibling on the left; `"R"`: on the right), and compare the
  result byte-for-byte with the anchored root. Every malformed input —
  bad direction token, non-hex or wrong-length sibling, wrong-length
  root or file hash — verifies false rather than raising. Sibling hex
  must be parsed strictly (reject any character outside
  `[0-9a-fA-F]`, reject odd length); parsing may accept uppercase hex
  because the comparison is on raw bytes. Folder-level re-verification
  rebuilds the local tree with the same exclusion list used at anchor
  time and compares lowercase 64-character roots exactly, against the
  manifest's `root_hex` only — never a fallback field.
- **Chain attestation.** For the full claim, upgrade and verify the
  `.ots` against the Bitcoin chain with an OpenTimestamps client; the
  block's header commits the aggregation root, and the block's
  timestamp bounds the existence claim.

## 5. Threat model

Stated against the shipped implementation. The office's design
assumption is that any of the parties below — including the office —
may later be adversarial, and that the receipt must survive them.

- **Operator tamper (the office itself).** The office holds no signing
  key whose compromise could forge a receipt; there is nothing of that
  kind to steal or subpoena. A receipt altered after issuance — a
  changed digest, an edited timestamp — fails independent verification:
  the `.ots` files bind the digest at byte offset 33, the calendar's
  proof path binds it to a Bitcoin commitment, and the customer may
  hold a copy of the entire receipt bundle. The office's storage is a
  convenience mirror, not the instrument. What the office *could* do
  is misreport status on its own pages — which is precisely why the
  verification path (§4) never consults the office.
- **Back-dating.** To assert an earlier time than the truth, a forger
  must place the digest inside a Bitcoin block that closed before the
  digest existed. That requires either rewriting the chain from the
  claimed depth forward at a cost that grows without bound as blocks
  accumulate, or finding a colliding preimage (below). The converse
  also holds and is stated plainly: the receipt cannot prove the bytes
  did *not* exist before the anchor; anchoring late proves nothing
  about early.
- **Calendar failure or hostility.** Five independently operated
  calendars each hold an independent commitment path. The evidentiary
  claim survives if any single calendar's proof upgrades to a Bitcoin
  attestation; a calendar that goes silent, refuses service, or
  disappears reduces redundancy without invalidating the receipt.
  Receipts whose remaining calendars permanently fail to upgrade are
  frozen (§3) rather than silently re-polled forever, and remain
  independently checkable. Total acceptance failure at anchor time
  (zero calendars) is treated as no anchor at all.
- **Service disappearance.** Verification requires no call to the
  office (§4, §6). Every receipt already issued continues to verify
  against the chain using the customer-held bundle and open tooling.
- **Compromise of the customer's device at anchor time.** Out of
  scope. The office anchors what the customer's software submits; an
  adversary controlling that software can anchor anything the customer
  could. The receipt records what was submitted, not who meant it.
- **Hash collisions — the honest arithmetic.** The receipt's strength
  is bounded by SHA-256. Producing *some* colliding pair costs on the
  order of 2^128 work (birthday bound); producing a second preimage for
  a *given* digest costs on the order of 2^256 classically, reduced
  toward ~2^128 under a hypothetical large-scale quantum adversary
  running Grover's algorithm. No practical attack on either is known.
  Receipts that record the SHA-512 sibling require the attacker to
  defeat both functions on the same bytes simultaneously. These are
  work estimates, not impossibilities: the design goal is that forgery
  be detectably infeasible, and the office says "infeasible", never
  "impossible".
- **What a receipt cannot do, restated.** It cannot prove authorship.
  It cannot prove content truth. It records existence of a byte
  sequence by a time — that, and only that.

## 6. Continuity

The receipt is built to outlive its issuer. The `.ots` format is an
open standard with multiple independent implementations; the verifier
the office ships is MIT-licensed, standard-library-only, and vendorable;
the Merkle construction is fully specified above and in
`docs/VERIFIER_SPEC.md`; the trust anchor is the Bitcoin chain, which
the office does not operate. A customer holding the original file and
the receipt bundle possesses everything verification requires,
indefinitely. If the office's storage were lost in its entirety, no
issued receipt would lose its evidentiary force; if the office ceased
operating, the sole capability lost would be the issuance of new
receipts. The office regards this asymmetry as the design's central
obligation: the instrument must not depend on the persistence of the
issuer.

## References

- FIPS 180-4, *Secure Hash Standard* — SHA-256, SHA-512.
- RFC 6962, *Certificate Transparency* — Merkle tree construction,
  leaf/node domain separation, odd-node promotion.
- RFC 9162, *Certificate Transparency Version 2.0*.
- RFC 2119 — requirement-level key words as used in
  `docs/VERIFIER_SPEC.md`.
- ISO 8601 — timestamp representation in receipt fields.
- CVE-2012-2459 — the duplicate-node Merkle ambiguity the construction
  avoids.
- The OpenTimestamps protocol — <https://opentimestamps.org/>.
- `docs/VERIFIER_SPEC.md` — normative verification algorithm and error
  taxonomy.
- `docs/test-vectors/` — published, reproducible test vectors for the
  constructions in this document.
