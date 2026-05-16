# Vendor security questionnaire — Orphograph

Pre-canned answers to the questions B2B procurement reliably asks.
Use during vendor review without making the founder write fresh
prose at midnight.

**Last updated:** 2026-05-12.

---

## A. Company + product overview

**A.1 Legal entity.** Hydroboro Basic Industries LLC, a Wyoming
limited liability company. Trade name **Orphograph** for the
consumer-facing service.

**A.2 Headquarters.** USA (legal venue: Commonwealth of Puerto Rico per Terms of Service).

**A.3 Number of employees with access to Personal Data.** One (the
founder). No shared accounts.

**A.4 Service description.** Browser-based file-timestamping service
anchoring SHA-256 hashes to the Bitcoin blockchain via OpenTimestamps.

## B. Authentication + access control

**B.1 How are user accounts authenticated?** Magic-link email
sign-in. Tokens are 24-byte URL-safe random (`secrets.token_urlsafe(24)`,
192 bits), one-time use, 24h TTL, stored on-disk only as SHA-256
hashes. No passwords are stored.

**B.2 How are administrator accounts authenticated?** Fly.io account
MFA (founder), Stripe account MFA, Resend account MFA, registrar
MFA. No shared admin accounts.

**B.3 How are session cookies hardened?** `__Host-` prefix (enforces
same-host + Path=/ + Secure), `HttpOnly`, `SameSite=Lax`. Session
IDs are 24-byte random, stored as SHA-256 hashes; revocation via
append-only ledger.

**B.4 Password complexity requirements.** N/A — no passwords.

**B.5 MFA enforcement.** Operator-side: yes (on Fly, Stripe, Resend).
Customer-side: not yet (magic-link is the auth model; adding MFA on
top is roadmapped).

## C. Encryption

**C.1 Encryption in transit.** TLS 1.2+ via Fly's edge for all
public traffic. HTTPS forced (`force_https = true` in `fly.toml`).
HSTS `max-age=31536000; includeSubDomains` set on every response.

**C.2 Encryption at rest.** Persistent volume encrypted by Fly.io
provider. Secrets are stored only via `fly secrets set` (encrypted
at rest by Fly). The HMAC secret for email IDs is generated as
`secrets.token_bytes(32)` at first boot and stored with 0600 mode.

**C.3 Encryption of backups.** Fly volume snapshots inherit volume
encryption. Manual local backups (`scripts/backup_volume.sh`)
gpg-encrypt to the founder's public key before writing.

**C.4 Cryptographic algorithms.** SHA-256 (Bitcoin anchor + receipt
ID seed + token hashing), SHA-512 (sibling quantum-hedge witness),
HMAC-SHA256 (Stripe webhook signature, per-installation email IDs).
No deprecated algorithms in use.

## D. Data handling

**D.1 What Personal Data do you store?** Email addresses
(buyers and subscribers), truncated IP prefixes (`/24` IPv4, `/48`
IPv6), Stripe customer IDs, SHA-256 hashes (pseudonymous; not
Personal Data per recital 26 of the GDPR).

**D.2 What Personal Data do you NOT store?** Full IP addresses,
file contents, file metadata (EXIF), credit card data (Stripe holds
it), passwords (none exist).

**D.3 How long is data retained?** Receipts: indefinitely (the
product). Free-tier receipts: may be pruned at 30 days per ToS.
Emails: lifetime of active claim code or subscription, plus 7 years
for tax/refund records per Privacy Policy. Truncated IP prefixes:
24h log rotation (Fly default).

**D.4 How is data deleted?** Append-only ledgers with tombstone
events — `/api/me/delete` appends an `email_deleted` event in every
relevant ledger; read paths honor the deletion. Historical rows
remain for audit but no live state resolves to the deleted email.

**D.5 Cross-border transfers.** Data is hosted in the United States
(Fly region `iad`). EU/UK transfers covered by Standard Contractual
Clauses (Module 2) — see `deploy/compliance/SUBPROCESSORS.md`.

## E. Application security

**E.1 Source code review.** Internal. Three audits closed:
forensic, security, payment+PII. Reports at `deploy/`. Full code
review by an external firm is offered to B2B customers on a
customer-funded timeline.

