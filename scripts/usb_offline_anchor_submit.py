#!/usr/bin/env python3
"""usb_offline_anchor_submit.py — online-machine half of the air-gapped workflow.

Reads a manifest previously written to a USB by usb_air_gap_hash.py, POSTs
it to the Orphograph anchor endpoint, and writes the receipt into a NEW
subdirectory on the same USB. The original manifest subdirectory is left
exactly as it was — additive-only, like every other USB-touching script in
this office.

Stdlib only. MIT-licensed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

from _usb_safety import (  # noqa: E402
    UsbSafetyError,
    assert_drive_writable,
    manifest_of_writes,
    reserve_new_path,
    safe_copy_file,
    safe_mkdir,
    safe_write_bytes,
    safe_write_text,
    stamp_dirname,
)


EXIT_OK = 0
EXIT_ARG_OR_IO = 1
EXIT_USB_SAFETY = 2
EXIT_MANIFEST_MISSING = 3
EXIT_HTTP_NON_2XX = 4
EXIT_NETWORK_FAILURE = 5


# Replicates the User-Agent used by scripts/orphograph_watchdog.py so the
# CDN does not treat the request as a default urllib client.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT_S = 60


RECEIPT_README_TEMPLATE = """\
Anchor receipt for an air-gapped manifest.

Receipt id     : {receipt_id}
Root hex       : {root_hex}
Calendars ok   : {calendars_ok} of {calendars_total}
Server         : {server_url}

What is in this directory:
  * receipt.json      — full JSON response from the anchor endpoint.
  * verifier/         — a self-contained offline verifier (verify.py and
                        merkle.py). It can be run on the offline machine
                        against the original folder to confirm that the
                        receipt corresponds to the same file tree.
  * RECEIPT_README.txt — this note.
  * WHAT_WAS_ADDED.json — additive inventory of every file added by the
                          online script.

What the online script did:
  * Read manifest.json from the original offline manifest directory.
  * Submitted the manifest to the anchor endpoint with private mode set.
  * Wrote this directory next to the original one, without modifying it.

How to verify offline:
  * On the offline machine, run:
        python3 verifier/verify.py \\
            --receipt receipt.json \\
            --folder /path/to/the/original/folder
  * The verifier recomputes the Merkle root locally and compares it to the
    root in the receipt. No network access is required.
"""


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="usb_offline_anchor_submit.py",
        description=(
            "Online-machine half of the air-gapped anchoring workflow. "
            "Reads a manifest from a USB, POSTs it to the anchor endpoint, "
            "and writes the receipt into a new sibling subdir on the USB."
        ),
    )
    p.add_argument("--usb", required=True, type=Path)
    p.add_argument("--manifest-subdir", required=True)
    p.add_argument("--server-url", default="https://orphograph.com")
    p.add_argument("--api-key", default=None)
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def _load_manifest(manifest_dir: Path) -> dict:
    manifest_path = manifest_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest.json not found in {manifest_dir}")
    with open(manifest_path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _build_request(server_url: str, manifest: dict, api_key: str | None) -> urllib.request.Request:
    body = dict(manifest)
    body["private"] = True
    payload = json.dumps(body).encode("utf-8")
    url = server_url.rstrip("/") + "/api/anchor_folder"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    return urllib.request.Request(url, data=payload, headers=headers, method="POST")


def _copy_verifier(target_dir: Path) -> None:
    """Copy verify.py and merkle.py from dist/orphograph-verify/ into target/verifier/."""
    verifier_src = _REPO_ROOT / "dist" / "orphograph-verify"
    verifier_dst = target_dir / "verifier"
    safe_mkdir(verifier_dst)
    for name in ("verify.py", "merkle.py"):
        src = verifier_src / name
        if not src.is_file():
            raise FileNotFoundError(f"verifier asset missing: {src}")
        safe_copy_file(src, verifier_dst / name)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else EXIT_ARG_OR_IO

    try:
        usb_root = assert_drive_writable(args.usb)
    except UsbSafetyError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USB_SAFETY

    manifest_dir = (usb_root / args.manifest_subdir).resolve()
    # Guard against path traversal: manifest_dir must live under usb_root.
    try:
        manifest_dir.relative_to(usb_root)
    except ValueError:
        print("error: manifest-subdir escapes the usb mount", file=sys.stderr)
        return EXIT_MANIFEST_MISSING

    if not manifest_dir.is_dir():
        print(f"error: manifest subdir not found: {manifest_dir}", file=sys.stderr)
        return EXIT_MANIFEST_MISSING

    try:
        manifest = _load_manifest(manifest_dir)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_MANIFEST_MISSING
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read manifest: {e}", file=sys.stderr)
        return EXIT_ARG_OR_IO

    req = _build_request(args.server_url, manifest, args.api_key)

    if args.dry_run:
        print("dry-run plan:")
        print(f"  usb mount            : {usb_root}")
        print(f"  manifest subdir      : {manifest_dir}")
        print(f"  server url           : {args.server_url}")
        print(f"  endpoint             : {req.full_url}")
        print(f"  private              : True")
        print(f"  api key sent         : {bool(args.api_key)}")
        return EXIT_OK

    # POST to the anchor endpoint.
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        print(
            f"error: anchor endpoint returned HTTP {e.code}: {err_body[:400]}",
            file=sys.stderr,
        )
        return EXIT_HTTP_NON_2XX
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"error: network failure contacting anchor endpoint: {e}", file=sys.stderr)
        return EXIT_NETWORK_FAILURE

    if not (200 <= status < 300):
        print(f"error: anchor endpoint returned HTTP {status}", file=sys.stderr)
        return EXIT_HTTP_NON_2XX

    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"error: anchor response was not valid JSON: {e}", file=sys.stderr)
        return EXIT_HTTP_NON_2XX

    receipt_id = str(receipt.get("receipt_id") or "unknown")
    root_hex = str(receipt.get("root_hex") or "")
    calendars_ok = receipt.get("calendars_ok", 0)
    calendars_total = receipt.get("calendars_total", 0)

    # Reserve a NEW subdir for the receipt, alongside (not inside) the
    # original manifest subdir.
    receipt_subdir_name = stamp_dirname(receipt_id, "airgap_receipt")
    try:
        target = reserve_new_path(usb_root, receipt_subdir_name)
        safe_mkdir(target)
        safe_write_bytes(target / "receipt.json", json.dumps(receipt, indent=2).encode("utf-8"))
        summary = RECEIPT_README_TEMPLATE.format(
            receipt_id=receipt_id,
            root_hex=root_hex,
            calendars_ok=calendars_ok,
            calendars_total=calendars_total,
            server_url=args.server_url,
        )
        safe_write_text(target / "RECEIPT_README.txt", summary)
        _copy_verifier(target)
        added = manifest_of_writes(target)
        safe_write_bytes(
            target / "WHAT_WAS_ADDED.json",
            json.dumps(added, indent=2).encode("utf-8"),
        )
    except UsbSafetyError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USB_SAFETY
    except (OSError, FileNotFoundError, shutil.Error) as e:
        print(f"error: filesystem error during write: {e}", file=sys.stderr)
        return EXIT_ARG_OR_IO

    print("anchor receipt written.")
    print(f"  receipt_id           : {receipt_id}")
    print(f"  root_hex             : {root_hex}")
    print(f"  calendars_ok         : {calendars_ok} of {calendars_total}")
    print(f"  target               : {target}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
