# DESIGN: Verifiable Edit-Lineage (Merkle-Linked Version Chains)

Status: DESIGN ONLY — no implementation in this cycle.
Scope: additive. No existing endpoint, receipt field, manifest field, or verifier
behavior changes meaning. Everything below is a new, optional layer.

Language discipline (binding on all copy derived from this doc): this feature is
**tamper-evident provenance of anchor-time ordering**. It is not authorship proof,
not AI detection, and no claim in this document or in product copy may use
stronger framing than "tamper-evident" / "independently checkable".

---

## 0. Grounding — what exists today (verified against the code)

All statements in this doc are grounded in these files as read on 2026-08-02:

| Component | File | Facts used |
|---|---|---|
| Single-hash anchor | `server/engine.py` | `anchor_hash(hash_hex, client_label=None, sha512_hex=None, source="free", private=False, owner_id=None, attestation=None, metadata=None, c2pa_manifest_hash=None)` submits the 32-byte digest to 5 OTS calendars in parallel, writes `receipts/<rid>/receipt.json` + one `.ots` per successful calendar, appends to `ledger.jsonl`. Receipt record fields: `receipt_id, created_at, hash_hex, sha512_hex, client_label, source, private, owner_id, attestation, c2pa_manifest_hash, metadata, calendars_ok, calendars_total, successes, failures` (engine.py lines 181–217). |
| Receipt verify | `server/engine.py` | `verify_receipt(receipt_id)` checks each `.ots` file's magic header and that bytes at `len(OTS_HEADER_MAGIC)+2 .. +34` equal the receipt's `hash_hex`. Folder receipts additionally surface `kind`, `leaf_count`, `merkle_algorithm`, `paths_public`, `signature_verified`, `signer_kid` when present (engine.py lines 323–343). |
| Merkle tree | `server/merkle.py` | RFC 6962 style. Leaf = `SHA-256(0x00 || rel_path_utf8 || 0x00 || file_sha256)` (`_leaf_hash`), internal = `SHA-256(0x01 || left || right)` (`_internal_hash`), lone-last node PROMOTED (no CVE-2012-2459 duplication). `ALGORITHM = "orphograph-merkle-v1-rfc6962"`, `VERSION = 1`. `MerkleTree.from_manifest` re-derives every leaf from `(path, file_sha256_hex)` and refuses a manifest whose recomputed root ≠ `root_hex`. Manifest shape: `{algorithm, version, root_hex, leaves:[{path, file_sha256_hex, leaf_hex, size_bytes}]}` (`MerkleTree.manifest()`). |
| Folder anchor endpoint | `server/app.py` `_handle_anchor_folder` (line 3282) | Body: `{manifest, client_label?, private?, paths_public?}` or the raw manifest (disambiguated by the `algorithm` tag, line 3343). Validates `leaves` non-empty and ≤ `MAX_FOLDER_LEAVES` (50,000; body ≤ `MAX_FOLDER_MANIFEST_BYTES` 8 MiB, app.py lines 97–98). Rebuilds the tree via `merkle.MerkleTree.from_manifest`, anchors `tree.root_hex()` through `engine.anchor_hash`, persists the manifest to `RECEIPTS_DIR/<rid>/manifest.json` with `receipt_id` and `kind:"folder"` injected, then rewrites `receipt.json` adding `kind`, `leaf_count`, `merkle_algorithm` (lines 3417–3450). |
| Manifest signature | `server/manifest_signature.py` | Optional Ed25519 `signature` block, top-level sibling of `root_hex`; signature is over `canonical_manifest_bytes`, which strips the server-added `receipt_id` and `kind` before recomputing (module docstring lines 20–27; `verify_manifest_signature`). Enforced in `_handle_anchor_folder` lines 3365–3386: absent = fine, present-but-invalid = 400. |
| Offline verifier | `dist/orphograph-verify/verify.py` | Two subcommands: `file` (inclusion proof → root) and `folder` (re-walk local dir via vendored `merkle.py`, compare recomputed root to manifest `root_hex` VERBATIM — no "helpful" lowercasing, per docs/VERIFIER_SPEC.md §4.2 / AUDIT_VERIFIER_DRIFT D1). Optional `--ots` invokes the local `ots` binary (shell=False) as a chain sub-check. Exit codes 0/2/3/4. |
| MCP server | `mcp/orphograph_mcp.py` | Tools: `orphograph_anchor_file`, `orphograph_anchor_folder` (`tool_anchor_folder`, line 301 — builds the manifest locally in `_build_folder_manifest`, posts `{manifest, client_label?}` to `/api/anchor_folder`), `orphograph_anchor_output`, `orphograph_verify_receipt` (`tool_verify_receipt`, line 389 — GET `/api/verify/<rid>`), `orphograph_list_vault`. The MCP re-implements the same leaf/internal/promotion rules locally (`_merkle_root` line 237, `_build_folder_manifest` line 250). |
| BTC pin status | `server/upgrade_worker.py` | Receipts gain `status` ("pending"→"partial"/"pinned") and `btc_pinned_at` when a calendar returns a Bitcoin block attestation (lines 252–254). |
| Export bundle | `server/receipt_export.py` | `export_zip` packages `receipt.json` + `*.ots` only (lines 31–53). |

