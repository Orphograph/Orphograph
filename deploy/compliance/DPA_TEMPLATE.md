# Data Processing Addendum — template

**Last updated:** 2026-05-12.
**Status:** template. Founder fills in [BRACKETED] fields per signing customer.

This DPA forms part of the Master Services Agreement between
**[CUSTOMER LEGAL NAME]** ("Controller") and **[PROCESSOR LEGAL NAME —
e.g. Orphograph LLC]**, a Wyoming limited liability company
("Processor"), and applies wherever Processor processes Personal Data
on behalf of Controller in the course of providing the Orphograph
service.

## 1. Definitions

Unless otherwise defined here, terms have the meanings given in
Regulation (EU) 2016/679 (the **GDPR**), UK GDPR, and where
applicable the California Consumer Privacy Act / California Privacy
Rights Act ("CCPA/CPRA").

- **Personal Data** — any information relating to an identified or
  identifiable natural person.
- **Processing** — any operation performed on Personal Data,
  including collection, storage, transmission, deletion.
- **Sub-processor** — any third party engaged by Processor to
  process Personal Data on behalf of Controller.

## 2. Subject matter, duration, nature, and purpose

| Item | Details |
|---|---|
| Subject matter | Provision of the Orphograph file-timestamping service |
| Duration | Term of the underlying Master Services Agreement plus 30 days |
| Nature and purpose of processing | Receiving SHA-256 hashes, anchoring to Bitcoin via OpenTimestamps, delivering receipts, processing payments, supporting accounts |
| Categories of data subjects | Controller's end users who use the Orphograph service |
| Categories of Personal Data | Email address; truncated IP prefix; SHA-256 hash of files (the file bytes themselves are not received); Stripe customer ID; authentication tokens (stored only as hashes) |
| Sensitive data | None. Processor does not request, accept, or process special-category data under Article 9 of the GDPR. |

## 3. Processor obligations

Processor shall:

1. Process Personal Data only on documented instructions from Controller, including with regard to transfers to a third country, unless required to do so by EU or Member State law to which the Processor is subject; in such a case, the Processor shall inform the Controller of that legal requirement before processing, unless the law prohibits such information on important grounds of public interest.
2. Ensure that persons authorized to process Personal Data have committed themselves to confidentiality or are under an appropriate statutory obligation of confidentiality.
3. Implement appropriate technical and organizational measures to ensure a level of security appropriate to the risk (see Section 6).
4. Engage Sub-processors only under the conditions of Section 5.
5. Assist Controller, taking into account the nature of the processing, in fulfilling its obligation to respond to data subject rights requests.
6. Assist Controller in ensuring compliance with the obligations pursuant to Articles 32 to 36 of the GDPR.
7. At the choice of the Controller, delete or return all Personal Data to the Controller after the end of the provision of services relating to processing, and delete existing copies unless EU or Member State law requires storage of the Personal Data.
8. Make available to the Controller all information necessary to demonstrate compliance with this DPA and the GDPR, and allow for and contribute to audits, including inspections.

## 4. Controller obligations

Controller shall:

1. Ensure that it has all necessary rights to provide the Personal Data to Processor for processing.
2. Provide instructions to Processor that are lawful and do not require Processor to violate applicable law.
3. Be responsible for the lawfulness of the processing, including (where required) obtaining consent from data subjects.

## 5. Sub-processors

Controller authorizes Processor to engage Sub-processors. Current
Sub-processors are listed at `deploy/compliance/SUBPROCESSORS.md` in
the Orphograph public repository. Processor will provide Controller
with notice of any intended changes concerning the addition or
replacement of Sub-processors. Controller may object to such changes
within 14 days of receipt of notice; if Controller objects in good
faith on reasonable grounds, the parties will work together to find a
commercially reasonable solution, failing which Controller may
terminate the affected services with no further obligation.

Processor remains fully liable to Controller for the performance of
each Sub-processor's obligations.

## 6. Security measures

Processor implements at least the following measures:

