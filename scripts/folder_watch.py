#!/usr/bin/env python3
"""folder_watch.py — watch a directory and anchor any new file dropped in.

Stdlib only. Cross-platform (Mac, Linux, Windows). Polls every N
seconds rather than relying on inotify/FSEvents, so it works
everywhere without dependencies.

For each file that appears in the watched directory:
  1. Compute SHA-256 + SHA-512 locally (matching the browser flow).
  2. POST the hashes to https://orphograph.com/api/anchor with the
     user's Creator-tier API key.
  3. Save the receipt JSON next to the original file as
     <filename>.orpho.json so it lives with the photo.
  4. Record the anchor in ~/.orpho/state.jsonl so we don't
     re-anchor files on restart.

The folder watcher is the killer feature for the Personal tier
that makes the recurring subscription make sense. The CLI is the
MVP; a native Mac menu-bar app is a follow-on.

Usage:
    python3 folder_watch.py /path/to/photos
    python3 folder_watch.py /path/to/photos --once   # single pass
    python3 folder_watch.py /path/to/photos --base https://staging.orphograph.com

Setup:
    1. Get a Creator-tier API key at https://orphograph.com/account.html
    2. echo "orpho_xxxxxxxxxxxxxxxxxxxxxxxx" > ~/.orpho/api_key
       chmod 600 ~/.orpho/api_key
       (or set ORPHO_API_KEY env var)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- config ----------------------------------------------------------------

HOME = Path.home()
ORPHO_DIR = HOME / ".orpho"
STATE_PATH = ORPHO_DIR / "state.jsonl"
KEY_FILE = ORPHO_DIR / "api_key"

DEFAULT_BASE = os.environ.get("ORPHO_API_BASE", "https://orphograph.com")
POLL_SEC = float(os.environ.get("ORPHO_POLL_SEC", "10"))
ALLOWED_EXT = {
    # photos
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".raw", ".cr2", ".cr3",
    ".nef", ".arw", ".dng", ".heic", ".webp", ".gif", ".bmp",
    # video
    ".mp4", ".mov", ".m4v", ".avi", ".mkv",
    # audio
    ".wav", ".mp3", ".flac", ".aac", ".m4a",
    # documents (occasional use)
    ".pdf",
}
SIZE_CAP_BYTES = 4 * 1024 * 1024 * 1024  # 4GB — sanity cap, not a server limit


# --- helpers ---------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_api_key() -> str:
    env = os.environ.get("ORPHO_API_KEY", "").strip()
    if env:
        return env
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip().splitlines()[0]
    return ""


def _ensure_state_dir() -> None:
    ORPHO_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ORPHO_DIR, 0o700)
    except OSError:
        pass


def _already_anchored(state_path: Path) -> set[str]:
    """Return the set of file paths we've already anchored, by reading
    the local state ledger."""
    out: set[str] = set()
    if not state_path.exists():
        return out
    with state_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            p = row.get("path")
            if p:
                out.add(p)
    return out


def _record_state(state_path: Path, row: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with state_path.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _hashes(path: Path) -> tuple[str, str, int]:
    s256, s512 = hashlib.sha256(), hashlib.sha512()
    n = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            s256.update(chunk)
            s512.update(chunk)
            n += len(chunk)
    return s256.hexdigest(), s512.hexdigest(), n


def _anchor(base: str, api_key: str, path: Path) -> dict:
    h256, h512, _ = _hashes(path)
    body = json.dumps({
        "hash_hex": h256,
        "sha512_hex": h512,
        "client_label": path.name,
    }).encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/api/anchor",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Orpho-Api-Key": api_key,
            "User-Agent": "orphograph-folder-watch/0.1",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _process(base: str, api_key: str, path: Path, state_path: Path,
             verbose: bool = True) -> tuple[bool, str]:
    """Anchor a single file and write the receipt next to it.
    Returns (ok, message). Idempotent: if .orpho.json already exists
    next to the file, we skip."""
    sidecar = path.with_suffix(path.suffix + ".orpho.json")
    if sidecar.exists():
        return True, "already has receipt sidecar; skipping"

    try:
        size = path.stat().st_size
    except OSError as e:
        return False, f"stat failed: {e}"

    if size == 0:
        return False, "empty file; skipping"
    if size > SIZE_CAP_BYTES:
        return False, f"file > {SIZE_CAP_BYTES // (1024*1024*1024)}GB; skipping"

    try:
        receipt = _anchor(base, api_key, path)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode("utf-8"))
            msg = err.get("error", str(e))
        except (json.JSONDecodeError, ValueError, OSError):
            msg = f"HTTP {e.code}"
        return False, msg
    except (urllib.error.URLError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"

    sidecar.write_text(json.dumps(receipt, indent=2))
    _record_state(state_path, {
        "ts": _now_iso(),
        "path": str(path.resolve()),
        "receipt_id": receipt.get("receipt_id"),
        "size_bytes": size,
        "calendars_ok": receipt.get("calendars_ok"),
        "calendars_total": receipt.get("calendars_total"),
    })
    if verbose:
        print(f"  anchored: {path.name} → {receipt.get('receipt_id')} "
              f"({receipt.get('calendars_ok')}/{receipt.get('calendars_total')} calendars)")
    return True, receipt.get("receipt_id", "?")


def _scan(folder: Path, allowed_ext: set[str], anchored: set[str]) -> list[Path]:
    """Find anchorable files in the folder that we haven't seen yet."""
    candidates: list[Path] = []
    try:
        with os.scandir(folder) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if entry.name.startswith("."):
                    continue
                if entry.name.endswith(".orpho.json"):
                    continue
                p = Path(entry.path)
                if p.suffix.lower() not in allowed_ext:
                    continue
                if str(p.resolve()) in anchored:
                    continue
                # also skip if sidecar already exists (idempotency)
                sidecar = p.with_suffix(p.suffix + ".orpho.json")
                if sidecar.exists():
                    continue
                candidates.append(p)
    except OSError as e:
        print(f"  scan error: {e}", file=sys.stderr)
        return []
    candidates.sort(key=lambda p: p.stat().st_mtime)  # oldest first
    return candidates


