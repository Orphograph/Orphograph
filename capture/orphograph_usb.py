#!/usr/bin/env python3
"""orphograph_usb.py — USB provenance recorder.

Plug in a USB drive; every file on it is SHA-256/512 hashed LOCALLY and anchored
to Bitcoin via orphograph.com/api/anchor. The receipts are written to a
`.orphograph/` folder ON THE DRIVE — so the provenance **travels with the USB**.
Move the stick to another machine and its proofs move with it; verify there, or
offline with `verify_cli.py`, even if our service is gone.

Privacy: the file bytes never leave the machine — only the SHA-256/512 hashes do.
The on-drive index records relative paths (your data, on your own drive); the
filename is NOT sent to the server unless --include-names.

How it differs from orphograph_capture.py (the folder daemon):
  - recursive walk of a mounted volume (a USB is a tree, not one folder)
  - volume auto-detection (--volume NAME finds it under the OS mount root)
  - on-drive portable sidecar/index (.orphograph/ on the stick, not next to files)
  - content-hash dedup (files on a USB get moved/renamed; we key on bytes)
  - mount/unmount awareness (waits for the drive, resumes on re-insert)

Self-contained (mirrors orphograph_capture's anchor contract) so it can ship on
the drive itself. Stdlib only; macOS/Linux/Windows.

Usage:
    python3 capture/orphograph_usb.py --volume ORPHOGRAPH        # auto-find
    python3 capture/orphograph_usb.py --mount /Volumes/MYUSB
    python3 capture/orphograph_usb.py --mount /Volumes/MYUSB --once --dry-run
    python3 capture/orphograph_usb.py --mount /Volumes/MYUSB --status
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
DEFAULT_POLL_INTERVAL = 5     # seconds between scans while the drive is mounted
DEFAULT_MIN_FILE_AGE = 2      # don't hash a file modified within this many seconds
HTTP_TIMEOUT_SEC = 30
USER_AGENT = "orphograph-usb/0.1 (stdlib)"
ORPHO_DIR = ".orphograph"     # the on-drive provenance folder

# Filesystem/OS junk we never anchor (and never descend into).
SKIP_DIR_NAMES = {
    ORPHO_DIR, ".Spotlight-V100", ".Trashes", ".fseventsd", ".TemporaryItems",
    ".DocumentRevisions-V100", "System Volume Information", "$RECYCLE.BIN",
    "found.000",
}
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─── Mount discovery (cross-platform best-effort) ───────────────────────────
def mount_root() -> Path:
    if sys.platform == "darwin":
        return Path("/Volumes")
    if sys.platform.startswith("linux"):
        user = os.environ.get("USER", "")
        for cand in (f"/media/{user}", f"/run/media/{user}", "/media", "/mnt"):
            if Path(cand).is_dir():
                return Path(cand)
        return Path("/mnt")
    return Path("/Volumes")  # fallback


def find_volume(name: str) -> Path | None:
    """Resolve a volume by label under the OS mount root (e.g. 'ORPHOGRAPH')."""
    p = mount_root() / name
    return p if p.is_dir() else None


# ─── Hash + anchor (self-contained; mirrors orphograph_capture) ─────────────
def hash_file(path: Path) -> tuple[str, str]:
    """Return (sha256_hex, sha512_hex), streaming in 4MB chunks. Bytes stay local."""
    s256, s512 = hashlib.sha256(), hashlib.sha512()
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            s256.update(chunk)
            s512.update(chunk)
    return s256.hexdigest(), s512.hexdigest()


def anchor_hash(endpoint: str, hash_hex: str, sha512_hex: str,
                label: str, api_key: str) -> tuple[bool, dict]:
    """POST the hash to /api/anchor. Returns (ok, response_dict). On HTTP error
    the dict carries status_code so callers can detect rate-limits (429)."""
    body = {"hash_hex": hash_hex, "sha512_hex": sha512_hex, "client_label": label}
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    req = urllib.request.Request(
        endpoint.rstrip("/") + "/api/anchor", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return True, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode())
        except Exception:
            d = {"error": str(e)}
        d["status_code"] = e.code
        return False, d
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return False, {"error": f"{type(e).__name__}: {e}"}


def _is_rate_limited(resp: dict) -> bool:
    if resp.get("status_code") == 429:
        return True
    return "rate" in str(resp.get("error", "")).lower()


def fetch_proof_bundle(endpoint: str, rid: str, dest_dir: Path, api_key: str = "") -> bool:
    """Best-effort: download /api/receipt/<rid>.zip (receipt.json + the 5 .ots
    proofs) and extract into dest_dir/<rid>/, so the FULL proof verifies OFFLINE
    via verify_cli.py even with our service gone — the "provenance rides on the
    drive" promise. Returns True on success; failures are non-fatal (the index
    still records receipt_id + url for an on-demand fetch later)."""
    import io
    import zipfile
    url = endpoint.rstrip("/") + f"/api/receipt/{rid}.zip"
    headers = {"User-Agent": USER_AGENT}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                    timeout=HTTP_TIMEOUT_SEC) as resp:
            blob = resp.read()
        out = dest_dir / rid
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for member in z.namelist():
                # guard against zip-slip path traversal
                if member.startswith("/") or ".." in Path(member).parts:
                    continue
                z.extract(member, out)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout,
            OSError, zipfile.BadZipFile) as e:
        print(f"{_now()} proof-bundle fetch failed for {rid}: {type(e).__name__}", flush=True)
        return False


# ─── On-drive index (.orphograph/ travels with the stick) ───────────────────
def _orpho_paths(mount: Path) -> tuple[Path, Path, Path]:
    base = mount / ORPHO_DIR
    return base, base / "index.jsonl", base / "receipts"


def load_index(mount: Path) -> dict:
    """Map sha256 -> record from the on-drive index (content-keyed dedup)."""
    _, index_file, _ = _orpho_paths(mount)
    out: dict[str, dict] = {}
    if not index_file.exists():
        return out
    try:
        with index_file.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sha = row.get("sha256")
                if sha:
                    out[sha] = row  # last write wins (e.g. pending -> anchored)
    except OSError:
        pass
    return out


def _append_index(mount: Path, record: dict) -> None:
    base, index_file, _ = _orpho_paths(mount)
    base.mkdir(parents=True, exist_ok=True)
    with index_file.open("a") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


def _write_receipt(mount: Path, receipt: dict) -> None:
    base, _, receipts_dir = _orpho_paths(mount)
    rid = receipt.get("receipt_id")
    if not rid:
        return
    receipts_dir.mkdir(parents=True, exist_ok=True)
    (receipts_dir / f"{rid}.json").write_text(json.dumps(receipt, indent=2))


# ─── Walk ───────────────────────────────────────────────────────────────────
def walk_files(mount: Path):
    """Yield Paths of real files under the mount, pruning OS junk + .orphograph/."""
    for root, dirs, files in os.walk(mount):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES and not d.startswith("._")]
        for name in files:
            if name in SKIP_FILE_NAMES or name.startswith("._"):
                continue
            yield Path(root) / name


# ─── Scan ─────────────────────────────────────────────────────────────────--
def scan_once(mount: Path, *, endpoint: str, api_key: str, include_names: bool,
              extensions: set[str], min_age: int, dry_run: bool = False,
              fetch_proofs: bool = False, anchor_fn=anchor_hash,
              fetch_fn=fetch_proof_bundle) -> dict:
    """One recursive pass over the drive. Returns counts. Content-hash dedup;
    rate-limit aborts the pass cleanly so we don't hammer the API."""
    counts = {"checked": 0, "skipped_seen": 0, "skipped_young": 0,
              "skipped_ext": 0, "anchored": 0, "failed": 0, "rate_limited": 0,
              "dry_run": 0, "proofs_fetched": 0}
    index = load_index(mount)
    now = time.time()
    for entry in walk_files(mount):
        counts["checked"] += 1
        if extensions and entry.suffix.lower() not in extensions:
            counts["skipped_ext"] += 1
            continue
        try:
            if (now - entry.stat().st_mtime) < min_age:
                counts["skipped_young"] += 1
                continue
        except OSError:
            continue
        try:
            sha256, sha512 = hash_file(entry)
        except OSError as e:
            print(f"{_now()} hash failed {entry.name}: {e}", flush=True)
            counts["failed"] += 1
            continue
        prior = index.get(sha256)
        if prior and prior.get("status") == "anchored":
            counts["skipped_seen"] += 1
            continue
        try:
            relpath = str(entry.relative_to(mount))
        except ValueError:
            relpath = entry.name
        if dry_run:
            counts["dry_run"] += 1
            print(f"{_now()} [dry-run] would anchor {relpath} ({sha256[:12]}…)", flush=True)
            continue
        label = relpath if include_names else ""
        ok, resp = anchor_fn(endpoint, sha256, sha512, label, api_key)
        if not ok:
            if _is_rate_limited(resp):
                counts["rate_limited"] += 1
                print(f"{_now()} rate-limited — pausing this pass. Add --api-key "
                      f"(a paid pack/subscription) for high-volume drives.", flush=True)
                _append_index(mount, {"sha256": sha256, "sha512": sha512,
                                      "relpath": relpath, "status": "pending",
                                      "reason": "rate_limited", "ts": _now()})
                break  # stop the pass; retry next cycle
            counts["failed"] += 1
            print(f"{_now()} anchor failed {relpath}: {resp.get('error', resp)}", flush=True)
            _append_index(mount, {"sha256": sha256, "sha512": sha512,
                                  "relpath": relpath, "status": "failed",
                                  "reason": str(resp.get("error", resp))[:200], "ts": _now()})
            continue
        rid = resp.get("receipt_id", "")
        record = {
            "sha256": sha256, "sha512": sha512, "relpath": relpath,
            "receipt_id": rid,
            "receipt_url": f"{endpoint.rstrip('/')}/r/{rid}" if rid else "",
            "anchored_at": resp.get("created_at"),
            "calendars_ok": resp.get("calendars_ok"),
            "status": "anchored", "ts": _now(),
        }
        try:
            _write_receipt(mount, resp)
            _append_index(mount, record)
        except OSError as e:
            # The anchor succeeded even if the drive is read-only/full; surface it.
            print(f"{_now()} anchored {relpath} -> {rid} but could not write "
                  f"on-drive sidecar: {e}", flush=True)
        index[sha256] = record
        counts["anchored"] += 1
        print(f"{_now()} anchored {relpath} -> {rid} "
              f"({resp.get('calendars_ok', 0)}/{resp.get('calendars_total', 0)} OTS)", flush=True)
        # Pull the full verifiable proof bundle onto the drive (offline-verifiable).
        if fetch_proofs and rid:
            _, _, receipts_dir = _orpho_paths(mount)
            if fetch_fn(endpoint, rid, receipts_dir, api_key):
                counts["proofs_fetched"] += 1
    return counts


