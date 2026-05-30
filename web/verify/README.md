# orphograph-verify

Standalone verifier for [Orphograph](https://orphograph.com) receipts.

If Orphograph's domain disappears tomorrow, this 100-line Python file is all
you need to prove that a file existed at a moment in time. It validates
the OpenTimestamps `.ots` proofs in a receipt against the embedded hash,
and (optionally) re-hashes the original file to confirm it produced
that receipt.

Stdlib only. No dependencies. No network calls.

## Why this repo exists

Orphograph is a service that hashes files in your browser and anchors
those hashes to the Bitcoin blockchain via OpenTimestamps. The crypto
is open; the convenience is what we sell.

But "we sell convenience" only matters if the underlying proof outlives
our company. This verifier is published as MIT so that:

1. You can audit it. No proprietary verifier-only formats — your receipt
   is just `receipt.json` + 5 standard OTS proof files.
2. You can run it offline, without us.
3. If we vanish, you still have everything you need.

That's the deal.

## Install

Download the verifier bundle directly — no account, no clone:

```bash
curl -O https://orphograph.com/verify/orphograph-verify-0.1.tar.gz
tar xzf orphograph-verify-0.1.tar.gz
```

Or grab the single file from the public repo
(`github.com/Orphograph/Orphograph`, under `web/verify/verify.py`).

That's it. The verifier is a single Python 3.9+ file. No `pip install` needed.

## Usage

Verify the proofs in a receipt are well-formed and match the receipt's hash:

```bash
python3 verify.py path/to/receipt.json
```

Verify the proofs **and** re-hash a file to confirm it produced that receipt:

```bash
python3 verify.py path/to/receipt.json --file path/to/original-file
```

Exit codes:
- `0` — receipt valid (and file matches, if `--file` given)
- `2` — receipt or file not found
- `3` — file hash does not match the receipt
- `4` — one or more OTS proofs failed validation

## Try it on the bundled sample

The `examples/sample/` directory contains a real receipt anchored
on 2026-05-12.

```bash
python3 verify.py examples/sample/receipt.json --file examples/sample/sample.txt
```

You should see all five `.ots` proofs marked `OK` and the file hash
match. Tamper with `sample.txt` (add a space, anything) and the file
match will flip to `NO`.

## Quantum-hedge dual hashing

Receipts created after 2026-05-12 include an optional `sha512_hex` field
alongside the SHA-256 hash that gets anchored to Bitcoin. The OTS
protocol requires SHA-256 (calendars don't accept other hashes), but
the SHA-512 sibling rides along in the receipt as an independent
witness.

When you run `verify.py … --file <original>`, the verifier checks both
hashes. To forge a file matching the receipt, an attacker would need
simultaneous collisions in SHA-256 *and* SHA-512 — substantially
harder than either alone. SHA-256 under Grover's algorithm retains
~128-bit effective security; SHA-512 retains ~256-bit. Together they
are a deliberate post-quantum hedge.

If your receipt has no `sha512_hex` field (older receipts), the
verifier checks SHA-256 only, which is still NIST-acceptable.

## Upgrading proofs to a full Bitcoin merkle proof

The `.ots` files in a fresh receipt are "calendar-pending" — they
prove your hash was submitted to an OpenTimestamps calendar, which
will batch it into a Bitcoin transaction within ~1 hour.

To upgrade a receipt to a fully block-attested Bitcoin proof, run:

```bash
pip install opentimestamps-client
ots upgrade examples/sample/*.ots
```

This contacts the calendar servers and rewrites each `.ots` file to
include the Bitcoin block attestation. After that, `ots verify
*.ots` against the original file will confirm against the chain
without trusting any single calendar.

## License

MIT. See `LICENSE`. Copyright (c) 2026 the Orphograph contributors.
