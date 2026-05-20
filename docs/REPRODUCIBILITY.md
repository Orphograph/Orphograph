# How a folder receipt stays reproducible

## In one paragraph

Two people running the office's software on the same folder should get the
same 64-character receipt code. Anyone can rebuild the receipt later from
the folder itself and check that it matches the one originally stored. This
document spells out the rules the software follows so that this remains
true on Mac, Windows, and Linux, today and in ten years.

## In plain English

A folder receipt is a single short code that stands for every byte of every
file in the folder. The code is built by:

1. Listing every file inside the folder.
2. Skipping a small set of files the operating system creates on its own
   (hidden temp files, OS clutter — see the exclude list below).
3. Sorting the remaining files in a fixed order so the result does not
   depend on the order in which the operating system happened to return them.
4. Computing a 64-character fingerprint of each file's contents.
5. Pairing the fingerprints up and combining them, two at a time, until a
   single 64-character code remains. That code is the receipt.

If any byte of any file changes, that file's fingerprint changes, every
combination above it changes, and the receipt no longer matches. Renaming
a file also changes the receipt, on purpose — a renamed file is treated
as a different file.

The receipt is anchored to the Bitcoin chain, so the time the receipt was
issued can be checked against a public, neutral record.

## For developers

The algorithm tag is `orphograph-merkle-v1-rfc6962`. The receipt is the
root of a binary Merkle tree built per RFC 6962 (§2.1) with the following
fixed choices:

```
leaf_i      = SHA-256( 0x00 || relative_path_utf8 || 0x00 || file_sha256_i )
internal    = SHA-256( 0x01 || left_child || right_child )
```

The `0x00` and `0x01` prefixes are domain separation. Without them, a
file whose contents matched the byte pattern of an internal node could
be smuggled into a tree, breaking second-preimage resistance. Domain
separation is mandatory.

### Canonical file ordering

Files are listed by their POSIX relative path inside the folder (forward
slashes only — Windows backslashes are normalised to forward slashes
before sorting). The sort key is the UTF-8 byte representation of the
path; ties are not possible because two distinct files cannot share the
same path inside a single folder.

Unicode normalisation is **not** applied. A path containing a
pre-composed character and an otherwise-identical path containing the
decomposed form are treated as distinct paths. This matches Git's choice
and is the safer default for evidentiary work — the path on disk is the
path that was committed.

### Symlinks, hidden files, empty folders

- Symlinks are **skipped**, not followed.
- Hidden files (names beginning with `.`) are **included** by default.
  Evidentiary cases often need them.
- Empty folders are **rejected** in v1. The caller is expected to add at
  least one file before anchoring.

### Default exclude list

These names are excluded by default because they are operating-system
artefacts, not user content. The exclude list is itself recorded in the
manifest so a verifier can reproduce the same selection.

```
.DS_Store
Thumbs.db
desktop.ini
.git/*
node_modules/*
__pycache__/*
*.tmp
*.swp
*.swo
~$*
```

A caller can override the list. Passing an empty list disables exclusion
entirely.

### Odd-level handling

When a level of the tree has an odd number of nodes, the lone last node
is **promoted unchanged** to the next level (RFC 6962, §2.1). The lone
node is **not** duplicated. Duplication is the rule used by the Bitcoin
block tree and produces an ambiguity (CVE-2012-2459) in which two
distinct trees can collide on the same root. The office uses promotion.

### Streaming

Files are hashed in 1 MiB chunks. No file is ever loaded fully into
memory. There is no upper bound on file size from the algorithm itself;
practical limits come from the device.

### Manifest shape

The manifest is the JSON object below, with `signature` optional:

```json
{
  "algorithm": "orphograph-merkle-v1-rfc6962",
  "version": 1,
  "root_hex": "<64 lowercase hex characters>",
  "leaves": [
    {
      "path": "<posix relative path>",
      "file_sha256_hex": "<64 hex>",
      "leaf_hex": "<64 hex>",
      "size_bytes": <integer>
    }
  ]
}
```

The `signature` block, if present, is computed over the canonical JSON
serialisation of the manifest with the `signature` field removed.
Canonical here means `sort_keys=True`, `separators=(",", ":")`,
`ensure_ascii=True`. See `docs/THREAT-MODEL.md` for the model the
signature is meant to address.

### Verification recipe

A third party can verify a folder receipt independently:

1. Walk the folder under the same rules above.
2. Build the manifest the same way.
3. Compute the root.
4. Compare to the `root_hex` recorded in the receipt.
5. Run the standard OpenTimestamps client (`ots verify`) against the
   `.ots` file shipped with the receipt to check the Bitcoin anchor.

No call to the office is required. The standalone verifier under
`dist/orphograph-verify/verify.py` is one implementation of this recipe.

## When this document can change

The on-disk choices above are part of the receipt. Changing any of them
produces a different root for the same folder, so a backwards-compatible
change must travel under a new algorithm tag — `orphograph-merkle-v2-...`,
not a quiet edit to v1. Existing receipts continue to verify against the
v1 rules forever.
