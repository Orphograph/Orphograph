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
                label: str, api_key: str,
                hardware_attestation: dict | None = None) -> tuple[bool, dict]:
    body = {"hash_hex": hash_hex, "sha512_hex": sha512_hex, "client_label": label}
    # Opt-in hardware attestation (--attest): a Secure-Enclave-held key's
    # signature over this hash (docs/HARDWARE_ATTESTATION_SPIKE.md). Only
    # added when actually produced — absent field keeps the wire shape
    # identical to every prior daemon.
    if hardware_attestation is not None:
        body["hardware_attestation"] = hardware_attestation
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


# ─── Seen-tracker (so we don't anchor the same content twice) ────────────────
def _load_seen() -> tuple[dict[str, dict], set[str]]:
    """Returns (by_path, pairs).

    by_path maps path -> its latest row, used as an mtime+size fast-skip so
    unchanged files are not re-hashed every pass. pairs holds "path|sha256"
    keys — the actual dedup identity. Keying on path alone meant a file
    EDITED in place was never re-anchored, silently dropping proof of every
    version after the first; content is what a receipt attests, so content
    is what dedup must key on."""
    by_path: dict[str, dict] = {}
    pairs: set[str] = set()
    if not SEEN_DB.exists():
        return by_path, pairs
    try:
        with SEEN_DB.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                path = row.get("path", "")
                if not path:
                    continue
                by_path[path] = row
                if row.get("sha256"):
                    pairs.add(f"{path}|{row['sha256']}")
    except OSError:
        return {}, set()
    return by_path, pairs


def _record_seen(path: Path, receipt_id: str, sha256_hex: str,
                 mtime: float | None = None, size: int | None = None) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with SEEN_DB.open("a") as f:
        f.write(json.dumps({
            "path": str(path),
            "receipt_id": receipt_id,
            "sha256": sha256_hex,
            "mtime": mtime,
            "size": size,
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
    # Hardware attestation rides in the sidecar only when the server echoed
    # it in the receipt — additive, shape-stable for every existing sidecar.
    if receipt.get("hardware_attestation"):
        payload["hardware_attestation"] = receipt["hardware_attestation"]
    sidecar.write_text(json.dumps(payload, indent=2))
    return sidecar


# ─── Watcher loop ───────────────────────────────────────────────────────────
def _make_hw_attestation(hash_hex: str) -> dict | None:
    """Opt-in Secure Enclave attestation for one hash. Honest degrade: any
    failure (non-macOS, no swiftc, no SE, denied) returns None and the file
    is anchored WITHOUT attestation — never faked, never blocked."""
    try:
        import orphograph_attest
        return orphograph_attest.make_attestation(hash_hex, log=_log)
    except Exception as e:  # noqa: BLE001 — attestation must never block anchoring
        _log(f"hw-attest unavailable ({type(e).__name__}: {e}); anchoring without attestation")
        return None


def scan_once(watch_dirs: list[Path], extensions: set[str], include_filename: bool,
              endpoint: str, api_key: str, min_age: int, *,
              attest: bool = False) -> dict:
    """One scan pass. Returns counts."""
    seen_by_path, seen_pairs = _load_seen()
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
                st = entry.stat()
            except OSError:
                continue
            if (now - st.st_mtime) < min_age:
                counts["skipped_young"] += 1
                continue
            # Skip sidecars + already-anchored content.
            if entry.name.endswith(".orpho.json"):
                continue
            prior = seen_by_path.get(str(entry))
            if (prior is not None and prior.get("mtime") == st.st_mtime
                    and prior.get("size") == st.st_size):
                # unchanged since last anchor — skip without re-hashing
                counts["skipped_seen"] += 1
                continue
            # Hash first: dedup keys on content, so an edited file (same
            # path, new bytes) gets a fresh anchor for the new version.
            try:
                sha256, sha512 = hash_file(entry)
            except OSError as e:
                _log(f"hash failed for {entry}: {e}")
                counts["failed"] += 1
                continue
            if f"{entry}|{sha256}" in seen_pairs:
                # same content already anchored (touch-only change, or an
                # old-format row without mtime) — refresh the row so the
                # next pass fast-skips without re-hashing
                counts["skipped_seen"] += 1
                _record_seen(entry, (prior or {}).get("receipt_id", ""),
                             sha256, st.st_mtime, st.st_size)
                continue
            # Anchor. Attestation is opt-in and best-effort: when it cannot
            # be produced the call falls back to the exact pre-existing shape.
            label = entry.name if include_filename else ""
            hw = _make_hw_attestation(sha256) if attest else None
            if hw is not None:
                ok, resp = anchor_hash(endpoint, sha256, sha512, label, api_key, hw)
            else:
                ok, resp = anchor_hash(endpoint, sha256, sha512, label, api_key)
            if not ok:
                _log(f"anchor failed for {entry}: {resp.get('error', resp)}")
                counts["failed"] += 1
                continue
            rid = resp.get("receipt_id", "")
            _write_receipt_sidecar(entry, resp, endpoint)
            _record_seen(entry, rid, sha256, st.st_mtime, st.st_size)
            _log(f"anchored {entry.name} → {rid} ({resp.get('calendars_ok', 0)}/{resp.get('calendars_total', 0)} OTS)")
            counts["anchored"] += 1
    return counts


def watch_loop(watch_dirs: list[Path], extensions: set[str], include_filename: bool,
              endpoint: str, api_key: str, interval: int, min_age: int, *,
              attest: bool = False) -> None:
    _log(f"orphograph-capture starting; watching {len(watch_dirs)} dir(s), interval={interval}s"
         + (" [hw-attest]" if attest else ""))
    for wd in watch_dirs:
        _log(f"  watch: {wd}")
    while True:
        try:
            counts = scan_once(watch_dirs, extensions, include_filename,
                              endpoint, api_key, min_age, attest=attest)
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
    _by_path, pairs = _load_seen()
    total = len(pairs)  # anchored versions, not just paths
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
    p.add_argument("--attest", action="store_true",
                   help="opt-in hardware attestation: sign each anchored hash with a "
                        "Secure-Enclave-held device key (macOS; degrades honestly — "
                        "anchors without attestation when unavailable)")
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
                          args.endpoint, args.api_key, args.min_age,
                          attest=args.attest)
        print(json.dumps(counts, indent=2))
        return 0

    try:
        watch_loop(watch, extensions, args.include_filename,
                  args.endpoint, args.api_key, args.interval, args.min_age,
                  attest=args.attest)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
