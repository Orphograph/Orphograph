# Recipient test: software release artifacts

## Static handoff specimen

- Release tag/commit: `<REPLACE_WITH_TAG_AND_COMMIT>`
- Artifact inventory: `<REPLACE_WITH_EXACT_FILES>`
- `orphograph-receipts.json`: `<ATTACH_FROM_ACTION_OUTPUT>`
- Job summary/run URL: `<ATTACH>`
- Published-channel evidence: `<ATTACH_REGISTRY_OR_RELEASE_RECORD_SEPARATELY>`
- Confirmed OpenTimestamps proof for each claimed artifact: `<ATTACH>`

This specimen contains placeholders and does not establish a release.

## Exact proof statement

For each row backed by a successfully verified receipt/proof, recomputing the
artifact digest can prove that those exact bytes existed no later than the
attesting Bitcoin block. The release action creates individual receipts for
matched files; it does not create one atomic receipt for the release as a whole.

## Exact non-proof statement

The receipts do **not** prove authorship, source-to-binary correspondence,
reproducibility, signature identity, safety, quality, approval, publication,
availability, deployment, or that the listed files comprise the complete
release. With the default `fail_on_error: false`, a release job may continue
after unmatched files, rate limits, network failures, or other anchor errors.

## Acceptance interview script

1. Compare the intended artifact inventory with successful receipt rows. Fail
   if any required artifact is missing or only appears in an error message.
2. Recompute every required artifact SHA-256 and compare it with its row.
3. Verify the retained timestamp proof and state the Bitcoin block-time bound.
4. Ask: “Do these receipts prove the artifacts were published?” Required: “No;
   publication needs separate release or registry evidence.”
5. Ask: “Is the release atomically committed?” Required: “No; this action
   anchors matched files individually.”
6. Inspect workflow policy. Require `fail_on_error: true` or a separate gate
   that rejects missing/failed receipts if anchoring is release-critical.
7. Ask whether credentials appearing in environment variables prove who
   released the artifacts. Required answer: “No; API credentials authorize the
   request but the receipt does not establish release identity or authority.”

Acceptance requires complete row coverage, digest/proof verification, all four
scope answers, and separate publication evidence when publication is claimed.
