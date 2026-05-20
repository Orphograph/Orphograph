# orphograph

Anchor any file to the Bitcoin blockchain in about ten seconds.
For photographers, journalists, indie creators, and developers who need to
prove a file existed before a given moment — especially before an AI model
saw it. Files never leave the browser; only the 32-byte SHA-256 fingerprint
is submitted. Free tier (1 anchor/month), $7 Pack (10 anchors), $5/mo
Personal (unlimited), $19/mo Creator (capture-time app + API + verifier badge).

---

## Quick demo

Four steps a developer can run from a terminal, no signup, no install
beyond `curl` and `shasum`.

1. Compute the SHA-256 of any file on disk:

   ```bash
   shasum -a 256 ~/Pictures/my-photo.jpg
   # → 3f8c1b...  my-photo.jpg
   ```

2. Submit the hash to the public anchor endpoint:

   ```bash
   curl -sS -X POST https://orphograph.com/api/anchor \
        -H "Content-Type: application/json" \
        -d '{"hash_hex":"<paste the 64-char hex digest>"}'
   ```

   The response is a JSON receipt with a `receipt_id` and a list of the
   OpenTimestamps calendars that accepted the submission.

3. Pull the receipt back later by ID (or visit `/r/<id>`):

   ```bash
   curl -sS https://orphograph.com/api/verify/<receipt_id>
   ```

4. Verify offline against the original file with the standalone verifier
   (no orphograph code, MIT, ~100 lines of stdlib Python):

   ```bash
   # Single file with an inclusion proof:
   python3 dist/orphograph-verify/verify.py file \
       --file ~/Pictures/my-photo.jpg \
       --proof receipts/<receipt_id>/proof.json
   # Whole folder against its manifest:
   python3 dist/orphograph-verify/verify.py folder \
       --dir ~/Pictures/evidence \
       --manifest receipts/<receipt_id>/manifest.json
   ```

   The verifier re-hashes the file (or walks the folder) locally and
   confirms the result reproduces the manifest's root. Add `--ots
   <path>.ots` to also invoke the OpenTimestamps client and check the
   chain witness references the same root.

---

## Architecture

Python 3.11+ stdlib only on the server (`http.server`, `urllib`, `hashlib`,
`json`, `secrets`, `fcntl`) — zero pip dependencies in the anchor engine.
Vanilla HTML + CSS + JS on the client, hashing via WebCrypto
`SubtleCrypto.digest` so file bytes never leave the browser. Each anchor
fans out a single 32-byte POST to five independent OpenTimestamps
calendars (a.pool, b.pool, alice, finney, btc.catallaxy); the calendars
batch many users' hashes into a single Merkle root and write the root to
Bitcoin roughly hourly, which is why our marginal on-chain cost is
effectively zero. The `.ots` proofs are stored per-receipt and verify
against the public Bitcoin chain forever, with or without us. Bitcoin
custody is receive-only: a single watch-only address printed in
`btc_address.txt` accepts payments; no signing keys live on production
hosts.

---

## Why this exists

By spring 2026 a large share of new images circulating online are
AI-generated or AI-modified. Watermarks can be stripped, C2PA labels can
be re-signed, EXIF is one shell command away from being anything you
want. What survives that is cryptographic proof — a Bitcoin block that
already existed at a known time, with the hash of your file committed
inside it. Orphograph is the cheapest, most boring way to put a
fingerprint of your work into that block before anyone disputes it.
Long-form on the thesis: [/blog/written-by-an-ai](content/blog/written-by-an-ai.md).

---

## Privacy properties

What touches what, in one table.

| Datum | Stays on your machine | Sent to server | Submitted to OTS calendars | Notes |
|---|---|---|---|---|
| File bytes | yes | never | never | WebCrypto hashes locally |
| SHA-256 (32 bytes) | yes | yes | yes | The only thing on-chain |
| SHA-512 sibling (64 bytes) | yes | yes | no | Quantum hedge; stored in receipt only |
| Filename / label | yes | opt-in | no | Off by default; pass `--label` to include |
| Email address | yes | only when needed | no | Required only for Pack delivery + Personal subscriptions |
| IP address | n/a | truncated to /24 (IPv4) or /48 (IPv6) | no | Full IPs are never persisted |

---

## Repo structure

```
orphograph/
├── server/                       Python stdlib HTTP server + anchor engine
│   ├── engine.py                anchor + verify core (the file in this README)
│   ├── app.py                   ThreadingHTTPServer with /api/anchor /api/verify
│   ├── verify_cli.py            standalone receipt verifier (no engine imports)
│   ├── auth.py                  magic-link tokens + HttpOnly session cookies
│   ├── credits.py               append-only Pack claim-code ledger
│   ├── stripe_webhook.py        HMAC signature verify + idempotent handler
│   ├── subscriptions.py         Personal-tier state derived from Stripe
│   ├── mailer.py                Resend HTTP send (inert when key unset)
│   ├── rate_limit.py            persistent token bucket
│   ├── file_lock.py             fcntl.flock helper for ledger atomicity
│   ├── upgrade_worker.py        OTS upgrade fetcher (cron)
│   ├── expire_worker.py         free-tier receipt pruner (cron)
│   └── gdpr.py                  /api/me/export + /api/me/delete
│
├── web/                          vanilla HTML/CSS/JS — no bundler, no framework
│   ├── index.html, app.js       landing + drop zone + verify section
│   ├── account.html, account.js Personal-tier dashboard
│   ├── signin.html, signin.js   magic-link request form
│   ├── receipt.html, receipt.js print-friendly receipt at /r/<id>
│   ├── style.css                dark glassmorphism, neon-green accent
│   └── terms.html, privacy.html legal pages
│
├── content/blog/                 markdown blog posts (SEO + manifesto)
│   ├── written-by-an-ai.md
│   └── prove-photo-existed-before-ai.md
│
├── tests/                        pytest suite (~98 cases, all stdlib)
│   └── test_*.py                engine / verifier / credits / rate_limit /
│                                 auth / stripe / subscriptions / gdpr / ui
│
├── marketplace/orphograph-plugin/  Claude Code plugin
│   ├── README.md
│   └── skills/anchor, skills/verify
│
├── lightroom-plugin/             Adobe Lightroom Classic plugin (Lua)
│
├── capture/                      desktop capture daemon (Creator tier)
│
├── dist/orphograph-verify/       MIT standalone verifier (vendorable)
│
├── scripts/                      operational helpers
│   ├── smoke_test.sh            end-to-end live OTS calendar test
│   ├── dev_setup.sh             fresh-clone sanity check
│   ├── init_volume.sh           container entrypoint (Fly)
│   ├── upgrade_cron.sh          OTS upgrade scheduler
│   ├── expire_cron.sh           free-tier expiry scheduler
│   └── refund_pack.py           zero a claim code after a Stripe refund
│
└── deploy/                       deploy + ops docs
    ├── FLY_PREFLIGHT.md
    ├── EMAIL_AND_LEGAL_COMPLIANCE.md
    ├── PAYOUT.md
    ├── RUNBOOK.md
    └── launch_drafts/
