# orphograph

Bitcoin-anchored folder receipts that a relying party can verify **without
the issuer**. Anchoring goes through the Orphograph service; `verify-inclusion`
checks a file against its saved proof with no server, no account, and no
network. File contents never leave the local machine; only the manifest
(paths plus SHA-256 digests) and the Merkle root are transmitted at anchor
time.

- **License:** MIT
- **Runtime:** Node 20 or later
- **Dependencies:** none at runtime. The package uses the Node standard
  library only (`node:crypto`, `node:fs`, `node:http`, `node:https`,
  `node:path`).
- **Algorithm:** RFC 6962 binary Merkle tree, tag
  `orphograph-merkle-v1-rfc6962`. The TypeScript implementation is
  bit-for-bit compatible with the reference Python module and the
  browser implementation.

## Install

```
npm install orphograph
```

## Usage

```ts
import {
  anchorFolder,
  verifyFolder,
  inclusionProof,
  verifyInclusion,
} from "orphograph";

const result = await anchorFolder("/path/to/folder", {
  serverUrl: "https://orphograph.com",
  apiKey: process.env.ORPHO_API_KEY, // optional
  clientLabel: "Case file 2026-05-20",
});
// {
//   receipt_id: "abcd1234...",
//   root_hex: "f3a9...",
//   leaf_count: 42,
//   calendars_ok: 5,
//   calendars_total: 5,
// }

const ok = await verifyFolder("/path/to/folder", result.receipt_id, {
  serverUrl: "https://orphograph.com",
});

const proof = await inclusionProof(result.receipt_id, "sub/photo.jpg", {
  serverUrl: "https://orphograph.com",
});

const ok2 = await verifyInclusion(
  "/path/to/sub/photo.jpg",
  "sub/photo.jpg",
  proof.proof,
  proof.root_hex,
);
```

`verifyInclusion` requires no network access. Once the proof and the root
are in hand, a third party can confirm locally that a file they hold
belonged to the anchored folder.

## Command-line interface

The package registers an `orphograph` binary.

```
npx orphograph anchor /path/to/folder
# {"receipt_id":"abcd1234...","root_hex":"f3a9...","leaf_count":42,"calendars_ok":5,"calendars_total":5}

npx orphograph verify /path/to/folder abcd1234
# {"ok":true}
# exit code 0 on match, 1 on mismatch

npx orphograph proof abcd1234 sub/photo.jpg
# {"receipt_id":"abcd1234","root_hex":"...","path":"sub/photo.jpg",...}

npx orphograph verify-inclusion /path/to/sub/photo.jpg sub/photo.jpg proof.json <root_hex>
```

Flags:

- `--server URL` overrides the default `https://orphograph.com` endpoint.
- `--api-key KEY` (or environment variable `ORPHO_API_KEY`) attaches an
  API key as the `X-Orpho-Api-Key` request header.
- `--label TEXT` records a free-form client label on the receipt
  (truncated to 200 characters by the server).

## What crosses the network

The body of `POST /api/anchor_folder` is the manifest only. The manifest
is a JSON object containing:

- the algorithm tag and version,
- the relative POSIX path of each file,
- the SHA-256 digest of each file in lowercase hex,
- the corresponding leaf hash, the file size in bytes, and the Merkle
  root in lowercase hex.

The raw bytes of the files being anchored are read locally, streamed
through SHA-256 in 1 MiB chunks, and incorporated into the tree in
memory. No file body is ever placed in a request.

## Algorithm summary

- Leaf: `SHA-256(0x00 || rel_path_utf8 || 0x00 || file_sha256)`.
- Internal: `SHA-256(0x01 || left || right)`.
- Odd-level handling: the lone last node is promoted to the next level
  (RFC 6962). Nodes are not duplicated, to avoid the second-preimage
  ambiguity documented in CVE-2012-2459.
- Empty folders are rejected. A single-file folder yields a root equal
  to that file's leaf hash.
- Symbolic links are skipped, not followed. Hidden dotfiles are
  included by default; evidentiary cases frequently require them.
- Paths are normalised to POSIX form before sorting. The sort key is
  the UTF-8 byte order of the path string.
- The default exclude list filters incidental files (`.DS_Store`,
  `Thumbs.db`, `desktop.ini`, `.git/*`, `node_modules/*`, `__pycache__/*`,
  `*.tmp`, `*.swp`, `*.swo`, `~$*`). Passing `exclude: []` disables
  exclusion; passing a custom list replaces the defaults.

## Compatibility with the Python and browser implementations

The same fixture folder must produce the same `root_hex` under all three
implementations. The test suite in `test/merkle.test.ts` shells out to
`python3` against the reference module at `server/merkle.py` and asserts
equality of the root and per-leaf hashes. When `python3` is not present
on the host, the cross-check subtests are skipped automatically; the
remaining unit-level vector tests still execute.

## Development

```
npm install
npm run build      # tsc to ./dist
npm test           # node --experimental-strip-types --test test/*.test.ts
```

Source layout:

- `src/merkle.ts` — RFC 6962 tree, folder walk, leaf and internal hashes.
- `src/client.ts` — HTTP transport over `node:http` and `node:https`.
- `src/index.ts` — public API (`anchorFolder`, `verifyFolder`,
  `inclusionProof`, `verifyInclusion`).
- `src/cli.ts` — command-line entry point registered as the `orphograph`
  binary.

## License

MIT. See `LICENSE`.