# ─── Watch loop (mount/unmount aware) ───────────────────────────────────────
def watch_loop(mount: Path, *, endpoint: str, api_key: str, include_names: bool,
               extensions: set[str], interval: int, min_age: int,
               fetch_proofs: bool = True) -> None:
    print(f"{_now()} orphograph-usb watching {mount} (every {interval}s)", flush=True)
    announced_missing = False
    while True:
        try:
            if not mount.is_dir():
                if not announced_missing:
                    print(f"{_now()} drive {mount} not mounted — waiting for insert…", flush=True)
                    announced_missing = True
                time.sleep(interval)
                continue
            announced_missing = False
            counts = scan_once(mount, endpoint=endpoint, api_key=api_key,
                               include_names=include_names, extensions=extensions,
                               min_age=min_age, fetch_proofs=fetch_proofs)
            if counts["anchored"] or counts["failed"] or counts["rate_limited"]:
                print(f"{_now()} scan: {counts}", flush=True)
        except KeyboardInterrupt:
            print(f"{_now()} interrupted, exiting", flush=True)
            return
        except Exception as e:  # pragma: no cover — defensive
            print(f"{_now()} scan error: {type(e).__name__}: {e}", flush=True)
        time.sleep(interval)


def status(mount: Path) -> dict:
    index = load_index(mount)
    anchored = [r for r in index.values() if r.get("status") == "anchored"]
    pending = [r for r in index.values() if r.get("status") in ("pending", "failed")]
    last = max((r.get("ts", "") for r in index.values()), default=None)
    return {
        "mount": str(mount), "mounted": mount.is_dir(),
        "anchored": len(anchored), "pending_or_failed": len(pending),
        "last_activity": last, "index": str(_orpho_paths(mount)[1]),
    }


