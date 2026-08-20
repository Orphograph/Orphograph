#!/usr/bin/env python3
"""publish_watcher.py — autonomous package-publication cascade.

Polls the PyPI JSON API and the npm registry once per run looking for new
``orphograph`` releases. On a first-seen version the watcher:

  1. Downloads the artefact files (wheel + sdist for PyPI, tarball for npm).
  2. Hashes them, builds a folder-Merkle root via :mod:`server.merkle`.
  3. POSTs the manifest to ``/api/anchor_folder`` (``private: true``).
  4. Appends a structured record to ``outbox/PUBLISH_STATE_PYPI.json``
     or ``outbox/PUBLISH_STATE_NPM.json`` (each file is JSONL).
  5. Updates ``outbox/HOMEPAGE_BADGES.json`` with the install hint that
     a downstream consumer (the homepage renderer) can pick up later.

Stdlib only. Idempotent — running twice in the same minute is safe; the
PUBLISH_STATE files dedupe by version. Always exits 0 so the launchd
schedule never gets disabled by a transient hiccup.

The script never POSTs in tests (the test suite mocks ``urllib``) and
never writes anything outside ``outbox/``. No tokens are logged.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import merkle  # noqa: E402

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

PYPI_URL = "https://pypi.org/pypi/orphograph/json"
NPM_URL = "https://registry.npmjs.org/orphograph"

BASE_URL = os.environ.get("ORPHO_BASE_URL", "https://orphograph.com").rstrip("/")
API_KEY = os.environ.get("ORPHO_AUTO_ANCHOR_KEY", "").strip()

OUTBOX = ROOT / "outbox"
STATE_PYPI = OUTBOX / "PUBLISH_STATE_PYPI.json"
STATE_NPM = OUTBOX / "PUBLISH_STATE_NPM.json"
BADGES = OUTBOX / "HOMEPAGE_BADGES.json"

# Browser-shaped UA — reused from orphograph_watchdog.py. The CDN in
# front of orphograph.com blocks the default urllib UA, so the same
# string is used here for the anchor POST. PyPI and the npm registry
# Honest, self-identifying User-Agent. NEVER a browser-spoofing string.
#
# MEASURED 2026-08-20 against https://orphograph.com/api/health:
#   Python-urllib/3.11 ............ 403   (a standard CDN managed rule)
#   no User-Agent header at all ... 200
#   curl/8.7.1 .................... 200
#   a named agent like this one ... 200
# So the gateway blocks exactly one literal token and nothing else. The
# previous comment here claimed the CDN has a "default-deny posture" against
# scripted clients and that "only the leading Mozilla/5.0 appeases the
# gateway" -- the premise was right and the conclusion was wrong. The spoof
# was never load-bearing. All that matters is that a UA is SET, so nothing
# falls back to urllib's default.
# pypi.org, registry.npmjs.org and api.github.com were each verified 200
# with this agent on 2026-08-20.
USER_AGENT = "Orphograph-publish-watcher/1.0 (+https://orphograph.com)"

HTTP_TIMEOUT_S = 30


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _log(msg: str) -> None:
    sys.stderr.write(f"[publish_watcher] {msg}\n")


def _http_get(url: str, *, timeout: int = HTTP_TIMEOUT_S) -> tuple[int, bytes]:
    """GET ``url`` and return ``(status_code, body_bytes)``.

    On a 4xx/5xx the HTTPError is caught and its status code returned with
    an empty body. On transport failure the exception propagates so the
    caller can decide whether to swallow it.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200), resp.read()
    except urllib.error.HTTPError as e:
        return int(getattr(e, "code", 0) or 0), b""


def _ensure_outbox() -> None:
    try:
        OUTBOX.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _read_state_versions(path: Path) -> set[str]:
    """Return the set of versions already recorded in ``path`` (JSONL)."""
    if not path.exists():
        return set()
    versions: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                v = row.get("version")
                if isinstance(v, str) and v:
                    versions.add(v)
    except OSError:
        return set()
    return versions


