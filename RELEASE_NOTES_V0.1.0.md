# Orphograph v0.1.0 — MVP Launch

## What's New

**Orphograph is a Bitcoin-anchored file hashing service.** Drop any file in your browser, get a SHA-256 hash computed client-side (never uploaded), anchor it to Bitcoin via OpenTimestamps, and receive a receipt anyone can verify forever — even if we disappear.

### Core Features

- **Client-side hashing**: Your files stay on your machine. Only the 32-byte SHA-256 digest reaches our servers.
- **Bitcoin anchoring**: 5 independent OpenTimestamps calendars (a.pool, b.pool, alice, finney, btc.catallaxy) batch your hash into the Bitcoin blockchain within ~1 hour.
- **Verifiable receipts**: Each anchor produces a JSON receipt + 5 OTS proof files. Verify against Bitcoin's public chain using our open-source verifier or command-line tool — no login required.
- **Free tier**: 1 anchor per month, rate-limited. Free-tier receipts may be pruned after 30 days, but your local copy remains independently verifiable forever.
- **Pack tier** ($7 one-shot): 10 anchor credits. Credits never expire.
- **Personal tier** ($5/mo): Unlimited anchors, folder monitoring, API access.
- **Creator tier** ($19/mo): Orphograph Capture app (capture-time provenance), 100 anchors/mo, verifier badge, custom branding. [*Coming 2026-06*]

### Security & Compliance

All security and payment audits **PASSED**:

- ✅ **SECURITY.md**: Transport (HTTPS + HSTS), CSP (no third-party scripts), authentication (magic-link, token supersession), webhook verification (HMAC-SHA256), rate limiting (10/hour/IP-/24)
- ✅ **PAYMENT_PII_AUDIT.md**: 5 HIGH findings fixed — email masking in logs, HMAC-keyed email IDs, `__Host-` cookie prefix, magic-link token supersession, response-body email omission
- ✅ **GDPR compliance**: `/api/me/export` (full data export), `/api/me/delete` (account deletion), 30-day grace period for refunds
- ✅ **Privacy doctrine**: IP truncation to /24 (IPv4) or /48 (IPv6), no analytics, no third-party trackers, filename privacy by default

### Code Quality

- **262 tests passing** (payment flow, auth, privacy, security, concurrent access)
- **5,475 LOC** production-grade Python + vanilla JS
- **Zero dependencies** for the engine (`http.server`, `urllib`, `hashlib`, `json`, `secrets` only)
- **Dockerfile excludes runtime state** — data lives on persistent volumes only

### What You're Getting

1. **Working web app**: landing page, drop zone, receipt viewer, account dashboard
2. **Backend server**: Python stdlib, Stripe integration, OpenTimestamps fan-out, rate limiting, GDPR endpoints
3. **Frontend**: Vanilla HTML/CSS/JS with WebCrypto SHA-256 hashing
4. **Deployment**: Fly.io config, volume setup, health checks, daily backup to B2
5. **Docs**: security posture (SECURITY.md), payment findings (PAYMENT_PII_AUDIT.md), principles (CLAUDE.md), roadmap (MARKET_ROADMAP.md)

### What You're NOT Getting

- **Not court-admissible**: We don't claim legal evidence status. Bitcoin timestamps are proof-of-existence, not eIDAS-qualified or court-binding. If you need legal timestamps, consult a qualified trust-service provider.
- **Not file upload service**: Files stay in your browser. We see only the hash.
- **Not a blockchain storage service**: We don't store your files on-chain. The Bitcoin transaction contains only a Merkle root linking ~100 users' hashes.

## Known Limitations

- DNS not yet live (orphograph.com domain registered, DNS verification pending)
- Email delivery not yet tested end-to-end (Resend API configured, awaiting test)
- Free-tier beta test pending (3 users to validate UX before public launch)
- Creator Capture app deferred until Month 2 (build once first paying customers exist)

## Launch Readiness

**Status: CONDITIONAL GO**

✅ Passing:
- Code audits (SECURITY.md, PAYMENT_PII_AUDIT.md)
- Payment flow + Stripe integration
- Data persistence + file safety
- Privacy doctrine enforcement
- Frontend security (CSP, no third-party scripts)
- Deployment readiness (Docker, Fly)
- Test suite (262 tests)

⏳ Pending (founder action required):
1. Publish Privacy Policy + Terms (DONE ✅)
2. Verify orphograph.com DNS live + email delivery working
3. Run plagiarism check on marketing copy (DONE ✅)
4. Beta test with 3 friends

**Expected launch: 2026-05-18** (pending domain + email verification)

## How to Deploy

```bash
# 1. Set environment variables
export ORPHO_DATA_DIR=/mnt/volume  # or $HOME/.orphograph for local testing
export STRIPE_SECRET_KEY="sk_..."
export STRIPE_WEBHOOK_SECRET="whsec_..."
export RESEND_API_KEY="re_..."

# 2. Initialize volume
mkdir -p $ORPHO_DATA_DIR && chmod 700 $ORPHO_DATA_DIR

# 3. Run server
python3 server/app.py

# 4. Visit http://localhost:8000
```

## How to Verify a Receipt

**In-browser:** Visit `https://orphograph.com/verify`, upload your receipt JSON + OTS files.

**Command-line** (open-source, no server required):
```bash
python3 server/verify_cli.py receipt.json *.ots
```

**Verifier source:** `/web/verify/` (standalone JavaScript + Python reference impl)

## How to Use

1. **Drop a file** on the landing page
2. **Get a receipt** (JSON + 5 OTS files) — save it locally
3. **Verify anytime** with the browser tool or CLI
4. **Buy Pack credits** ($7 for 10 anchors) if you need more after 1 free anchor/month
5. **Subscribe to Personal** ($5/mo) for unlimited anchors + folder monitoring

## Architecture

- **Hashing**: WebCrypto `SubtleCrypto.digest('SHA-256', ...)` in the browser
- **Anchoring**: Python HTTP POST to OpenTimestamps calendars (proxied)
- **Storage**: Append-only JSONL ledgers (`credits.ledger`, `stripe_processed_events.jsonl`, etc.)
- **Concurrency**: `fcntl.flock()` multiprocess safety
- **Payments**: Stripe Subscriptions API + webhook handling
- **Email**: Resend transactional service
- **Hosting**: Fly.io with persistent volume

## Links

- **Homepage**: https://orphograph.com
- **Security Posture**: [SECURITY.md](deploy/SECURITY.md)
- **Payment Audit**: [PAYMENT_PII_AUDIT.md](deploy/PAYMENT_PII_AUDIT.md)
- **Project Principles**: [CLAUDE.md](CLAUDE.md)
- **Launch Readiness**: [LAUNCH_INDEX.md](LAUNCH_INDEX.md)
- **Open-Source Verifier**: [web/verify/](web/verify/)

## Support

**Email**: hello@orphograph.com  
**Privacy inquiries**: privacy@orphograph.com  
**Status page**: https://orphograph.com/status

---

**Orphograph v0.1.0** — Prove your art existed. Anchored to Bitcoin. Verifiable forever.
