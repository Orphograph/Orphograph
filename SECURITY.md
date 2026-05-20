# Security policy

## Contact

Security findings should be reported to **`security@orphograph.com`**.
General correspondence belongs at **`hello@orphograph.com`**.

The office uses brand-domain addresses exclusively for all
external communication. No personal email address of any
individual operator is published, asked for, or accepted as a
formal channel for security or business correspondence. If
something looks like an office reply from a non-`@orphograph.com`
address, it is not an office reply.

## What we want to know

- Findings that allow an unauthenticated third party to read a
  receipt that is not theirs.
- Findings that allow tampering with an existing receipt, a
  manifest, or a Bitcoin attestation in a way that the open
  verifier would still accept.
- Findings that expose customer email addresses, IP addresses
  past their truncation, or any other personally identifying
  information beyond what the customer placed in their own
  attestation.
- Findings that allow an attacker to consume office resources
  in a way that denies service to legitimate customers — but
  please test rate limits in moderation; the office's rate
  limiter is intentionally aggressive.
- Findings that compromise the build pipeline, the deploy
  pipeline, or the integrity of the open-source verifier.

## What is out of scope

- Vulnerabilities in third-party services the office uses but
  does not operate (the Bitcoin chain, the OpenTimestamps
  calendars, the office's payment processor, the office's
  hosting provider, the office's mail forwarder). Please
  report those upstream.
- Findings that require physical access to a customer's device
  or to the office's hardware.
- Self-imposed denial of service (e.g., an attempt at log
  flooding from a single IP — the rate limiter will see you).
- Reports that consist only of automated scanner output without
  a manually verified exploit path.

## What we will do

- Acknowledge a report within a small number of business days.
- Investigate, reproduce, and respond with our assessment.
- Coordinate disclosure timing with the reporter.
- Publish a written acknowledgment for reports that lead to a
  change in the protocol, the verifier, or the office's
  operational posture, where the reporter wishes to be credited.

The office is small and runs on stdlib-only code; we do not
currently fund a paid bounty program. We will fund acknowledgment
artifacts (a written credit, an anchored thank-you, a customer-
visible note in the changelog) and we will respond promptly.

## Verifying that a message is from the office

All formal office correspondence comes from `@orphograph.com`.
Customer-initiated transactional mail (a receipt sent in reply
to a customer's anchor) is signed by SPF, DKIM, and DMARC on the
brand domain; the headers of any genuine reply will pass all
three. If a header check fails, the message is not from the
office.

This file is itself anchored to Bitcoin on issuance. The current
contact addresses recorded here therefore have a verifiable date
of publication.
