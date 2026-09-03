# Orphograph GitHub Action — anchor release artifacts

Anchor your release artifacts on Bitcoin (via OpenTimestamps) from CI.
For each matched file the action computes SHA-256 and SHA-512 **on the
runner**, sends only the hashes to the Orphograph API, and records a
receipt. The artifact bytes never leave the runner.

## Usage

```yaml
name: Release
on:
  release:
    types: [published]

jobs:
  build-and-anchor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: make dist          # produce your artifacts in dist/

      - name: Anchor artifacts on Bitcoin
        uses: Orphograph/Orphograph/integrations/github-action@master
        with:
          paths: "dist/*"
          api_key: ${{ secrets.ORPHO_API_KEY }}

      - name: Upload receipts
        uses: actions/upload-artifact@v7
        with:
          name: orphograph-receipts
          path: orphograph-receipts.json
```

## Inputs

| Input | Required | Default | Description |
| --- | --- | --- | --- |
| `paths` | no | `dist/*` | Glob pattern(s) for files to anchor. Space-separated; `**` supported. |
| `api_key` | no | — | Orphograph API key (subscription). Lifts the free-tier limit. |
| `pack_token` | no | — | Orphograph pack token (prepaid credits). Alternative to `api_key`. |
| `base_url` | no | `https://orphograph.com` | API base URL. |
| `fail_on_error` | no | `false` | If `true`, fail the job when anchoring fails (rate limit, network, no matched files). Default is warn-and-continue so anchoring never blocks a release. |

## Outputs

| Output | Description |
| --- | --- |
| `receipts` | JSON array of `{file, sha256, receipt_id, receipt_url}`. |

The action also writes `orphograph-receipts.json` to the workspace and
appends a receipt table to the job's step summary.

## What a receipt proves — and what it does not

- **Proves:** these exact bytes existed no later than the anchoring
  time, attested by the Bitcoin blockchain through OpenTimestamps.
  Anyone can verify a receipt at `https://orphograph.com/r/<id>`
  independently of Orphograph.
- **Does not prove:** authorship, ownership, originality, or any legal
  claim. A timestamp is evidence of existence in time — nothing more.
  Useful for supply-chain integrity: later, anyone can check that a
  published artifact matches the bytes that were anchored at release.

## Rate limits and secrets

Without credentials the API allows **3 anchors per day per IP**.
GitHub-hosted runners share IP addresses with many other users, so the
free tier is frequently already exhausted — expect `429` responses.
For reliable CI anchoring, use an API key or pack token:

1. Add the key as a repository secret (e.g. `ORPHO_API_KEY`).
2. Pass it via `api_key: ${{ secrets.ORPHO_API_KEY }}`.

The action passes credentials to the script through environment
variables only (never command-line arguments), and never prints them.
On a `429` without credentials the action explains the limit and exits
successfully unless `fail_on_error: true`.