| Domain | Measure |
|---|---|
| Encryption in transit | TLS 1.2+ on all client and Sub-processor connections; HTTPS forced at the edge |
| Encryption at rest | Cloud-provider volume encryption (Fly.io managed); secrets stored only via `fly secrets set` |
| Access control | Production secrets accessible only to founder + named operators via Fly account MFA; no shared accounts |
| Logging | IP addresses truncated to /24 (IPv4) or /48 (IPv6) before write; email addresses masked in webhook stderr logs |
| Pseudonymization | Subscriber email IDs in receipt source fields use HMAC-SHA256 with a per-installation secret |
| Authentication | Magic-link email sign-in, one-time use, 24h TTL; sessions stored only as SHA-256 hashes |
| Webhook signature verification | HMAC-SHA256 per Stripe specification with `hmac.compare_digest` (timing-safe); multi-`v1=` rotation support; idempotency by event ID |
| Cookie hardening | `__Host-` prefix, `HttpOnly`, `SameSite=Lax`, `Secure` in production |
| CSP | `default-src 'self'`; no third-party scripts; no cookies for tracking |
| Backups | Daily volume snapshots retained 5 days by Fly.io |
| Test coverage | 98 pytest cases; multi-process double-spend probe; signature timing-safety regression |
| Audits | Internal forensic + security + payment-PII audits; external SOC 2 not yet performed |

## 7. Data subject rights

Processor will assist Controller in responding to data subject
rights requests:

- **Access:** `GET /api/me/export` returns all data tied to the requesting email when authenticated.
- **Deletion:** `POST /api/me/delete` tombstones the email across every ledger. Append-only design retains the deletion *event* for audit; no live records resolve to the email afterward.
- **Rectification:** Email update via support@orphograph.com.
- **Portability:** Same `/api/me/export` endpoint returns JSON suitable for re-import.
- **Restriction / Objection:** Honored via the deletion path or by explicit support request.

Response window: **within 30 days** of request, per GDPR Article 12(3).

## 8. International transfers

Where Personal Data is transferred from the EEA, UK, or Switzerland
to a country without an adequacy decision, transfers are made under
the European Commission's **Standard Contractual Clauses** (Module Two:
Controller to Processor), incorporated herein by reference.

Sub-processor transfers (see `SUBPROCESSORS.md`) are similarly covered.

## 9. Personal Data breach notification

Processor will notify Controller without undue delay (and in any
event within **72 hours** of becoming aware) of a Personal Data
breach affecting Controller's data, providing:

1. A description of the nature of the breach including the categories and approximate number of data subjects and records concerned.
2. The name and contact details of the founder or designated contact.
3. A description of the likely consequences.
4. A description of measures taken or proposed to address the breach and mitigate its possible adverse effects.

Notification address: **[CUSTOMER PRIMARY CONTACT EMAIL]**.

## 10. Audits

Controller may, upon written request and no more than once per
12-month period (except where required by a supervisory authority or
in case of a Personal Data breach), audit Processor's compliance
with this DPA. Audits will be conducted at Controller's expense,
during business hours, with reasonable notice, and may be performed
remotely.

In lieu of an on-site audit, Processor will provide third-party
attestations or self-assessments upon request.

## 11. Liability

Liability under this DPA is governed by the Master Services
Agreement. Nothing in this DPA limits any liability that cannot be
limited under applicable law.

## 12. Governing law and jurisdiction

This DPA is governed by the laws of the Commonwealth of Puerto Rico
and the United States. Disputes will be resolved in the courts of
Puerto Rico.

For EU/UK-originated disputes, the parties acknowledge that data
subjects retain the right to lodge complaints with their local
supervisory authority.

## 13. Entire agreement

This DPA together with the Master Services Agreement constitutes
the entire agreement between the parties regarding the processing
of Personal Data and supersedes any prior agreements on the subject.

---

**Signed for and on behalf of Processor:**

Name: [FOUNDER LEGAL NAME]
Title: Member, Hydroboro Basic Industries LLC
Date: [DATE]
Signature: __________________________________

**Signed for and on behalf of Controller:**

Name: [CUSTOMER SIGNATORY NAME]
Title: [CUSTOMER SIGNATORY TITLE]
Date: [DATE]
Signature: __________________________________

---

*This template is for internal use as a starting point for B2B
contract negotiations. Have a qualified attorney review the final
DPA before signing the first one — local jurisdictional carve-outs
(EU representative under Article 27 GDPR, etc.) may apply.*
