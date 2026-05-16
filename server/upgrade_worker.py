#!/usr/bin/env python3
"""upgrade_worker.py — periodically upgrade pending .ots files to BTC-pinned versions.

OpenTimestamps protocol:
- POST /digest returns a calendar-pending proof immediately.
- After the calendar batches and writes to Bitcoin (~hourly), GET
  /timestamp/<hex-hash> returns the upgraded proof that includes the
  block attestation. A 404 means "still pending."

This worker is honest about what it does: it fetches the latest blob
from each calendar and writes it back. It does NOT parse the OTS
binary or independently confirm Bitcoin inclusion — that's what the
standalone verify_cli.py + `ots upgrade` is for. What we DO get from
storing the upgraded blob is a server-side hint that the proof is
no longer calendar-pending: that's what status="pinned" reflects.

Run via cron / launchd / scripts/upgrade_cron.sh.

Public API:
    upgrade_all(max_age_sec=3600, min_age_sec=3600) -> dict
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_lock import locked  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ORPHO_DATA_DIR", str(ROOT / "data") if (ROOT / "data").is_dir() else str(ROOT)))
RECEIPTS_DIR = Path(os.environ.get("ORPHO_RECEIPTS_DIR", str(DATA_DIR / "receipts")))
UPGRADE_LOG = Path(os.environ.get("ORPHO_UPGRADE_LOG", str(DATA_DIR / "upgrade_log.jsonl")))

# Matches engine.py header so an upgraded .ots stays well-formed.
OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_VERSION = b"\x01"
OTS_TAG_SHA256 = b"\x08"
HTTP_TIMEOUT_SEC = 15
USER_AGENT = "orphograph-upgrade/0.1 (stdlib)"


def _fetch_upgrade(calendar_url: str, hash_hex: str) -> tuple[bool, bytes | str]:
    url = calendar_url.rstrip("/") + "/timestamp/" + hash_hex
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/vnd.opentimestamps.v1",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return True, resp.read()
    except urllib.error.HTTPError as e:
        # 404 is the documented "still pending" signal — not an error.
        return False, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as e:
        return False, f"{type(e).__name__}"


def _build_ots(hash_hex: str, body: bytes) -> bytes:
    return OTS_HEADER_MAGIC + OTS_VERSION + OTS_TAG_SHA256 + bytes.fromhex(hash_hex) + body


def _log(event: dict) -> None:
    # flock so concurrent upgrade-cron runs across machines don't interleave lines.
    with locked(UPGRADE_LOG, mode="a", exclusive=True) as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def _calendar_short(url: str) -> str:
    return url.split("//", 1)[1].split(".", 1)[0]


def _upgrade_one(receipt_dir: Path, record: dict) -> dict:
    hash_hex = record["hash_hex"]
    upgrades: list[dict] = []
    for entry in record.get("successes", []):
        cal = entry["calendar"]
        ots_path = receipt_dir / (_calendar_short(cal) + ".ots")
        if not ots_path.exists():
            continue
        ok, body = _fetch_upgrade(cal, hash_hex)
        if not ok:
            upgrades.append({"calendar": cal, "pinned": False, "reason": str(body)})
            continue
        new_blob = _build_ots(hash_hex, body)
        old_blob = ots_path.read_bytes()
        if new_blob == old_blob:
            upgrades.append({"calendar": cal, "pinned": True, "changed": False})
            continue
        ots_path.write_bytes(new_blob)
        upgrades.append({"calendar": cal, "pinned": True, "changed": True})

    pinned_count = sum(1 for u in upgrades if u.get("pinned"))
    if pinned_count == len(record.get("successes", [])) and pinned_count > 0:
        status = "pinned"
    elif pinned_count > 0:
        status = "partial"
    else:
        status = "pending"

    record["status"] = status
    if pinned_count > 0 and not record.get("btc_pinned_at"):
        record["btc_pinned_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (receipt_dir / "receipt.json").write_text(json.dumps(record, indent=2))
    return {
        "receipt_id": record["receipt_id"],
        "status": status,
        "pinned_count": pinned_count,
        "upgrades": upgrades,
    }


def upgrade_all(min_age_sec: int = 3600) -> dict:
    """Walk receipts/, upgrade any whose .ots files are older than min_age_sec.

    Skips already-pinned receipts.
    """
    if not RECEIPTS_DIR.exists():
        return {"scanned": 0, "upgraded": 0, "skipped": 0, "results": []}
    now = time.time()
    scanned = 0
    upgraded = 0
    skipped = 0
    results = []
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
        if record.get("status") == "pinned":
            skipped += 1
            continue
        age = now - receipt_file.stat().st_mtime
        if age < min_age_sec:
            skipped += 1
            continue
        result = _upgrade_one(receipt_dir, record)
        results.append(result)
        if result["status"] in ("pinned", "partial"):
            upgraded += 1
    summary = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scanned": scanned,
        "upgraded": upgraded,
        "skipped": skipped,
        "results": results,
    }
    _log(summary)
    return summary


def main() -> int:
    summary = upgrade_all()
    sys.stdout.write(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2) + "\n")
    if summary["results"]:
        sys.stdout.write(f"{len(summary['results'])} receipt(s) attempted upgrade\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
