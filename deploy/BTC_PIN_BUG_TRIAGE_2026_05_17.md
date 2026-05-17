# BTC-Pin Bug Triage — 2026-05-17

Genesis receipt `o3WGD22T4UwqfCrb` stuck at `status:"pending"` 28+ hours after
creation. Local upgrade_worker shows `scanned: 13, upgraded: 0` across the
entire local receipts/ tree (including a receipt from 2026-05-12, 5 days old).

## Root cause

**Wrong commitment hash passed to `/timestamp/<X>` in `server/upgrade_worker.py`.**

The OpenTimestamps calendar `/timestamp/<HEX>` endpoint expects the
**per-calendar commitment digest** — the running hash AFTER the calendar's
nonce op-chain (the APPEND / PREPEND / SHA256 ops the calendar returned when
we first POSTed `/digest`). The current worker sends `record["hash_hex"]`,
which is the user's original SHA-256, before any calendar nonce was applied.
The calendars have no index keyed by that hash, so they return **HTTP 404
forever**, which `_fetch_upgrade` correctly interprets as "still pending."

Result: no receipt ever transitions out of `pending`, regardless of how long
Bitcoin confirms. Confirmed live (read-only):

```
hash_hex (original):     7accf9e90453280e6fb081fd9d83dfb1...
GET /timestamp/<hash_hex>                          -> HTTP 404 (every calendar)

commitment after walking a.ots op-chain to the pending marker:
                         6a036668e52de8df...4a34c5c83fe3368491dfb52d
GET /timestamp/<commitment>                        -> HTTP 200, 1000 bytes
                                                      (contains BTC attestation
                                                       tag 0x0588960d73d71901)
```

So the proof has been Bitcoin-confirmed at alice (and almost certainly the
other 4 calendars) for days. The bug is entirely client-side in our worker.

## Secondary issue (cosmetic, not blocking)

`/api/stats` reports `calendars: 0/5 reachable` because
`server/health.py:_check_calendars_parallel` returns
`{"reachable": None, "checked": False}` when `ORPHO_HEALTH_ACTIVE_PROBES != "1"`,
and `server/stats.py:_calendars_public` then counts `r.get("reachable")` as
falsy. Production likely has `ACTIVE_PROBES` unset. This is independent of the
upgrade bug — calendars ARE reachable, the stats endpoint just isn't probing.

## Fix status

**Patch PROPOSED, not applied.** Edit permissions were denied this turn, so
the working tree is untouched. The fix is small and contained — replace
`_fetch_upgrade(cal, hash_hex)` + `_build_ots(hash_hex, body)` with a routine
that:

1. Reads the existing per-calendar `.ots` blob.
2. Locates the pending-attestation marker
   `b"\x00\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e"` inside it.
3. Walks the op-chain from the original 32-byte hash up to that marker,
   computing the running digest (APPEND `0xf0`, PREPEND `0xf1`, SHA256
   `0x08`).
4. GETs `<calendar>/timestamp/<running_digest.hex()>`.
5. On 200, writes `ots_blob[:marker_idx] + response_body` back to disk.
6. On 404, leaves the .ots untouched and reports pending (correct existing
   behavior, just on the RIGHT commitment).

Suggested concrete patch (drop-in replacement for the two helpers + the
loop body in `_upgrade_one`):

```python
PENDING_ATTESTATION_MARKER = b"\x00\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e"

def _commitment_for_pending(ots_blob: bytes) -> tuple[str | None, int]:
    """Walk the op-chain in an .ots file up to its pending-attestation marker.

    Returns (commitment_hex, marker_index). commitment_hex is None if the
    blob is malformed or already-upgraded (no pending marker)."""
    import hashlib
    if not ots_blob.startswith(OTS_HEADER_MAGIC):
        return None, -1
    i = len(OTS_HEADER_MAGIC) + 1  # skip version
    if ots_blob[i:i+1] != OTS_TAG_SHA256:
        return None, -1
    i += 1
    cur = ots_blob[i:i+32]
    i += 32
    marker_idx = ots_blob.find(PENDING_ATTESTATION_MARKER, i)
    if marker_idx < 0:
        return None, -1
    while i < marker_idx:
        op = ots_blob[i]; i += 1
        if op == 0xf0:  # APPEND
            ln = ots_blob[i]; i += 1
            cur = cur + ots_blob[i:i+ln]; i += ln
        elif op == 0xf1:  # PREPEND
            ln = ots_blob[i]; i += 1
            cur = ots_blob[i:i+ln] + cur; i += ln
        elif op == 0x08:  # SHA256
            cur = hashlib.sha256(cur).digest()
        else:
            return None, -1
    return cur.hex(), marker_idx
```

Then in `_upgrade_one`, replace the body of the per-calendar loop with:

```python
old_blob = ots_path.read_bytes()
commitment_hex, marker_idx = _commitment_for_pending(old_blob)
if commitment_hex is None:
    # Already upgraded (no pending marker) — count as pinned, no-op.
    upgrades.append({"calendar": cal, "pinned": True, "changed": False})
    continue
ok, body = _fetch_upgrade(cal, commitment_hex)
if not ok:
    upgrades.append({"calendar": cal, "pinned": False, "reason": str(body)})
    continue
new_blob = old_blob[:marker_idx] + body
ots_path.write_bytes(new_blob)
upgrades.append({"calendar": cal, "pinned": True, "changed": True})
```

`_build_ots` becomes unused for upgrades (keep it; engine.py still imports
the same constants for new anchors).

## Verification commands for founder

After applying the patch locally:

```
cd ~/orphograph
python3 -m pytest tests/ -p no:anchorpy -q       # expect 370 passing
python3 server/upgrade_worker.py                 # expect upgraded > 0
python3 -c "import json; print(json.dumps(json.loads(open('data/receipts/XwTULwlh76PcCst9/receipt.json').read()), indent=2))"
# -> status should now be "pinned", btc_pinned_at populated
```

To confirm production fix after deploy:

```
curl -s https://orphograph.com/api/receipt/o3WGD22T4UwqfCrb | python3 -m json.tool
# -> "status": "pinned", "btc_pinned_at": "<iso-ts>"
```

For the secondary `calendars: 0/5` cosmetic issue: set
`ORPHO_HEALTH_ACTIVE_PROBES=1` in `fly.toml` env, redeploy. (Optional —
purely an observability fix, does not affect anchor correctness.)

## Files touched

- None (Edit permissions denied). Patch proposal is above; apply manually to
  `/Users/founder/orphograph/server/upgrade_worker.py`.
- This triage report:
  `/Users/founder/orphograph/deploy/BTC_PIN_BUG_TRIAGE_2026_05_17.md`

## Outcome category

**Bug (c) — upgrade-detection: worker calls /timestamp with wrong commitment.**
NOT a "wait longer" scenario. NOT a network/firewall scenario. The bug is
deterministic and reproduces on day-5-old receipts.
