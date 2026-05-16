# Project: orphograph — Bitcoin-Anchored File Hashing Service

> **Brand etymology:** `Orphic` (of the Orphic mysteries — hidden, permanent,
> initiated knowledge; Orpheus moved stones with his song) + `-graph`
> (writer, scribe). "The Orphic writer." A device that turns moments into
> permanent records. Coined compound, not a borrowed deity name.

## What this is
A pre-launch web application that hashes files **client-side** in the browser
and anchors the resulting SHA-256 hash to the Bitcoin blockchain via
OpenTimestamps, producing a verifiable receipt that anyone can validate without
trusting us. Solo-founder bootstrapped.

## Current status
- Pre-launch, no paying customers, no audience
- Working MVP on disk (Python stdlib server + vanilla web frontend)
- Smoke-tested end-to-end: 5/5 OTS calendars succeeded
- No payments wired yet
- Brand: Orphograph. Domain pending registration.
- Pricing direction (subject to validation): 1 file free, $7 one-shot for 10
  receipts, $5/mo unlimited personal, $19/mo Creator tier (capture-time
  provenance desktop app — see "Creator tier" section below)

## Stack (actual, not aspirational)
- **Backend:** Python 3.11+ stdlib only (`http.server`, `urllib`, `hashlib`,
  `json`, `secrets`). Zero pip dependencies for the engine.
- **Frontend:** Vanilla HTML + CSS + JS. WebCrypto `SubtleCrypto` for hashing.
  No bundler, no framework.
- **Hosting (planned):** Fly.io single container or Cloudflare Workers + tiny
  VPS for the calendar HTTP fan-out.
- **Payments (planned):** Stripe + BTCPay Server (or NOWPayments). Not yet wired.
- **Domain / DNS (planned):** TBD.

## Architecture (as-built)
- **Hashing:** Client-side, SHA-256 via `crypto.subtle.digest` in the browser.
  The file's bytes never leave the user's machine.
- **Anchoring:** Server submits the 32-byte hash to 5 OpenTimestamps calendars
  (a.pool, b.pool, alice, finney, btc.catallaxy). Calendars batch many users'
  hashes into a Merkle root and write the root to a Bitcoin tx (~hourly).
  Our per-receipt marginal cost on-chain is effectively $0.
- **Receipt format:** JSON (`receipt.json`) + 5 binary `.ots` proof files,
  one per calendar. Branded PDF receipts planned, not built.
- **Verification:** Two paths. (a) In-app via `GET /api/verify/<id>`.
  (b) Standalone `server/verify_cli.py` — no engine.py imports, vendorable
  as the public open-source verifier.
- **Payments:** Not implemented.
- **Auth:** None. Receipt ID is bearer-token-like; users save their receipt
  JSON locally.

## Repo structure
```
orphograph/
├── server/
│   ├── engine.py        # anchor + verify core, stdlib only
│   ├── app.py           # http.server with /api/anchor /api/verify /api/health
│   └── verify_cli.py    # standalone receipt verifier (open-source-ready)
├── web/
│   ├── index.html       # landing + drop zone + verify section
│   ├── style.css        # dark glassmorphism, neon-green accent
│   └── app.js           # WebCrypto SHA-256, fetch /api/anchor
├── receipts/            # one dir per receipt: receipt.json + 5 .ots files
├── ledger.jsonl         # append-only registry
├── docs/
│   ├── audits/          # /audit, /landing, /pricing, /economics outputs
│   ├── decisions/       # architecture decision records
│   └── weeks/           # /launch-week outputs
├── scripts/             # one-shot utility scripts
├── tests/               # pytest tests (none yet)
└── .claude/
    ├── commands/        # /audit /landing /pricing /economics /launch-week /kill-check
    └── settings.local.json
```

## Non-negotiable principles
1. **Files NEVER touch the server.** Hashing must be client-side via
   WebCrypto. If a code change would upload file bytes, refuse it.
