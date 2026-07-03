#!/usr/bin/env python3
"""orphograph_watch.py — sealed-folder daemon for Orphograph.

Watches a directory recursively. When a new or changed file has been stable
for one full scan interval, it computes SHA-256 + SHA-512 locally and anchors
the hashes via POST /api/anchor. The file's contents never leave the machine.

A receipt proves the file's exact bytes existed at anchor time — nothing more.
It does not prove authorship, ownership, or legal validity.

Usage:
    python3 orphograph_watch.py <dir> [--api-key K | --pack-token T]
                                [--base https://orphograph.com]
                                [--interval 30] [--once] [--dry-run]

Stdlib only. State lives in <dir>/.orphograph/ (state.json, receipts.jsonl).
"""

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

STATE_DIR_NAME = ".orphograph"
STATE_FILE = "state.json"
RECEIPTS_FILE = "receipts.jsonl"
CLIENT_LABEL = "watch-folder"
USER_AGENT = "orphograph-watch-folder/1.0"
MAX_BACKOFF = 15 * 60  # cap network-error backoff at 15 minutes

_stop = False


def _sigint(_signum, _frame):
    global _stop
    _stop = True
    print("[watch] interrupt received, finishing up...", flush=True)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(msg):
    print("[watch] %s" % msg, flush=True)


def scan_dir(root):
    """Return {relpath: (mtime, size)} for eligible files under root."""
    seen = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # skip .orphograph and any hidden directories
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue  # hidden files
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue  # vanished mid-scan
            if st.st_size == 0:
                continue  # zero-byte files
            rel = os.path.relpath(full, root)
            seen[rel] = (st.st_mtime, st.st_size)
    return seen


def hash_file(path):
    """Return (sha256_hex, sha512_hex) or None if unreadable."""
    h256 = hashlib.sha256()
    h512 = hashlib.sha512()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                h256.update(chunk)
                h512.update(chunk)
    except OSError as e:
        log("cannot read %s: %s" % (path, e))
        return None
    return h256.hexdigest(), h512.hexdigest()


def load_state(state_path):
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_state(state_path, state):
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, state_path)


