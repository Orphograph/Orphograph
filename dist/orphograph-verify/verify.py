#!/usr/bin/env python3
"""verify.py — standalone Orphograph verifier (MIT, stdlib only).

This script is fully self-contained. It depends on `merkle.py` sitting in
the same directory (a vendored copy of `server/merkle.py` — see the banner
at the top of that file). No `pip install` is required.

Two subcommands are provided:

    verify.py file   --file F --proof P.json [--ots O.ots]
    verify.py folder --dir D  --manifest M.json [--ots O.ots] [--exclude GLOB ...]

`file` mode walks an inclusion proof bottom-up from the local file's
SHA-256 and checks that the result matches the manifest root recorded
in the proof JSON.

`folder` mode walks the local directory through the same RFC 6962
algorithm `server/merkle.py` uses on the server, compares the
recomputed root against the supplied manifest's `root_hex`, and prints
OK/FAIL. A folder anchored with custom excludes can only re-derive the
same root when verified with the SAME excludes — pass the identical
repeatable `--exclude GLOB` flags the anchor used (supplying any
--exclude replaces the default deny-list, matching the SDK CLI).

When `--ots` is supplied, the script additionally invokes the local
`ots` binary (the OpenTimestamps reference client) via subprocess with
shell=False, list-form argv. Its stdout/stderr is inspected for the
root_hex the user is verifying — if the verifier finds the expected
hash anywhere in the ots output, the chain check is considered to
have at least touched the right hash. (Full chain verification still
requires a Bitcoin node; that is the ots client's job, not ours.)

Exit codes:
    0  OK
    2  invalid arguments / missing files
    3  hash recomputation failed (file or folder did not match)
    4  OTS sub-check failed (root_hex absent from ots output)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

# Vendored — see the banner at the top of merkle.py.
import merkle  # noqa: E402
# The single place this bundle decides whether `ots` confirmed a Bitcoin
# attestation. Do NOT re-derive that verdict here — see otscheck.py's banner
# for the inverted-verdict defect that made it necessary.
import otscheck  # noqa: E402

CHUNK = 1024 * 1024


def _sha256_file(path: Path) -> bytes:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()


def _ots_subcheck(ots_path: Path, root_hex: str) -> int:
    """Ask the OpenTimestamps client whether this hash is confirmed on Bitcoin.

    Returns 0 only when the client CONFIRMED it; 4 otherwise. See
    otscheck.py: the previous implementation decided by looking for the hash
    in the client's stdout, which passed a verification the client had
    explicitly rejected (the failure message contains the hash) and would
    have failed a genuine success (the success message does not).

    The client is invoked with shell=False, list-form argv — never a shell
    string. A missing `ots` binary is a 4, not a pass: the chain step did not
    run, and "did not run" must never read as "confirmed".
    """
    status, height, msg = otscheck.chain_verdict(ots_path, root_hex)
    print(f"  [OTS] {status}: {msg}")
    if status in otscheck.PASSING:
        return 0
    if status == otscheck.UNAVAILABLE:
        print("        The chain check did NOT run. The Merkle/file "
              "verification above still stands on its own.")
    return 4


def _verify_file(args: argparse.Namespace) -> int:
    file_path = Path(args.file).expanduser().resolve()
    proof_path = Path(args.proof).expanduser().resolve()
    if not file_path.is_file():
        print(f"file not found: {file_path}", file=sys.stderr)
        return 2
    if not proof_path.is_file():
        print(f"proof not found: {proof_path}", file=sys.stderr)
        return 2

    try:
        proof_doc = json.loads(proof_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read proof JSON: {e}", file=sys.stderr)
        return 2

    rel_path = proof_doc.get("path")
    root_hex = proof_doc.get("root_hex")
    raw_proof = proof_doc.get("proof")
    if not (isinstance(rel_path, str) and isinstance(root_hex, str) and isinstance(raw_proof, list)):
        print("proof JSON missing required fields: path, root_hex, proof[]", file=sys.stderr)
        return 2

    print(f"  file:        {file_path.name}")
    print(f"  proof path:  {rel_path}")
    print(f"  expected root: {root_hex}")

    file_digest = _sha256_file(file_path)
    print(f"  file sha256: {file_digest.hex()}")

    # Cross-check against the recorded file_sha256_hex when present.
    # The stored side is compared VERBATIM — the office issues lowercase hex
    # and only the locally computed side is ever normalised (the local
    # .hex() is already lowercase). A proof whose recorded hash matches
    # only after lowercasing was edited out-of-band and MUST fail
    # (docs/VERIFIER_SPEC.md §3.2; AUDIT_VERIFIER_DRIFT D1).
    expected_file_hex = proof_doc.get("file_sha256_hex")
    if isinstance(expected_file_hex, str) and expected_file_hex != file_digest.hex():
        if expected_file_hex.lower() == file_digest.hex():
            print("  [FAIL] proof's file_sha256_hex is not in canonical form")
            print("         (matches only after lowercasing — the proof was edited)")
        else:
            print("  [FAIL] local file SHA-256 does not match proof's file_sha256_hex")
            print(f"         expected: {expected_file_hex}")
        return 3

    # Normalise proof step shape — accept both [dir, hex] lists and tuples.
    steps: list[tuple[str, str]] = []
    for step in raw_proof:
        if not (isinstance(step, (list, tuple)) and len(step) == 2):
            print("  [FAIL] malformed proof step", file=sys.stderr)
            return 3
        direction, sibling_hex = step[0], step[1]
        if direction not in ("L", "R") or not isinstance(sibling_hex, str):
            print("  [FAIL] malformed proof step", file=sys.stderr)
            return 3
        steps.append((direction, sibling_hex))

    try:
        root_bytes = bytes.fromhex(root_hex)
    except ValueError:
        print("  [FAIL] root_hex is not valid hex", file=sys.stderr)
        return 3

    ok = merkle.MerkleTree.verify_inclusion(file_digest, rel_path, steps, root_bytes)
    if not ok:
        print("  [FAIL] inclusion proof did not reproduce root")
        return 3
    print("  [OK]   inclusion proof verifies against root")

    if args.ots:
        ots_path = Path(args.ots).expanduser().resolve()
        if not ots_path.is_file():
            print(f"ots file not found: {ots_path}", file=sys.stderr)
            return 2
        sub = _ots_subcheck(ots_path, root_hex)
        if sub != 0:
            return sub
    return 0


def _verify_folder(args: argparse.Namespace) -> int:
    folder = Path(args.dir).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve()
    if not folder.is_dir():
        print(f"folder not found: {folder}", file=sys.stderr)
        return 2
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"could not read manifest JSON: {e}", file=sys.stderr)
        return 2

    expected_root = manifest.get("root_hex")
    if not isinstance(expected_root, str):
        print("manifest missing root_hex", file=sys.stderr)
        return 2

    print(f"  folder:        {folder}")
    print(f"  manifest:      {manifest_path.name}")
    print(f"  expected root: {expected_root}")

    try:
        tree = merkle.MerkleTree.from_folder(folder, exclude=args.exclude)
    except ValueError as e:
        print(f"  [FAIL] could not build tree from folder: {e}")
        return 3
    actual_root = tree.root_hex()
    print(f"  recomputed:    {actual_root}")
    # Exact comparison of lowercase hex strings (docs/VERIFIER_SPEC.md §4.2).
    # root_hex() is lowercase by construction; the manifest side is compared
    # VERBATIM. A manifest root that matches only after lowercasing is not
    # in canonical form — it was edited out-of-band and MUST fail
    # (AUDIT_VERIFIER_DRIFT D1: never "helpfully" lowercase the stored side).
    if actual_root != expected_root:
        if actual_root == expected_root.lower():
            print("  [FAIL] manifest root_hex is not in canonical form")
            print("         (matches only after lowercasing — the manifest was edited)")
        else:
            print("  [FAIL] recomputed root does not match manifest")
        return 3
    print("  [OK]   recomputed root matches manifest")

    if args.ots:
        ots_path = Path(args.ots).expanduser().resolve()
        if not ots_path.is_file():
            print(f"ots file not found: {ots_path}", file=sys.stderr)
            return 2
        sub = _ots_subcheck(ots_path, expected_root)
        if sub != 0:
            return sub
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="verify.py",
        description="Standalone Orphograph verifier (file inclusion + folder root).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    pf = sub.add_parser("file", help="verify a single file against an inclusion proof")
    pf.add_argument("--file", required=True, help="path to the file to verify")
    pf.add_argument("--proof", required=True, help="path to inclusion-proof JSON")
    pf.add_argument("--ots", default=None, help="optional .ots file for chain sub-check")

    pd = sub.add_parser("folder", help="verify a folder against a manifest root")
    pd.add_argument("--dir", required=True, help="path to the folder to verify")
    pd.add_argument("--manifest", required=True, help="path to manifest.json")
    pd.add_argument("--ots", default=None, help="optional .ots file for chain sub-check")
    pd.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="GLOB",
        help=(
            "glob pattern to exclude (repeatable). Supplying any --exclude "
            "REPLACES the default deny-list rather than extending it. "
            "Verification must use the same excludes the folder was "
            "anchored with, or the recomputed root cannot match."
        ),
    )

    args = p.parse_args(argv)
    if args.command == "file":
        return _verify_file(args)
    if args.command == "folder":
        return _verify_folder(args)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
