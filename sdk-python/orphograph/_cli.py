"""orphograph._cli — argparse-based command-line interface.

Subcommands:

    anchor <folder>                 Anchor a folder; prints one line of JSON.
    verify <folder> <receipt_id>    Verify a folder against a receipt.
    inclusion-proof <rid> <path>    Fetch an inclusion proof; prints JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import anchor_folder, inclusion_proof, verify_folder
from ._client import DEFAULT_SERVER_URL, OrphographError


def _env_server() -> str:
    return os.environ.get("ORPHO_SERVER_URL", DEFAULT_SERVER_URL)


def _env_api_key() -> Optional[str]:
    val = os.environ.get("ORPHO_API_KEY", "").strip()
    return val or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orphograph",
        description="Anchor folders to Bitcoin via the Orphograph service.",
    )
    parser.add_argument(
        "--server-url",
        default=None,
        help="Base URL of the Orphograph service (default: ORPHO_SERVER_URL or https://orphograph.com).",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Optional API key (default: ORPHO_API_KEY).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_anchor = sub.add_parser("anchor", help="Anchor a folder.")
    p_anchor.add_argument("folder", help="Local folder to anchor.")
    p_anchor.add_argument("--label", default=None, help="Optional short client label.")

    p_verify = sub.add_parser("verify", help="Verify a folder against a receipt.")
    p_verify.add_argument("folder", help="Local folder to verify.")
    p_verify.add_argument("receipt_id", help="Receipt id returned at anchor time.")

    p_proof = sub.add_parser("inclusion-proof", help="Fetch an inclusion proof.")
    p_proof.add_argument("receipt_id", help="Folder receipt id.")
    p_proof.add_argument("path", help="POSIX relative path inside the folder.")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    server_url = args.server_url or _env_server()
    api_key = args.api_key if args.api_key is not None else _env_api_key()

    try:
        if args.command == "anchor":
            result = anchor_folder(
                args.folder,
                server_url=server_url,
                api_key=api_key,
                client_label=args.label,
            )
            sys.stdout.write(json.dumps(result) + "\n")
            return 0
        if args.command == "verify":
            ok = verify_folder(
                args.folder,
                args.receipt_id,
                server_url=server_url,
                api_key=api_key,
            )
            sys.stdout.write(json.dumps({"match": bool(ok)}) + "\n")
            return 0 if ok else 1
        if args.command == "inclusion-proof":
            proof = inclusion_proof(
                args.receipt_id,
                args.path,
                server_url=server_url,
                api_key=api_key,
            )
            sys.stdout.write(json.dumps(proof) + "\n")
            return 0
    except OrphographError as e:
        sys.stderr.write(json.dumps({"error": e.message, "status": e.status}) + "\n")
        return 2
    except (OSError, ValueError) as e:
        sys.stderr.write(json.dumps({"error": str(e)}) + "\n")
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