```

---

## Local dev

```bash
git clone https://github.com/orphograph/orphograph ~/orphograph
cd ~/orphograph
bash scripts/dev_setup.sh           # offline checks + verifier roundtrip
python3 server/app.py               # serves http://127.0.0.1:8989
```

Run the test suite at any time:

```bash
python3 -m pytest tests/ -q
```

The smoke test hits live OTS calendars and requires network:

```bash
bash scripts/smoke_test.sh
```

---

## Production deploy

Single-container Fly.io with a mounted volume for `receipts/` and the
JSONL ledgers; Stripe webhook verification, Resend for transactional
email, CSP `default-src 'self'`. Step-by-step in
[`deploy/FLY_PREFLIGHT.md`](deploy/FLY_PREFLIGHT.md).

---

## Pricing

| Tier | Price | What's included |
|---|---|---|
| Free | $0 | 1 anchor per month per IP-prefix |
| Pack | $7 one-time | 10 anchors, claim code by email, never expires, skips rate limit |
| Personal | $5/mo or $60/yr | Unlimited anchors, email delivery, account dashboard, anchor history |
| Creator | $19/mo | Personal + Orphograph Capture (capture-time desktop app) + API access + verifier badge |

The `$7 / $5` price points sit deliberately below WordProof and OriginStamp.
The wedge is browser-based UX plus the open-source verifier — not the
cryptography, which is the public OpenTimestamps protocol.

---

## License

- `dist/orphograph-verify/` — **MIT**. Vendor it, ship it, audit it. This
  is the trust artifact: a receipt produced today must verify against
  Bitcoin five years from now even if orphograph.com no longer exists.
- `marketplace/orphograph-plugin/` — **MIT**.
- Everything else — private until launch decisions settle. The founder
  retains the option to open-source after six months of customer signal.

---

## On the guarantee

The privacy contract of Orphograph is **structural**, not promissory.
Files are hashed in the user's browser; only the SHA-256 (and SHA-512)
fingerprint is transmitted. The server cannot reconstruct, identify,
or retransmit the file, because it never receives the file. This
property holds independently of who maintains the code.
