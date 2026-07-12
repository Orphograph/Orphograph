# Orphograph Receipt Verification — Canonical Specification

Status: NORMATIVE. Version 1 (2026-07-12).
Canon: `server/engine.py` (`verify_receipt`, `verify_hash_against_receipt`) and
`server/merkle.py` (`MerkleTree.verify_inclusion`). Where any independent
verifier disagrees with the engine, the engine's observed behaviour is the
specification and the verifier is wrong — for a notary, a verifier that
disagrees with the server is a correctness bug, not a style choice.

Conformance vectors: `tests/vectors/verifier_vectors.json`
(format `orphograph-verifier-vectors-v1`), replayed against the engine by
`tests/test_verifier_vectors.py`.

The key words MUST, MUST NOT, SHOULD, and MAY are to be interpreted as in
RFC 2119.

---

## 0. What verification proves — and what it does not (honest-claims framing)

A successful Orphograph verification establishes exactly this:

> A file with this SHA-256 fingerprint was submitted to Orphograph at the
> recorded time, and the submitted digest is embedded in one or more
> OpenTimestamps proof files that can be independently upgraded to a
> Bitcoin-block attestation.

It does **not** establish authorship, ownership, originality, legality, or
that the file's *content* is true. It does not prove the file did not exist
earlier. A folder-inclusion proof additionally establishes only that a
(path, file-digest) pair was a leaf of the Merkle tree whose root was
anchored — nothing about the other leaves is revealed or claimed.

Verifier output and documentation MUST NOT overstate these claims.

---

## 1. Objects and inputs

### 1.1 Receipt

A receipt is a directory `RECEIPTS_DIR/<receipt_id>/` containing:

- `receipt.json` — the receipt record. Required fields for verification:
  - `hash_hex` — string, exactly 64 hex characters, the anchored SHA-256.
    The anchoring path (`anchor_hash`) normalises to **lowercase** at write
    time; verifiers MUST treat the stored string as authoritative bytes-of-
    string (see §3.2 for the case rule).
  - `created_at` — ISO-8601 UTC timestamp (surfaced, not validated).
  - Optional: `sha512_hex` (sibling witness), `client_label`, `private`,
    `owner_id`, `attestation`, `metadata`, `status`, `btc_pinned_at`,
    and for folder anchors: `kind: "folder"`, `leaf_count`,
    `merkle_algorithm`, `paths_public`, `signature_verified`, `signer_kid`.
- Zero or more `*.ots` files — one per calendar that accepted the digest.
- For folder anchors: `manifest.json` (see §4).

### 1.2 OTS proof file layout (as built by the engine)

```
offset 0   : 31-byte header magic
             00 4F 70 65 6E 54 69 6D 65 73 74 61 6D 70 73 00 00 50 72 6F 6F
             66 00 BF 89 E2 E8 84 E8 92 94
offset 31  : version byte        0x01
offset 32  : hash-algorithm tag  0x08  (SHA-256)
offset 33  : 32-byte anchored digest (raw bytes)
offset 65+ : calendar response body (opaque to this spec)
```

The digest offset is FIXED at `len(magic) + 2 = 33`. The engine builds its
own `.ots` files with a single-byte version and tag, so a conforming verifier
MUST read the digest at offset 33 and MUST NOT attempt varint parsing for
receipts issued by this service.

---

## 2. Algorithm: `verify_receipt(receipt_id)`

Given a `receipt_id`:

1. If `RECEIPTS_DIR/<receipt_id>/receipt.json` does not exist, return
   `{receipt_id, found: false, error: "receipt not found"}`. STOP.
2. Parse `receipt.json`. If parsing fails, or `hash_hex` is missing, or
   `hash_hex` is not a string of length exactly 64, or `hash_hex` is not
   decodable as hex, return
   `{receipt_id, found: false, error: "corrupt receipt"}`. STOP.
   - MUST: the two failure shapes above are the ONLY error shapes; both
     carry `found: false` and no other verification fields.
3. Let `expected_hash = hex_decode(hash_hex)` (32 bytes).
4. For each `*.ots` file in the receipt directory, in **sorted filename
   order**, produce a check object:
   - `magic_ok` — file bytes start with the 31-byte header magic.
   - `hash_match` — `bytes[33..65) == expected_hash`. If the file is
     truncated the slice is short and the comparison is simply false;
     truncation MUST NOT raise or produce a distinct error.
     If `magic_ok` is false, `hash_match` MUST be false (the engine compares
     the empty string).
   - `ok` — `magic_ok AND hash_match`.
