#!/usr/bin/env python3
"""
parity.py — three-way Merkle root parity across every ORPHOGRAPH implementation.

WHY
---
Two verifier defects were found separately. The structural risk is that they are
symptoms: four independent RFC 6962 implementations have never been proven to
agree end-to-end on the same input. If the tree logic has drifted, a folder
anchored by the browser may not verify through an SDK — silently, and forever.

Implementations compared:
  server/merkle.py               canon (Python stdlib)
  sdk-python/orphograph/_merkle  the Python SDK's vendored copy
  sdk-node/dist/merkle.js        the Node SDK
  web/folder.js                  the browser impl (SubtleCrypto)

The test feeds all four the SAME leaves — (relative POSIX path, file SHA-256) —
and requires byte-identical roots. Leaf construction and sort order are part of
what is under test, so each implementation does its own.

GOLDEN VECTORS (the edge cases where RFC 6962 implementations classically drift)
  empty tree · single leaf · odd leaf count at several levels · duplicate
  filenames · unicode paths · paths differing only by case · empty files

`web/folder.js` keeps its core module-private, so the harness writes a temp copy
with a single appended `export { _leafFor, _buildTree };` line. Nothing else is
modified — the algorithm under test is theirs.

Exit 1 if ANY case fails to reach unanimous agreement.

Usage:
    python3 tools/audit/differential/parity.py [--markdown|--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
NODE = shutil.which("node") or "/opt/homebrew/bin/node"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "sdk-python"))


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ───────────────────────── golden vectors ──────────────────────────
def golden_cases() -> list[dict]:
    """Each case is a list of (relative POSIX path, file SHA-256 hex)."""
    d = lambda s: sha(s.encode())  # noqa: E731

    def n(count: int) -> list[tuple[str, str]]:
        return [(f"f{i:02d}.txt", d(f"content {i}")) for i in range(count)]

    return [
        {"name": "empty_tree", "why": "0 leaves — RFC 6962 empty root is SHA-256(\"\")",
         "leaves": []},
        {"name": "single_leaf", "why": "1 leaf — root must be the leaf itself, not re-hashed",
         "leaves": n(1)},
        {"name": "two_leaves", "why": "perfect pair", "leaves": n(2)},
        {"name": "three_leaves_odd_L0", "why": "odd at level 0 — promote vs duplicate",
         "leaves": n(3)},
        {"name": "five_leaves_odd_multi", "why": "odd at two levels", "leaves": n(5)},
        {"name": "seven_leaves_odd_multi", "why": "odd at three levels", "leaves": n(7)},
        {"name": "eight_leaves_balanced", "why": "fully balanced control", "leaves": n(8)},
        {"name": "empty_files", "why": "zero-byte files — digest of empty input",
         "leaves": [("a.txt", sha(b"")), ("b.txt", sha(b""))]},
        {"name": "duplicate_filenames",
         "why": "same basename in different dirs — paths differ, must not collide",
         "leaves": [("x/report.txt", d("one")), ("y/report.txt", d("two"))]},
        {"name": "identical_path_twice",
         "why": "the SAME path twice — degenerate input; all impls must agree on whatever they do",
         "leaves": [("dup.txt", d("one")), ("dup.txt", d("two"))]},
        {"name": "unicode_paths",
         "why": "non-ASCII must be UTF-8 encoded identically before sorting",
         "leaves": [("café/naïve.txt", d("u1")), ("日本語/ファイル.txt", d("u2")),
                    ("emoji/🔒.txt", d("u3"))]},
        {"name": "case_only_difference",
         "why": "A.txt vs a.txt — byte-order sort must be case-SENSITIVE; a case-insensitive "
                "sort reorders leaves and changes the root",
         "leaves": [("A.txt", d("upper")), ("a.txt", d("lower"))]},
        {"name": "sort_boundary_chars",
         "why": "'-' (0x2D) '.' (0x2E) '/' (0x2F) '0' (0x30) straddle the separator byte",
         "leaves": [("a-b.txt", d("dash")), ("a.b.txt", d("dot")),
                    ("a/b.txt", d("slash")), ("a0b.txt", d("zero"))]},
        {"name": "deep_nesting", "why": "long nested path", "leaves":
            [("/".join(f"d{i}" for i in range(12)) + "/deep.txt", d("deep"))]},
    ]


# ───────────────────── python implementations ──────────────────────
def _py_root(module_name: str, leaves: list[tuple[str, str]]) -> tuple[str | None, str]:
    """Drive a Python implementation at the leaf level.

    Both Python copies expose module-private `_leaf_hash` and `_build_levels`
    with the same names; the sort is by UTF-8 byte order of the path
    (server/merkle.py, sdk-python/_merkle.py:151).
    """
    try:
        if module_name == "server":
            from server import merkle as m  # type: ignore
        else:
            from orphograph import _merkle as m  # type: ignore
    except Exception as e:
        return None, f"import failed: {type(e).__name__}: {e}"

    try:
        rows = sorted(leaves, key=lambda e: e[0].encode("utf-8"))
        leaf_hashes = [m._leaf_hash(p, bytes.fromhex(h)) for p, h in rows]
        if not leaf_hashes:
            # Empty tree: RFC 6962 says SHA-256 of the empty string.
            return hashlib.sha256(b"").hexdigest(), "empty-tree convention"
        levels = m._build_levels(leaf_hashes)
        return levels[-1][0].hex(), ""
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ────────────────────── node implementations ───────────────────────
def _node_roots(impl: str, module_path: Path, cases: list[dict]) -> dict:
    job = {
        "impl": impl,
        "module": str(module_path),
        "cases": [{"name": c["name"], "leaves": c["leaves"]} for c in cases],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(job, f)
        job_path = f.name
    try:
        p = subprocess.run([NODE, str(HERE / "parity_bridge.mjs"), job_path],
                           capture_output=True, text=True, timeout=180)
        if not p.stdout.strip():
            return {c["name"]: {"error": (p.stderr or "no output")[:160]} for c in cases}
        return json.loads(p.stdout).get("results", {})
    except Exception as e:
        return {c["name"]: {"error": f"{type(e).__name__}: {e}"} for c in cases}
    finally:
        Path(job_path).unlink(missing_ok=True)


def _shimmed_folder_js(tmp: Path) -> Path:
    """Copy web/folder.js and append ONE export line so its private core is
    reachable. The algorithm itself is untouched."""
    src = (REPO / "web" / "folder.js").read_text(encoding="utf-8")
    dst = tmp / "folder_shim.mjs"
    dst.write_text(src + "\n\nexport { _leafFor, _buildTree, _byteCompare };\n",
                   encoding="utf-8")
    return dst


# ───────────────────────────── runner ──────────────────────────────
IMPLS = ["server/merkle.py", "sdk-python/_merkle", "sdk-node/dist", "web/folder.js"]


def run() -> tuple[list[dict], int]:
    cases = golden_cases()
    tmp = Path(tempfile.mkdtemp(prefix="orpho_parity_"))
    try:
        node_sdk = _node_roots("sdk_node", REPO / "sdk-node" / "dist" / "merkle.js", cases)
        node_web = _node_roots("web_folder", _shimmed_folder_js(tmp), cases)

        rows = []
        for c in cases:
            got: dict[str, tuple[str | None, str]] = {}
            r, note = _py_root("server", c["leaves"]);        got["server/merkle.py"] = (r, note)
            r, note = _py_root("sdk", c["leaves"]);           got["sdk-python/_merkle"] = (r, note)
            for label, res in (("sdk-node/dist", node_sdk.get(c["name"], {})),
                               ("web/folder.js", node_web.get(c["name"], {}))):
                got[label] = (res.get("root"), res.get("error", ""))

            roots = {v[0] for v in got.values() if v[0]}
            errored = [k for k, v in got.items() if not v[0]]
            agree = len(roots) == 1 and not errored
            rows.append({"case": c, "got": got, "agree": agree,
                         "distinct": len(roots), "errored": errored})
        failures = sum(1 for r in rows if not r["agree"])
        return rows, failures
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render(rows: list[dict], failures: int) -> str:
    out = ["# Three-way Merkle parity — RFC 6962 golden vectors", ""]
    out.append("Implementations: " + " · ".join(f"`{i}`" for i in IMPLS))
    out.append("")
    out.append("A row passes only if **all four produce a byte-identical root**.")
    out.append("")
    out.append("| case | why it matters | " + " | ".join(f"`{i}`" for i in IMPLS) + " | verdict |")
    out.append("|---|---|" + "---|" * (len(IMPLS) + 1))
    for r in rows:
        cells = []
        for i in IMPLS:
            root, note = r["got"][i]
            cells.append(f"`{root[:12]}…`" if root else f"**{(note or 'ERROR')[:34]}**")
        verdict = "AGREE" if r["agree"] else f"**DRIFT ({r['distinct']} distinct)**"
        out.append(f"| `{r['case']['name']}` | {r['case']['why']} | " + " | ".join(cells)
                   + f" | {verdict} |")
    out.append("")
    if failures:
        out.append(f"**FAIL — {failures} of {len(rows)} vectors did not reach unanimous agreement.**")
        out.append("")
        for r in rows:
            if r["agree"]:
                continue
            out.append(f"### `{r['case']['name']}`")
            for i in IMPLS:
                root, note = r["got"][i]
                out.append(f"- `{i}` → {root or 'ERROR: ' + note}")
            out.append("")
    else:
        out.append(f"**PASS — all {len(rows)} vectors agree across all four implementations.**")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows, failures = run()
    if a.json:
        print(json.dumps([{
            "case": r["case"]["name"], "why": r["case"]["why"], "agree": r["agree"],
            "roots": {k: v[0] for k, v in r["got"].items()},
            "errors": {k: v[1] for k, v in r["got"].items() if not v[0]},
        } for r in rows], indent=2))
    else:
        print(render(rows, failures))
    print(f"\n{len(rows)} vectors · {failures} drift", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