One load-bearing observation from `engine.anchor_hash`: **only the 32 bytes of
`hash_hex` are submitted to the calendars** (`_submit` posts `hash_bytes`). Fields
that live in `receipt.json` — `attestation`, `c2pa_manifest_hash`, `metadata` —
are recorded, not hash-committed to Bitcoin. Any lineage design that puts the
parent link only in `receipt.json` inherits that weaker property. The strong
property requires the parent commitment to be **inside the anchored 32 bytes**,
i.e. inside the Merkle root. This drives the recommendation in §1.

---

## 1. Chain structure

### Goal

For a sequence of drafts D1, D2, …, Dn, produce receipts R1, R2, …, Rn such that
anyone holding the receipts + manifests + `.ots` files can check, fully offline:

- each draft's bytes match its anchored root (existing property), and
- Rk+1's anchored root **cryptographically commits to** Rk's anchored root,

so that "the root of draft N was already fixed when draft N+1 was anchored" is
checkable without contacting Orphograph.

### Options considered

**Option A — parent root as a typed leaf inside the RFC-6962 manifest.**
Exploit the fact that `merkle._leaf_hash(rel_path, file_digest)` takes any
32-byte digest. Add one reserved-path leaf whose `file_sha256_hex` is the
**parent receipt's anchored root** (`hash_hex` of the parent receipt):

```
{ "path": ".orphograph/parent",
  "file_sha256_hex": "<parent root_hex>",
  "leaf_hex": "<sha256(0x00 || '.orphograph/parent' || 0x00 || parent_root)>",
  "size_bytes": 0 }
```

`MerkleTree.from_manifest` validates this leaf **unchanged** — it already
re-derives `leaf_hex` from `(path, file_sha256_hex)` and folds it into the root.
No change to `server/merkle.py`, no new algorithm tag. The parent root is now
inside the anchored 32 bytes: forging the link after the fact requires a SHA-256
second preimage.

- Pro: the link is hash-committed to Bitcoin via OTS; offline-checkable from the
  manifest alone; zero changes to the tree code or the wire `algorithm` tag.
- Con: `verify.py folder` mode recomputes the root by walking the **disk**
  (`MerkleTree.from_folder`), and no `.orphograph/parent` file exists on disk, so
  folder-mode recomputation needs either (a) a verifier flag that injects the
  synthetic leaf, or (b) the client writing a real sidecar file into the draft
  folder before anchoring. (a) is non-invasive and is what §3 specifies.
- Con: paths sort by UTF-8 byte order (`_walk_folder` line 144); the reserved
  leaf must be inserted at its correct sorted position, and a real user file
  named `.orphograph/parent` would collide (see §7 Q2).

**Option B — `parent_root` / `parent_receipt_id` fields on the receipt record.**
Additive kwargs on `engine.anchor_hash`, echoed into `receipt.json` and the
ledger.

- Pro: trivially additive; works for single-hash anchors too; gives the server
  and MCP a cheap way to *discover* the chain (walk `parent_receipt_id`).
