# ADR 0002 — MIT license for the verifier and the protocol code

**Status:** Accepted
**Date:** 2026-05-12

## Context

A central claim of the office is that any receipt verifies against
Bitcoin independently of Orphograph's continued operation. That claim
is only credible if the verifier source is itself published, the
license permits independent use, and the verifier is small enough to
audit.

## Decision

The verifier and the surrounding protocol code are published under
MIT at https://github.com/Orphograph/Orphograph. The license is
explicit. No copyleft clause attempts to bind downstream operators
into matching license terms.

## Consequences

**Positive.**
- The verifier can be embedded in any environment — including hostile
  ones — without legal friction.
- The "verifies without us" claim becomes operationally testable.
- An acquirer cannot extract value by closing the source after
  acquisition; the prior version remains permanently available.

**Negative.**
- A competitor can take the code and ship it under their own brand.
  The brand and the operating presence remain the moat, not the code.
- The MIT permission is irrevocable for already-distributed copies.

## Why this is not a contradiction with the desire for IP protection

The IP that is protected is:
- The brand (`Orphograph` wordmark, common-law trademark, with
  filed-USPTO upgrade anticipated).
- The customer relationships and operating infrastructure.
- The specific architectural choices, defensively published with
  Bitcoin-anchored dates — preventing any third party from later
  claiming priority on the methods.

The IP that is intentionally given away is the code, because giving
it away is the only way the central trust claim of the product
remains coherent.
