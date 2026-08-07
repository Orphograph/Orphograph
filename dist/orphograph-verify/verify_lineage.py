#!/usr/bin/env python3
"""verify_lineage.py — standalone Orphograph edit-lineage verifier (MIT, stdlib only).

Sibling of verify.py (whose `file` / `folder` contract is untouched). It
depends only on the vendored `merkle.py` sitting in the same directory.
No `pip install` is required and no network call is ever made.

    verify_lineage.py --chain CHAIN_DIR [--tip RID] [--dir RID=PATH ...]
                      [--ots-check] [--max-depth N] [--exclude GLOB ...]

CHAIN_DIR holds one subdirectory per link (the export-bundle contents):
``<rid>/receipt.json``, ``<rid>/manifest.json``, ``<rid>/*.ots``. The
verifier picks the tip (``--tip`` or the unique receipt whose root appears
in no other manifest's reserved ``.orphograph/parent`` leaf) and walks the
parent links downward, per link:

  STRUCT  — manifest present, algorithm/version supported.
  ROOT    — MerkleTree.from_manifest recomputes every leaf and the root.
  BIND    — manifest.root_hex == receipt.hash_hex, compared VERBATIM
            (docs/VERIFIER_SPEC.md §4.2; never "helpfully" lowercase the
            stored side).
  PARENT  — the reserved ``.orphograph/parent`` leaf (absent → genesis).
            Its file_sha256_hex is the committed parent root P; the leaf
            hash is re-derived from (path, P). Receipt ``lineage`` hints
            and the manifest ``parent`` block are UNTRUSTED — any
            disagreement with the committed leaf FAILS the link.
            A reserved leaf with size_bytes != 0 FAILS the link (the
            ``.orphograph/`` prefix is reserved; real files may not shadow
            it).
  LOOKUP  — the receipt whose hash_hex == P must be in CHAIN_DIR; a
            missing parent is a BROKEN chain, reported at that link.
  OTS     — every *.ots must start with the OpenTimestamps magic and embed
            receipt.hash_hex at the fixed offset. With --ots-check the
            local `ots` binary (shell=False, list argv) is additionally
            invoked per file and, best-effort, Bitcoin block heights are
            parsed and required to be non-increasing walking tip → genesis.
  CONTENT — optional, per ``--dir RID=PATH``: the local draft folder is
            re-walked and the synthetic reserved leaf is injected in sorted
            position before re-folding to the root (no sidecar file on disk
            is needed or expected).

What a green chain establishes: each child root cryptographically commits
to its parent root, and each root carries OTS attestations — anchor-time
ordering. It does NOT establish that any draft is a derivative of another,
does not establish authorship, and does not establish that no other
versions or parallel children exist. A fork (two children committing the
same parent root, both present in CHAIN_DIR) is reported informationally,
never as a failure.

Exit codes (consistent with verify.py's):
    0  all links OK
    2  invalid arguments / unreadable chain dir / ambiguous tip
    3  a link failed recomputation / binding / hint-vs-commitment
    4  OTS sub-check failed (--ots-check)
    5  chain broken (missing parent, cycle, or --max-depth exceeded)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

# Vendored — see the banner at the top of merkle.py. The explicit path
# insert keeps the import working regardless of the caller's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import merkle  # noqa: E402
# Shared chain verdict — see otscheck.py's banner. This file previously
# carried its own copy of the same inverted logic.
import otscheck  # noqa: E402

RESERVED_PARENT_PATH = ".orphograph/parent"
OTS_HEADER_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"

NOTE = (
    "NOTE: a green chain verifies anchor-time ordering only — each child root\n"
    "cryptographically commits to its parent root, and each root carries OTS\n"
    "attestations. It does NOT establish that any draft is a derivative of\n"
    "another, does NOT establish authorship, and does NOT establish that no\n"
    "other versions or parallel children exist."
)

EXIT_OK = 0
EXIT_ARGS = 2
EXIT_LINK = 3
EXIT_OTS = 4
EXIT_BROKEN = 5


def _is_lower_hex(s: object, length: int = 64) -> bool:
    return isinstance(s, str) and len(s) == length and all(c in "0123456789abcdef" for c in s)


def _lineage_leaf_hex(parent_root_hex: str) -> str:
    """SHA-256(0x00 || '.orphograph/parent' || 0x00 || parent_root) — byte-identical
    to merkle._leaf_hash(RESERVED_PARENT_PATH, parent_root_bytes)."""
    return hashlib.sha256(
        b"\x00" + RESERVED_PARENT_PATH.encode("utf-8") + b"\x00" + bytes.fromhex(parent_root_hex)
    ).hexdigest()


def _load_chain(chain_dir: Path) -> dict[str, dict]:
    """Load every <rid>/receipt.json (+ optional manifest.json) under chain_dir."""
    links: dict[str, dict] = {}
    for sub in sorted(chain_dir.iterdir()):
        if not sub.is_dir():
            continue
        receipt_file = sub / "receipt.json"
        if not receipt_file.is_file():
            continue
        try:
            receipt = json.loads(receipt_file.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"  [WARN] unreadable receipt in {sub.name}: {e}")
            continue
        rid = receipt.get("receipt_id") or sub.name
        manifest = None
        manifest_file = sub / "manifest.json"
        if manifest_file.is_file():
            try:
                manifest = json.loads(manifest_file.read_text())
            except (OSError, json.JSONDecodeError) as e:
                print(f"  [WARN] unreadable manifest in {sub.name}: {e}")
        links[rid] = {"dir": sub, "receipt": receipt, "manifest": manifest}
    return links


def _reserved_leaves(manifest: dict) -> list[dict]:
    leaves = manifest.get("leaves")
    if not isinstance(leaves, list):
        return []
    return [
        leaf for leaf in leaves
        if isinstance(leaf, dict) and leaf.get("path") == RESERVED_PARENT_PATH
    ]


def _committed_parent_root(manifest: dict | None) -> str | None:
    """The parent root committed by a manifest's reserved leaf, if any.
    Discovery-only helper (tip selection / fork scan) — the walk itself
    re-derives and validates the leaf."""
    if not isinstance(manifest, dict):
        return None
    reserved = _reserved_leaves(manifest)
    if len(reserved) != 1:
        return None
    root = reserved[0].get("file_sha256_hex")
    return root if _is_lower_hex(root) else None


def _ots_static_check(link_dir: Path, hash_hex: str) -> tuple[bool, list[str]]:
    """Magic + embedded-hash check on every *.ots — the exact check
    engine.verify_receipt performs. Zero .ots files FAILS the link: with no
    proof file the checks below never run, so a proofless bundle would
    otherwise be reported as verified."""
    msgs: list[str] = []
    ots_files = sorted(link_dir.glob("*.ots"))
    if not ots_files:
        # NOT informational — this is the whole evidence base for the link.
        # With zero .ots files the loop below never runs and the link was
        # reported as passing: a fabricated chain with no proof file anywhere
        # exited 0 claiming "anchor-time ordering holds", even with
        # --ots-check explicitly passed. A link with no attestation proves
        # nothing about when it existed.
        msgs.append("NO .ots FILES — this link carries no timestamp evidence; "
                    "nothing here establishes when it existed")
        return False, msgs
    expected = bytes.fromhex(hash_hex)
    offset = len(OTS_HEADER_MAGIC) + 2
    ok = True
    for ots in ots_files:
        try:
            data = ots.read_bytes()
        except OSError as e:
            msgs.append(f"{ots.name}: unreadable ({e})")
            ok = False
            continue
        magic_ok = data.startswith(OTS_HEADER_MAGIC)
        hash_ok = magic_ok and data[offset:offset + 32] == expected
        if magic_ok and hash_ok:
            msgs.append(f"{ots.name}: magic OK, embedded hash matches receipt")
        else:
            msgs.append(
                f"{ots.name}: FAIL ("
                + ("bad magic" if not magic_ok else "embedded hash != receipt hash_hex")
                + ")"
            )
            ok = False
    return ok, msgs


def _ots_binary_check(link_dir: Path, hash_hex: str) -> tuple[bool, int | None, list[str]]:
    """Ask the OpenTimestamps client for a verdict on every .ots in this link.

    Returns (ok, best-effort max Bitcoin block height, messages). Delegates to
    otscheck so this file and verify.py cannot drift apart again: both used to
    treat "the hash appears in the client's stdout" as confirmation, which
    passed verifications the client had rejected. Zero .ots files is a
    failure, not a vacuous pass.
    """
    return otscheck.check_dir(link_dir, hash_hex)


def _check_link(rid: str, entry: dict) -> tuple[int, str | None, list[str]]:
    """Run STRUCT/ROOT/BIND/PARENT/OTS(static) for one link.

    Returns (exit_code 0|EXIT_LINK, committed parent root or None, messages).
    """
    msgs: list[str] = []
    receipt = entry["receipt"]
    manifest = entry["manifest"]
    hash_hex = receipt.get("hash_hex")
    if not _is_lower_hex(hash_hex):
        return EXIT_LINK, None, ["receipt hash_hex is not 64 lowercase hex characters"]

    # STRUCT — v1 lineage links are folder anchors (design Q3 working
    # assumption: folder-anchor parents only; a bare single-hash link is out
    # of scope for the offline walk).
    if manifest is None:
        return EXIT_LINK, None, [
            "manifest.json missing — v1 edit-lineage links must be folder "
            "anchors (folder-anchor parents only)"
        ]

    # ROOT — recompute every leaf and the root, exactly as the server does
    # at anchor time. Also enforces the supported algorithm/version tags.
    try:
        merkle.MerkleTree.from_manifest(manifest)
    except (KeyError, TypeError, ValueError) as e:
        return EXIT_LINK, None, [f"manifest recomputation failed: {e}"]
    msgs.append("manifest leaves re-derive and fold to root_hex: OK")

    # BIND — VERBATIM string compare of stored hex (D1 rule parity).
    manifest_root = manifest.get("root_hex")
    if manifest_root != hash_hex:
        if isinstance(manifest_root, str) and manifest_root.lower() == hash_hex:
            return EXIT_LINK, None, [
                "manifest root_hex is not in canonical form "
                "(matches only after lowercasing — the manifest was edited)"
            ]
        return EXIT_LINK, None, ["manifest root_hex does not match receipt hash_hex"]
    msgs.append("manifest root_hex == receipt hash_hex (verbatim): OK")

    # PARENT — reserved leaf, recomputed; hints never trusted.
    reserved = _reserved_leaves(manifest)
    if len(reserved) > 1:
        return EXIT_LINK, None, [f"more than one reserved {RESERVED_PARENT_PATH!r} leaf"]
    parent_root: str | None = None
    if reserved:
        leaf = reserved[0]
        if leaf.get("size_bytes") != 0:
            return EXIT_LINK, None, [
                f"reserved {RESERVED_PARENT_PATH!r} leaf has size_bytes != 0 — "
                "the reserved path may not shadow a real file"
            ]
        parent_root = leaf.get("file_sha256_hex")
        if not _is_lower_hex(parent_root):
            return EXIT_LINK, None, [
                "reserved parent leaf's file_sha256_hex is not 64 lowercase hex "
                "characters (canonical form required)"
            ]
        if _lineage_leaf_hex(parent_root) != leaf.get("leaf_hex"):
            return EXIT_LINK, None, [
                "reserved parent leaf_hex does not derive from its file_sha256_hex"
            ]
        msgs.append(f"committed parent root: {parent_root[:16]}…")
        # Hint-vs-commitment consistency: the committed leaf is the authority.
        hint = receipt.get("lineage")
        if isinstance(hint, dict) and hint.get("parent_root") != parent_root:
            return EXIT_LINK, parent_root, [
                "receipt lineage.parent_root disagrees with the committed "
                "reserved leaf — hints are untrusted, the committed leaf wins: FAIL"
            ]
        parent_block = manifest.get("parent")
        if isinstance(parent_block, dict) and parent_block.get("root_hex") != parent_root:
            return EXIT_LINK, parent_root, [
                "manifest parent.root_hex disagrees with the committed "
                "reserved leaf: FAIL"
            ]
    else:
        if isinstance(receipt.get("lineage"), dict) and receipt["lineage"].get("committed"):
            return EXIT_LINK, None, [
                "receipt claims committed lineage but the manifest has no "
                "reserved parent leaf: FAIL"
            ]
        msgs.append("no reserved parent leaf — genesis link")

    # OTS (static) — magic + embedded hash.
    ots_ok, ots_msgs = _ots_static_check(entry["dir"], hash_hex)
    msgs.extend("  " + m for m in ots_msgs)
    if not ots_ok:
        return EXIT_LINK, parent_root, msgs + [".ots binding failed"]

    return EXIT_OK, parent_root, msgs


def _check_content(entry: dict, draft_dir: Path, parent_root: str | None,
                   exclude: list[str] | None) -> tuple[bool, list[str]]:
    """CONTENT — recompute the draft folder root, injecting the synthetic
    reserved leaf when the link commits a parent. Pure re-use of the vendored
    merkle primitives; no sidecar file on disk is needed."""
    receipt = entry["receipt"]
    try:
        disk_leaves = merkle.MerkleTree.from_folder(draft_dir, exclude=exclude).manifest()["leaves"]
    except ValueError as e:
        return False, [f"could not build tree from folder: {e}"]
    if parent_root is not None:
        disk_leaves.append({
            "path": RESERVED_PARENT_PATH,
            "file_sha256_hex": parent_root,
            "leaf_hex": _lineage_leaf_hex(parent_root),
            "size_bytes": 0,
        })
    disk_leaves.sort(key=lambda leaf: leaf["path"].encode("utf-8"))
    levels = merkle._build_levels([bytes.fromhex(leaf["leaf_hex"]) for leaf in disk_leaves])
    recomputed = levels[-1][0].hex()
    if recomputed != receipt.get("hash_hex"):
        return False, [
            f"recomputed folder root {recomputed[:16]}… does not match the "
            "receipt's anchored root — draft bytes differ from what was anchored"
        ]
    return True, ["local draft folder re-derives the anchored root (with synthetic parent leaf): OK"
                  if parent_root is not None else
                  "local draft folder re-derives the anchored root: OK"]


def _scan_forks(links: dict[str, dict]) -> dict[str, list[str]]:
    """Map committed parent root → child receipt ids, for every manifest
    present in CHAIN_DIR. Multiple children of one parent = fork (allowed by
    design; reported informationally, never a failure)."""
    children: dict[str, list[str]] = {}
    for rid, entry in links.items():
        parent = _committed_parent_root(entry["manifest"])
        if parent is not None:
            children.setdefault(parent, []).append(rid)
    return {p: rids for p, rids in children.items() if len(rids) > 1}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="verify_lineage.py",
        description=(
            "Standalone Orphograph edit-lineage verifier: walk parent links "
            "from a directory of receipts + manifests + .ots files and check "
            "anchor-time ordering, fully offline."
        ),
    )
    p.add_argument("--chain", required=True, help="directory of per-link bundles (<rid>/receipt.json …)")
    p.add_argument("--tip", default=None,
                   help="receipt id of the newest link (auto-detected when unique). "
                        "Receipt ids may begin with '-': use the --tip=<id> form.")
    p.add_argument(
        "--dir", action="append", default=None, metavar="RID=PATH",
        help="optionally recompute a link's root from a local draft folder "
             "(repeatable; use --dir=<rid>=<path> if the rid begins with '-')",
    )
    p.add_argument("--ots-check", action="store_true",
                   help="additionally run the local `ots` binary per .ots file")
    p.add_argument("--max-depth", type=int, default=32,
                   help="maximum number of links to walk (default 32)")
    p.add_argument(
        "--exclude", action="append", default=None, metavar="GLOB",
        help=(
            "glob pattern for --dir recomputation (repeatable). Supplying any "
            "--exclude REPLACES the default deny-list; use the same excludes "
            "the folder was anchored with."
        ),
    )
    args = p.parse_args(argv)

    chain_dir = Path(args.chain).expanduser().resolve()
    if not chain_dir.is_dir():
        print(f"chain dir not found: {chain_dir}", file=sys.stderr)
        return EXIT_ARGS
    if args.max_depth < 1:
        print("--max-depth must be >= 1", file=sys.stderr)
        return EXIT_ARGS

    dir_map: dict[str, Path] = {}
    for spec in (args.dir or []):
        rid, sep, path = spec.partition("=")
        if not sep or not rid or not path:
            print(f"--dir expects RID=PATH, got: {spec}", file=sys.stderr)
            return EXIT_ARGS
        dpath = Path(path).expanduser().resolve()
        if not dpath.is_dir():
            print(f"--dir folder not found: {dpath}", file=sys.stderr)
            return EXIT_ARGS
        dir_map[rid] = dpath

    links = _load_chain(chain_dir)
    if not links:
        print(f"no <rid>/receipt.json bundles found under {chain_dir}", file=sys.stderr)
        return EXIT_ARGS
    by_root = {}
    for rid, entry in links.items():
        root = entry["receipt"].get("hash_hex")
        if isinstance(root, str):
            if root in by_root:
                print(f"  [WARN] two receipts share root {root[:16]}… "
                      f"({by_root[root]}, {rid}); using {by_root[root]}")
            else:
                by_root[root] = rid

    # Tip selection.
    if args.tip:
        if args.tip not in links:
            print(f"--tip {args.tip} not found in chain dir", file=sys.stderr)
            return EXIT_ARGS
        tip = args.tip
    else:
        committed_parents = set()
        for entry in links.values():
            parent = _committed_parent_root(entry["manifest"])
            if parent is not None:
                committed_parents.add(parent)
        candidates = [
            rid for rid, entry in links.items()
            if entry["receipt"].get("hash_hex") not in committed_parents
        ]
        if len(candidates) != 1:
            print(
                "cannot auto-detect the tip "
                f"({len(candidates)} candidates: {', '.join(sorted(candidates)) or 'none'}); "
                "pass --tip RID",
                file=sys.stderr,
            )
            return EXIT_ARGS
        tip = candidates[0]

    forks = _scan_forks(links)

    print(f"  chain dir: {chain_dir}")
    print(f"  tip:       {tip}")
    print(f"  links in dir: {len(links)}")

    walked: list[dict] = []   # tip → downward
    exit_code = EXIT_OK
    ots_exit = EXIT_OK
    prev_height: int | None = None  # child (later link) height while walking downward
    visited: set[str] = set()
    current: str | None = tip
    while current is not None:
        if current in visited:
            print(f"\n  [FAIL] cycle detected at {current} — chain is not a chain")
            exit_code = exit_code or EXIT_BROKEN
            break
        if len(walked) >= args.max_depth:
            print(f"\n  [FAIL] --max-depth {args.max_depth} exceeded before reaching genesis")
            exit_code = exit_code or EXIT_BROKEN
            break
        visited.add(current)
        entry = links[current]
        receipt = entry["receipt"]
        print(f"\n  link {current}:")
        code, parent_root, msgs = _check_link(current, entry)
        for m in msgs:
            print(f"    {m}")
        info = {
            "rid": current,
            "root": receipt.get("hash_hex"),
            "created_at": receipt.get("created_at"),
            "btc_pinned_at": receipt.get("btc_pinned_at"),
            "status": receipt.get("status", "pending"),
            "ok": code == EXIT_OK,
            "genesis": parent_root is None and code == EXIT_OK,
        }
        walked.append(info)
        if code != EXIT_OK:
            print(f"    [FAIL] link {current} failed verification")
            exit_code = EXIT_LINK
            break

        # CONTENT (optional).
        if current in dir_map:
            ok, cmsgs = _check_content(entry, dir_map[current], parent_root, args.exclude)
            for m in cmsgs:
                print(f"    {m}")
            if not ok:
                print(f"    [FAIL] link {current} content recomputation failed")
                exit_code = EXIT_LINK
                info["ok"] = False
                break

        # OTS binary sub-check (optional) + best-effort height monotonicity:
        # walking tip → genesis, each parent's height must be <= the child's.
        if args.ots_check:
            bok, height, bmsgs = _ots_binary_check(entry["dir"], receipt.get("hash_hex", ""))
            for m in bmsgs:
                print(f"    [OTS] {m}")
            if not bok:
                ots_exit = EXIT_OTS
            if height is not None and prev_height is not None and height > prev_height:
                print(
                    f"    [OTS] FAIL: this link is Bitcoin-attested at block {height}, "
                    f"LATER than its child's block {prev_height} — "
                    "non-monotonic anchor-time ordering"
                )
                ots_exit = EXIT_OTS
            if height is not None:
                prev_height = height

        print(f"    [OK]   link {current} verifies" + ("  (genesis)" if parent_root is None else ""))

        if parent_root is None:
            current = None  # genesis reached
            continue
        parent_rid = by_root.get(parent_root)
        if parent_rid is None:
            print(
                f"\n  [BROKEN] chain broken at {current}: it provably commits to "
                f"parent root {parent_root[:16]}…, but no presented receipt "
                "anchors that root. Each presented link above still verifies "
                "individually; the committed parent is simply not present."
            )
            exit_code = exit_code or EXIT_BROKEN
            break
        # Hints must agree with the committed lookup (receipt id is not
        # committed, but a disagreeing hint means the bundle was edited).
        hint = receipt.get("lineage")
        if isinstance(hint, dict) and hint.get("parent_receipt_id") not in (None, parent_rid):
            print(
                f"    [FAIL] receipt lineage.parent_receipt_id "
                f"({hint.get('parent_receipt_id')}) disagrees with the receipt "
                f"actually anchoring the committed parent root ({parent_rid})"
            )
            exit_code = EXIT_LINK
            break
        parent_block = entry["manifest"].get("parent")
        if isinstance(parent_block, dict) and parent_block.get("receipt_id") not in (None, parent_rid):
            print(
                f"    [FAIL] manifest parent.receipt_id "
                f"({parent_block.get('receipt_id')}) disagrees with the receipt "
                f"actually anchoring the committed parent root ({parent_rid})"
            )
            exit_code = EXIT_LINK
            break
        current = parent_rid

    # Fork report — informational, never a failure (lineage shows *a* chain,
    # not *the only* chain).
    for parent, rids in sorted(forks.items()):
        print(f"\n  FORK at {parent[:16]}…: children {', '.join(sorted(rids))} "
              "(informational — parallel children are allowed by design)")

    # Chain summary, oldest → newest.
    print("\n  chain (oldest anchor → newest):")
    for info in reversed(walked):
        mark = "OK " if info["ok"] else "FAIL"
        tag = "genesis" if info.get("genesis") else "committed"
        print(
            f"    [{mark}] {info['rid']}  root {str(info['root'])[:16]}…  "
            f"{tag}  created_at={info['created_at']}  "
            f"status={info['status']}  btc_pinned_at={info['btc_pinned_at']}"
        )
    print()
    print(NOTE)

    if exit_code != EXIT_OK:
        return exit_code
    if ots_exit != EXIT_OK:
        return ots_exit
    print("\n  [OK] all presented links verify; anchor-time ordering holds.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
