# orphograph

A Python SDK for anchoring a local folder to the Bitcoin chain through the
[Orphograph](https://orphograph.com) hosted service.

- License: MIT
- Python: 3.9 or newer
- Runtime dependencies: Python standard library only

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
python -m orphograph anchor /path/to/folder
python -m orphograph verify /path/to/folder <receipt_id>
python -m orphograph inclusion-proof <receipt_id> <posix/rel/path>
```

Each subcommand writes a single line of JSON to standard output. The
`verify` subcommand exits with status `0` on a match and `1` on a
mismatch.

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
