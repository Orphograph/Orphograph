# Verifier Drift Audit — 2026-07-12 (INTERNAL)

Scope: the three shipped independent verification implementations —
`sdk-python/`, `sdk-node/`, `verifier-js/` — audited against the canonical
server logic (`server/engine.py:verify_receipt` /
`verify_hash_against_receipt`, lines 277-354, and
`server/merkle.py:MerkleTree.verify_inclusion`). Adjacent implementations
noted for completeness: `server/verify_cli.py` (vendored standalone, also
copied under `dist/orphograph-verify/`) and the browser Merkle in
`web/folder.js`. Doctrine: the engine is canon; a verifier that disagrees
with the server is a correctness bug.

Deliverables landed with this audit:

- `docs/VERIFIER_SPEC.md` — normative algorithm + error taxonomy.
- `tests/vectors/verifier_vectors.json` — 20 conformance vectors, expected
  values produced by EXECUTING the engine.
- `tests/test_verifier_vectors.py` — replays every vector against the engine.

Node/JS verifiers were exercised against the vectors (no installs;
`/opt/homebrew/bin/node` + `sdk-node/dist` + `verifier-js` ES module).
Results below.

---

## 1. What each implementation actually verifies

| Capability | engine (canon) | verify_cli.py | verifier-js | sdk-python | sdk-node |
|---|---|---|---|---|---|
| Receipt lookup + shape check | yes | yes (local receipt.json) | no (takes receipt object) | no (server-side via API) | no (server-side via API) |
| `.ots` magic + embedded-digest check | yes | yes (same offset 33) | **no** (documented out of scope) | no | no |
| File re-hash vs receipt (SHA-256) | hash-string compare only | yes (`--file`) | yes | no | no |
| SHA-512 sibling check | surfaces field only | yes (fails run on mismatch) | yes (fails `ok`) | no | no |
| Folder root recompute vs manifest | n/a (server serves manifest) | no | no | yes (`verify_folder`) | yes (`verifyFolder`) |
| Merkle inclusion proof | via server/merkle.py | no | no | yes | yes |

No single independent verifier covers the whole receipt. verifier-js checks
binding only; the SDKs check folder/Merkle only. That split is documented in
each module and now normative in the spec (§7), but customers combining them
should be pointed at the pairing explicitly.

## 2. Drift matrix — worst first

### D1. verifier-js lowercases the receipt's stored hash — WRONG ANSWER vs canon (severity: HIGH)

`verifier-js/orphograph_verify.js:157` —
`String(receipt.hash_hex || …).toLowerCase()`. The engine
(`engine.py:352-353`) lowercases only the SUPPLIED side and compares against
the stored string verbatim. For a receipt whose stored `hash_hex` is
uppercase, canon says nothing matches; verifier-js says `ok: true`.

Demonstrated live against vector `v09_receipt_hash_stored_uppercase`:
engine → `supplied_matches_receipt: false`; verifier-js → `ok: true,
sha256_match: true, sha512_match: true`. **DRIFT confirmed by execution.**

Mitigation in practice: `anchor_hash` normalises to lowercase at write time
(`engine.py:116-118`), so service-issued receipts never hit this. It bites
only on out-of-band-edited receipt JSON — which is exactly the adversarial
case a verifier exists for. Spec §3.2 pins the canon behaviour.

### D2. sdk-python `verify_folder` hardcodes the default exclude list — WRONG ANSWER between SDKs (severity: HIGH)

`sdk-python/orphograph/__init__.py:101` — `MerkleTree.from_folder(root,
exclude=None)` with no way for the caller to override, while
`anchor_folder` DOES accept `exclude`. A folder anchored with a custom
exclude list can never verify through the Python SDK (root mismatch → false
negative, permanently). sdk-node `verifyFolder` accepts `options.exclude`
(`sdk-node/src/index.ts:110,126-128`), so the two SDKs return different
answers for the same folder + receipt. Fix pass: add `exclude` kwarg to
`verify_folder` (signature-compatible; not touched in this pass per scope).

### D3. sdk-node `verifyFolder` falls back to `receipt.hash_hex` when the manifest root is absent — DIFFERENT ANSWER in the degraded path (severity: MEDIUM) — **FIXED 2026-07-15**

> **FIXED 2026-07-15** (branch `fix/verifier-minor-drifts`): fallback removed
> in `sdk-node/src/index.ts` — remote side is `manifest.root_hex` or the
> result is `false`, per spec §4.2. Verified live against a mock server:
> degraded response (no manifest, matching `receipt.hash_hex`) → `false`;
> full response → `true`. Regression tests added in
> `sdk-node/test/client.test.ts`.

`sdk-node/src/index.ts:135-138`: remote root :=
`manifest.root_hex || receipt.hash_hex || ""`. sdk-python
(`__init__.py:95-98`) returns `False` when `manifest.root_hex` is missing.
For folder receipts `hash_hex == root_hex` so the fallback usually agrees,
but on a redacted/partial response the node SDK can return `true` where
Python returns `false`. Spec §4.2 rules: manifest root or fail.

### D4. sdk-node `fromHex` is lenient in the second nibble — DIFFERENT ERROR / lenient parse (severity: MEDIUM-LOW) — **FIXED 2026-07-15**

> **FIXED 2026-07-15** (branch `fix/verifier-minor-drifts`): strict per-pair
> regex (`/^[0-9a-fA-F]{2}$/`) added to `fromHex` in
> `sdk-node/src/merkle.ts`, per spec §4.1. Verified by execution:
> `fromHex("a?")`, `fromHex("1g")`, `fromHex("zz")` all throw
> "invalid hex string"; `"deadbeef"`/`"DEADBEEF"` still decode.
> `verifyInclusion` with a lenient-nibble sibling stays a clean `false`.
> Regression tests added in `sdk-node/test/merkle.test.ts`.