- Con: **not hash-committed.** `receipt.json` is written by the server after
  anchoring; only `hash_hex` went to the calendars. A receipt field alone is
  server-attested bookkeeping, tamper-evident only against the server's own
  ledger — it does not survive the "prove it offline to a stranger" test.

**Option C — both (RECOMMENDED).**
The typed leaf (Option A) is the *proof*; the receipt fields (Option B) are the
*index*. The offline verifier treats `parent_receipt_id`/`parent_root` in
`receipt.json` as untrusted hints and **requires** the manifest's reserved leaf
to match — a receipt whose hint disagrees with the committed leaf FAILS.

### Why C

- A leaf-only design makes chain walking awkward (you'd grep manifests for which
  receipt has that root); a field-only design is cryptographically hollow. The
  pair keeps discovery cheap and proof strong, mirroring the existing pattern
  where `receipt.json` gets convenience mirrors (`kind`, `leaf_count`,
  `merkle_algorithm` — app.py lines 3437–3448) of facts whose real authority is
  the manifest + `.ots`.
- It reuses the folder-anchor path exactly as constrained by doctrine
  (`/api/anchor_folder` requires a `{leaves}` manifest): **every lineage anchor
  is a folder anchor**, even for a single-file draft — a single-file draft
  becomes a 2-leaf tree (content leaf + parent leaf). Genesis drafts (no parent)
  are plain v1 anchors, unchanged.

### ASCII — chain of three drafts

```
 D1 (genesis)                D2                              D3
 ┌─────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐
 │ leaves:     │   │ leaves:                  │   │ leaves:                  │
 │  draft.md   │   │  .orphograph/parent ─────┼─┐ │  .orphograph/parent ─────┼─┐
 │             │   │     = root1              │ │ │     = root2              │ │
 │             │   │  draft.md (v2 bytes)     │ │ │  draft.md (v3 bytes)     │ │
 ├─────────────┤   ├──────────────────────────┤ │ ├──────────────────────────┤ │
 │ root1       │◄──┼── committed inside root2 │◄┼─┼── committed inside root3 │ │
 └──────┬──────┘   └────────────┬─────────────┘ │ └────────────┬─────────────┘ │
        │                       │               │              │               │
     5x .ots                 5x .ots     root1 is a leaf    5x .ots     root2 is a leaf
        │                       │        input of root2        │        input of root3
        ▼                       ▼                              ▼
   OTS calendars ──────────► Bitcoin block attestations (upgrade_worker.py)
```

```
 Inside one lineage anchor (D2), RFC 6962 as implemented in server/merkle.py:

        root2 = SHA256(0x01 || L_parent || L_content)          (sorted order may
              ▲                                                 differ — leaves
   ┌──────────┴───────────┐                                     sort by UTF-8
   │                      │                                     byte order of
 L_parent             L_content                                 path)
 = SHA256(0x00        = SHA256(0x00
   || ".orphograph/     || "draft.md"
      parent"           || 0x00
   || 0x00              || sha256(D2 bytes))
   || root1)
```

The arrow of time is the arrow of commitment: root2 cannot be computed without
root1, so root1's bytes were fixed **no later than** the moment root2 was
anchored. The `.ots`/Bitcoin attestation on root2 then upper-bounds that moment.

---

## 2. API surface (additive)

### 2.1 Manifest delta (client → `/api/anchor_folder`)

Two additive elements, both optional; a manifest with neither anchors exactly as
today:

```jsonc
{
  "algorithm": "orphograph-merkle-v1-rfc6962",   // unchanged tag — see §7 Q1
  "version": 1,
  "root_hex": "…",
  "leaves": [
    { "path": ".orphograph/parent",              // NEW reserved typed leaf
      "file_sha256_hex": "<parent receipt's hash_hex (root or file hash)>",
      "leaf_hex": "<derived exactly per merkle._leaf_hash>",
      "size_bytes": 0 },
    { "path": "draft.md", "file_sha256_hex": "…", "leaf_hex": "…", "size_bytes": 8412 }
  ],
  "parent": {                                    // NEW top-level hint block
    "receipt_id": "XwTULwlh76PcCst9",
    "root_hex": "<must equal the reserved leaf's file_sha256_hex>"
  }
}
```

