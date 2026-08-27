# Scaling gates

SCALE-1 through SCALE-5 are architecture options, not the current constraint.
The system must not spend irreversible complexity on synthetic office traffic.

```text
complete attribution
        |
        v
external demand? -- no --> run/reject offer test; keep SCALE parked
        |
       yes
        v
measured saturation? -- no --> publish headroom; keep SCALE parked
        |
       yes
        v
SCALE-1 -> SCALE-2 -> SCALE-3 -> SCALE-4 -> SCALE-5
```

## Entry gates

All of these must be true before SCALE-1 starts:

1. Demand ledger quality is `complete` for 30 consecutive days.
2. Office automation is excluded and separately nonzero during scheduled jobs.
3. At least 1,000 external successful anchors in 30 days from at least 100
   privacy-safe cohorts, or at least 25 paying external customers.
4. A reproducible load test reaches at least 60% of one measured production
   bottleneck at the observed peak traffic shape.
5. The proposed scale unit has an acceptance test, rollback plan, measured
   capacity target, and owner.

If any gate is false, the correct state is `PARKED — GATE NOT MET`, not blocked
and not in progress. Forecasts and “1M-user rates” are not measurements.

## Exit evidence

| Unit | Evidence required to call it done |
| --- | --- |
| SCALE-1 | Backward-compatible receipt/proof contract, storage-per-anchor measurement, migration and rollback tests |
| SCALE-2 | Chaining specification, fork/replay tests, verifier vectors, recovery from an unavailable prior root |
| SCALE-3 | Versioned load generator, raw results, hardware/region/config, p50/p95/p99 and error rate, published measured capacity |
| SCALE-4 | Persisted-object quota invariant, concurrency tests, deletion/accounting recovery, abuse simulation |
| SCALE-5 | Read/write failure matrix, consistency contract, regional failover drill, cost and latency before/after |

Capacity claims must state the measured configuration and date. A future test
cannot retroactively justify building the preceding layers.