2. **Anchoring must stay batched / free.** OpenTimestamps calendars batch
   our hashes into their own Bitcoin transactions. We do **not** broadcast
   per-file BTC txs. Marginal cost per receipt: <$0.01 (essentially free).
   If a proposal would have us paying per-receipt on-chain fees, reject it.
3. **Receipts must verify without us.** If our domain dies in 5 years, a
   user with the original file + `receipt.json` + the standalone
   `verify_cli.py` must still be able to validate against the public
   Bitcoin chain. No proprietary verifier-only formats.
4. ~~**No feature creep before 10 paying customers** for the current feature set.~~
   **OVERRIDDEN 2026-05-13** by founder authorization: building Y3-band
   deferred items (Creator API, verifier badge, referral, subscription
   cancel, history search, folder watcher) in advance of customer signal.
   Founder accepts the risk that some of these features may not match what
   customers actually want when they arrive — the trade is reduced
   time-to-feature-completeness at the cost of validation precision.
5. **Honest copy only.** No "court-admissible," "legally binding," or
   "notarized" claims. We sell proof-of-existence, not legal evidence.
6. **Clean-rewrite engine.** No imports from external projects. The anchor
   engine here is an independent implementation of the public OTS protocol.
   No shared commit history with prior infra work.

## Security Compromises Now Closed
These were fixed after a full security pass on 2026-05-15. Treat them as
project constraints when adding features:

- **No runtime state in images.** Docker builds must never include local
  `receipts/`, root `ledger.jsonl*`, `data/`, `logs/`, or other JSONL ledgers.
  Production state lives on `ORPHO_DATA_DIR` only. The checked-in sample
  receipt under `web/sample/` is the only seed artifact allowed.
- **Do not trust client proxy headers by default.** Rate limits use the socket
  peer unless `ORPHO_TRUST_PROXY_HEADERS=1` is set by a trusted deployment
  layer. Local tunnels and direct dev servers must keep it off.
- **Public health is passive.** `/api/health` must not make live network calls
  to calendars, price oracles, or third-party APIs unless
  `ORPHO_HEALTH_ACTIVE_PROBES=1` is intentionally set for a private/deep probe.
- **Stripe webhooks fail closed.** If `STRIPE_WEBHOOK_SECRET` is unset,
  production returns 503. The unsigned probe escape hatch
  `ORPHO_ALLOW_UNSIGNED_WEBHOOK_PROBE=1` is dev-only and must not be set on Fly.
- **Runtime data is private by default.** `data/`, ledgers, receipt JSON, and
  `.ots` files should be created with owner-only permissions (`0700` dirs,
  `0600` files). New append-only ledgers should use `file_lock.locked()` or
  explicitly chmod after write.
- **Founder credentials do not belong in normal customer flows.** Existing
  `localStorage` founder-token support is tolerated for launch ops, but new
  founder/admin features should prefer short-lived, HttpOnly server-side
  sessions or stay off the public app surface.

## Buyer hypothesis (current best guess)
- **Primary:** Photographers / illustrators worried about AI training
  scraping their work. Want cheap proof-of-pre-AI-era creation.
- **Secondary:** Indie musicians, freelance designers proving delivery dates.
- **Tertiary:** Crypto-curious general users (low LTV, impulse buys).

This is a HYPOTHESIS. Customer interviews are pending.

## Competitive landscape
- **OpenTimestamps:** Free. Same underlying protocol we use. Biggest
  competitor. Differentiation must be UX, custodial convenience, receipt
  presentation, payment rails — not the cryptography.
- **OriginStamp:** 12yo, pivoted to B2B Swiss enterprise May 2025.
- **WordProof:** WordPress-only, €10–€40/mo.
- **Bernstein.io:** B2B IP / law firm, EU+China qualified timestamps.
- **Proof of Existence (poex.io):** Abandoned, SSL expired 2021.
- **Stampery, Po.et:** Failed / pivoted / dead.

The graveyard is loud. Anything we build must answer: why won't we end
up on this list?

## Pricing roadmap (subject to validation)
- Free: 1 file/month forever
- $7 one-shot: 10 receipts (impulse tier)
- $5/month: unlimited personal + folder monitoring
- $19/month: Creator plan — Orphograph Capture desktop app (capture-time
  provenance) + API + 100 receipts/mo + verifier badge + custom branding
