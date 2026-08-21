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


class _AgeUnknown(Exception):
    """created_at is present but unparseable. An unknown age never licenses
    deletion, so the caller counts an error and keeps the receipt."""


def _age_basis(record: dict, receipt_file: Path) -> tuple[float, str]:
    """Return (epoch_seconds, basis) for the receipt's age.

    The ToS promises pruning "30 days after creation", so `created_at` is the
    only field that means what the policy says. mtime is NOT creation time:
    upgrade_worker.py rewrites receipt.json on every OTS upgrade, which resets
    it and postpones expiry indefinitely. mtime is used ONLY when the field is
    absent (records predating it); a present-but-unparseable value raises
    _AgeUnknown. The basis is recorded per run.
    """
    created = record.get("created_at")
    if created is None:
        return receipt_file.stat().st_mtime, "mtime"
    if not isinstance(created, str) or not created:
        raise _AgeUnknown(repr(created))
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError as e:
        raise _AgeUnknown(created) from e
    # A naive value is read as UTC, not machine-local: the server writes UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp(), "created_at"


def expire_old_free(days: int = EXPIRY_DAYS, dry_run: bool = False) -> dict:
    cutoff = time.time() - days * 86400
    scanned = expired = skipped_paid = skipped_fresh = errors = orphans = 0
    bases: dict[str, int] = {}
    expired_ids: list[str] = []
    dir_exists = RECEIPTS_DIR.is_dir()

    for receipt_dir in (sorted(RECEIPTS_DIR.iterdir()) if dir_exists else []):
        if not receipt_dir.is_dir():
            continue
        receipt_file = receipt_dir / "receipt.json"
        if not receipt_file.exists():
            # A directory with no receipt.json is either foreign or a
            # half-deleted receipt from a failed rmtree. Loud, never silent.
            orphans += 1
            continue
        scanned += 1
        try:
            record = json.loads(receipt_file.read_text())
            if not isinstance(record, dict):
                raise ValueError("receipt.json is not an object")
            source = record.get("source", "unknown")
            # Conservative: only prune things explicitly marked "free".
            # Anything else (paid, unknown legacy, malformed/null source) is
            # kept — safer to err on retention than on deletion.
            if not isinstance(source, str) or not source.startswith("free"):
                skipped_paid += 1
                continue
            age_ts, basis = _age_basis(record, receipt_file)
            bases[basis] = bases.get(basis, 0) + 1
            if age_ts > cutoff:
                skipped_fresh += 1
                continue
            rid = record.get("receipt_id", receipt_dir.name)
            if not dry_run:
                shutil.rmtree(receipt_dir, ignore_errors=False)
            expired += 1
            expired_ids.append(rid)
        except Exception:
            # One bad receipt (unreadable, non-UTF-8, not an object, unknown
            # age, unremovable) must never abort the scan and lose the run's
            # log line — that is how a crashed run and a run that never
            # happened become indistinguishable.
            errors += 1
            continue

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "dry_run": dry_run,
        "scanned": scanned,
        "expired": expired,
        "skipped_paid": skipped_paid,
        "skipped_fresh": skipped_fresh,
        "errors": errors,
        "orphans": orphans,
        "clock_basis": bases,
        # Run provenance. Without it a laptop's healthy expiry_log reads as
        # evidence that production prunes. Logged even when the receipts dir
        # is missing, so a misconfigured prod run is visible, not silent.
        "host": socket.gethostname(),
        "receipts_dir": str(RECEIPTS_DIR),
        "receipts_dir_exists": dir_exists,
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
