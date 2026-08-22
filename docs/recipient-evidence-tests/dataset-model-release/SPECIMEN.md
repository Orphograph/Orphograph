# Recipient test: dataset and model release

## Static handoff specimen

- Dataset bundle name: `<REPLACE_WITH_DATASET_NAME>`
- Model artifact/version: `<REPLACE_WITH_MODEL_ARTIFACT_AND_VERSION>`
- `certificate.json`: `<ATTACH_FROM_CLI_OUTPUT>`
- `manifest.json`: `<ATTACH_FROM_CLI_OUTPUT>`
- Dataset bundle for local recomputation, or selected file plus inclusion proof: `<ATTACH>`
- OpenTimestamps receipt/proof: `<ATTACH_CONFIRMED_PROOF>`
- Release approval/deployment records: `<ATTACH_SEPARATELY_IF_CLAIMED>`

This specimen contains placeholders and is unanchored.

## Exact proof statement

For an anchored certificate whose retained proof verifies, a matching
path-bound Merkle root proves that the supplied bundle/manifest commitment
existed no later than the attesting Bitcoin block. Recomputing the root detects
file, membership, or relative-path changes. A valid inclusion proof establishes
membership of the disclosed file/path without requiring the other file bytes.

## Exact non-proof statement

The receipt does **not** prove lawful sourcing, ownership, consent, license
validity, truth or completeness of the acquisition log, authorship, uninterrupted
custody, dataset quality, that training consumed the bundle, that the named model
resulted from it, or that either dataset or model was reviewed, approved,
released, or deployed. An offline certificate with `anchor.status: unanchored`
proves no Bitcoin time bound.

## Acceptance interview script

1. Have the recipient inspect `anchor.status`; require `anchored` and a retained,
   verifiable proof before any Bitcoin-time claim.
2. Have the recipient rebuild the root from the supplied bundle, or verify one
   disclosed file/path with its inclusion proof. Record pass/fail and tool version.
3. Ask: “Does this prove the model trained on this dataset?” Required: “No.”
4. Ask: “Does the presence of license documents establish legal permission?”
   Required: “No; only those document bytes and paths are committed.”
5. Ask: “What separate evidence supports release?” Require approval, registry,
   deployment, or publication evidence; the receipt alone is insufficient.
6. Ask which metadata left the environment. Require recognition that online
   folder anchoring submits literal paths, digests, sizes, and the root.

Acceptance requires correct answers to all six prompts and a successful
cryptographic check. Store the interview record outside this static template.
