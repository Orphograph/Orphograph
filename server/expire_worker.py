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


def expire_old_free(days: int = EXPIRY_DAYS, dry_run: bool = False) -> dict:
    cutoff = time.time() - days * 86400
    scanned = 0
    expired = 0
    skipped_paid = 0
    skipped_fresh = 0
    expired_ids: list[str] = []

    if not RECEIPTS_DIR.exists():
        return {"scanned": 0, "expired": 0, "skipped_paid": 0, "skipped_fresh": 0, "expired_ids": []}

    for receipt_dir in sorted(RECEIPTS_DIR.iterdir()):
        if not receipt_dir.is_dir():
            continue
        receipt_file = receipt_dir / "receipt.json"
        if not receipt_file.exists():
            continue
        scanned += 1
        try:
            record = json.loads(receipt_file.read_text())
        except json.JSONDecodeError:
            continue
        source = record.get("source", "unknown")
        # Conservative: only prune things explicitly marked "free". Anything
        # else (paid, unknown legacy receipts) we keep — safer to err on
        # retention than on deletion.
        if not source.startswith("free"):
            skipped_paid += 1
            continue
        mtime = receipt_file.stat().st_mtime
        if mtime > cutoff:
            skipped_fresh += 1
            continue
        if dry_run:
            expired += 1
            expired_ids.append(record.get("receipt_id", receipt_dir.name))
            continue
        # Actually delete.
        rid = record.get("receipt_id", receipt_dir.name)
        shutil.rmtree(receipt_dir, ignore_errors=False)
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