# --- CLI -------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description="Watch a folder and anchor new files to Bitcoin.")
    p.add_argument("folder", help="Directory to watch (non-recursive).")
    p.add_argument("--base", default=DEFAULT_BASE,
                   help=f"Orphograph base URL (default: {DEFAULT_BASE})")
    p.add_argument("--once", action="store_true",
                   help="Single pass — anchor anything new and exit.")
    p.add_argument("--poll", type=float, default=POLL_SEC,
                   help=f"Polling interval in seconds (default: {POLL_SEC})")
    p.add_argument("--quiet", action="store_true",
                   help="Only print errors.")
    p.add_argument("--ext", action="append", default=None,
                   help="Override file extensions (e.g. --ext .jpg --ext .raw). "
                        "By default we watch ~25 common photo/video types.")
    args = p.parse_args()

    _ensure_state_dir()
    api_key = _read_api_key()
    if not api_key:
        print(f"ERROR: no API key found. Either set ORPHO_API_KEY or write it to {KEY_FILE}",
              file=sys.stderr)
        print(f"Get a key at {args.base}/account.html (Creator-tier subscription required).",
              file=sys.stderr)
        return 2

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"ERROR: {folder} is not a directory", file=sys.stderr)
        return 2

    allowed = set(e.lower() if e.startswith(".") else "." + e.lower()
                  for e in (args.ext or ALLOWED_EXT))

    verbose = not args.quiet
    if verbose:
        print(f"orphograph-folder-watch: watching {folder}")
        print(f"  base: {args.base}")
        print(f"  poll: every {args.poll}s")
        print(f"  extensions: {len(allowed)} types ({sorted(allowed)[:5]}...)")
        print()

    while True:
        anchored = _already_anchored(STATE_PATH)
        candidates = _scan(folder, allowed, anchored)
        if candidates and verbose:
            print(f"[{_now_iso()}] found {len(candidates)} new file(s)")
        for c in candidates:
            ok, msg = _process(args.base, api_key, c, STATE_PATH, verbose=verbose)
            if not ok and verbose:
                print(f"  skipped: {c.name} — {msg}")
        if args.once:
            return 0
        try:
            time.sleep(args.poll)
        except KeyboardInterrupt:
            if verbose:
                print("\nstopping.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