Server-side validation added in `_handle_anchor_folder` (all additive):

1. If a leaf with `path == ".orphograph/parent"` exists, a top-level `parent`
   block MUST exist and `parent.root_hex` MUST equal that leaf's
   `file_sha256_hex`; else 400. And vice versa.
2. `parent.receipt_id` is validated against the receipt-id alphabet (same rule
   as `tool_verify_receipt` line 394: alnum + `_-`, ≤ 64). If the receipt exists
   locally, the server checks `parent_record["hash_hex"] == parent.root_hex`;
   mismatch = 400. If it does not exist locally (e.g. anchored elsewhere or
   pruned), the anchor still proceeds — the commitment is self-contained — and
   the response carries `"parent_receipt_found": false`.
3. Everything else (`from_manifest` root check, signature check, size caps)
   is untouched. Note the reserved leaf counts toward `MAX_FOLDER_LEAVES`.

Ed25519 interaction: `canonical_manifest_bytes` strips only the server-added
`receipt_id` and `kind` (manifest_signature.py docstring lines 20–27). The
`parent` block and reserved leaf are **client-supplied before signing**, so a
signed lineage manifest verifies with zero signature-code changes. The server
must NOT inject or rewrite `parent` post-signature.

### 2.2 `engine.anchor_hash` delta

One additive keyword, mirroring how `c2pa_manifest_hash` was added:

```python
def anchor_hash(
    hash_hex: str,
    ...,
    c2pa_manifest_hash: str | None = None,
    parent_root: str | None = None,        # NEW — 64 lowercase hex, validated via _is_hex
    parent_receipt_id: str | None = None,  # NEW — id-alphabet validated
) -> dict:
```

These are recorded in the receipt as hints only (see §2.3). `anchor_hash` does
not and cannot make them binding for a bare single-hash anchor — the doc-level
rule is: **binding lineage goes through the manifest path**; the bare
`/api/anchor` path may record hints but the verifier reports such links as
`recorded_only`, never `committed`.

### 2.3 Receipt JSON delta (exact)

Only present when supplied — single-file receipts remain shape-stable, matching
the existing pattern for `kind`/`leaf_count` (engine.py lines 323–331):

```jsonc
{
  "receipt_id": "aB3xY9…",
  "created_at": "2026-08-02T17:00:00+00:00",
  "hash_hex": "<root3>",
  // … all existing fields unchanged …
  "kind": "folder",
  "leaf_count": 2,
  "merkle_algorithm": "orphograph-merkle-v1-rfc6962",

  "lineage": {                               // NEW block, absent unless lineage anchor
    "parent_receipt_id": "XwTULwlh76PcCst9",
    "parent_root": "<root2>",
    "committed": true                        // true iff the reserved leaf is in the manifest
  }
}
```

`verify_receipt` gains a symmetric passthrough (same style as lines 326–336):

```python
if record.get("lineage"):
    out["lineage"] = record["lineage"]
```

### 2.4 Response delta for `/api/anchor_folder`

```jsonc
{ "receipt_id": "…", "root_hex": "…", "leaf_count": 2, "kind": "folder",
  "merkle_algorithm": "orphograph-merkle-v1-rfc6962",
  "lineage": { "parent_receipt_id": "…", "parent_root": "…", "committed": true,
               "parent_receipt_found": true } }   // NEW, only when parent supplied
```

---

## 3. Offline "preceded" verifier

New subcommand in `dist/orphograph-verify/verify.py` (vendored `merkle.py`
untouched):

```
verify.py lineage --chain CHAIN_DIR [--dir DRAFT_DIR ...] [--ots-check]
```

`CHAIN_DIR` holds one subdirectory per link (the export bundle contents):
`<rid>/receipt.json`, `<rid>/manifest.json`, `<rid>/*.ots`. No server contact.

### Algorithm