# ─── CLI ────────────────────────────────────────────────────────────────────
def main() -> int:
    p = argparse.ArgumentParser(description="orphograph USB provenance recorder")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--mount", help="path to the mounted USB volume (e.g. /Volumes/MYUSB)")
    g.add_argument("--volume", help="volume label to auto-find under the OS mount root")
    p.add_argument("--endpoint", default=os.environ.get("ORPHO_ENDPOINT", DEFAULT_ENDPOINT))
    p.add_argument("--api-key", default=os.environ.get("ORPHOGRAPH_API_KEY", ""),
                   help="API key (paid pack/subscription) for high-volume drives")
    p.add_argument("--interval", type=int, default=DEFAULT_POLL_INTERVAL)
    p.add_argument("--min-age", type=int, default=DEFAULT_MIN_FILE_AGE)
    p.add_argument("--include-names", action="store_true",
                   help="send relative paths as the anchor label (default off, privacy-preserving)")
    p.add_argument("--all-extensions", action="store_true",
                   help="anchor every file (default: same media/doc set as the capture daemon)")
    p.add_argument("--once", action="store_true", help="one scan pass then exit")
    p.add_argument("--dry-run", action="store_true", help="hash + report, no anchoring/writes")
    p.add_argument("--no-proofs", action="store_true",
                   help="don't download the .ots proof bundle onto the drive (faster; index keeps the receipt URL for later)")
    p.add_argument("--status", action="store_true", help="print on-drive status JSON and exit")
    args = p.parse_args()

    if args.mount:
        mount = Path(args.mount).expanduser()
    else:
        found = find_volume(args.volume)
        if found is None:
            print(json.dumps({"error": f"volume {args.volume!r} not mounted under {mount_root()}"}))
            return 2
        mount = found

    if args.status:
        print(json.dumps(status(mount), indent=2))
        return 0

    # Reuse the folder daemon's default extension set when available; else a sane default.
    try:
        from orphograph_capture import DEFAULT_EXTENSIONS  # type: ignore
    except Exception:
        DEFAULT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".txt", ".md",
                              ".mp4", ".mov", ".docx", ".raw", ".dng"}
    extensions = set() if args.all_extensions else set(DEFAULT_EXTENSIONS)

    if args.once or args.dry_run:
        if not mount.is_dir():
            print(json.dumps({"error": f"{mount} not mounted"}))
            return 2
        counts = scan_once(mount, endpoint=args.endpoint, api_key=args.api_key,
                           include_names=args.include_names, extensions=extensions,
                           min_age=args.min_age, dry_run=args.dry_run,
                           fetch_proofs=(not args.no_proofs and not args.dry_run))
        print(json.dumps(counts, indent=2))
        return 0

    try:
        watch_loop(mount, endpoint=args.endpoint, api_key=args.api_key,
                   include_names=args.include_names, extensions=extensions,
                   interval=args.interval, min_age=args.min_age,
                   fetch_proofs=not args.no_proofs)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