5. Return `found: true` with:
   - `calendars_ok` — count of checks with `ok == true`,
   - `calendars_total` — count of `.ots` files (MAY be 0; zero proofs is
     NOT an error — the receipt is found, with `calendars_total: 0`),
   - `checks` — the per-file list,
   - the surfaced receipt fields (`created_at`, `hash_hex`, `sha512_hex`,
     `client_label`, `private`, `attestation`, `metadata`, `status`
     (default `"pending"`), `btc_pinned_at`), and the folder /
     signature fields only when present (shape-stability rule: absent
     fields stay absent).

### Pseudocode

```
verify_receipt(rid):
  f = RECEIPTS_DIR/rid/receipt.json
  if not exists(f):            return {rid, found:false, error:"receipt not found"}
  try:
    rec = json.parse(read(f))
    hh  = rec["hash_hex"]
    require isinstance(hh, str) and len(hh) == 64
    expected = hex_decode(hh)          # raises on non-hex
  except:                       return {rid, found:false, error:"corrupt receipt"}
  checks = []
  for ots in sorted(glob(RECEIPTS_DIR/rid/*.ots)):
    data      = read_bytes(ots)
    magic_ok  = data.startswith(MAGIC)
    embedded  = magic_ok ? data[33:65] : b""
    checks   += {file, magic_ok, hash_match: embedded == expected,
                 ok: magic_ok and (embedded == expected)}
  return {rid, found:true, hash_hex:hh, ..., calendars_ok, calendars_total, checks}
```

---

## 3. Algorithm: `verify_hash_against_receipt(receipt_id, supplied_hash)`

1. Run `verify_receipt(receipt_id)`. If `found` is false, return that result
   unchanged (the supplied hash MUST NOT be echoed for a missing/corrupt
   receipt).
2. `supplied = supplied_hash.strip().lower()` — the supplied side is
   whitespace-stripped and lowercased.
3. `supplied_matches_receipt = (supplied == receipt.hash_hex)` — a plain
   string comparison against the **stored string verbatim**.
4. Add `supplied_hash` (the normalised form) and `supplied_matches_receipt`
   to the result and return it.

### 3.1 Malformed supplied hashes

A supplied hash that is not hex, is the wrong length, or is empty is NOT an
error: it simply fails to match (`supplied_matches_receipt: false`, receipt
still `found: true`). Verifiers MUST NOT raise on garbage input here.
(Vector `v05_supplied_hash_malformed_hex`.)

### 3.2 Case rule (canon quirk — normative)

Because only the *supplied* side is lowercased, a receipt whose stored
`hash_hex` contains uppercase characters is `found: true`, its `.ots` byte
comparison still passes, but NO supplied hash can ever match it. The
anchoring path never writes uppercase, so this arises only for
out-of-band-edited receipts — but conforming verifiers MUST reproduce it
rather than "helpfully" lowercasing both sides.
(Vector `v09_receipt_hash_stored_uppercase`. Known deviation: verifier-js
lowercases the stored side — see `AUDIT_VERIFIER_DRIFT_2026_07_12.md` D1.)

### 3.3 Field-name strictness

The anchored digest lives in `hash_hex` and nowhere else. Verifiers MUST NOT
accept alias fields (`sha256`, `sha256_hex`, …) as a substitute; a receipt
without a usable `hash_hex` is corrupt (§2 step 2).

### 3.4 SHA-512 sibling

`sha512_hex`, when present, is a sibling witness recorded at anchor time.
The engine's hash check covers SHA-256 only. File-side verifiers (which hash
the actual bytes, e.g. `server/verify_cli.py --file`, verifier-js) SHOULD
also recompute and compare SHA-512 when the receipt carries one, and MUST
treat a SHA-256 match with a SHA-512 mismatch as a verification FAILURE
(post-collision tamper or wrong file).

---

## 4. Folder anchors: Merkle inclusion

Algorithm tag: `orphograph-merkle-v1-rfc6962` (manifest `algorithm` field;
`version: 1`).

- Leaf: `SHA-256(0x00 || rel_path_utf8 || 0x00 || file_sha256)` — the POSIX
  relative path is bound into the leaf; renaming a file changes the root.
- Internal: `SHA-256(0x01 || left || right)`.
- Odd level: the lone last node is PROMOTED unchanged (RFC 6962; never
  duplicated).
- Leaves are sorted by UTF-8 byte order of the POSIX relative path.

### 4.1 `verify_inclusion(file_hash, rel_path, proof, root) -> bool`

Inputs: raw 32-byte SHA-256 of the file **content** (not the leaf), the
committed POSIX relative path, a proof list of `[direction, sibling_hex]`
steps ordered leaf-upward (`"L"` = sibling on the left, `"R"` = right), and
the 32-byte root.