**E.2 Vulnerability management.** No third-party dependencies in
the application runtime (stdlib-only Python + vanilla JS). pytest
is the only dev-time dep. GitHub Dependabot would surface CVEs
when the repo goes public.

**E.3 Penetration testing.** Not yet performed by an external firm.
The audits in `deploy/` cover the same threat-model topics
(input validation, authentication, payment integrity, PII
handling, idempotency, signature replay).

**E.4 OWASP Top 10 coverage.** See `deploy/SECURITY.md` for the
checklist. Headlines: parametrized data access (no SQL), strict
input validation (regex shape checks on every untrusted input),
HMAC signature verification on webhooks, CSP `default-src 'self'`,
no inline scripts, `__Host-` cookie prefix.

**E.5 Input validation.** Receipt IDs: `^[A-Za-z0-9_-]{1,64}$`.
Emails: `^[^@\s,]{1,64}@[^@\s,]{1,255}$`. Hashes: 64-char
lowercase hex strictly enforced. Path traversal in URL segments
rejected at the route layer.

**E.6 Rate limiting.** Token-bucket per `/24` IP prefix, default
10/hour for anchor + auth + waitlist + event endpoints. Bucket
state persists across restarts.

**E.7 Logging + monitoring.** First-party only. Application logs
to Fly stderr with IPs truncated. `/api/health` exposes uptime,
calendar reachability, ledger sizes. Public `/status.html`
auto-refreshes every 30s.

**E.8 Incident response.** `deploy/RUNBOOK.md` documents
chargeback, calendar outage, secret rotation, backup/restore.
Personal Data breach notification per DPA: within 72h.

## F. Operational security

**F.1 Change management.** All code changes go through pytest
green + smoke test green before deploy. GitHub Actions CI gates
merges to main. No direct production edits.

**F.2 Backups.** Fly volume snapshots: daily, 5-day retention.
Manual backups via `scripts/backup_volume.sh` (gpg-encrypted to
founder's key) at founder's discretion.

**F.3 Disaster recovery.** RTO < 4h (fly volume restore + redeploy).
RPO < 24h (daily snapshot cadence). For tighter RTO/RPO,
documented escalation path: contact founder, restore snapshot,
redeploy.

**F.4 Vendor management.** Sub-processors documented at
`deploy/compliance/SUBPROCESSORS.md`. 14-day customer notice on
changes per DPA §5.

## G. Compliance + certifications

**G.1 SOC 2.** Not yet. Roadmapped at `deploy/MARKET_ROADMAP.md` §Y5-base. Available on customer-funded timeline.

**G.2 ISO 27001.** No.

**G.3 PCI DSS.** N/A — we do not store, process, or transmit
cardholder data. Stripe handles 100% of payment flow.

**G.4 GDPR / UK GDPR.** Compliant. Data subject rights endpoints
live: `/api/me/export`, `/api/me/delete`. EU representative under
Article 27: not yet appointed; only required when offering
goods/services to EU residents at scale. Available on contract.

**G.5 CCPA / CPRA.** Compliant. "Do Not Sell My Personal
Information": N/A — we sell nothing.

**G.6 HIPAA.** N/A — we do not accept or process Protected Health
Information. Service ToS explicitly disclaims this use case.

**G.7 FedRAMP.** No.

## H. Business continuity

**H.1 Number of customers (current).** Pre-launch; pilot only.

**H.2 Largest customer concentration.** N/A.

**H.3 Service-level agreement (SLA).** Standard tier: best-effort,
no formal SLA. B2B tier: 99.5% uptime SLA available on request.

**H.4 Status communication.** Public status page at
`/status.html`. No third-party status page (Statuspage.io,
Atlassian) integrated.

## I. Contacts

| Topic | Contact |
|---|---|
| Security disclosures | security@orphograph.com (PGP key available on request) |
| Privacy / GDPR requests | privacy@orphograph.com |
| Customer support | support@orphograph.com |
| Legal / contract | legal@orphograph.com (founder) |

---

*Fill in [BRACKETED] customer-specific fields per signing.
Some questions may have follow-up versions specific to a
prospect's industry — answer those individually rather than
extending this canned answer set.*
