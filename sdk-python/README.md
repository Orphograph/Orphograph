# orphograph

Bitcoin-anchored folder receipts that a relying party can verify **without
the issuer**. Anchoring goes through the [Orphograph](https://orphograph.com)
service; verifying a file against its saved proof needs no server, no
account, and no network — the service can be unreachable or gone.

- License: MIT
- Python: 3.9 or newer
- Runtime dependencies: Python standard library only

## Verify without the issuer

This is the command a relying party runs. It needs the file, the POSIX path
the file had inside the anchored folder, the `proof.json` the anchoring
party saved, and the Merkle root from the receipt. Nothing is fetched.

```
python -m orphograph verify-inclusion /path/to/sub/photo.jpg sub/photo.jpg proof.json <root_hex>
# {"ok": true, "root_hex": "…", "root_source": "argument"}
# exit 0 on a match, 1 on a mismatch, 2 on an I/O or parse error
```

`root_hex` is the root recorded on the receipt (and in its OpenTimestamps
proof). Pin it. `ok` means "this file is in the tree with THAT root"; it
says nothing about a root you did not check against the receipt.

Same thing from Python, reading the object `inclusion-proof` writes
(`root_hex` and `proof` keys; the CLI additionally accepts a bare proof
array when `root_hex` is passed explicitly):

```python
import json
from orphograph import verify_inclusion

with open("proof.json") as f:
    saved = json.load(f)
ok = verify_inclusion(
    file_path="/path/to/sub/photo.jpg",
    rel_path="sub/photo.jpg",
    proof=saved["proof"],
    root_hex=saved["root_hex"],   # match this to the receipt's root first
)
```

`verify_inclusion` reads the file, recomputes its SHA-256, walks the proof
up to the root, and compares. It takes no `server_url` and opens no socket.
A missing local file raises `FileNotFoundError` rather than returning
`False`, so an I/O problem is never mistaken for a "not included" verdict.

To hand a relying party what they need, the anchoring party saves the
proof once, while the service is reachable:

```
python -m orphograph inclusion-proof <receipt_id> sub/photo.jpg > proof.json
```

`proof.json` carries `root_hex` and `proof`. The 4th argument may be
omitted, in which case the root inside `proof.json` is used and the verdict
reports `"root_source": "proof_json"`. That root came from the same file as
the proof, so a bundle is always self-consistent: compare the echoed
`root_hex` to the receipt before treating `ok` as meaningful. An explicit
`root_hex` always overrides the one in the file. When `proof.json` records a
`path` that differs from `rel_path`, one JSON warning line goes to standard
error; standard output and the exit code are unchanged.

The receipt's Bitcoin anchoring is an OpenTimestamps proof over the same
root. The anchoring party exports it with the rest of their vault
(`GET /api/me/anchors.zip`, authenticated; each receipt folder carries its
`receipt.json`, `.ots` files and, for folder receipts, `manifest.json`) or
per receipt (`GET /api/receipt/<receipt_id>.zip`, no login for public
receipts) and hands the `.ots` alongside `proof.json`. `ots info <file>.ots`
prints the attested Bitcoin block height with no service and no node;
`ots verify -d <root_hex> <file>.ots` checks the root against the chain
itself and needs a local Bitcoin node to do it (it exits 1 without one).
Neither step depends on this service.

## Privacy contract

The library does not transmit file contents. For each file the SDK reads
the bytes locally, streams them through SHA-256 in one megabyte chunks,
and commits the digest into an RFC 6962 Merkle leaf bound to the file's
POSIX relative path. Only the resulting manifest (paths, per-file digests,
leaf hashes, and the 32-byte root) is sent across the network. The
verification path is symmetric: the root is recomputed locally from the
folder on disk and compared to the root recorded in the receipt.

The Merkle module is a verbatim copy of the server's reference
implementation, carrying the source SHA-256 in its header so divergence
from the canonical algorithm is immediately visible.

## Install

```
pip install orphograph
```

## Anchor a folder

```python
from orphograph import anchor_folder

result = anchor_folder("/path/to/folder")
# {
#   "receipt_id": "...",
#   "root_hex": "...",
#   "leaf_count": 42,
#   "calendars_ok": 5,
#   "calendars_total": 5,
# }
```

Optional arguments:

| Argument       | Purpose                                                  |
| -------------- | -------------------------------------------------------- |
| `server_url`   | Base URL of the service (default `https://orphograph.com`). |
| `api_key`      | Sent as `X-Orpho-Api-Key` when present.                  |
| `client_label` | Short free-form label persisted with the receipt.        |
| `exclude`      | Sequence of `fnmatch` patterns. `None` selects the default deny list (OS detritus, editor backups, build caches). Passing `[]` disables exclusion. |

## Verify a folder

```python
from orphograph import verify_folder

ok = verify_folder("/path/to/folder", receipt_id="...")
```

The folder is walked locally, the Merkle root is recomputed, and the SDK
returns `True` only if the recomputed root equals the root recorded in
the receipt's manifest.

## Inclusion proofs

A folder receipt can be queried for a proof that a single file belonged
to the anchored tree. The proof is verified locally; no further network
call is required to confirm it.

```python
from orphograph import inclusion_proof, verify_inclusion

proof = inclusion_proof(receipt_id="...", path="sub/photo.jpg")
ok = verify_inclusion(
    file_path="/path/to/sub/photo.jpg",
    rel_path="sub/photo.jpg",
    proof=proof["proof"],
    root_hex=proof["root_hex"],
)
```

## Command line

The package installs an `orphograph` console script and is also runnable
as a module.

```
python -m orphograph verify-inclusion <file> <posix/rel/path> proof.json [root_hex]   # local only
python -m orphograph anchor /path/to/folder
python -m orphograph verify /path/to/folder <receipt_id>
python -m orphograph inclusion-proof <receipt_id> <posix/rel/path>
```

Each subcommand writes a single line of JSON to standard output. The
`verify` and `verify-inclusion` subcommands exit with status `0` on a
match and `1` on a mismatch. `verify-inclusion` ignores `--server-url`
and `--api-key`; it never connects anywhere.

Environment variables:

| Variable           | Purpose                                       |
| ------------------ | --------------------------------------------- |
| `ORPHO_SERVER_URL` | Default base URL.                             |
| `ORPHO_API_KEY`    | Default API key (sent as `X-Orpho-Api-Key`).  |

## Algorithm

The Merkle construction is RFC 6962 with a domain-separated leaf
(`0x00 || rel_path_utf8 || 0x00 || file_sha256`) and a domain-separated
internal node (`0x01 || left || right`). Odd-level remainders are
promoted, never duplicated, to avoid the second-preimage ambiguity of
the duplicate-last construction. The algorithm tag
`orphograph-merkle-v1-rfc6962` is embedded in every manifest.

## License

MIT. See `LICENSE`.
