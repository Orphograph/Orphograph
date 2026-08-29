"""orphograph._cli — argparse-based command-line interface.

Subcommands:

    verify-inclusion <file> <rel_path> <proof.json> [root_hex]
                                    Verify ONE file against a saved proof.
                                    Purely local: no server, no network.
    anchor <folder>                 Anchor a folder; prints one line of JSON.
    verify <folder> <receipt_id>    Verify a folder against a receipt.
    inclusion-proof <rid> <path>    Fetch an inclusion proof; prints JSON.

``verify-inclusion`` is the relying party's command. It needs only the file,
its POSIX path inside the anchored folder, and the ``proof.json`` written by
``inclusion-proof`` (or a bare proof array plus an explicit ``root_hex``).
It never consults ``--server-url``; the service can be gone. Same positional
shape as the Node CLI's ``verify-inclusion``, except that ``root_hex`` may be
omitted when ``proof.json`` already carries it.

Both ``anchor`` and ``verify`` accept repeatable ``--exclude GLOB`` flags.
A folder anchored with custom excludes can only re-derive the same Merkle
root when verified with the SAME excludes (AUDIT_VERIFIER_DRIFT D2). Since
Wedge 01 the manifest records them in its ``scope`` block and ``verify``
reads them from there (the manifest is authoritative — VERIFIER_SPEC §4.2);
the ``--exclude`` flag on ``verify`` only applies to manifests that carry no
scope block (issued before scope existed).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from . import anchor_folder, inclusion_proof, verify_folder, verify_inclusion
from ._client import DEFAULT_SERVER_URL, OrphographError


def _env_server() -> str:
    return os.environ.get("ORPHO_SERVER_URL", DEFAULT_SERVER_URL)


def _env_api_key() -> Optional[str]:
    val = os.environ.get("ORPHO_API_KEY", "").strip()
    return val or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="orphograph",
        description=(
            "Bitcoin-anchored folder receipts. verify-inclusion checks a file "
            "against a saved proof with no server involved; anchor / verify / "
            "inclusion-proof talk to the Orphograph service."
        ),
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

    exclude_help = (
        "Glob pattern to exclude (repeatable). Supplying any --exclude "
        "REPLACES the default deny-list rather than extending it. "
        "On verify: applies only to manifests without a scope block — a "
        "manifest's recorded scope.exclude is authoritative (VERIFIER_SPEC §4.2)."
    )

    p_anchor = sub.add_parser("anchor", help="Anchor a folder.")
    p_anchor.add_argument("folder", help="Local folder to anchor.")
    p_anchor.add_argument("--label", default=None, help="Optional short client label.")
    p_anchor.add_argument(
        "--exclude", action="append", default=None, metavar="GLOB", help=exclude_help
    )

    p_verify = sub.add_parser("verify", help="Verify a folder against a receipt.")
    p_verify.add_argument("folder", help="Local folder to verify.")
    p_verify.add_argument("receipt_id", help="Receipt id returned at anchor time.")
    p_verify.add_argument(
        "--exclude", action="append", default=None, metavar="GLOB", help=exclude_help
    )

    p_proof = sub.add_parser("inclusion-proof", help="Fetch an inclusion proof.")
    p_proof.add_argument("receipt_id", help="Folder receipt id.")
    p_proof.add_argument("path", help="POSIX relative path inside the folder.")

    p_vi = sub.add_parser(
        "verify-inclusion",
        help="Verify one file against a saved proof. Local only; no network.",
    )
    p_vi.add_argument("file", help="Local file to check.")
    p_vi.add_argument("rel_path", help="POSIX relative path the file had inside the anchored folder.")
    p_vi.add_argument(
        "proof_json",
        help="Path to the JSON written by `inclusion-proof` (or a bare proof array).",
    )
    p_vi.add_argument(
        "root_hex",
        nargs="?",
        default=None,
        help="Merkle root to verify against. Overrides the root inside proof_json; "
             "required when proof_json is a bare array.",
    )

    return parser


def _load_proof(proof_json: str, root_override: Optional[str]) -> tuple:
    """Read ``proof.json`` and pin the root.

    Accepts the object ``inclusion-proof`` writes (``{"root_hex", "proof", ...}``)
    or a bare ``[[direction, hex], ...]`` array, exactly like the Node CLI.
    An explicit ``root_hex`` always wins over the one inside the file: a
    relying party handed the root out-of-band is pinning THAT root.
    """
    with open(proof_json, "r", encoding="utf-8") as f:
        parsed = json.load(f)
    if isinstance(parsed, list):
        proof, embedded_root = parsed, None
    elif isinstance(parsed, dict):
        proof, embedded_root = parsed.get("proof", []), parsed.get("root_hex")
    else:
        raise ValueError("proof_json must be an object or an array")
    root = root_override if root_override is not None else embedded_root
    if not isinstance(root, str) or not root:
        raise ValueError("no root_hex: pass it as the 4th argument or use the JSON written by inclusion-proof")
    if not isinstance(proof, list):
        raise ValueError("proof must be a list of [direction, hex] steps")
    return proof, root


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    server_url = args.server_url or _env_server()
    api_key = args.api_key if args.api_key is not None else _env_api_key()

    try:
        if args.command == "verify-inclusion":
            # Deliberately first and deliberately without server_url/api_key:
            # this branch must stay correct with the service unreachable.
            proof, root_hex = _load_proof(args.proof_json, args.root_hex)
            ok = verify_inclusion(args.file, args.rel_path, proof, root_hex)
            sys.stdout.write(json.dumps({"ok": bool(ok)}) + "\n")
            return 0 if ok else 1
        if args.command == "anchor":
            result = anchor_folder(
                args.folder,
                server_url=server_url,
                api_key=api_key,
                client_label=args.label,
                exclude=args.exclude,
            )
            sys.stdout.write(json.dumps(result) + "\n")
            return 0
        if args.command == "verify":
            ok = verify_folder(
                args.folder,
                args.receipt_id,
                server_url=server_url,
                api_key=api_key,
                exclude=args.exclude,
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
