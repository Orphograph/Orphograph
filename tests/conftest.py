"""conftest.py — shared pytest fixtures for orphograph tests."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


# --- shared OpenTimestamps test bodies (one definition, four users) ---------
# sha256 then a Bitcoin attestation for block 949156 (varint a4 f7 39,
# payload length 3): the smallest calendar body the guard accepts.
# What a calendar returns from POST /digest: append(16-byte nonce) · sha256 ·
# pending attestation (URI "x"). engine.anchor_hash accepts nothing less.
import ots_timestamp as _ots  # noqa: E402  (SERVER_DIR inserted above)

PENDING_BODY = (b"\xf0\x10" + b"\x01" * 16 + b"\x08"
                + b"\x00" + _ots.PENDING_ATTESTATION_TAG + b"\x02\x01x")

PINNED_BODY = b"\x08\x00" + _ots.BITCOIN_ATTESTATION_TAG + b"\x03\xa4\xf7\x39"


def make_pending_ots(digest: bytes = b"\x11" * 32, ops: bytes = b"") -> bytes:
    """A well-formed pending .ots blob as engine.py writes it: header +
    version + sha256 tag + digest + optional op run + pending attestation
    (URI 'x'). `upgrade_worker._commitment_for_pending` parses it."""
    import upgrade_worker  # noqa: E402  (server/ is on sys.path above)
    pending = upgrade_worker.PENDING_ATTESTATION_MARKER + b"\x02\x01x"
    return (upgrade_worker.OTS_HEADER_MAGIC + upgrade_worker.OTS_VERSION
            + upgrade_worker.OTS_TAG_SHA256 + digest + ops + pending)


# --- synthetic receipt fixture (one definition, every fixture-hungry test) ---
# Tests that need a receipt DIRECTORY used to point at a production receipt
# under data/receipts/, which is gitignored and, for the dispute-bundle
# fixture, no longer exists anywhere (2026-09-03: absent locally, public ZIP
# 404). They skipped everywhere and read as green. This builds a receipt the
# way engine.anchor_hash lays one out — receipt.json + one pending .ots per
# calendar, each embedding the digest — so verify_cli / dispute_bundle /
# engine.verify_receipt exercise real code on a deterministic input.
import hashlib as _hashlib
import json as _json
from datetime import datetime as _dt, timezone as _tz

FIXTURE_RECEIPT_ID = "fixtureTestRcpt1"
FIXTURE_HASH_HEX = _hashlib.sha256(b"test").hexdigest()


def write_fixture_receipt(receipts_dir: Path, rid: str = FIXTURE_RECEIPT_ID,
                          hash_hex: str = FIXTURE_HASH_HEX) -> Path:
    """Write <receipts_dir>/<rid>/{receipt.json, <calendar>.ots x N}. Returns the dir."""
    import engine  # noqa: E402  (server/ is on sys.path above)
    rd = receipts_dir / rid
    rd.mkdir(parents=True, exist_ok=True)
    digest = bytes.fromhex(hash_hex)
    successes = []
    for cal in engine.CALENDARS:
        short = engine._calendar_short(cal)
        (rd / f"{short}.ots").write_bytes(make_pending_ots(digest))
        successes.append({"calendar": cal, "ots_path": f"receipts/{rid}/{short}.ots"})
    record = {
        "receipt_id": rid,
        "created_at": _dt(2026, 1, 2, 3, 4, 5, tzinfo=_tz.utc).isoformat(),
        "hash_hex": hash_hex,
        "client_label": "fixture",
        "source": "test",
        "private": False,
        "owner_id": None,
        "attestation": None,
        "metadata": {},
        "calendars_ok": len(successes),
        "calendars_total": len(engine.CALENDARS),
        "successes": successes,
        "failures": [],
        "status": "pending",
        "bitcoin_attested": False,
    }
    (rd / "receipt.json").write_text(_json.dumps(record, indent=2))
    return rd


# --- no green-by-skip -------------------------------------------------------
# A skipped test is not a passed test. 21 tests in this suite skipped in CI for
# weeks (missing local receipt, snarkjs not installed) and the gate stayed
# green. Every skip must now match a budgeted reason, or the session fails.
# Local runs without the tooling can opt out: PYTEST_ALLOW_SKIPS=1. (Harness
# knob, deliberately outside the ORPHO_* product namespace that
# test_no_phantom_env_knobs.py polices — nothing shipped reads it.)
import os as _os
import re as _re

ALLOWED_SKIP_REASONS = (
    # (regex, why it is acceptable) — keep this list short and justified.
)
_SKIPS: list = []


def pytest_runtest_logreport(report):
    if report.skipped and report.when in ("setup", "call"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        _SKIPS.append((report.nodeid, reason))


def pytest_sessionfinish(session, exitstatus):
    if _os.environ.get("PYTEST_ALLOW_SKIPS") == "1":
        return
    unbudgeted = [
        (nid, reason) for nid, reason in _SKIPS
        if not any(_re.search(rx, reason) for rx, _why in ALLOWED_SKIP_REASONS)
    ]
    if unbudgeted:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr:
            tr.write_line("")
            tr.write_line(f"GREEN-BY-SKIP: {len(unbudgeted)} skip(s) outside the budget "
                          "(set PYTEST_ALLOW_SKIPS=1 for local runs without the tooling):", red=True)
            for nid, reason in unbudgeted:
                tr.write_line(f"  {nid}: {reason}", red=True)
        session.exitstatus = 1
