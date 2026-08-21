#!/usr/bin/env python3
"""expire_worker.py — periodic pruning of free-tier receipts.

Per the ToS: free-tier receipts may be pruned 30 days after creation.
Paid-tier (Pack / Subscription) receipts NEVER expire.

The original receipt JSON + the 5 .ots files leaves with the user
(downloaded after anchoring), so pruning the server copy doesn't break
independent verification — the user can still run verify_cli.py
against their local copy + the public OTS calendars. What is lost is
the in-app `/api/verify/<id>` shortcut.

Run via cron / fly machines schedule. Idempotent and safe to run often.

Public API:
    expire_old_free(days: int = 30, dry_run: bool = False) -> dict
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
RECEIPTS_DIR = Path(os.environ.get("ORPHO_RECEIPTS_DIR", str(DATA_DIR / "receipts")))
EXPIRY_LOG = Path(os.environ.get("ORPHO_EXPIRY_LOG", str(DATA_DIR / "expiry_log.jsonl")))
EXPIRY_DAYS = int(os.environ.get("ORPHO_EXPIRY_DAYS", "30"))


def _log_event(event: dict) -> None:
    EXPIRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EXPIRY_LOG.open("a") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def _age_basis(record: dict, receipt_file: Path) -> tuple[float, str]:
    """Return (epoch_seconds, basis) for the receipt's age.

    The ToS promises pruning "30 days after creation", so `created_at` is the
    only field that means what the policy says. mtime is NOT creation time:
    upgrade_worker.py rewrites receipt.json on every OTS upgrade, which resets
    it and postpones expiry indefinitely. mtime survives only as a fallback for
    records predating the field, and the basis is recorded per run so a reader
    can tell which clock produced a given result.
    """
    created = record.get("created_at")
    if isinstance(created, str) and created:
        try:
            dt = datetime.fromisoformat(created)
        except ValueError:
            dt = None
        if dt is not None:
            # A naive legacy value is read as UTC, not as machine-local:
            # the server writes UTC, and guessing local would shift the
            # cutoff by the host's offset.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp(), "created_at"
    return receipt_file.stat().st_mtime, "mtime"


def expire_old_free(days: int = EXPIRY_DAYS, dry_run: bool = False) -> dict:
    cutoff = time.time() - days * 86400
    scanned = 0
    expired = 0
    skipped_paid = 0
    skipped_fresh = 0
    errors = 0
    bases: dict[str, int] = {}
    expired_ids: list[str] = []

    if not RECEIPTS_DIR.exists():
        return {"scanned": 0, "expired": 0, "skipped_paid": 0, "skipped_fresh": 0,
                "errors": 0, "clock_basis": {}, "expired_ids": []}

    for receipt_dir in sorted(RECEIPTS_DIR.iterdir()):
        if not receipt_dir.is_dir():
            continue
        receipt_file = receipt_dir / "receipt.json"
        if not receipt_file.exists():
            continue
        scanned += 1
        try:
            record = json.loads(receipt_file.read_text())
        except (json.JSONDecodeError, OSError):
            errors += 1
            continue
        source = record.get("source", "unknown")
        # Conservative: only prune things explicitly marked "free". Anything
        # else (paid, unknown legacy receipts) we keep — safer to err on
        # retention than on deletion.
        if not source.startswith("free"):
            skipped_paid += 1
            continue
        try:
            age_ts, basis = _age_basis(record, receipt_file)
        except OSError:
            errors += 1
            continue
        bases[basis] = bases.get(basis, 0) + 1
        if age_ts > cutoff:
            skipped_fresh += 1
            continue
        if dry_run:
            expired += 1
            expired_ids.append(record.get("receipt_id", receipt_dir.name))
            continue
        # Actually delete.
        rid = record.get("receipt_id", receipt_dir.name)
        try:
            shutil.rmtree(receipt_dir, ignore_errors=False)
        except OSError:
            # One unremovable receipt must not abort the scan and lose the
            # whole run's log line — that is how a crashed run and a run
            # that never happened become indistinguishable.
            errors += 1
            continue
        expired += 1
        expired_ids.append(rid)

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "dry_run": dry_run,
        "scanned": scanned,
        "expired": expired,
        "skipped_paid": skipped_paid,
        "skipped_fresh": skipped_fresh,
        "errors": errors,
        "clock_basis": bases,
        # Run provenance. Without it a laptop's healthy expiry_log reads as
        # evidence that production prunes, which is exactly how this stayed
        # unnoticed for 99 runs.
        "host": socket.gethostname(),
        "receipts_dir": str(RECEIPTS_DIR),
        "expired_ids": expired_ids,
    }
    _log_event(summary)
    return summary


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    summary = expire_old_free(dry_run=dry_run)
    sys.stdout.write(json.dumps({k: v for k, v in summary.items() if k != "expired_ids"}, indent=2) + "\n")
    if summary["expired"] and dry_run:
        sys.stdout.write(f"would expire {summary['expired']} receipt(s) — re-run without --dry-run to apply\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