```
1. Load all receipt.json files; index by receipt_id and by hash_hex (root).
2. Pick the tip: the receipt the caller names, or the unique receipt whose
   root appears in no other manifest's reserved leaf.
3. Walk downward. For each link R_child:
   a. STRUCT   — parse manifest.json; require algorithm/version supported.
   b. ROOT     — MerkleTree.from_manifest(manifest); ValueError → FAIL(link).
                 (This already recomputes every leaf and the root — the same
                  check the server runs at anchor time.)
   c. BIND     — manifest.root_hex == receipt.hash_hex (VERBATIM string
                 compare, per the D1 rule in verify.py lines 212–223).
   d. PARENT   — find leaf path ".orphograph/parent". If absent → this is the
                 genesis link; stop after step f.
                 Let P = leaf.file_sha256_hex.
                 If receipt.lineage present, lineage.parent_root MUST equal P
                 (hint vs commitment consistency); mismatch → FAIL(link).
   e. LOOKUP   — find the receipt with hash_hex == P in CHAIN_DIR.
                 Missing → report chain BROKEN at this link (see §4.4).
   f. OTS      — check each *.ots: starts with OTS_HEADER_MAGIC and the 32
                 bytes at offset len(magic)+2 equal receipt.hash_hex (the
                 exact check engine.verify_receipt performs, lines 292–305).
                 With --ots-check, additionally run the local `ots` binary
                 per _ots_subcheck (subprocess, shell=False) for the
                 Bitcoin-attestation sub-check.
   g. CONTENT  — optional, per --dir: recompute the draft folder root via
                 MerkleTree.from_folder and confirm it matches after
                 injecting the synthetic parent leaf:
                     leaves_disk + [_leaf_hash(".orphograph/parent", bytes.fromhex(P))]
                     → sort by UTF-8 byte order of path → _build_levels → root
                 (pure re-use of merkle.py primitives; no disk sidecar needed).
4. Emit the chain: R1 → R2 → … → Rn with per-link status, plus per-link
   created_at / btc_pinned_at / status fields read from each receipt
   (informational — see "what this does NOT prove").
Exit codes: 0 all links OK; 3 a link failed recomputation/binding;
            4 OTS sub-check failed; 5 chain broken (missing parent);
            2 bad arguments — consistent with verify.py's existing codes.
```

### What a green chain establishes

- Each draft's manifest internally consistent and bound to its receipt's root.
- Each child root **cryptographically commits to** its parent root: computing
  root(N+1) requires root(N) as a preimage input. Reversing or reordering the
  chain after the fact requires breaking SHA-256.
- Each root carries OTS attestations; when Bitcoin-pinned (`btc_pinned_at`,
  upgrade_worker.py lines 252–254), each root's existence has an
  independently checkable upper time bound. Together: **anchor-time ordering**
  — draft N's root was fixed no later than draft N+1's anchor.

### What it does NOT establish (must appear in verifier output and all copy)

- NOT that draft N+1 is a revision, derivative, or continuation of draft N's
  *content*. The link binds roots, not semantics — anyone can anchor an
  unrelated blob with someone else's root as parent (see §4.3).
- NOT authorship, and NOT who held the drafts between anchors.
- NOT that no other versions existed between N and N+1 (unanchored drafts are
  invisible), and NOT exclusivity — nothing prevents parallel children (§4.3).
- NOT wall-clock creation time of the content — only anchor time. `created_at`
  is a server-written string inside receipt.json; the independently checkable
  bound is the Bitcoin attestation reached through the `.ots` files, and full
  chain verification is the `ots` client's / a Bitcoin node's job, exactly as
  scoped in verify.py's module docstring (lines 25–31).

---

## 4. Tamper cases

