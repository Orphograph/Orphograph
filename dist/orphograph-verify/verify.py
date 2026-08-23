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
same root when verified with the SAME excludes. Rule (VERIFIER_SPEC §4.2,
shared with the Python SDK): the manifest's own `scope.exclude` — recorded
by the office at anchor time — is authoritative when present (the patterns
are printed, with a WARN if the block's self-checksum no longer matches);
`--exclude GLOB` flags (repeatable; any replaces the default deny-list)
apply to manifests without a scope block, and are ignored with a warning
when one is present unless `--ignore-manifest-scope` is given; otherwise
the standard deny-list.

When `--ots` is supplied, the script additionally asks otscheck.py for a
chain verdict: first it checks LOCALLY that the .ots file's embedded digest
is the root_hex being verified (otherwise UNBOUND), then it runs the local
`ots` binary (the OpenTimestamps reference client) as
`ots verify -d <root_hex> <file.ots>` via subprocess with shell=False,
list-form argv, and classifies the client's exit code + wording into
VERIFIED / PENDING / FAILED / UNAVAILABLE / INDETERMINATE. Only VERIFIED is
a pass. The output is never scanned for the hash as evidence. (Full chain
verification needs a Bitcoin node; that is the ots client's job, not ours.)

Exit codes:
    0  OK
    2  invalid arguments / missing files
    3  hash recomputation failed (file or folder did not match)
    4  OTS chain step did not PASS. stdout carries which non-pass state:
       FAILED (client rejected it) · PENDING (not yet on Bitcoin) ·
       UNAVAILABLE (check could not run: no `ots` / no node) ·
       UNBOUND (.ots is about a different hash) · INDETERMINATE.
       Only FAILED means the proof is bad; the others mean "no verdict".
       The Merkle/file result above it stands on its own either way.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
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

def _printable(value, limit: int = 200) -> str:
    """Render an UNTRUSTED manifest/proof string for stdout.

    Control characters (newline, CR, ESC, …) are replaced so a hostile field
    cannot forge a verdict line ("\\n  [OK]   …") or an ANSI overwrite in the
    verifier's own output; long values are truncated. The exit code never
    depended on this — only what a human or a log-scraper READS did.
    """
    s = str(value)
    s = "".join(ch if ch.isprintable() else "?" for ch in s)
    # Square brackets are how this tool marks verdicts ([OK] [FAIL] [OTS] [WARN]);
    # untrusted text must not be able to imitate one, even inline, for readers
    # or log-scrapers — render them as visibly different glyphs.
    s = s.replace("[", "⟦").replace("]", "⟧")
    return s if len(s) <= limit else s[:limit] + "…"


_HEX = re.compile(r"^[0-9A-Fa-f]{64}$")


def _hex_or_note(value) -> str:
    """Echo a hash field only if it LOOKS like one; anything else is described,
    not printed, so a hostile manifest cannot forge verdict lines in stdout.
    (Case is still compared verbatim below — D1 — this only guards the echo.)"""
    if isinstance(value, str) and _HEX.match(value):
        return value
    n = len(value) if isinstance(value, str) else 0
    return f"<not a 64-hex string ({type(value).__name__}, {n} chars) — content not echoed>"


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
    print(f"  proof path:  {_printable(rel_path)}")
    print(f"  expected root: {_hex_or_note(root_hex)}")

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
            print(f"         expected: {_printable(expected_file_hex)}")
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
    print(f"  expected root: {_hex_or_note(expected_root)}")

    # Which excludes to walk with. Rule (docs/VERIFIER_SPEC.md §4.2, shared
    # with the Python SDK): the manifest's own `scope.exclude` is AUTHORITATIVE
    # when present — the office recorded what the anchor used, so a holder who
    # was just handed the bundle needs no flags. `--exclude` applies to
    # manifests WITHOUT a scope block (issued before scope existed, or by a
    # producer that does not write one); with a scope present it is ignored
    # with a warning unless --ignore-manifest-scope says the operator means it.
    scope = manifest.get("scope")
    scope_list = None
    scope_malformed = None
    if scope is not None:
        if not isinstance(scope, dict):
            scope_malformed = f"scope is a {type(scope).__name__}, not an object"
        elif not isinstance(scope.get("exclude"), list):
            scope_malformed = "scope.exclude is not a list"
        elif not all(isinstance(x, str) for x in scope["exclude"]):
            scope_malformed = "scope.exclude has non-string entries"
        else:
            scope_list = list(scope["exclude"])
    if scope_malformed:
        # server/merkle.py from_manifest REJECTS such a manifest; here the root
        # still decides, but the reader must be told the block is broken, not
        # absent — "no scope block" would send them hunting for the wrong thing.
        print(f"  [WARN] manifest scope block is malformed ({scope_malformed}) — "
              f"ignoring it; walking with --exclude or the standard deny-list")
    if scope_list is not None and not args.ignore_manifest_scope:
        excludes = scope_list
        src = scope.get("exclude_source")
        src = src if src in ("default", "custom") else "unrecognised"
        print(f"  excludes:      {len(excludes)} pattern(s) from the manifest's scope block (source={src})")
        for pat in excludes[:50]:
            print(f"                 - {_printable(pat, 120)}")
        if len(excludes) > 50:
            print(f"                 … and {len(excludes) - 50} more")
        if args.exclude:
            print("  [WARN] --exclude given but the manifest carries a scope block, which is "
                  "authoritative — flags ignored (pass --ignore-manifest-scope to override)")
        # The scope block is self-checksummed (scope_hex). A mismatch means the
        # block was edited after anchoring. It is a WARNING, not a verdict: the
        # anchored value is root_hex alone, and the root decides below
        # (server/merkle.py, "LIMIT, stated plainly").
        recorded = scope.get("scope_hex")
        if recorded is None:
            print("  [WARN] scope block carries no scope_hex — edits to the scope cannot "
                  "be detected; the root comparison below still decides")
        elif not isinstance(recorded, str) or recorded != merkle.scope_hex(scope):
            print("  [WARN] scope_hex does not match the scope block — the scope was "
                  "edited after anchoring; the root comparison below still decides")
    elif args.exclude:
        excludes = args.exclude
        why = ("manifest scope ignored on request" if scope_list is not None
               else "manifest scope block ignored as malformed" if scope_malformed
               else "manifest carries no scope block")
        print(f"  excludes:      {len(excludes)} pattern(s) from --exclude ({why})")
        for pat in excludes[:50]:
            print(f"                 - {_printable(pat, 120)}")
    else:
        excludes = None
        why = "manifest scope block ignored as malformed" if scope_malformed else "manifest carries no scope block"
        print(f"  excludes:      standard deny-list ({why})")

    try:
        tree = merkle.MerkleTree.from_folder(folder, exclude=excludes)
    except ValueError as e:
        if excludes is not None and scope_list is not None and excludes == scope_list \
                and any(p.is_file() for p in folder.rglob("*")):
            print("  [FAIL] the manifest's scope.exclude matches EVERY file in this folder — "
                  "the recorded scope, not the folder, is the cause (pass "
                  "--ignore-manifest-scope to walk with your own excludes)")
            return 3
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
            "glob pattern to exclude (repeatable; any --exclude REPLACES the "
            "default deny-list). Used for manifests WITHOUT a scope block; a "
            "manifest's own scope.exclude is authoritative and wins unless "
            "--ignore-manifest-scope is given."
        ),
    )
    pd.add_argument(
        "--ignore-manifest-scope",
        action="store_true",
        help="walk with --exclude (or the default deny-list) even if the manifest records a scope block",
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