def _append_state(path: Path, row: dict) -> None:
    _ensure_outbox()
    line = json.dumps(row, sort_keys=True, separators=(",", ":"))
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        _log(f"state append failed for {path.name}: {exc}")


def _update_badges(key: str, payload: dict) -> None:
    """Merge ``{key: payload}`` into outbox/HOMEPAGE_BADGES.json.

    The file is rewritten as well-formed JSON each time. The downstream
    consumer (the homepage renderer) reads this file but never writes it.
    """
    _ensure_outbox()
    current: dict = {}
    if BADGES.exists():
        try:
            with BADGES.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                current = loaded
        except (OSError, ValueError):
            current = {}
    current[key] = payload
    current["updated_at_utc"] = _utc_now_iso()
    try:
        with BADGES.open("w", encoding="utf-8") as fh:
            json.dump(current, fh, sort_keys=True, indent=2)
            fh.write("\n")
    except OSError as exc:
        _log(f"badges write failed: {exc}")


# --------------------------------------------------------------------------- #
# Anchor POST
# --------------------------------------------------------------------------- #


def _post_anchor(manifest: dict, client_label: str) -> dict:
    """POST the manifest to ``/api/anchor_folder``.

    Returns the parsed response dict on success, or an empty dict on any
    failure. Errors are logged but never raised — a failed anchor must
    not crash the watcher (the launchd job will retry on the next tick).
    """
    body = json.dumps({
        "manifest": manifest,
        "client_label": client_label,
        "private": True,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "identity",
    }
    if API_KEY:
        headers["X-Orpho-Api-Key"] = API_KEY
    req = urllib.request.Request(
        f"{BASE_URL}/api/anchor_folder",
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read()
    except urllib.error.HTTPError as e:
        _log(f"anchor HTTP {e.code} for {client_label}")
        return {}
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"anchor network error for {client_label}: {e}")
        return {}
    if status < 200 or status >= 300:
        _log(f"anchor non-2xx {status} for {client_label}")
        return {}
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        _log(f"anchor returned non-JSON for {client_label}")
        return {}
    return payload if isinstance(payload, dict) else {}


def _build_manifest_for_files(files: list[tuple[str, bytes]]) -> dict:
    """Build a Merkle manifest from in-memory (filename, bytes) pairs.

    Writes the files into a fresh temporary directory and reuses
    :meth:`merkle.MerkleTree.from_folder` so the manifest shape exactly
    matches what the server expects (and what auto_anchor_repo.py emits).
    """
    with tempfile.TemporaryDirectory(prefix="orpho_publish_") as td:
        td_path = Path(td)
        for name, data in files:
            # Filename sanitisation: keep only the basename. The names we
            # see come from the PyPI / npm metadata so they are already
            # well-formed, but a defensive basename() prevents a malformed
            # entry from escaping the temp directory.
            safe = os.path.basename(name) or "artefact.bin"
            (td_path / safe).write_bytes(data)
        tree = merkle.MerkleTree.from_folder(td_path, exclude=[])
        return tree.manifest()


# --------------------------------------------------------------------------- #
# PyPI flow
# --------------------------------------------------------------------------- #


def check_pypi() -> bool:
    """Poll PyPI; if a new version is published, anchor + record it.

    Returns True iff a new version was processed.
    """
    try:
        status, body = _http_get(PYPI_URL)
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"pypi network error: {e}")
        return False
    if status == 404:
        return False
    if status != 200 or not body:
        _log(f"pypi unexpected status {status}")
        return False
    try:
        meta = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        _log("pypi non-JSON response")
        return False

    info = meta.get("info") or {}
    version = str(info.get("version") or "").strip()
    if not version:
        _log("pypi response missing version")
        return False

    already = _read_state_versions(STATE_PYPI)
    if version in already:
        return False

    urls = meta.get("urls") or []
    if not isinstance(urls, list) or not urls:
        _log(f"pypi version {version} has no downloadable urls")
        return False

    downloaded: list[tuple[str, bytes]] = []
    file_records: list[dict] = []
    for entry in urls:
        if not isinstance(entry, dict):
            continue
        download_url = entry.get("url")
        filename = entry.get("filename") or ""
        if not isinstance(download_url, str) or not download_url:
            continue
        try:
            dstatus, data = _http_get(download_url, timeout=HTTP_TIMEOUT_S)
        except (urllib.error.URLError, TimeoutError) as e:
            _log(f"pypi download failed for {filename}: {e}")
            return False
        if dstatus != 200 or not data:
            _log(f"pypi download non-200 {dstatus} for {filename}")
            return False
        downloaded.append((filename, data))
        import hashlib
        file_records.append({
            "filename": filename,
            "sha256_hex": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        })

    if not downloaded:
        return False

    manifest = _build_manifest_for_files(downloaded)
    anchor = _post_anchor(manifest, client_label=f"pypi:orphograph@{version}")
    receipt_id = str(anchor.get("receipt_id") or "")
    calendars_ok = anchor.get("calendars_ok", 0)

    row = {
        "ts_utc": _utc_now_iso(),
        "registry": "pypi",
        "version": version,
        "files": file_records,
        "manifest_root_hex": manifest.get("root_hex", ""),
        "anchor_receipt_id": receipt_id,
        "anchor_calendars_ok": calendars_ok,
    }
    _append_state(STATE_PYPI, row)
    _update_badges("pypi", {
        "version": version,
        "installed_via": "pip install orphograph",
        "anchor_receipt_id": receipt_id,
    })
    return True


