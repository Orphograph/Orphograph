# Published test vectors

Machine-readable, reproducible vectors for the constructions described in
`docs/WHITEPAPER.md` and specified normatively in `docs/VERIFIER_SPEC.md`.
They exist so that any independent implementation — in any language — can
prove agreement with the canonical implementation (`server/engine.py`,
`server/merkle.py`) without access to the service.

Format tag: `orphograph-published-vectors-v1`.

| File | Contents |
|---|---|
| `single-file.json` | Single-file digest and receipt vectors: fixed byte strings, expected lowercase SHA-256, a minimal `receipt.json` shape, engine-shaped `.ots` blobs, and the expected verdicts — including three negatives (uppercase stored hash, one-byte-flipped content, truncated stored hash). |
| `folder.json` | A three-file folder: the manifest, per-file digests, leaf hashes, the Merkle root, inclusion proofs (one exercising RFC 6962 promotion), and two negatives (tampered content, renamed path). |
| `generate.py` | The generator. Every expected value is produced by **executing** the canonical implementation — never written by hand. |

These vectors complement (and do not replace) the conformance suite at
`tests/vectors/verifier_vectors.json`; that suite is broader on engine error
taxonomy, while this one is the small, documented, publishable set that
pairs with the whitepaper.

## Regenerating

```bash
python3 docs/test-vectors/generate.py
```

Regeneration with an unchanged engine is byte-identical;
`tests/test_published_vectors.py` runs the generator and fails if its output
drifts from the committed files. If the engine's behaviour changes
deliberately, regenerate in the same commit and treat the diff as a breaking
change for every independent verifier.

## Notes on reading the vectors

- All digests are 64 (SHA-256) or 128 (SHA-512) **lowercase** hex
  characters. The strict comparison rule (VERIFIER_SPEC §3.2) is pinned by
  `sf03_negative_stored_hash_uppercase`: only the *supplied* side of a
  comparison is stripped and lowercased; the receipt's stored `hash_hex` is
  compared **verbatim**, so an UPPERCASE stored hash matches no supplied
  digest. A verifier that lowercases both sides accepts a tampered receipt
  and fails this vector. (This encodes the correct behaviour that the
  historical verifier-js drift D1 — see `AUDIT_VERIFIER_DRIFT_2026_07_12.md`
  — got wrong; the shipped verifier-js has since been fixed and passes.)
- `ots_files` values are hex-encoded blobs in the engine's layout: 31-byte
  header magic, version `0x01`, tag `0x08`, the 32-byte digest at offset 33,
  then a clearly labelled **synthetic** body. They exercise the
  binding checks (magic + embedded digest); they are not real calendar
  proofs and cannot be upgraded against Bitcoin.
- Folder proofs are lists of `[direction, sibling_hex]` steps, leaf upward:
  `"L"` means the sibling sits on the left of the running hash, `"R"` on
  the right. A promoted (lone-last) node contributes no step —
  `f02_inclusion_promoted_leaf` pins that rule.

## Running against the JavaScript verifier (`verifier-js/`)

`verifier-js/orphograph_verify.js` checks the file-to-receipt binding (it
does not parse `.ots` structure — documented subset). Expected outcome per
single-file vector: `ok === true` for the two positives, `ok === false` for
all three negatives.

```js
// node run_vectors.mjs   (Node 18+, run from the repo root)
import { readFileSync } from "node:fs";
import { verifyReceiptAgainstFile } from "./verifier-js/orphograph_verify.js";

const suite = JSON.parse(readFileSync("docs/test-vectors/single-file.json", "utf8"));
const hex2bytes = h => Uint8Array.from(h.match(/../g)?.map(b => parseInt(b, 16)) ?? []);

for (const v of suite.vectors) {
  const receipt = JSON.parse(v.receipt_json);
  // For sf04 the candidate file is the flipped content.
  const content = hex2bytes(v.flipped_content_hex ?? v.content_hex);
  const res = await verifyReceiptAgainstFile(content, receipt);
  const expected = v.kind === "single_file";   // negatives must fail
  console.log(v.id, res.ok === expected ? "PASS" : "FAIL");
}
```

## Running against the Python SDK (`sdk-python/`)

The Python SDK's local primitive is `verify_inclusion`; its vendored
`MerkleTree` must agree with `folder.json` exactly:

```python
# python3 run_vectors.py   (run from the repo root)
import hashlib, json, sys
sys.path.insert(0, "sdk-python")
from orphograph._merkle import MerkleTree

suite = json.load(open("docs/test-vectors/folder.json"))
assert MerkleTree.from_manifest(suite["manifest"]).root_hex() == suite["root_hex"]

for v in suite["vectors"]:
    file_hash = hashlib.sha256(bytes.fromhex(v["content_hex"])).digest()
    got = MerkleTree.verify_inclusion(
        file_hash, v["rel_path"],
        [tuple(step) for step in v["proof"]],
        bytes.fromhex(v["root_hex"]))
    assert got == v["expect"]["included"], v["id"]
    print(v["id"], "PASS")
```

(The installed-package form `orphograph.verify_inclusion(file_path, ...)`
takes a filesystem path; write `content_hex` to a temp file to use it.)

## Running against the canonical engine

`tests/test_published_vectors.py` does this automatically in the suite. By
hand: materialise each single-file vector's `receipt_json` and `ots_files`
under a receipts directory as `receipts/<receipt_id>/`, point
`engine.RECEIPTS_DIR` at it, and compare `engine.verify_receipt` /
`engine.verify_hash_against_receipt` output against each operation's
`expect` block. The standalone `server/verify_cli.py <dir>/receipt.json`
reports the same binding verdicts for the materialised receipts.
