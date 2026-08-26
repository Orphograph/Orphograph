# Demand Pack v1 — disabled smoke test

This experiment asks whether the price/value gap, rather than awareness, is
blocking paid demand. It is deliberately non-transactional: a visitor can
register that they would buy 10 anchors for $5, but cannot be charged.

```text
default                         founder-enabled test
hidden form                     visible candidate offer
normal pricing + checkout  ->   email-interest capture only
no changed entitlement          no changed entitlement
```

Enable only after the office/external demand ledger reports `complete`:

```sh
ORPHO_DEMAND_PACK_V1=1
```

The switch accepts exact `1` only. The offer is fixed in code, carries no
checkout URL or Stripe price id, and says “not checkout” beside the form.
Disable it by removing the variable or setting it to `0`.

## Decision rule

Run for either 14 days or 200 unique external pricing visitors, whichever is
later. Count distinct accepted `demand_pack_v1` waitlist records after normal
email deduplication. Do not count office/test addresses.

- 10 or more qualified signals and at least 5% visitor-to-interest conversion:
  validate a real SKU and its margin, then build a separate transactional test.
- Fewer than 10 or below 5%: reject this offer; do not build SCALE-1–5 on its
  behalf.
- Unavailable/degraded attribution: extend the test; never interpret missing
  instrumentation as zero.

This test does not authorize changing the free tier, Stripe products, pack
credits, receipts, or production checkout.
