#!/usr/bin/env python3
"""upgrade_worker.py — periodically upgrade pending .ots files to BTC-pinned versions.

OpenTimestamps protocol:
- POST /digest returns a calendar-pending proof immediately.
- After the calendar batches and writes to Bitcoin (~hourly), GET
  /timestamp/<hex-hash> returns the upgraded proof that includes the
  block attestation. A 404 means "still pending."

What this worker establishes, and no more: a calendar's /timestamp body is
accepted only when it parses as one well-formed OpenTimestamps timestamp
carrying a Bitcoin attestation (server/ots_timestamp.py, reference size
caps included), and a stored proof with no pending marker counts as pinned
only when it parses the same way. It does NOT replay the ops against a
Bitcoin block header — that needs a node and is what verify_cli.py /
`ots verify` are for. status="pinned" means "this proof carries a
Bitcoin attestation", never "we confirmed inclusion".

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

# After this many consecutive eligible runs that make NO forward progress (no
# calendar newly pinned), stop re-fetching a stuck receipt. A pool calendar
# whose commitment digest permanently 404s would otherwise be re-queried on
# every cron run forever — burning thousands of calls and growing the log
# unbounded. verify_cli.py stays the authoritative Bitcoin-inclusion check;
# freezing only halts the wasteful polling. Set 0 to disable; clear
# `upgrade_frozen` on a record to resume it.
MAX_UPGRADE_STALLS = int(os.environ.get("ORPHO_MAX_UPGRADE_STALLS", "24"))
# Bound the append-only upgrade log: rotate to a single .1 backup past this
# size so a long-stuck backlog can't grow it without limit.
UPGRADE_LOG_MAX_BYTES = int(os.environ.get("ORPHO_UPGRADE_LOG_MAX_BYTES", str(5 * 1024 * 1024)))

# Matches engine.py header so an upgraded .ots stays well-formed.
OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"
OTS_VERSION = b"\x01"
OTS_TAG_SHA256 = b"\x08"
# Magic bytes that begin a calendar-pending attestation inside an .ots blob.
# The calendar's commitment digest is the running hash AT this point in the
# op-chain, not the user's original hash. /timestamp/<digest> on the calendar
# is keyed by THIS hash; querying with the original SHA-256 returns 404 forever.
PENDING_ATTESTATION_MARKER = b"\x00\x83\xdf\xe3\x0d\x2e\xf9\x0c\x8e"
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


import ots_timestamp  # noqa: E402
from ots_timestamp import proof_verdict, read_varint, timestamp_verdict  # noqa: E402

# Re-exported: tests and callers address the tag through this module.
BITCOIN_ATTESTATION_TAG = ots_timestamp.BITCOIN_ATTESTATION_TAG


def calendar_body_verdict(body: bytes) -> tuple[bool, str]:
    """A /timestamp body may replace a proof only if it is one well-formed
    timestamp with a Bitcoin attestation (see ots_timestamp.timestamp_verdict)."""
    return timestamp_verdict(body, require_bitcoin=True)


def stored_proof_verdict(blob: bytes) -> tuple[bool, str]:
    """A stored proof with no pending marker is 'pinned' only if it parses
    as a Bitcoin-attested timestamp — not merely because the marker is gone."""
    return proof_verdict(blob, require_bitcoin=True)


def _commitment_for_pending(ots_blob: bytes) -> tuple[str | None, int]:
    """Walk the op-chain in an .ots blob up to its pending-attestation marker.

    Returns (commitment_hex, marker_index). commitment_hex is None when the
    blob is malformed or already upgraded (no pending marker remaining).
    """
    import hashlib
    if not ots_blob.startswith(OTS_HEADER_MAGIC):
        return None, -1
    i = len(OTS_HEADER_MAGIC) + len(OTS_VERSION)  # past header + version byte
    if ots_blob[i:i + 1] != OTS_TAG_SHA256:
        return None, -1
    i += 1
    cur = ots_blob[i:i + 32]
    i += 32
    marker_idx = ots_blob.find(PENDING_ATTESTATION_MARKER, i)
    if marker_idx < 0:
        return None, -1
    while i < marker_idx:
        op = ots_blob[i]
        i += 1
        if op == 0xf0:  # OP_APPEND
            ln, i = read_varint(ots_blob, i)
            cur = cur + ots_blob[i:i + ln]
            i += ln
        elif op == 0xf1:  # OP_PREPEND
            ln, i = read_varint(ots_blob, i)
            cur = ots_blob[i:i + ln] + cur
            i += ln
        elif op == 0x08:  # OP_SHA256
            cur = hashlib.sha256(cur).digest()
        else:
            return None, -1
    return cur.hex(), marker_idx


def _rotate_log_if_needed() -> None:
    """Rotate the upgrade log to a single .1 backup once it exceeds the size
    cap. Best-effort: any OSError leaves the current log in place — logging
    must never crash the worker."""
    if UPGRADE_LOG_MAX_BYTES <= 0:
        return
    try:
        if UPGRADE_LOG.exists() and UPGRADE_LOG.stat().st_size >= UPGRADE_LOG_MAX_BYTES:
            os.replace(UPGRADE_LOG, UPGRADE_LOG.with_suffix(UPGRADE_LOG.suffix + ".1"))
    except OSError:
        pass


def _log(event: dict) -> None:
    # flock so concurrent upgrade-cron runs across machines don't interleave lines.
    _rotate_log_if_needed()
    with locked(UPGRADE_LOG, mode="a", exclusive=True) as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def _calendar_short(url: str) -> str:
    return url.split("//", 1)[1].split(".", 1)[0]


def _send_pin_email_if_needed(record: dict) -> None:
    """Fire transactional pin-notification email exactly once per receipt.

    Trigger conditions (all required):
      - record has a notify_email opted-in by the customer at anchor time
      - btc_pinned_at was just set on this run (the transition itself)
      - pin_email_sent_at is not already on the record (idempotency)
      - Resend API returns 2xx; otherwise we log to stderr and leave
        pin_email_sent_at unset so the next worker run can retry.

    Crashes/exceptions from the mailer are swallowed — credit-grant
    integrity beats notification. Pin-email is best-effort.
    """
    notify_email = record.get("notify_email")
    if not isinstance(notify_email, str) or not notify_email.strip():
        return
    if record.get("pin_email_sent_at"):
        return
    if not record.get("btc_pinned_at"):
        return
    # Lazy-import the mailer so unit tests that don't exercise email can
    # avoid module-load side effects (Resend env, etc.).
    try:
        import mailer  # type: ignore
    except ImportError:
        sys.stderr.write("[upgrade:pin_email] mailer import failed\n")
        return
    try:
        ok = mailer.send_pin_email(notify_email.strip(), record)
    except Exception as e:  # noqa: BLE001 — never crash the upgrade worker
        sys.stderr.write(f"[upgrade:pin_email] {type(e).__name__}: {e}\n")
        return
    if ok:
        record["pin_email_sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        # Afterglow follow-up: "what you can do with your receipt now" rides
        # the pin moment (hours after anchoring — the habit-formation window).
        # FOUNDER-GATED: sends only when ORPHO_INTEGRATION_EMAIL is set.
        if os.environ.get("ORPHO_INTEGRATION_EMAIL") and not record.get("integration_email_sent_at"):
            try:
                if mailer.send_integration_email(notify_email.strip(), record):
                    record["integration_email_sent_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            except Exception as e:  # noqa: BLE001 — never crash the upgrade worker
                sys.stderr.write(f"[upgrade:integration_email] {type(e).__name__}: {e}\n")
    # Webhook dispatch for `anchor.btc_pinned`. Done after the email
    # send so an email-side failure does not block notifying integrators
    # — and done only on the same transition the email path uses, so
    # both surfaces fire exactly once.
    try:
        import webhooks  # type: ignore
        site_url = os.environ.get("SITE_URL", "https://orphograph.com").rstrip("/")
        rid = record.get("receipt_id", "")
        webhooks.dispatch("anchor.btc_pinned", notify_email.strip(), {
            "receipt_id": rid,
            "hash_hex": record.get("hash_hex"),
            "btc_pinned_at": record.get("btc_pinned_at"),
            "pinned_count": int(record.get("pinned_count", 0)),
            "pinned_total": int(record.get("pinned_total", 0)),
            "status": record.get("status"),
            "receipt_url": f"{site_url}/r/{rid}",
        })
    except Exception as e:  # noqa: BLE001 — never crash the upgrade worker
        sys.stderr.write(f"[upgrade:webhook] {type(e).__name__}: {e}\n")


def _upgrade_one(receipt_dir: Path, record: dict) -> dict:
    # Snapshot pre-state so we can detect the pending→pinned/partial
    # transition AFTER calendars have been queried. Email fires once on
    # the transition, not on subsequent runs.
    was_pinned_before = bool(record.get("btc_pinned_at"))
    upgrades: list[dict] = []
    for entry in record.get("successes", []):
        cal = entry["calendar"]
        ots_path = receipt_dir / (_calendar_short(cal) + ".ots")
        if not ots_path.exists():
            continue
        old_blob = ots_path.read_bytes()
        commitment_hex, marker_idx = _commitment_for_pending(old_blob)
        if commitment_hex is None:
            # No pending marker. That is what an upgraded proof looks like —
            # and also what a proof that had garbage spliced into it looks
            # like. Parse the stored bytes and let THAT decide; a blob that
            # is not a Bitcoin-attested timestamp is not pinned, whatever
            # the marker says.
            ok, why = stored_proof_verdict(old_blob)
            if ok:
                upgrades.append({"calendar": cal, "pinned": True, "changed": False})
            else:
                upgrades.append({"calendar": cal, "pinned": False,
                                 "reason": f"stored proof malformed: {why}"})
            continue
        ok, body = _fetch_upgrade(cal, commitment_hex)
        if not ok:
            upgrades.append({"calendar": cal, "pinned": False, "reason": str(body)})
            continue
        # The body replaces the customer's proof bytes and decides "pinned":
        # parse it before either happens. An unparseable or still-pending
        # 200 is a non-event for this receipt, not an upgrade.
        valid, why = calendar_body_verdict(body)
        if not valid:
            upgrades.append({"calendar": cal, "pinned": False, "reason": why})
            continue
        new_blob = old_blob[:marker_idx] + body
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
    # Pin counters live alongside btc_pinned_at on the record. calendars_ok
    # on the original receipt reflects ACCEPTANCE at anchor time; these new
    # fields reflect Bitcoin PIN confirmation, which can be a strict subset.
    record["pinned_count"] = pinned_count
    record["pinned_total"] = len(record.get("successes", []))
    malformed = sorted(_calendar_short(u["calendar"]) for u in upgrades
                       if str(u.get("reason", "")).startswith("stored proof malformed"))
    if malformed:
        record["proof_malformed"] = malformed
    else:
        record.pop("proof_malformed", None)
    # Stall/freeze accounting. A receipt stuck at pending/partial because a
    # pool calendar's commitment permanently 404s would otherwise be re-fetched
    # on every run forever. Count consecutive eligible runs that make no forward
    # progress (no calendar's blob changed this run); once the ceiling is hit,
    # freeze it so upgrade_all() skips it like a pinned receipt. This touches
    # only polling cadence — never the proof bytes or the commitment walk.
    progressed = any(u.get("changed") for u in upgrades)
    record["upgrade_attempts"] = int(record.get("upgrade_attempts", 0) or 0) + 1
    if status == "pinned" or progressed:
        record["upgrade_stalls"] = 0
    else:
        record["upgrade_stalls"] = int(record.get("upgrade_stalls", 0) or 0) + 1
    if (MAX_UPGRADE_STALLS > 0 and status != "pinned"
            and record["upgrade_stalls"] >= MAX_UPGRADE_STALLS
            and not record.get("upgrade_frozen")):
        record["upgrade_frozen"] = True
        record["upgrade_frozen_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record["upgrade_frozen_reason"] = (
            f"no Bitcoin-pin progress after {record['upgrade_stalls']} attempts; "
            "polling halted (verify_cli remains authoritative)"
        )
    # Email the customer exactly on the pending→pinned/partial transition.
    # was_pinned_before guards against re-sending if btc_pinned_at was
    # already populated on a prior run. _send_pin_email_if_needed also
    # checks pin_email_sent_at for belt-and-suspenders idempotency.
    if not was_pinned_before and record.get("btc_pinned_at"):
        _send_pin_email_if_needed(record)
    (receipt_dir / "receipt.json").write_text(json.dumps(record, indent=2))
    return {
        "receipt_id": record["receipt_id"],
        "status": status,
        "pinned_count": pinned_count,
        "stalls": record.get("upgrade_stalls", 0),
        "frozen": bool(record.get("upgrade_frozen")),
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
        if record.get("status") == "pinned" or record.get("upgrade_frozen"):
            # Pinned = done. Frozen = a permanently-stuck partial we've stopped
            # polling (see MAX_UPGRADE_STALLS) so it can't burn calls forever.
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
