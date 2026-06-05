# orphograph (Python SDK)

Bitcoin-anchored file timestamping in two lines. The content is hashed **locally**
(SHA-256 + SHA-512); only the fingerprints are transmitted — the bytes never
leave your machine. Receipts verify against the public Bitcoin chain via
OpenTimestamps, independently of orphograph.com.

Stdlib only. No dependencies. Python 3.9+. MIT.

## Install

```bash
pip install orphograph          # once published
# or, from this repo:
pip install ./sdk
```

## Use

```python
import orphograph

# a file
r = orphograph.anchor_file("contract.pdf")
print(r.receipt_id, r.receipt_url, r.calendars_ok)

# bytes
orphograph.anchor_bytes(open("photo.raw", "rb").read(), label="shoot-2026-05")

# a string — e.g. an AI agent's output, a CI artifact, a transcript (no file)
orphograph.anchor_text(model_output, label="run-42")

# verify later (against the calendars + Bitcoin)
print(orphograph.verify(r.receipt_id))
```

A paid API key (from `orphograph.com/account.html`) raises rate limits and ties
anchors to your subscription — set `ORPHOGRAPH_API_KEY` or pass `api_key=...`.
Without one you get the free tier.

```python
from orphograph import Client
c = Client(api_key="…")            # reuse across many anchors
for p in paths:
    c.anchor_file(p)
```

## In CI (GitHub Actions)

```yaml
- run: pip install orphograph
- run: python -c "import orphograph,os; print(orphograph.anchor_file('dist/app.tar.gz', label=os.environ['GITHUB_SHA']).receipt_url)"
  env: { ORPHOGRAPH_API_KEY: ${{ secrets.ORPHOGRAPH_API_KEY }} }
```

## What it proves

That the exact bytes existed at or before a Bitcoin block. **Not** authorship,
human-vs-AI origin, or legal authenticity. Proof-of-existence-in-time, verifiable
by anyone — including with our service turned off.
