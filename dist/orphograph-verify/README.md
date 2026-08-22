# orphograph-verify

Standalone verifier for [Orphograph](https://orphograph.com) receipts.

If Orphograph's domain disappears tomorrow, this directory is all you
need to prove that a file — or a whole folder — existed at a moment in
time. It re-derives the Merkle root of a folder, walks an inclusion
proof for a single file, and (optionally) invokes the OpenTimestamps
reference client to confirm that the Bitcoin-chain witness references
the same root.

Stdlib only. No `pip install`. No network calls (except for the
optional `ots verify` sub-check, which is performed by the OpenTimestamps
reference client when present, not by this script).

## Why this directory exists

Orphograph is a service that hashes files in your browser and anchors
those hashes to the Bitcoin blockchain via OpenTimestamps. The crypto
is open; the convenience is what is sold.

That convenience only matters if the underlying proof outlives the
company that issued it. This verifier is published as MIT so that:

1. The format is auditable. A receipt is `receipt.json` plus standard
   `.ots` proof files; a folder anchor adds a `manifest.json`.
2. It runs offline.
3. If the service is unavailable, the receipt still verifies.

## Layout

```
verify.py       — entry point with two subcommands (file / folder)
merkle.py       — vendored copy of server/merkle.py (RFC 6962, banner notes)
README.md       — this file
LICENSE         — MIT
examples/       — sample receipts and folders for smoke-testing
```

The vendored `merkle.py` carries a sha256 banner at the top so the
copy can be re-derived from the source of truth at any time.

## Usage

### Verify a single file via an inclusion proof

```
python3 verify.py file --file path/to/original.jpg \
                       --proof path/to/proof.json \
                       [--ots path/to/root.ots]
```

The proof JSON is the document returned by the Orphograph
`/api/inclusion_proof` endpoint (or any equivalent producer). It
carries the relative path, the expected root hex, the file's
SHA-256, and the list of sibling hashes that walk the leaf up to the
root.

The verifier re-hashes the local file, cross-checks against the
proof's recorded `file_sha256_hex` when present, and then walks the
proof bottom-up using the RFC 6962 algorithm defined in `merkle.py`.

### Verify a whole folder via its manifest

```
python3 verify.py folder --dir path/to/folder \
                         --manifest path/to/manifest.json \
                         [--ots path/to/root.ots] \
                         [--exclude GLOB ...]
```

The verifier walks the local directory through the same RFC 6962
algorithm the server uses, computes the root, and compares it to the
`root_hex` recorded in the manifest. A mismatch is a `FAIL`; an
exact match is an `OK`. The comparison is strict: the recomputed root
(lowercase by construction) must equal the manifest's `root_hex`
byte-for-byte — a manifest edited to uppercase does not verify.

If the folder was anchored with custom excludes, pass the SAME
repeatable `--exclude GLOB` flags here. Supplying any `--exclude`
replaces the default deny-list rather than extending it (identical
semantics to the Orphograph SDK CLI); with different excludes the
recomputed root cannot match the manifest.

### Optional OpenTimestamps sub-check

When `--ots` is supplied, the verifier first checks, offline, that the
`.ots` file's embedded digest is the `root_hex` it just reproduced
(otherwise the state is `UNBOUND` — the proof is about a different
hash). It then runs `ots verify -d <root_hex> <file.ots>` via subprocess
(list-form, never via a shell) and classifies the client's exit code and
wording into `VERIFIED` / `PENDING` / `FAILED` / `UNAVAILABLE` /
`INDETERMINATE`. Only `VERIFIED` is a pass. The client's output is never
searched for the hash as evidence of anything.

Install the OpenTimestamps reference client when needed:

```
pip install opentimestamps-client
```

If the binary is not on `PATH`, the verifier reports the absence and
returns exit code 4 — the core Merkle check is unaffected.

## Exit codes

- `0` — verification succeeded
- `2` — invalid arguments or missing input files
- `3` — file or folder did not reproduce the recorded root
- `4` — the OpenTimestamps chain step did not pass. The line `[OTS] <STATE>:` on
  stdout says which: `FAILED` (the client rejected the proof) · `PENDING` (not yet
  on Bitcoin) · `UNAVAILABLE` (the check could not run — no `ots` binary or no
  Bitcoin node) · `UNBOUND` (the `.ots` commits to a different hash) ·
  `INDETERMINATE`. Only `FAILED` means the proof is bad; the others mean the
  chain step rendered no verdict. The Merkle/file result is unaffected either way.

## License

MIT. See `LICENSE`. Copyright (c) 2026 the Orphograph contributors.