def append_receipt(receipts_path, record):
    with open(receipts_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__("rate limited")
        self.retry_after = retry_after


class AnchorPaused(Exception):
    pass


def post_anchor(base, sha256_hex, sha512_hex, api_key=None, pack_token=None):
    """POST /api/anchor. Returns parsed JSON dict on 200.

    Raises RateLimited (429), AnchorPaused (503), or urllib errors."""
    body = json.dumps({
        "hash_hex": sha256_hex,
        "sha512_hex": sha512_hex,
        "client_label": CLIENT_LABEL,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    elif pack_token:
        headers["X-Pack-Token"] = pack_token
    req = urllib.request.Request(base.rstrip("/") + "/api/anchor",
                                 data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = 60.0
            try:
                payload = json.loads(e.read().decode("utf-8"))
                retry_after = float(payload.get("retry_after_seconds", 60))
            except (ValueError, TypeError):
                pass
            raise RateLimited(retry_after)
        if e.code == 503:
            raise AnchorPaused()
        raise


def run_scan(root, args, state, pause):
    """One scan pass. Mutates state; returns (anchored_count, new_pause).

    pause: {"until": epoch, "backoff": seconds} network/rate-limit gate."""
    state_dir = os.path.join(root, STATE_DIR_NAME)
    os.makedirs(state_dir, exist_ok=True)
    state_path = os.path.join(state_dir, STATE_FILE)
    receipts_path = os.path.join(state_dir, RECEIPTS_FILE)

    seen = scan_dir(root)
    pending = state.setdefault("_pending", {})  # rel -> (mtime, size) awaiting stability
    files = state.setdefault("files", {})       # rel -> [mtime, size, sha256]
    anchored = 0

    # drop pending/known entries for files that disappeared
    for rel in list(pending):
        if rel not in seen:
            del pending[rel]

    for rel, (mtime, size) in sorted(seen.items()):
        known = files.get(rel)
        if known and known[0] == mtime and known[1] == size:
            pending.pop(rel, None)
            continue  # unchanged since last anchor

        prev = pending.get(rel)
        stable_by_age = (time.time() - mtime) >= args.interval
        if prev != [mtime, size] and not stable_by_age:
            # fresh write — debounce one full interval before hashing
            pending[rel] = [mtime, size]
            log("observed %s (waiting one interval for stability)" % rel)
            continue

        # stable (unchanged across scans, or mtime older than one interval)
        full = os.path.join(root, rel)
        digests = hash_file(full)
        if digests is None:
            pending.pop(rel, None)
            continue
        sha256_hex, sha512_hex = digests
        if known and known[2] == sha256_hex:
            # touched but content identical — record new mtime, don't re-anchor
            files[rel] = [mtime, size, sha256_hex]
            pending.pop(rel, None)
            log("unchanged content, skipping re-anchor: %s" % rel)
            continue

        if args.dry_run:
            log("would anchor %s sha256=%s (dry-run, no request sent)"
                % (rel, sha256_hex))
            files[rel] = [mtime, size, sha256_hex]
            pending.pop(rel, None)
            anchored += 1
            continue

        if time.time() < pause["until"]:
            # still backing off — leave in pending for a later scan
            continue

        try:
            result = post_anchor(args.base, sha256_hex, sha512_hex,
                                 api_key=args.api_key,
                                 pack_token=args.pack_token)
        except RateLimited as e:
            pause["until"] = time.time() + e.retry_after
            log("rate limited (429); backing off %.0fs — %s stays queued"
                % (e.retry_after, rel))
            continue
        except AnchorPaused:
            pause["until"] = time.time() + 300
            log("anchoring paused server-side (503); retrying in 5min")
            continue
        except (urllib.error.URLError, OSError, ValueError) as e:
            pause["backoff"] = min(max(pause["backoff"] * 2, 30), MAX_BACKOFF)
            pause["until"] = time.time() + pause["backoff"]
            log("network error (%s); backing off %.0fs" % (e, pause["backoff"]))
            continue

        pause["backoff"] = 0  # success resets exponential backoff
        receipt_id = result.get("receipt_id", "")
        receipt_url = "https://orphograph.com/r/%s" % receipt_id
        append_receipt(receipts_path, {
            "ts": now_iso(),
            "path": rel,
            "sha256": sha256_hex,
            "receipt_id": receipt_id,
            "receipt_url": receipt_url,
        })
        files[rel] = [mtime, size, sha256_hex]
        pending.pop(rel, None)
        anchored += 1
        log("anchored %s -> %s (%d/%d calendars) %s"
            % (rel, receipt_id, result.get("calendars_ok", 0),
               result.get("calendars_total", 0), receipt_url))

    save_state(state_path, state)
    return anchored


def main():
    p = argparse.ArgumentParser(
        description="Watch a folder; anchor new/changed files on Orphograph.")
    p.add_argument("dir", help="directory to watch (recursively)")
    p.add_argument("--api-key", help="Orphograph subscription API key")
    p.add_argument("--pack-token", help="prepaid pack token")
    p.add_argument("--base", default="https://orphograph.com",
                   help="API base URL (default: https://orphograph.com)")
    p.add_argument("--interval", type=float, default=30,
                   help="scan interval seconds (default: 30)")
    p.add_argument("--once", action="store_true",
                   help="run one scan pass and exit (for cron)")
    p.add_argument("--dry-run", action="store_true",
                   help="hash and log, but never POST")
    args = p.parse_args()

    if args.api_key and args.pack_token:
        p.error("use --api-key or --pack-token, not both")
    if args.interval < 5:
        p.error("--interval must be >= 5 seconds")

    root = os.path.abspath(args.dir)
    if not os.path.isdir(root):
        p.error("not a directory: %s" % root)

    signal.signal(signal.SIGINT, _sigint)

    state_path = os.path.join(root, STATE_DIR_NAME, STATE_FILE)
    state = load_state(state_path)
    pause = {"until": 0.0, "backoff": 0}

    log("watching %s (interval %.0fs%s%s)"
        % (root, args.interval,
           ", once" if args.once else "",
           ", dry-run" if args.dry_run else ""))

    while not _stop:
        run_scan(root, args, state, pause)
        if args.once:
            break
        # sleep in 1s slices so SIGINT exits promptly
        deadline = time.time() + args.interval
        while not _stop and time.time() < deadline:
            time.sleep(1)

    log("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
