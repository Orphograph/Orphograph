# Orphograph — canonical artifact lifecycle and verification states

Status: normative for the states and exit codes named here; descriptive for
everything else. `tests/test_lifecycle_contract.py` checks that the state
vocabulary and exit codes in this document are the ones the code actually
uses, and `tests/test_independent_verification_matrix.py` drives every row
of §4 through the shipped verifier. If either test goes red, fix the code
or this document — never the test.

This document does not add behaviour. It names, in one place, the lifecycle
that is spread across `server/engine.py`, `server/merkle.py`,
`dist/orphograph-verify/`, `mcp/orphograph_mcp.py`, `sdk-python/`,
`sdk-node/`, `integrations/watch-folder/`, `capture/`,
`integrations/github-action/`, `dataset-provenance/`, and `server/verify_cli.py`.

## 1. Lifecycle

| # | Stage | What exists after it | Where it is produced | Where it is read |
|---|---|---|---|---|
| 1 | **Capture** | bytes of an artifact (file, folder, text, agent output) and its SHA-256 (and, for files, SHA-512 sibling) | browser (`web/`), capture daemon (`capture/`), watch folder (`integrations/watch-folder/`), SDKs, MCP `orphograph_anchor_*`, GitHub Action | — |
| 2 | **Manifest** | for folders: RFC 6962 Merkle tree; `manifest.json` with `root_hex`, `leaves[]` (`path`, `file_sha256_hex`), and the `scope` block recording what was excluded (Wedge 01, `tests/test_manifest_scope.py`; the scope block sits beside the root and does not change it) | `server/merkle.py` (server) and the SDKs' Merkle modules write the scope block; the vendored `dist/orphograph-verify/merkle.py` recomputes roots only and does **not** read `scope` — the user passes the same `--exclude` flags the anchor used (see `verify.py --help`). Auto-applying `scope.excludes` in the dist verifier is a staged additive item (§6) | every folder verifier |
| 3 | **Receipt** | `receipt.json` (`receipt_id`, `hash_hex`, timestamps, optional `lineage`, optional `zk_provenance`, optional C2PA preservation) + one `.ots` per calendar | `server/engine.py` `anchor_*` | `/r/<id>`, `/api/receipt/<id>`, SDK `verify()`, MCP `verify_receipt` |
| 4 | **Verification (structural)** | per-`.ots` checks `{magic_ok, hash_match, ok}`; `found`; `supplied_matches_receipt` | `server/engine.py:verify_receipt`, `verify_hash_against_receipt` | web/SDK/MCP |
| 5 | **Preservation** | the receipt directory is immutable ("the books", `docs/LOG_RETENTION_POLICY.md` §2.1); `.ots` proofs are **upgraded** in place as calendars confirm (`ots upgrade`); C2PA manifests carried alongside, never rewritten | `server/`, renewal path `docs/DESIGN_RENEWAL_PATH.md` | — |
| 6 | **Edit / derivative lineage** | a child receipt whose `lineage.parent_root` names the parent; chain walked by `dist/orphograph-verify/verify_lineage.py` and MCP `verify_lineage` (`max_depth` 1–256, `broken_at`, `depth_capped`) | `server/` lineage endpoint (`tests/test_lineage_endpoint.py`, `tests/test_edit_lineage.py`) | lineage verifiers |
| 7 | **Revocation / invalidation** | **Not a lifecycle state.** A receipt is never revoked: the claim "hash H existed before block N" cannot be un-made (`docs/QUANTUM_EXPOSURE_AUDIT.md`). What exists instead: (a) **expiry** of the hosted copy by retention policy (`server/expire_worker.py`); (b) **API-key revocation** (`server/api_keys.py`); (c) an acceptance-layer flag set `{issuer_profile, issuer_trusted, revoked, disputed}` (`server/acceptance_hook.py`) that a relying party's policy may consult — it is not a verifier verdict | — | acceptance hook |
| 8 | **Export** | the receipt bundle (receipt.json + .ots files + manifest/proof) and the dispute bundle (`tests/test_dispute_bundle_contents.py`) leave the service | `/r/<id>` download, SDK, MCP `list_vault`, dataset-provenance bundles | the holder |
| 9 | **Independent verification** | a verdict produced **without** Orphograph: `dist/orphograph-verify/verify.py` (Merkle/file) + `otscheck.py` (chain, via the user's own `ots` client) + `verify_lineage.py` + `verify_hw.py`/`verify_zk.py`/`verify_snark.py`/`verify_renewal.py`; browser twin `verifier-js/`; structural receipt checker `web/verify/verify.py` | shipped zip/tarball (`scripts/build_verifier_dist.py`) | anyone |
| 10 | **Invalid state** | the artifact or receipt does not reproduce the committed value — `[FAIL]`, exit `3`; or the chain client **rejected** the proof — `[OTS] FAILED`, exit `4` | verifier | the holder / relying party |
| 11 | **Indeterminate state** | no verdict could be rendered — `[OTS] UNAVAILABLE` (no client / no node), `PENDING` (not yet on Bitcoin), `UNBOUND` (this `.ots` is about another hash), `INDETERMINATE` (client exit 0 but no recognised wording) — exit `4`, with the Merkle/file result still printed and standing | verifier | the holder / relying party |

## 2. State vocabulary (normative)

The chain-check states are defined once, in `dist/orphograph-verify/otscheck.py`,
and are exactly:

```states
VERIFIED PENDING FAILED UNAVAILABLE UNBOUND INDETERMINATE
```

Only `VERIFIED` is a pass (`otscheck.PASSING`). Every other state is a
non-pass that must be shown with its name; none of them may be reported as a
pass, and `UNAVAILABLE`/`PENDING`/`UNBOUND`/`INDETERMINATE` must never be
reported as "the proof is bad".

`dist/orphograph-verify/verify.py` exit codes (normative):

```exits
0 OK
2 invalid arguments / missing or unparseable inputs
3 hash recomputation failed (file, proof, or folder did not reproduce the committed value)
4 chain step did not pass (stdout names FAILED / PENDING / UNAVAILABLE / UNBOUND / INDETERMINATE)
```

`dist/orphograph-verify/verify_lineage.py` exit codes (normative; constants in the file):

```lineage_exits
0 EXIT_OK chain intact
2 EXIT_ARGS invalid arguments / unreadable inputs
3 EXIT_LINK a link does not commit to its parent (manifest root ≠ receipt hash) — the tamper case
4 EXIT_OTS the chain step for a link did not pass (only with --ots-check; PENDING on a fresh chain is expected)
5 EXIT_BROKEN chain broken (missing receipt / unreachable parent)
```

A relying party must treat **2, 3, 4, 5** as "not intact"; `3` is the forged-link
case and is not `5`. A fork is reported informationally, not as broken.

Exit `4` is shared by "rejected" and "no verdict" on purpose (changing it
would break every script that already branches on `4`); the discriminator is
the `[OTS] <STATE>:` line. A relying party that reads only the exit code
cannot tell invalid from indeterminate and must read that line.

## 3. Semantics per interface (descriptive — where each reports what)

| Interface | Verify entry | Result shape | Tri-state? |
|---|---|---|---|
| Web service | `server/engine.py` `verify_receipt(receipt_id)` | `{receipt_id, found, checks:[{file, magic_ok, hash_match, ok}]}`; `found:false` + `error` for missing/corrupt (`docs/VERIFIER_SPEC.md` §5) | no — booleans + `found` |
| Web service | `verify_hash_against_receipt` | `supplied_matches_receipt: bool` (never raises) | no |
| Python SDK (master) | `orphograph.verify_folder(...)` → `bool`; `verify_inclusion(...)` → `bool`; `_client.get_verify_folder(...)` → server dict (`sdk-python/orphograph/{__init__,_client}.py`) | booleans; CLI exit 1 on mismatch. (A hash-only `verify()`/`get_receipt()` surface is pending on another branch and is not described here until it merges.) | no |
| Node SDK | `verifyFolder`, `verifyInclusion` | booleans | no |
| MCP | `orphograph_verify_receipt` → `{ok, receipt_id, anchored_hash_sha256, anchored_hash_sha512, calendars_ok, calendars_total, note, …}` (`mcp/orphograph_mcp.py:415-440`); `orphograph_verify_lineage` → `{ok, tip, chain, depth, broken_at, forks_seen, note}` plus `depth_capped: true` **only when** the walk was capped (`:626-635`) | `ok` booleans with counts; `ok` for lineage is false when any link fails, the chain is broken, or the depth was capped | no |
| Independent verifier | `dist/orphograph-verify/verify.py` | exit code + `[OK]`/`[FAIL]` + `[OTS] <STATE>:` | **yes** (the only tri-state surface) |
| Structural receipt checker | `web/verify/verify.py` | exit codes documented in `web/verify/README.md`; explicitly no chain check | no (structural only) |

Consequence (documented, not fixed here): web, SDK, and MCP collapse the
chain question into booleans/counts (`calendars_ok/total`); only the
independent verifier distinguishes "rejected" from "could not run". Callers
that need that distinction must use the independent verifier or the
`[OTS]` line. Promoting the tri-state vocabulary into the MCP/SDK responses
is an additive change (new optional field) — staged follow-up §6.1.

## 4. What independent verification covers (each row = one test)

| Case | Input | Verifier result | Row in `tests/test_independent_verification_matrix.py` |
|---|---|---|---|
| Valid original (file) | untouched bytes + its inclusion proof | exit 0, `[OK]` | `test_valid_original_file` |
| Valid original (folder) | untouched folder + its manifest | exit 0 | `test_valid_original_folder` |
| Altered artifact | one byte changed | exit 3, `[FAIL]`, no `[OK]` | `test_altered_artifact_is_invalid_exit_3` |
| Altered receipt — root | `root_hex` replaced | exit 3 "did not reproduce root" | `test_altered_receipt_root_hex_is_invalid_exit_3` |
| Altered receipt — proof step | sibling hash replaced | exit 3 | `test_altered_receipt_proof_step_is_invalid_exit_3` |
| Altered manifest | `root_hex` replaced | exit 3 "does not match manifest" | `test_altered_manifest_root_is_invalid_exit_3` |
| Missing component | proof file / artifact / `.ots` file absent; required field absent; corrupt JSON | exit 2 (input problem, **not** a verdict — no `[FAIL]`) | `test_missing_*`, `test_corrupt_proof_json_*` |
| Unsupported / unknown version | extra unknown fields (`format_version: 99.0`) | exit 0 — **there is no format-version gate**; required fields decide; a future format that drops them is exit 2 | `test_unknown_extra_fields_are_ignored_forward_compatible` |
| Malformed proof structure | step direction not `L`/`R` | exit 3 "malformed proof step" | `test_malformed_proof_step_shape_is_invalid_exit_3` |
| Incorrect identity — wrong file | a proof for A applied to B | exit 3 "does not match proof's file_sha256_hex" | `test_proof_applied_to_a_different_file_is_invalid_exit_3` |
| Incorrect identity — wrong `.ots` | `.ots` committing to another hash | exit 4, `UNBOUND`; Merkle `[OK]` still printed | `test_ots_about_a_different_hash_is_unbound_exit_4` |
| Derivative artifact | edited copy vs the original's proof | exit 3 (lineage is `verify_lineage.py`, a separate claim) | `test_derivative_copy_is_invalid_for_the_original_receipt` |
| Metadata loss | same bytes, new name and mtime | exit 0 — verification is over bytes only | `test_metadata_loss_same_bytes_still_verify` |
| Unavailable service — no client | `--ots` with no `ots` on PATH | exit 4, `UNAVAILABLE`, "did NOT run", Merkle `[OK]` printed, no `FAILED` | `test_no_ots_binary_is_unavailable_not_failed` |
| Unavailable service — no node | client says it could not connect | exit 4, `UNAVAILABLE`, no `FAILED` | `test_unreachable_node_is_unavailable_not_failed` |
| Pending | client says pending | exit 4, `PENDING`, no `VERIFIED` | `test_pending_is_reported_pending_and_is_not_a_pass` |
| Invalid chain | client rejected it | exit 4, `FAILED`, no `UNAVAILABLE` | `test_client_rejection_is_failed_not_unavailable` |
| Confirmed | client confirmed a Bitcoin block | exit 0, `VERIFIED` | `test_client_confirmation_is_verified_exit_0` |
| Invalid vs indeterminate | the two above, side by side | same exit 4; stdout tokens never co-occur | `test_invalid_and_indeterminate_share_exit_4_but_differ_on_stdout` |

Expired or revoked key: **not applicable to `verify.py`** — it checks no
signature and no key. Two neighbours in the same bundle do touch keys:
`verify_hw.py` verifies an ECDSA P-256 signature against the device public
key embedded in a hardware receipt and performs **no revocation or expiry
check** (a revoked device key still verifies — stated limitation), and the
server's Ed25519 manifest signature (`server/manifest_signature.py`) is
verified server-side and in `verifier-js/orphograph_signature.js` but not by
the dist bundle. Both are staged follow-ups (§6.2, §6.3).

## 5. What verification proves — and does not (restates `docs/VERIFIER_SPEC.md` §0)

Proves, when every check passes: these exact bytes (or this exact folder
under the recorded scope) hashed to the committed value, and — only with
`[OTS] VERIFIED` — an OpenTimestamps attestation for that value exists in a
Bitcoin block the user's own client accepted.

Does **not** prove: who made the artifact (no identity assurance), that it is
original or unedited relative to anything outside the receipt (no authorship,
no AI-detection), what it depicts, that it was made when it claims (only that
the hash existed **before** the block), who held it in between (no custody
history unless a lineage/custody receipt exists and is separately verified),
that any process or law was followed (no process compliance), or that any
court will accept it (no legal admissibility). Technical integrity, identity
assurance, custody history, process compliance, and legal admissibility are
five different claims; this product supplies the first, can carry evidence
toward the third, and makes no claim on the other three.

## 6. Staged follow-ups named by this document (additive; none built yet)

1. **Tri-state in MCP/SDK responses** — add an optional `chain_state` field
   carrying the §2 vocabulary next to the existing `ok`/`calendars_ok` counts.
2. **`verify_hw.py` key status** — document/implement a device-key revocation
   or expiry input; until then the limitation in §4 stands.
3. **Manifest-signature check in the dist bundle** — optional Ed25519 check
   mirroring `verifier-js/orphograph_signature.js`.
4. **Dist verifier reads `scope.excludes`** — so a folder anchored with
   custom excludes verifies without retyping the flags (§1 row 2).
5. **Lineage + hw + zk + renewal verifiers in the §4 matrix** — today the
   matrix drives `verify.py` only; their exit codes are pinned by their own
   tests (`tests/test_edit_lineage.py`, `tests/test_verify_hw.py`, …).

The external run report that tracks these across cycles lives outside the
repo (`~/full-cycle-reports/2026-08-22_claude_codex_coexistence/ORPHOGRAPH_PRODUCTIZATION.md`);
this section is the in-repo source of truth.
