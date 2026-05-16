#!/usr/bin/env python3
"""orphograph_capture.py — capture-time provenance daemon (the $19 Creator tier).

Watches one or more folders (defaults: ~/Pictures, ~/Desktop, ~/Movies). When a
new file appears, computes SHA-256 + SHA-512 locally, POSTs the hash to
orphograph.com/api/anchor, and writes the resulting receipt next to the
original file as `<filename>.orpho.json`.

Privacy:
    The file's bytes never leave the user's machine. Only the SHA-256 and
    SHA-512 do. Filename inclusion is opt-in via --include-filename.

Architecture (per orphograph CLAUDE.md principle 6):
    Clean rewrite of capture-time provenance. No shared code with prior
    infra work. The OTS path used here is Orphograph's own /api/anchor
    endpoint — same engine that powers the website's drop-zone flow.

Stdlib only. Runs on macOS, Linux, Windows (cross-platform polling).

Usage:
    Foreground:  python3 capture/orphograph_capture.py --watch ~/Pictures
    Daemon:      launchctl load com.orphograph.capture.plist (see plist file)
    Status:      python3 capture/orphograph_capture.py --status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ─── Config ─────────────────────────────────────────────────────────────────
DEFAULT_ENDPOINT = "https://orphograph.com"
DEFAULT_POLL_INTERVAL = 5  # seconds
DEFAULT_MIN_FILE_AGE = 2   # don't hash files modified within last 2 seconds (still being written)
HTTP_TIMEOUT_SEC = 30
USER_AGENT = "orphograph-capture/0.1 (stdlib)"

# File extensions we consider capture-worthy. Empty set = all files.
# Defaulted to common photographer/journalist formats.
DEFAULT_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif", ".raw", ".nef", ".cr2",
    ".cr3", ".arw", ".dng", ".tiff", ".tif", ".webp", ".gif",
    ".mp4", ".mov", ".m4v", ".avi", ".mkv",
    ".mp3", ".m4a", ".wav", ".flac", ".aac",
    ".pdf", ".doc", ".docx", ".txt", ".md",
}

STATE_DIR = Path(os.environ.get("ORPHO_CAPTURE_STATE",
                                str(Path.home() / "Library" / "Application Support" / "Orphograph")))
SEEN_DB = STATE_DIR / "seen.jsonl"
LOG_FILE = STATE_DIR / "capture.log"


# ─── Logging ────────────────────────────────────────────────────────────────
def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ─── Hash + anchor (stdlib only) ────────────────────────────────────────────
def hash_file(path: Path) -> tuple[str, str]:
    s256, s512 = hashlib.sha256(), hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            s256.update(chunk)
            s512.update(chunk)
    return s256.hexdigest(), s512.hexdigest()


def anchor_hash(endpoint: str, hash_hex: str, sha512_hex: str,
                label: str, api_key: str) -> tuple[bool, dict]:
    body = {"hash_hex": hash_hex, "sha512_hex": sha512_hex, "client_label": label}
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/anchor",
        data=data, method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            raw = resp.read().decode()
            return True, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return False, json.loads(e.read().decode())
        except Exception:
            return False, {"status_code": e.code, "error": str(e)}
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


# ─── Seen-tracker (so we don't anchor the same file twice) ──────────────────
def _load_seen() -> set[str]:
    if not SEEN_DB.exists():
        return set()
    seen = set()
    try:
        with SEEN_DB.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    seen.add(row.get("path", ""))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return set()
    return seen


def _record_seen(path: Path, receipt_id: str, sha256_hex: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with SEEN_DB.open("a") as f:
        f.write(json.dumps({
            "path": str(path),
            "receipt_id": receipt_id,
            "sha256": sha256_hex,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, separators=(",", ":")) + "\n")


# ─── Receipt-sidecar writer ─────────────────────────────────────────────────
def _write_receipt_sidecar(file_path: Path, receipt: dict, endpoint: str) -> Path:
    """Save the receipt next to the original file as <filename>.orpho.json."""
    sidecar = file_path.with_name(file_path.name + ".orpho.json")
    payload = {
        "file": file_path.name,
        "receipt_id": receipt.get("receipt_id"),
        "receipt_url": f"{endpoint.rstrip('/')}/r/{receipt.get('receipt_id')}",
        "sha256": receipt.get("hash_hex"),
        "sha512": receipt.get("sha512_hex"),
        "anchored_at": receipt.get("created_at"),
        "calendars_ok": receipt.get("calendars_ok"),
        "calendars_total": receipt.get("calendars_total"),
        "captured_by": "orphograph-capture/0.1",
    }
    sidecar.write_text(json.dumps(payload, indent=2))
    return sidecar


# ─── Watcher loop ───────────────────────────────────────────────────────────
def scan_once(watch_dirs: list[Path], extensions: set[str], include_filename: bool,
              endpoint: str, api_key: str, min_age: int) -> dict:
    """One scan pass. Returns counts."""
    seen = _load_seen()
    counts = {"checked": 0, "skipped_seen": 0, "skipped_young": 0,
              "skipped_ext": 0, "anchored": 0, "failed": 0}
    now = time.time()
    for wd in watch_dirs:
        if not wd.exists() or not wd.is_dir():
            continue
        for entry in wd.iterdir():
            if not entry.is_file():
                continue
            counts["checked"] += 1
            # Filter by extension if set.
            if extensions and entry.suffix.lower() not in extensions:
                counts["skipped_ext"] += 1
                continue
            # Skip files still being written.
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if (now - mtime) < min_age:
                counts["skipped_young"] += 1
                continue
            # Skip sidecars + already-anchored.
            if entry.name.endswith(".orpho.json"):
                continue
            if str(entry) in seen:
                counts["skipped_seen"] += 1
                continue
            # Anchor.
            try:
                sha256, sha512 = hash_file(entry)
            except OSError as e:
                _log(f"hash failed for {entry}: {e}")
                counts["failed"] += 1
                continue
            label = entry.name if include_filename else ""
            ok, resp = anchor_hash(endpoint, sha256, sha512, label, api_key)
            if not ok:
                _log(f"anchor failed for {entry}: {resp.get('error', resp)}")
                counts["failed"] += 1
                continue
            rid = resp.get("receipt_id", "")
            _write_receipt_sidecar(entry, resp, endpoint)
            _record_seen(entry, rid, sha256)
            _log(f"anchored {entry.name} → {rid} ({resp.get('calendars_ok', 0)}/{resp.get('calendars_total', 0)} OTS)")
            counts["anchored"] += 1
    return counts


def watch_loop(watch_dirs: list[Path], extensions: set[str], include_filename: bool,
              endpoint: str, api_key: str, interval: int, min_age: int) -> None:
    _log(f"orphograph-capture starting; watching {len(watch_dirs)} dir(s), interval={interval}s")
    for wd in watch_dirs:
        _log(f"  watch: {wd}")
    while True:
        try:
            counts = scan_once(watch_dirs, extensions, include_filename,
                              endpoint, api_key, min_age)
            if counts["anchored"] > 0 or counts["failed"] > 0:
                _log(f"scan: {counts}")
        except KeyboardInterrupt:
            _log("interrupted, exiting")
            return
        except Exception as e:  # pragma: no cover — defensive
            _log(f"scan error: {type(e).__name__}: {e}")
        time.sleep(interval)


# ─── Status command ─────────────────────────────────────────────────────────
def status() -> dict:
    seen = _load_seen()
    total = len(seen)
    last_ts = None
    if SEEN_DB.exists():
        try:
            with SEEN_DB.open() as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        last_ts = json.loads(line).get("ts")
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
    return {
        "total_anchored": total,
        "last_anchor_at": last_ts,
        "state_dir": str(STATE_DIR),
        "log_file": str(LOG_FILE),
    }


# ─── CLI ────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="orphograph capture-time daemon")
    p.add_argument("--watch", action="append", default=[],
                   help="directory to watch (repeatable). Default: ~/Pictures, ~/Desktop")
    p.add_argument("--endpoint", default=os.environ.get("ORPHO_ENDPOINT", DEFAULT_ENDPOINT),
                   help=f"orphograph endpoint (default {DEFAULT_ENDPOINT})")
    p.add_argument("--api-key", default=os.environ.get("ORPHO_API_KEY", ""),
                   help="Orphograph API key (Creator tier subscription)")
    p.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL,
                   help=f"poll interval seconds (default {DEFAULT_POLL_INTERVAL})")
    p.add_argument("--min-age", type=int, default=DEFAULT_MIN_FILE_AGE,
                   help=f"skip files modified within last N seconds (default {DEFAULT_MIN_FILE_AGE})")
    p.add_argument("--include-filename", action="store_true",
                   help="include the filename in the anchor (default: off, privacy-preserving)")
    p.add_argument("--all-extensions", action="store_true",
                   help="anchor every file regardless of extension (default: photo/audio/doc only)")
    p.add_argument("--once", action="store_true",
                   help="run one scan pass and exit (for testing / cron use)")
    p.add_argument("--status", action="store_true",
                   help="print status JSON and exit")
    args = p.parse_args()

    if args.status:
        print(json.dumps(status(), indent=2))
        return 0

    watch = [Path(w).expanduser() for w in (args.watch or [])]
    if not watch:
        watch = [Path.home() / "Pictures", Path.home() / "Desktop"]

    extensions = set() if args.all_extensions else DEFAULT_EXTENSIONS

    if args.once:
        counts = scan_once(watch, extensions, args.include_filename,
                          args.endpoint, args.api_key, args.min_age)
        print(json.dumps(counts, indent=2))
        return 0

    try:
        watch_loop(watch, extensions, args.include_filename,
                  args.endpoint, args.api_key, args.interval, args.min_age)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