| # | Case | Detected? | By which check |
|---|------|-----------|----------------|
| 4.1 | Intermediate draft's bytes altered after anchoring (holder swaps `draft.md` content in link k) | **Detected** | Step g (CONTENT): recomputed folder root ≠ `root_hex` — same failure mode `verify.py folder` produces today (exit 3). If instead the *manifest* is edited to match the new bytes, step b/c fails: either `from_manifest` raises (leaf ≠ derived leaf / root ≠ leaves, merkle.py lines 253–269) or the rebound `root_hex` no longer matches `receipt.hash_hex` / the 32 bytes embedded in the `.ots` files (step f). The attacker would need a fresh anchor — which carries a later Bitcoin attestation, and whose root no longer equals the value committed in link k+1's reserved leaf (step d/e). |
| 4.2 | Reordered chain — presenting N+1 as if it preceded N, or rewriting parent pointers to reverse the order | **Detected** | Commitment direction: root(N+1) contains root(N) as a leaf preimage; root(N) contains no reference to root(N+1). Fabricating the reverse link means finding content for "earlier" N whose root both matches its already-anchored `.ots` bytes and commits to root(N+1) — a second-preimage problem. Editing `lineage.parent_*` hints alone is caught by step d (hint ≠ committed leaf). Independent corroboration: monotonically non-decreasing Bitcoin attestation heights along the chain (`--ots-check`); a "parent" pinned in a later block than its child is flagged. |
| 4.3 | Forked lineage — two children C1, C2 both committing the same parent root | **Allowed by design; not detected from a single chain; detected as a fork only when both branches are presented.** | There is no global uniqueness registry and none is proposed — a "no other child exists" claim is unprovable offline and would be false advertising. Branching is legitimate (translations, alternate edits — cf. Wedge 03's EN/ES pair). The verifier, when CHAIN_DIR contains multiple children of one parent, reports `FORK at <root>` listing all children — informational, not a failure. Server side, a ledger scan (`ledger.jsonl`) *could* enumerate known children of a root, but that only covers anchors this office saw. Product copy must say: lineage shows *a* chain, not *the only* chain. |
| 4.4 | Missing intermediate — holder presents R1 and R3 but not R2 | **Detected as a gap; cannot be silently spliced.** | Step e: R3's reserved leaf commits to root2, and no presented receipt has `hash_hex == root2` → chain BROKEN at R3 (exit 5). The holder cannot bridge R3 directly to R1 because the committed 32 bytes are root2, not root1, and forging a manifest for R3 that commits to root1 changes root3 → `.ots` binding fails (step f). What remains checkable: R1 and R3 each verify individually; R3 provably commits to *some* root that is not presented. The verifier reports exactly that, no more. |

Additional case worth naming — **wrong-parent grafting** (anchoring a new draft
whose reserved leaf commits to *someone else's* public root): not detectable
cryptographically, by construction — the commitment is public data. This is the
§3 "NOT a derivative-work claim" limitation restated; the receipt's
`attestation` block (engine.py `_sanitize_attestation`) remains the place for
the human claim, which is recorded, not proven.

---

## 5. MCP / CLI exposure sketch

### MCP tool (additive to `mcp/orphograph_mcp.py`, alongside `tool_verify_receipt`)

```jsonc
{
  "name": "orphograph_verify_lineage",
  "description": "Walk the parent links of a receipt's edit lineage and verify each
    link. Reports anchor-time ordering only — it does not assess content
    similarity, authorship, or whether siblings exist.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "receipt_id": { "type": "string", "description": "tip receipt id" },
      "max_depth":  { "type": "integer", "default": 32 }
    },
    "required": ["receipt_id"]
  }
}
```

Result shape:

```jsonc
{
  "ok": true,
  "tip": "aB3xY9…",
  "chain": [
    { "receipt_id": "XwTULwlh76PcCst9", "root_hex": "…", "created_at": "…",
      "btc_pinned_at": "…", "status": "pinned", "link": "genesis",  "checks_ok": true },
    { "receipt_id": "aB3xY9…",          "root_hex": "…", "created_at": "…",
      "btc_pinned_at": null, "status": "pending", "link": "committed", "checks_ok": true }
  ],
  "depth": 2, "broken_at": null, "forks_seen": [],
  "note": "Verified anchor-time ordering of these anchors. This does not establish
           that any draft is a derivative of another, nor that no other versions exist."
}
```

Implementation note: the MCP walks server-side via repeated
`GET /api/verify_folder/<rid>` (which already returns `{receipt, manifest}`,
app.py `_handle_verify_folder` line 3468) and re-runs the §3 checks locally
using its existing `_merkle_root`/leaf code — the MCP never trusts the server's
`lineage` hints without recomputing the leaf commitment. Anchoring side:
`tool_anchor_folder`/`tool_anchor_file` gain an optional `parent_receipt_id`
argument; the MCP fetches the parent root, injects the reserved leaf into
`_build_folder_manifest`'s output, and adds the `parent` block before POSTing.

### CLI

- Offline: `python3 verify.py lineage --chain ./bundle/` (§3).
- New server-side export to make that usable: lineage export must include
  `manifest.json` — today `receipt_export.export_zip` packages only
  `receipt.json` + `*.ots` (receipt_export.py lines 31–53). A
  `export_lineage_zip(tip_rid)` that walks `parent_receipt_id` and bundles
  every link's `receipt.json` + `manifest.json` + `*.ots` is part of the
  implementation cycle.

---

## 6. Test plan (implementation cycle)

Unit — manifest/leaf layer (extend `tests/` alongside existing merkle tests):

1. `test_parent_leaf_round_trip` — 2-leaf lineage manifest (content + reserved
   leaf) passes `MerkleTree.from_manifest` unchanged; root matches an
   independently hand-computed value (add to `docs/test-vectors/`, generated by
   `docs/test-vectors/generate.py`, as a new `lineage.json` vector).
2. `test_parent_leaf_sort_position` — reserved leaf sorts by UTF-8 byte order
   with sibling paths (before and after paths like `.a`, `draft.md`, `zzz`).
3. `test_anchor_hash_parent_kwargs` — `parent_root` validated via `_is_hex(…,64)`;
   bad hex → ValueError; absent → receipt has no `lineage` key (shape-stable).

Endpoint — `_handle_anchor_folder`:

4. `test_lineage_anchor_happy_path` — manifest with reserved leaf + matching
   `parent` block → 200, receipt.json contains `lineage.committed == true`,
   manifest persisted with the leaf intact.
5. `test_reserved_leaf_without_parent_block_400` and the converse.
6. `test_parent_block_root_mismatch_400` — `parent.root_hex` ≠ reserved leaf's
   `file_sha256_hex`.
7. `test_parent_receipt_unknown_still_anchors` — response carries
   `parent_receipt_found: false`.
8. `test_parent_receipt_known_but_root_wrong_400` — local parent exists,
   `hash_hex` mismatch.
9. `test_signed_lineage_manifest_verifies` — Ed25519 signature computed over a
   manifest that already includes leaf + `parent` block passes
   `verify_manifest_signature`; server injects nothing new into signed bytes.
10. `test_non_lineage_folder_anchor_unchanged` — byte-for-byte identical
    behavior for a manifest with no lineage elements (regression guard).

Offline verifier — `dist/orphograph-verify`:

11. `test_lineage_chain_of_three_ok` — exit 0; ordering reported.
12. `test_tampered_intermediate_content` — mutate one file → exit 3 at that link
    (case 4.1).
13. `test_tampered_manifest_leaf` — edit a leaf's `file_sha256_hex` → exit 3
    (from_manifest raises).
14. `test_hint_vs_commitment_mismatch` — edit `receipt.lineage.parent_root`
    only → FAIL at step d.
15. `test_missing_intermediate` — remove R2's bundle → exit 5, `broken_at` = R3
    (case 4.4).
16. `test_fork_reported_not_failed` — two children of one parent in CHAIN_DIR →
    exit 0 with `FORK` note (case 4.3).
17. `test_reordered_chain_rejected` — swap parent pointers in hints; committed
    leaves win; with `--ots-check`, non-monotonic attestation ordering flagged
    (case 4.2; the OTS half is best-effort per the `ots`-binary caveat).
18. `test_verbatim_root_compare` — uppercase root in a lineage manifest fails
    with the canonical-form message (D1 rule parity with verify.py lines
    212–223).
19. `test_content_recheck_with_synthetic_leaf` — `--dir` recomputation injects
    the synthetic parent leaf and matches root (step g).

MCP:

20. `test_tool_verify_lineage_shape` — result fields exactly as §5; `note`
    disclaimer string present.
21. `test_tool_anchor_folder_with_parent` — reserved leaf + `parent` block
    present in the POSTed manifest.

Export:

22. `test_export_lineage_zip_contains_manifests` — every link ships
    `receipt.json` + `manifest.json` + `.ots` files.

Mutation-style checks (per the existing harness habit): flip each verifier step
(b–g) off one at a time and confirm at least one test fails — no dead checks.

---

## 7. OPEN QUESTIONS

- **Q1 — Algorithm tag vs reserved leaf.** The reserved leaf rides inside
  `"orphograph-merkle-v1-rfc6962"` without a tag bump (nothing about the tree
  math changes). Is that acceptable, or should lineage manifests carry a
  distinct tag (e.g. `…-v1-rfc6962+lineage`) so pre-lineage verifiers refuse
  rather than silently treat the parent leaf as an ordinary file? Trade-off:
  old verifiers currently WOULD accept a lineage manifest and verify its root
  correctly (the leaf is just a leaf) — arguably a feature (graceful
  degradation), arguably a confusion risk (a stale verifier reports "2 files"
  for a 1-file draft).
- **Q2 — Reserved-path collision.** A user could have a real file at
  `.orphograph/parent`. Options: (a) server rejects lineage manifests where the
  reserved path's `size_bytes != 0`; (b) pick a path no `os.walk` result can
  produce. I could not identify a path shape that `_walk_folder`/
  `_build_folder_manifest` provably never emit (relative POSIX paths are almost
  unconstrained), so (a) plus a documented reservation of the `.orphograph/`
  prefix is the working assumption — needs a decision.
- **Q3 — Parent of a single-file (non-folder) receipt.** When draft N was
  anchored via plain `/api/anchor` (no manifest), its receipt's `hash_hex` is a
  file hash, not a Merkle root. The reserved leaf mechanism commits to that
  value identically (any 32 bytes), but the offline walker's step g has nothing
  to recompute for that link beyond `verify_cli.py`-style file re-hash. Confirm
  this "mixed chain" is in scope for v1 or whether genesis links must also be
  folder anchors.
- **Q4 — Free-tier expiry breaks chains.** `server/expire_worker.py` prunes
  free-tier receipts ("The original receipt JSON + the 5 .ots files leaves with
  the user", expire_worker.py line 7). A pruned intermediate is exactly case
  4.4 unless the user kept their export. Should lineage anchors be excluded
  from free-tier expiry, or is "export your bundle" (plus the §5 lineage zip)
  the answer? Pricing/positioning call, not a crypto one.
- **Q5 — `/api/verify_folder` path redaction vs lineage walking.** That
  endpoint redacts leaf paths for non-owners unless `paths_public`
  (app.py lines 3502–3529 region). The reserved leaf's *path* is what
  identifies it. Does redaction exempt the reserved leaf (it carries no user
  data beyond the parent root, which is already in the child's committed
  manifest), or does server-side lineage walking become owner-only? Needs a
  privacy-doctrine decision.
- **Q6 — Fork policy surface.** §4.3 allows forks. Should the hosted
  certificate page display known sibling children (server ledger scan), or is
  surfacing "other children exist" itself an information leak about other
  customers' receipts? Default: do not display; revisit with privacy doctrine.
- **Q7 — `sha512_hex` sibling witness for lineage.** Single-hash anchors carry
  an optional SHA-512 sibling witness (engine.py lines 109–114). The Merkle
  layer is SHA-256-only, so lineage links inherit SHA-256-only strength. Is a
  dual-hash lineage leaf (second reserved leaf committing a SHA-512 parent
  witness) worth the complexity now, or deferred to a v2 algorithm tag?
- **Q8 — Ledger mirror.** Should `lineage` also be appended to `ledger.jsonl`
  rows (it will be, automatically, if passed through `anchor_hash`'s record —
  confirm the weekly folder-Merkle receipt job (PR #126 lineage) tolerates the
  extra key)? I did not read that job's code this cycle — unconfirmed.
- **Q9 — MCP verify depth/abuse cap.** `max_depth` default 32 is a guess.
  Chains are user-constructed; a 50k-deep chain walk hitting
  `/api/verify_folder` per link is a request-amplification vector. Cap and
  rate-limit interaction with `_anchor_limiter`-style machinery to be decided
  at implementation time.