- Future B2B tier: $99–$299/mo (team, white-label)

### Creator tier: Orphograph Capture (capture-time provenance)
The $19 Creator plan is anchored by **Orphograph Capture**, a desktop/mobile
companion app that hashes and anchors content at the moment of capture
(shutter press, screen capture, screen recording, audio recording) — not
after-the-fact upload. The thesis: photographers and creators in AI-dispute
contexts ("prove this is real / pre-AI / mine") need provenance that begins
*at capture*, not at upload.

Status: planned, NOT yet built. Do not advertise on the public landing until
at least a beta exists. Soft-launch from the email list to existing customers
when ready.

**Architecture:** Orphograph Capture is a clean rewrite of capture-time
provenance. It must use Orphograph's own `server/engine.py` OTS submission
flow — no external project imports — to keep this product line fully
self-contained.

Pricing rationale: $19 sits above consumer backup ($8/mo Backblaze) but
below photographer portfolio SaaS ($45/mo SmugMug Pro). The capture-time app
is the feature that justifies the premium gap above Personal ($5).

## Realistic revenue targets
- Month 3: $50–$400 MRR
- Month 6: $200–$700 MRR
- Month 12: $500–$2,000 MRR
- 12-month ARR run-rate: $600 conservative → $24,000 optimistic
- 70% of micro-SaaS earn under $500/mo; this product is in that distribution.

If month-6 MRR is below $200, this becomes a side project (≤5 hrs/week)
and primary time shifts to the AI-agent retainer business
(see `project_ai_services_agency.md` in founder's memory).

## What this is NOT
- Not legal evidence software (no qualified TSA)
- Not a notary replacement
- Not a court-admissibility product
- Not a competitor to enterprise compliance platforms
- Not a token / ICO / token-gated anything

## How Claude Code should behave on this project
- Be direct. Don't pad. Don't over-explain.
- When asked for a feature, first check it against the non-negotiable
  principles above and call out conflicts.
- Write production-quality Python and JS — no placeholder TODOs unless
  explicitly asked for a sketch.
- Before declaring work done, run: `python3 -m py_compile server/*.py`
  and execute the smoke test in `scripts/smoke_test.sh` (when it exists).
- Prefer editing existing files over creating new ones.
- Never commit secrets. Use `.env.local`; reference `.env.example`.
- When uncertain, ask one focused question. Don't guess.
- Push back on bad ideas plainly.

## Slash commands
See `.claude/commands/`:
- `/audit` — full 12-section forensic audit
- `/landing` — landing page copy + conversion audit
- `/pricing` — pricing model analysis
- `/economics` — unit economics check
- `/launch-week` — generate weekly launch tasks
- `/kill-check` — apply kill-criteria thresholds to current MRR

## Open questions tracked
1. Register orphograph.com (brand picked 2026-05-11).
2. ~~Define the $19 tier concretely.~~ Resolved 2026-05-12: Creator tier =
   Orphograph Capture (capture-time provenance app). Build deferred until
   Free + Pack + Personal hit first paying customers. See "Creator tier"
   section above for architectural firewall vs ShutterProof.
3. Decide Stripe-only vs Stripe + BTCPay vs Stripe + NOWPayments for crypto.
4. Decide on Lightning Network for sub-$5 crypto.
5. Choose deploy target (Fly.io vs Cloudflare Worker + VPS vs single VPS).
6. Confirm beachhead persona and rewrite landing copy for that one.
7. PDF receipt template — design + library choice (`weasyprint` vs server-side
   HTML-to-PDF vs client-side jsPDF).

## Key files
- `server/engine.py` — anchor + verify core
- `server/app.py` — HTTP routes
- `server/verify_cli.py` — standalone verifier (the open-source trust artifact)
- `web/index.html` — landing + UI (audit target for `/landing`)
- `web/app.js` — client-side hashing
- `docs/audits/` — most recent audits
- `docs/decisions/` — architecture decision records
