#!/usr/bin/env python3
"""Take named anonymous free receipts off the service, reversibly.

Every public page and API answer for a receipt (/r/<id>, /certificate/<id>,
/api/verify, the badge, the vault) is built from DATA_DIR/receipts/<id>/ on
each request. Moving that directory out of receipts/ therefore removes the
receipt everywhere at once. This script moves it into
DATA_DIR/quarantine/removed-<stamp>/receipts/<id>, never deletes, and prints
the command that puts it back.

It does not touch ledger.jsonl or any other ledger ("the books are never
mutated"), so /api/stats keeps counting the removed anchors. It cannot reach
what left the volume: the root hash committed to the calendars and to
Bitcoin (only the hash was ever sent), edge caches (purge with
scripts/cf_purge.sh or wait for them to expire), Fly volume snapshots, and
earlier backups.

Usage (on the app machine; a dry run unless --apply):

    python3 /app/scripts/remove_receipts.py --list-free-on 2026-10-03 [--except ID ...] [--show-ids]
    python3 /app/scripts/remove_receipts.py [--apply --created-on 2026-10-03] -- ID [ID ...]

Put "--" before the ids: an id may start with "-".

Refuses the whole run (exit 3) if any named receipt is not exactly
source "free", is private, carries an owner, account, notify address or
office signature, is labelled like the weekly office receipt, is on the
Standing Record's historical list, or (with --created-on) was not created on
that UTC date. Exit codes: 0 done or nothing to do, 2 bad arguments,
3 refused, 4 an id was busy (the upgrade worker held it; run again),
5 post-check failed or a move raised mid-run (the moves already made are
listed with their undo lines), 6 an id was not found. Prints ids and counts only,
never labels, hashes or addresses.

Stdlib only.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import socket
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ID_RE = re.compile(r"[A-Za-z0-9_-]{16}", re.ASCII)  # secrets.token_urlsafe(12)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
MAX_IDS = 8
LOCK_NAME = ".upgrade.lock"  # upgrade_worker's per-receipt lock file
REFUSE_FIELDS = ("owner_id", "account_id", "notify_email", "office_signature")
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _created_on(rec: dict) -> str:
    # created_at is written in UTC (engine.py), e.g. 2026-10-03T05:20:11+00:00.
    return str(rec.get("created_at", ""))[:10]


def _guard(rid: str, rec: object, created_on: str | None, historical: frozenset) -> str | None:
    if not isinstance(rec, dict):
        return "receipt.json is not an object"
    if rec.get("receipt_id") != rid:
        return "receipt_id field does not match directory name"
    if rec.get("source") != "free":
        return "source is not exactly 'free'"
    if rec.get("private"):
        return "receipt is private"
    for f in REFUSE_FIELDS:
        if rec.get(f) not in (None, ""):
            return f"receipt carries {f}"
    if str(rec.get("client_label") or "").startswith("weekly-"):
        return "labelled like the weekly office receipt"
    if rid in historical:
        return "id is on the Standing Record historical list"
    if created_on and _created_on(rec) != created_on:
        return f"created_at is not on {created_on} (UTC)"
    return None


def _lock(path: Path):
    """flock the receipt's .upgrade.lock without waiting. Returns (fd, created)
    or (None, created) when the upgrade worker holds it."""
    created = not path.exists()
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None, created
    return fd, created


def _is_lock_stub(d: Path) -> bool:
    try:
        return d.is_dir() and [p.name for p in d.iterdir()] == [LOCK_NAME]
    except OSError:
        return False


def _load_historical(server_dir: str) -> frozenset:
    sys.path.insert(0, server_dir)
    import standing_record  # noqa: E402
    ids = frozenset(standing_record.HISTORICAL_RECEIPT_IDS)
    if not ids:
        raise ImportError("empty list")
    return ids


def _receipts_dir(data: Path) -> Path:
    # Same resolution as engine.py and upgrade_worker.py.
    return Path(os.environ.get("ORPHO_RECEIPTS_DIR", str(data / "receipts")))


def list_free_on(receipts: Path, day: str, known: set, show_ids: bool) -> int:
    rows = []
    for d in sorted(receipts.iterdir()):
        try:
            rec = json.loads((d / "receipt.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("source") == "free" and _created_on(rec) == day:
            rows.append(rec)
    others = [r for r in rows if r.get("receipt_id") not in known]
    other_sources = Counter()
    for d in sorted(receipts.iterdir()):
        try:
            rec = json.loads((d / "receipt.json").read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("source") != "free" and _created_on(rec) == day:
            other_sources[str(rec.get("source") or "none").split(":", 1)[0]] += 1
    hours = Counter(str(r.get("created_at", ""))[11:13] for r in others)
    print(f"free receipts created on {day} (UTC): {len(rows)}")
    print(f"  of those, not in --except: {len(others)}")
    for h in sorted(hours):
        print(f"    hour {h}Z: {hours[h]}")
    print(f"receipts from other sources that day (not removable by this tool): "
          f"{sum(other_sources.values())} {dict(sorted(other_sources.items()))}")
    if show_ids:
        for r in others:
            print(f"  {r.get('receipt_id')}  {r.get('created_at')}  kind={r.get('kind', 'file')}"
                  f"  calendars_ok={r.get('calendars_ok')}  leaf_count={r.get('leaf_count')}")
    elif others:
        print("  (add --show-ids to list them: id, time, kind and counts only)")
    return 0


def run(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="remove_receipts.py",
                                 description="Move named anonymous free receipts into quarantine.")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--data-dir", default=os.environ.get("ORPHO_DATA_DIR", "/app/data"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--created-on", default=None, help="YYYY-MM-DD, UTC; required with --apply")
    ap.add_argument("--reason", default="accidental production anchors")
    ap.add_argument("--list-free-on", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--except", dest="except_ids", nargs="*", default=[])
    ap.add_argument("--show-ids", action="store_true")
    ap.add_argument("--server-dir", default=str(SERVER_DIR))
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2

    data = Path(a.data_dir)
    receipts = _receipts_dir(data)
    root = os.path.realpath(data)
    if os.path.commonpath([root, os.path.realpath(receipts)]) != root:
        print("refused: the receipts directory resolves outside --data-dir", file=sys.stderr)
        return 2
    if not receipts.is_dir():
        print("refused: no receipts directory under --data-dir", file=sys.stderr)
        return 2

    if a.list_free_on is not None:
        if not DATE_RE.fullmatch(a.list_free_on) or a.ids or a.apply:
            print("refused: --list-free-on takes one YYYY-MM-DD and no ids", file=sys.stderr)
            return 2
        bad = [i for i in a.except_ids if not ID_RE.fullmatch(i)]
        if bad:
            print(f"refused: {len(bad)} --except argument(s) are not receipt ids", file=sys.stderr)
            return 2
        return list_free_on(receipts, a.list_free_on, set(a.except_ids), a.show_ids)

    if not a.ids:
        print("refused: no receipt ids", file=sys.stderr)
        return 2
    if len(a.ids) > MAX_IDS:
        print(f"refused: more than {MAX_IDS} ids", file=sys.stderr)
        return 2
    bad = [i for i in a.ids if not ID_RE.fullmatch(i)]
    if bad:
        print(f"refused: {len(bad)} argument(s) are not receipt ids", file=sys.stderr)
        return 2
    if a.created_on is not None and not DATE_RE.fullmatch(a.created_on):
        print("refused: --created-on must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if a.apply and not a.created_on:
        print("refused: --apply needs --created-on YYYY-MM-DD", file=sys.stderr)
        return 2
    ids = list(dict.fromkeys(a.ids))
    try:  # fail closed: the guard list must load
        historical = _load_historical(a.server_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"refused: cannot load the Standing Record ids ({type(exc).__name__})", file=sys.stderr)
        return 2

    plan, refused, gone = [], [], []
    for rid in ids:
        rf = receipts / rid / "receipt.json"
        if not rf.exists():
            gone.append(rid)
            continue
        try:
            rec = json.loads(rf.read_text())
        except (OSError, ValueError):
            refused.append((rid, "receipt.json unreadable"))
            continue
        why = _guard(rid, rec, a.created_on, historical)
        if why:
            refused.append((rid, why))
        else:
            plan.append(rid)
    qroot = data / "quarantine"
    done, stubs, not_found = [], [], []
    for rid in gone:
        d = receipts / rid
        if _is_lock_stub(d):
            stubs.append(rid)
        elif d.exists():
            refused.append((rid, "directory without receipt.json; left alone"))
        elif qroot.is_dir() and any((q / "receipts" / rid / "receipt.json").exists()
                                    for q in qroot.iterdir()):
            done.append(rid)
        else:
            not_found.append(rid)
    for rid, why in refused:
        print(f"REFUSED {rid}: {why}")
    for rid in not_found:
        print(f"NOT FOUND {rid}: not in receipts/ and not in quarantine/")
    if refused:
        return 3  # all-or-nothing: one refusal applies nothing
    if not_found:
        return 6  # a mistyped id stops the run before anything moves
    for rid in plan:
        print(f"{'will move' if a.apply else 'would move'} {rid}")
    for rid in done:
        print(f"already removed {rid}")
    for rid in stubs:
        print(f"{'remove' if a.apply else 'would remove'} lock stub {rid}")
    if not a.apply:
        print("dry run: nothing changed (add --apply --created-on YYYY-MM-DD)")
        return 0
    if not plan and not stubs:
        print("nothing to do (0 changes)")
        return 0

    run_dir = qroot / f"removed-{_now()}-{os.getpid()}-{os.urandom(3).hex()}"
    qrec = run_dir / "receipts"
    qroot.mkdir(mode=0o700, exist_ok=True)
    run_dir.mkdir(mode=0o700)
    qrec.mkdir(mode=0o700)
    if os.stat(qrec).st_dev != os.stat(receipts).st_dev:
        print("refused: quarantine is on another device", file=sys.stderr)
        return 5
    moved, busy, stubs_removed = [], [], []
    failed = None
    try:
        for rid in plan:
            d = receipts / rid
            lock = d / LOCK_NAME
            fd, created = _lock(lock)
            failed = rid
            if fd is None:
                busy.append(rid)
                failed = None
                print(f"BUSY {rid}: the upgrade worker holds it; run again")
                continue
            try:
                os.rename(d, qrec / rid)
                moved.append(rid)
                failed = None
                print(f"moved {rid}")
            finally:
                os.close(fd)
                # A lock file this run created is root-owned under fly ssh. Left
                # in the receipt, or carried into quarantine and restored by
                # the undo, it would stop the upgrade worker (uid orpho) from
                # opening it. Remove it wherever it now is.
                if created:
                    (qrec / rid / LOCK_NAME if rid in moved else lock).unlink(missing_ok=True)
        for rid in stubs:
            (receipts / rid / LOCK_NAME).unlink()
            (receipts / rid).rmdir()
            stubs_removed.append(rid)
    except OSError as exc:
        _write_removal(run_dir, qrec, receipts, a, moved, busy, stubs_removed, failed)
        print(f"STOPPED: moving {failed or 'a lock stub'} raised {type(exc).__name__}; quarantine {run_dir}",
              file=sys.stderr)
        for rid in moved:
            print(f"  undo {rid}: {_undo(qrec / rid, receipts / rid)}")
        return 5
    _write_removal(run_dir, qrec, receipts, a, moved, busy, stubs_removed, None)
    leftover = [rid for rid in moved if (receipts / rid / "receipt.json").exists()
                or not (qrec / rid / "receipt.json").exists()]
    print(f"moved {len(moved)}  busy {len(busy)}  lock stubs removed {len(stubs)}  quarantine {run_dir}")
    for rid in moved:
        print(f"  undo {rid}: {_undo(qrec / rid, receipts / rid)}")
    if any((receipts / rid).exists() for rid in moved):
        print("  note: the upgrade worker recreated an empty lock stub for a moved id; "
              "run this again to clear it before any undo", file=sys.stderr)
    if leftover:
        print(f"POST-CHECK FAILED for {len(leftover)} id(s)", file=sys.stderr)
        return 5
    return 4 if busy else 0


def _undo(src: Path, dst: Path) -> str:
    # -T: never move the receipt INTO an existing directory at dst (a lock stub
    # the worker recreated); mv then fails instead of nesting it.
    return f"mv -T {src} {dst}"


def _write_removal(run_dir: Path, qrec: Path, receipts: Path, a, moved, busy, stubs, failed) -> None:
    (run_dir / "removal.json").write_text(json.dumps({
        "ts": _now(), "host": socket.gethostname(), "reason": a.reason,
        "created_on": a.created_on, "moved": moved, "busy": busy,
        "lock_stubs_removed": stubs, "failed": failed,
        "files": {rid: sorted(p.name for p in (qrec / rid).iterdir()) for rid in moved},
        "undo": [_undo(qrec / rid, receipts / rid) for rid in moved],
    }, indent=2))


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