`sdk-node/src/merkle.ts:79-90` uses `parseInt(pair, 16)`, which parses
`"a?"` → 10 and `"1g"` → 1 (confirmed by execution: `fromHex("a?") → [10]`,
`fromHex("1g") → [1]`; Python `bytes.fromhex` raises on both). All executed
inclusion vectors still agreed (the mangled bytes hash to the wrong node, so
verification ends `false` either way), and `"zz"` throws in both. But the
node SDK accepts malformed proof/root hex that every other implementation
rejects — a strict-parse fix (`/^[0-9a-fA-F]{2}$/` per pair) is warranted.
Spec §4.1 requires strict hex.

### D5. verifier-js accepts alias receipt fields (`sha256_hex`, `sha256`, `id`, `receiptId`) — DIFFERENT ANSWER on non-canonical receipts (severity: MEDIUM-LOW)

`orphograph_verify.js:157-159`. A JSON object with only `sha256` (no
`hash_hex`) verifies `ok: true` in verifier-js (confirmed by execution);
the engine treats a receipt without `hash_hex` as corrupt. The leniency
widens what counts as "an Orphograph receipt" in third-party hands. Spec
§3.3 forbids aliases.

### D6. Malformed stored hash: "corrupt receipt" vs "file does not match" — DIFFERENT ERROR (severity: LOW)

Engine: `hash_hex` non-hex/wrong length → `found: false, error: "corrupt
receipt"` (vectors v07, v08). verifier-js with `hash_hex: "zz…"` → `ok:
false` with the note "the file is not the one attested" (confirmed by
execution) — a misleading diagnosis: the receipt is broken, the file may be
fine. Cosmetic-to-different-error; note text worth fixing next SDK pass.

### D7. sdk-python `verify_inclusion` returns False on missing file; sdk-node throws — DIFFERENT ERROR (severity: LOW) — **FIXED 2026-07-15**

`sdk-python/orphograph/__init__.py:134-135` conflates "file missing" with
"not included" (silent `False`); sdk-node `verifyInclusion` rejects with the
stream error. Same verdict class, different failure surface for callers.

> **FIXED 2026-07-15** (branch `fix/verifier-minor-drifts`): aligned on the
> error surface — a missing local file is an I/O precondition failure, not a
> "not included" verdict; for a notary a distinguishable error beats a
> silent `False`. sdk-python `verify_inclusion` now raises
> `FileNotFoundError` (documented in its docstring); sdk-node keeps
> rejecting with the filesystem error (ENOENT) and its JSDoc now documents
> the contract. Malformed proof/root inputs still return `false` per spec
> §4.1. Regression tests added on both sides.

### D8. Engine `verify_hash_against_receipt` has zero callers — dead canon (severity: LOW, process risk)

`grep` finds no call site in `server/`, `tests/` (before this audit),
`web/`, or `mcp/`. The web verify page and SDKs re-implement the comparison
client-side. Untested canon is how drift goes unnoticed — the new vector
suite now exercises it directly (v02-v05, v09).

### Cosmetic / non-drift observations

- Merkle copies are in sync: `server/merkle.py` SHA-256
  `564dd480…` matches the banner in `sdk-python/orphograph/_merkle.py` and
  the reference comment in `sdk-node/src/merkle.ts`; a diff of the Python
  copy against the server file (banner stripped) is empty.
- `verify_cli.py` uses the same magic + offset-33 digest read as the engine;
  it additionally fails the run on SHA-512 sibling mismatch (stricter than
  engine's string check, matching verifier-js semantics; blessed in spec §3.4).
- Both SDKs compare folder roots as case-sensitive lowercase hex strings
  while their inclusion-proof hex decoding is case-insensitive — consistent
  with canon since the server emits lowercase everywhere.
- `.ots` digest offset (`len(magic) + 2`) assumes 1-byte version + 1-byte
  tag; true for every engine-built file, now pinned in spec §1.2.

## 3. Vector run results (executed 2026-07-12)

| Implementation | Vectors run | Result |
|---|---|---|
| engine (canon), via pytest | all 20 (+3 meta checks) | 23/23 pass |
| sdk-python `MerkleTree.verify_inclusion` | 7 merkle vectors | 7/7 agree with canon |
| sdk-node `MerkleTree.verifyInclusion` (dist, node 22 ESM) | 7 merkle vectors | 7/7 agree with canon |
| verifier-js `verifyReceiptAgainstFile` | 3 binding-comparable vectors + 2 probes | 2/3 agree; **v09 DRIFT (D1)**; alias + malformed-hash probes confirm D5/D6 |

Full suite: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
--ignore=tests/test_biweekly_safety_audit.py` — result recorded in the
commit message of branch `chore/verifier-spec-vectors`.

## 4. Recommended next pass (NOT done here — SDKs untouched by instruction)

1. verifier-js: compare stored hash verbatim (drop `.toLowerCase()` on the
   receipt side), drop alias fields, and emit a "receipt is malformed"
   note for non-hex `hash_hex` (fixes D1, D5, D6).
2. sdk-python: add `exclude` parameter to `verify_folder` (fixes D2).
3. sdk-node: remove the `receipt.hash_hex` fallback in `verifyFolder`
   (fixes D3); strict per-pair hex regex in `fromHex` (fixes D4).
4. Wire all three into the vector suite in CI (node runner mirroring
   `tests/test_verifier_vectors.py`) so conformance is continuous, and add
   a server route or test that actually exercises
   `verify_hash_against_receipt` in situ (D8).