```
verify_inclusion(file_hash, rel_path, proof, root):
  if len(file_hash) != 32:  return false
  if len(root) != 32:       return false
  current = leaf_hash(rel_path, file_hash)
  for step in proof:
    if step is not a 2-item pair or step[0] not in {"L","R"}: return false
    sibling = hex_decode(step[1]);  on failure: return false
    if len(sibling) != 32:  return false
    current = step[0]=="L" ? internal(sibling, current)
                           : internal(current, sibling)
  return current == root
```

MUST behaviors:

- Every malformed input (bad direction token, non-hex sibling, wrong-length
  sibling or root, wrong-length file hash) returns **false**. The function
  MUST NOT raise. There is deliberately no error taxonomy here: an
  inclusion proof either verifies or it does not.
- Hex decoding of siblings/roots MUST be strict — reject any character
  outside `[0-9a-fA-F]` and any odd-length string. (Known deviation:
  sdk-node's `fromHex` is lenient in the second nibble — audit D4.)
- Hex decoding MAY accept uppercase (both `bytes.fromhex` and strict
  parsers do); the digest comparison is on raw bytes.
- A promoted (lone-last) node contributes no proof step at that level, so
  valid proofs may be shorter than `ceil(log2(n))`.

An empty proof list is valid and verifies iff `leaf_hash(rel_path,
file_hash) == root` (single-file tree: root == leaf).

### 4.2 Folder-level verification (`verify_folder`)

Client SDKs re-walk the local folder, rebuild the tree, and compare
`root_hex` (lowercase, 64 hex chars) against the `manifest.root_hex` served
by `/api/verify_folder/<rid>`:

- MUST use the manifest's `root_hex` as the remote side. If the manifest or
  its `root_hex` is absent, the result is **false** (no fallback to
  `receipt.hash_hex` — audit D3).
- MUST rebuild with the same exclusion list used at anchor time. SDKs
  SHOULD accept a caller-supplied `exclude` for parity with anchoring
  (audit D2).
- The comparison is an exact match of lowercase hex strings.

---

## 5. Error taxonomy (summary)

| Condition | Canonical result |
|---|---|
| Receipt directory / receipt.json missing | `found: false`, `error: "receipt not found"` |
| receipt.json unparseable, hash_hex missing / wrong length / non-hex | `found: false`, `error: "corrupt receipt"` |
| `.ots` bad magic | check `{magic_ok: false, hash_match: false, ok: false}`; receipt still found |
| `.ots` truncated mid-digest | `{magic_ok: true, hash_match: false, ok: false}` |
| `.ots` digest ≠ hash_hex | `{magic_ok: true, hash_match: false, ok: false}` |
| No `.ots` files | found, `calendars_total: 0` |
| Supplied hash wrong / malformed / empty | found, `supplied_matches_receipt: false` (never an exception) |
| Inclusion proof malformed in any way | `false` (never an exception) |

HTTP mapping (server): missing receipt → 404 with the not-found body;
invalid receipt-id shape → 400; private receipt viewed by non-owner → the
same 404 body as not-found (existence MUST NOT leak).

---

## 6. Test-vector format (`orphograph-verifier-vectors-v1`)

`tests/vectors/verifier_vectors.json`:

```jsonc
{
  "format": "orphograph-verifier-vectors-v1",
  "generated": "YYYY-MM-DD",
  "ots_header_magic_hex": "…",       // 31-byte magic, hex
  "vector_count": N,
  "vectors": [ … ]
}
```

Two vector kinds:

**`kind: "receipt"`** — materialise `receipt_json` (verbatim string; may be
intentionally invalid JSON; `null` = no receipt.json) and each
`ots_files[name]` (hex-encoded bytes) into `RECEIPTS_DIR/<receipt_id>/`,
then run `operation` (`verify_receipt`, or `verify_hash_against_receipt`
with `supplied_hash`). Every key in `expect` MUST be present in the result
and equal (`found`, `error`, `hash_hex`, `sha512_hex`, `calendars_ok`,
`calendars_total`, `status`, `checks`, `supplied_hash`,
`supplied_matches_receipt`).

**`kind: "merkle_inclusion"`** — decode `file_b64`, SHA-256 it (MUST equal
`file_sha256_hex`), run `verify_inclusion(file_hash, rel_path, proof,
root_hex)`; result MUST equal `expect.included`. The shared `manifest` is
included so implementations can also round-trip `from_manifest` and check
`root_hex`.

Regeneration rule: expected values are produced by EXECUTING the engine
(never written by hand). If the engine's behaviour changes deliberately,
regenerate the vectors in the same commit and treat it as a breaking change
for every independent verifier.

---

## 7. Conformance

An implementation conforms iff it passes every applicable vector in
`tests/vectors/verifier_vectors.json` with results identical to `expect`.
Implementations that verify only a subset of operations (e.g. verifier-js
checks file-to-receipt binding only, not `.ots` structure) MUST document the
subset and MUST NOT claim full receipt verification.