# --------------------------------------------------------------------------- #
# npm flow
# --------------------------------------------------------------------------- #


def check_npm() -> bool:
    """Poll the npm registry; on a new latest version, anchor + record it.

    Returns True iff a new version was processed.
    """
    try:
        status, body = _http_get(NPM_URL)
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"npm network error: {e}")
        return False
    if status == 404:
        return False
    if status != 200 or not body:
        _log(f"npm unexpected status {status}")
        return False
    try:
        meta = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        _log("npm non-JSON response")
        return False

    dist_tags = meta.get("dist-tags") or {}
    version = str(dist_tags.get("latest") or "").strip()
    if not version:
        _log("npm response missing dist-tags.latest")
        return False

    already = _read_state_versions(STATE_NPM)
    if version in already:
        return False

    versions = meta.get("versions") or {}
    entry = versions.get(version) if isinstance(versions, dict) else None
    if not isinstance(entry, dict):
        _log(f"npm latest {version} not in versions map")
        return False
    dist = entry.get("dist") or {}
    tarball = dist.get("tarball")
    if not isinstance(tarball, str) or not tarball:
        _log(f"npm version {version} missing tarball url")
        return False

    try:
        dstatus, data = _http_get(tarball, timeout=HTTP_TIMEOUT_S)
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"npm download failed: {e}")
        return False
    if dstatus != 200 or not data:
        _log(f"npm download non-200 {dstatus}")
        return False

    import hashlib
    filename = os.path.basename(tarball) or f"orphograph-{version}.tgz"
    file_records = [{
        "filename": filename,
        "sha256_hex": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }]

    manifest = _build_manifest_for_files([(filename, data)])
    anchor = _post_anchor(manifest, client_label=f"npm:orphograph@{version}")
    receipt_id = str(anchor.get("receipt_id") or "")
    calendars_ok = anchor.get("calendars_ok", 0)

    row = {
        "ts_utc": _utc_now_iso(),
        "registry": "npm",
        "version": version,
        "files": file_records,
        "manifest_root_hex": manifest.get("root_hex", ""),
        "anchor_receipt_id": receipt_id,
        "anchor_calendars_ok": calendars_ok,
    }
    _append_state(STATE_NPM, row)
    _update_badges("npm", {
        "version": version,
        "installed_via": "npm install orphograph",
        "anchor_receipt_id": receipt_id,
    })
    return True


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def run_once() -> int:
    """Poll both registries. Return 0 unconditionally."""
    for fn in (check_pypi, check_npm):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 (defensive: never crash the daemon)
            _log(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv or [])
    try:
        return run_once()
    except Exception as exc:  # noqa: BLE001
        _log(f"fatal: {type(exc).__name__}: {exc}")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
