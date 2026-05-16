# Sub-processors

**Last updated:** 2026-05-12.

Orphograph engages the following Sub-processors. Each is named with
the data scope, the lawful basis, the transfer mechanism, and the
public privacy/security URL where their own posture can be reviewed.

This list is normative — when it changes, B2B customers with a DPA
in place get 14 days' notice per `DPA_TEMPLATE.md` §5.

| Sub-processor | Purpose | Data scope | Region | Transfer mechanism | Privacy / DPA URL |
|---|---|---|---|---|---|
| **Fly.io** | Application hosting + persistent volume | All persistent data (ledgers, receipts, configs); no file bytes | US (region `iad`) | Standard Contractual Clauses (Module 2) for EU/UK transfers | https://fly.io/legal/privacy-policy + https://fly.io/legal/dpa |
| **Stripe, Inc.** | Payment processing | Buyer email; payment-method tokens; transaction records | Global (Stripe-managed) | SCC + Stripe's own DPA referenced at the link | https://stripe.com/privacy + https://stripe.com/legal/dpa |
| **Resend (Resend.com Inc.)** | Transactional email delivery | Recipient email; message contents (claim codes, receipt summaries) | US | SCC for EU/UK senders | https://resend.com/legal/privacy-policy + https://resend.com/legal/dpa |
| **OpenTimestamps calendar servers** | Bitcoin anchoring | 32-byte SHA-256 hashes only (no email, no IP, no file bytes) | Operator-managed (decentralized) | N/A — receives no Personal Data per GDPR (hashes are pseudonymous and not linkable to a data subject) | https://opentimestamps.org/ |
| **Porkbun (or Namecheap)** | Domain registration + DNS | Domain WHOIS contact (founder, not customers) | US | N/A — customer data not transmitted | https://porkbun.com/privacy |

## What we don't use

Explicitly NOT in our stack (and won't be without prior notice):

- **Google Analytics / Mixpanel / Amplitude / Segment** — no third-party trackers. We run a first-party `/api/event` endpoint storing only event type + page + truncated IP prefix.
- **Intercom / Drift / Crisp** — no third-party chat. Customer support is email-only at `support@orphograph.com`.
- **Sentry / Datadog / New Relic** — no third-party error tracking. App-level errors land in Fly's native logs only.
- **Auth0 / Cognito / Firebase Auth** — no third-party auth. Magic-link is implemented in stdlib Python at `server/auth.py`.
- **Cloudflare R2 / AWS S3** — no third-party object storage. Receipts land on the Fly persistent volume.
- **HubSpot / Mailchimp** — no marketing email tools. Resend handles transactional only.

## Data residency

| Data | Stored where |
|---|---|
| Receipts + .ots proofs | Fly volume in `iad` (US east) |
| Credit ledger | Fly volume in `iad` |
| Subscription state | Fly volume in `iad` |
| Email addresses | Fly volume in `iad`; mirrored at Stripe (for buyers) and Resend (for senders) |
| Authentication tokens | Fly volume in `iad`, stored only as SHA-256 hashes |
| Bitcoin anchor proofs | The Bitcoin chain itself (global, permissionless) |
| Truncated IP logs | Fly stderr → Fly logs, retained ~24h |

EU customers requiring a non-US data region: contact `support@orphograph.com`.
Currently we operate in a single region; expansion to `fra` or `lhr`
would require business-case justification.

## Audit reports available on request

| Sub-processor | Most recent attestation | Request via |
|---|---|---|
| Fly.io | SOC 2 Type 2 | https://fly.io/legal/security |
| Stripe | SOC 1 + SOC 2 + PCI DSS Level 1 | https://stripe.com/legal/dpa |
| Resend | SOC 2 Type 1 (in progress to Type 2) | support@resend.com |

Orphograph itself does **not** yet hold SOC 2 attestation. SOC 2 is
on the roadmap (`deploy/MARKET_ROADMAP.md` §Y5-base). For B2B
customers requiring it before contract, we can engage an auditor on
a customer-funded timeline.

## Changes

| Date | Change |
|---|---|
| 2026-05-12 | Initial list (Fly.io, Stripe, Resend, OpenTimestamps calendars, registrar) |

Any future addition triggers customer notification per the DPA.
