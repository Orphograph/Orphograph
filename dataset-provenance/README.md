# Dataset Provenance — built on Orphograph

A pipeline-friendly CLI that turns a dataset *plus the documents that say
where it came from* into a single Bitcoin-anchored receipt and a one-page
**provenance certificate**.

It is the proof-of-concept for the dataset-provenance wedge: ML and data
teams increasingly have to answer "what exactly was in this training set,
and when?" — for audits, customer due-diligence, the EU AI Act's data-
governance expectations, and copyright/consent disputes. This proves the
*integrity and time* half of that question with cryptography instead of a
spreadsheet someone could edit after the fact.

## What it does

You hand it a **bundle**:

```
my-dataset/
  data/                  the dataset itself (any tree of files)
  licenses/              license / consent / terms documents (PDF, txt, …)
  acquisition_log.json   where each source came from, when, under what terms
```

It hashes every file locally, builds one Merkle tree over the whole bundle
(Orphograph's `orphograph-merkle-v1-rfc6962`, RFC 6962-style), anchors the
**root** to Bitcoin via Orphograph, and writes:

- `certificate.txt` — a one-page human-readable certificate
- `certificate.json` — the machine-verifiable version
- `manifest.json` — every leaf (path + SHA-256), for offline inclusion proofs

The dataset **never leaves your environment.** Only the manifest (relative
paths + digests + the root) is sent to be anchored. `--offline` sends
nothing at all.

## What it proves — and what it doesn't

**Proves:** this dataset, these licenses, and this acquisition log all
existed in this exact form by the anchored date; nothing has changed since
(any edit moves the root); each file is independently verifiable without
re-disclosing the rest.

**Does not prove:** that the data was lawfully sourced or owned, that the
acquisition log is truthful, or who authored anything. It is corroborating
evidence of *integrity and time* — not a license, not ownership. The
certificate states this in plain language. Provenance tooling that
overclaims is a liability, not a feature.

## Usage

```bash
# Anchor a bundle (manifest only leaves the machine)
python3 provenance.py anchor --bundle my-dataset --name "My Dataset v1"

# Air-gapped: build the receipt + certificate, anchor nothing
python3 provenance.py anchor --bundle my-dataset --name "My Dataset v1" --offline

# Re-verify any time: rebuild the root from disk, compare to the certificate
python3 provenance.py verify --cert out/certificate.json --bundle my-dataset

# No local certificate? Verify the bundle against the LIVE anchored receipt
# (only the receipt id crosses the network — the bundle never does)
python3 provenance.py verify --receipt <receipt-id> --bundle my-dataset

# Prove one file belongs to the certified set (Merkle inclusion proof)
python3 provenance.py verify --cert out/certificate.json --bundle my-dataset \
        --file data/images/cat_001.jpg
```

Exit code is `0` on VERIFIED, non-zero on failure — drop `verify` into CI as
a gate, or `anchor` as a post-build step in a training pipeline.

## How verification survives Orphograph

The receipt's hash **is** the Merkle root, anchored to Bitcoin through
OpenTimestamps. Two independent paths, neither of which trusts us:

1. **Time** — verify the OpenTimestamps proof of the root with the public
   MIT verifier at `orphograph.com/verify` (stdlib Python, no network, no
   account). If Orphograph disappears, the Bitcoin anchor still stands.
2. **Membership** — `manifest.json` + this tool (or any RFC-6962
   implementation) recompute any file's inclusion proof offline.

No proprietary format, no lock-in. That's the deal Orphograph already makes
for single files; this extends it to datasets.

## Worked example

`sample-dataset/` is a tiny 3-image/2-class set with two license docs and an
acquisition log. Running `anchor` on it produced live receipt
`29GWE7buoOxbLQVo` (root `a51e1409…eb891`, 8 leaves). The tamper test in the
repo notes — relabelling one image after certification — is **detected** as
a root mismatch, which is exactly the audit failure this is meant to catch.

## Hosted certificate view

Every folder receipt now has a **hosted, shareable certificate page** at
`orphograph.com/certificate/<receipt-id>`. `anchor` prints the URL, and the
certificate's `anchor.certificate_url` field records it. A permanent live
example (this repo's `sample-dataset`, Bitcoin-anchored) renders at
[`/certificate/DatasetProvenanceSample`](https://orphograph.com/certificate/DatasetProvenanceSample). The page renders the
same content as `certificate.txt` — summary, the certifies/does-not-certify
scope, license/consent documents, the acquisition log, and the full file
manifest — plus:

- an **in-browser inclusion verifier**: click any file (or type a path) and it
  fetches the Merkle proof and recomputes the root *in your browser*, proving
  that file belongs to the certified set with no trust in us;
- the OpenTimestamps proofs and third-party Bitcoin-explorer links for the root;
- **Save-as-PDF** (it's print-styled) and a copy-link/share row.

Privacy: for a public receipt viewed by a non-owner, file *paths* are withheld
(each file's fingerprint and the proofs still show); the receipt owner, signed
in, sees full paths and the categorised document lists. Folder receipts opened
at the generic `/r/<id>` link auto-redirect here.

## Implementation note

This reuses Orphograph's canonical `server/merkle.py` so manifests are
byte-identical to what `/api/anchor_folder` and the public verifier expect.
The CLI remains a PoC for the wedge (single-tree bundles). Remaining next
steps if the wedge validates: server-rendered PDF export and a pipeline
plugin (Airflow/Prefect/GH Actions).
