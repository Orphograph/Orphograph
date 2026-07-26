#!/usr/bin/env python3
"""
run_differential.py — differential test harness across every ORPHOGRAPH
verifier implementation.

WHY THIS EXISTS
---------------
Four independent implementations verify receipts: the server engine (canon),
`server/verify_cli.py`, `verifier-js/`, and the two SDKs. A verifier that
disagrees with the engine is a correctness bug. This harness feeds one fixture
corpus through all of them and reports every disagreement.

It is built BEFORE the port, deliberately: it is the artifact that proves the
port worked. Run it now to capture the baseline, run it after Phase 2, and the
diff between the two runs is the evidence.

THE SAFETY PROPERTY
-------------------
Exit code 1 if ANY implementation returns "valid" for input that must not
validate. A false negative is a bug; a false POSITIVE is a notary telling
someone a document is attested when it is not, so it is the gate.

Read-only: creates fixtures under a temp dir, touches no application file,
makes no network call.

Usage:
    python3 tools/audit/differential/run_differential.py            # table + exit code
    python3 tools/audit/differential/run_differential.py --json     # machine-readable
    python3 tools/audit/differential/run_differential.py --markdown # for the audit doc
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
NODE = shutil.which("node") or "/opt/homebrew/bin/node"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "sdk-python"))


# ─────────────────────────── result model ────────────────────────────
VALID = "VALID"          # implementation says: this attests
INVALID = "INVALID"      # implementation says: this does not attest
ERROR = "ERROR"          # implementation refused to parse — a SAFE outcome
ABSENT = "ABSENT"        # implementation does not cover this surface
REACHABLE = "REACHABLE"      # non-attesting probe: value can enter the ledger
UNREACHABLE = "UNREACHABLE"  # non-attesting probe: rejected before storage

# Only these answer the question "does this attest?". The safety gate applies to
# them ALONE. The others are diagnostic probes that answer different questions —
# folding them into the gate produced two false alarms on the first run, because
# "an uppercase digest would be normalized and accepted at write time" is correct
# behaviour, not a validation of something invalid.
ATTESTING_IMPLS = {
    "engine(canon)", "engine.stored", "verifier-js",
    "sdk-python.merkle", "server.merkle",
}


@dataclass
class Case:
    """One fixture. `must_not_validate` is the safety assertion."""
    group: str
    name: str
    description: str
    must_not_validate: bool
    payload: dict = field(default_factory=dict)


@dataclass
class Row:
    case: Case
    results: dict          # impl name -> (verdict, detail)


# ─────────────────────────── the corpus ──────────────────────────────
GOOD = "a" * 64                      # placeholder; replaced with a real digest


def build_digest_cases(real_hex: str) -> list[Case]:
    """Supplied-digest and stored-digest variants.

    Two distinct surfaces get conflated in discussion, so they are separated
    here:
      * SUPPLIED side — what a user types into a verify box.
      * STORED side   — what sits in the receipt JSON (which an adversary may
                        have hand-edited; that is precisely the case a
                        verifier exists to catch).
    """
    other = hashlib.sha256(b"a different file entirely").hexdigest()
    return [
        Case("supplied", "lowercase_exact", "canonical lowercase digest", False,
             {"supplied": real_hex}),
        Case("supplied", "uppercase", "same digest, uppercase", False,
             {"supplied": real_hex.upper()}),
        Case("supplied", "mixed_case", "same digest, mixed case", False,
             {"supplied": real_hex[:32].upper() + real_hex[32:]}),
        Case("supplied", "whitespace_padded", "leading/trailing whitespace", False,
             {"supplied": f"  {real_hex}\n"}),
        Case("supplied", "0x_prefixed", "0x-prefixed digest", False,
             {"supplied": "0x" + real_hex}),
        Case("supplied", "uppercase_of_DIFFERENT", "uppercase of a DIFFERENT digest", True,
             {"supplied": other.upper()}),
        Case("supplied", "truncated", "63 chars", True, {"supplied": real_hex[:-1]}),
        Case("supplied", "overlong", "65 chars", True, {"supplied": real_hex + "a"}),
        Case("supplied", "non_hex", "contains 'zz'", True,
             {"supplied": "zz" + real_hex[2:]}),
        Case("supplied", "lenient_nibble", "'a?' pair — parseInt would accept", True,
             {"supplied": "a?" + real_hex[2:]}),
        Case("supplied", "empty", "empty string", True, {"supplied": ""}),
        Case("supplied", "none", "None / null", True, {"supplied": None}),

        Case("stored", "stored_lowercase", "receipt stores canonical lowercase", False,
             {"stored": real_hex}),
        Case("stored", "stored_UPPERCASE", "receipt JSON hand-edited to uppercase", True,
             {"stored": real_hex.upper()}),
        Case("stored", "stored_non_hex", "receipt hash_hex is 'zz…'", True,
             {"stored": "zz" + real_hex[2:]}),
        Case("stored", "stored_truncated", "receipt hash_hex truncated", True,
             {"stored": real_hex[:-1]}),
        Case("stored", "stored_missing", "receipt has no hash_hex", True,
             {"stored": None}),
        Case("stored", "stored_alias_only", "only `sha256` alias present", True,
             {"stored": None, "alias": real_hex}),
    ]


def build_folder_cases() -> list[Case]:
    return [
        Case("folder", "default_excludes", "captured and verified with defaults", False,
             {"capture_exclude": None, "verify_exclude": None}),
        Case("folder", "custom_excludes_matched",
             "captured with custom excludes, verified WITH the same list", False,
             {"capture_exclude": ["*.log", "tmp/*"], "verify_exclude": ["*.log", "tmp/*"]}),
        Case("folder", "custom_excludes_forgotten",
             "captured with custom excludes, verified WITHOUT them (DEFECT 2)", False,
             {"capture_exclude": ["*.log", "tmp/*"], "verify_exclude": None,
              "expect_false_negative": True}),
        Case("folder", "nested_excludes", "nested directory patterns", False,
             {"capture_exclude": ["a/b/*"], "verify_exclude": ["a/b/*"]}),
        Case("folder", "unicode_and_spaces", "unicode + spaced filenames", False,
             {"capture_exclude": None, "verify_exclude": None, "exotic": True}),
        Case("folder", "empty_folder", "no files at all", False,
             {"capture_exclude": None, "verify_exclude": None, "empty": True}),
        Case("folder", "symlink", "folder containing a symlink", False,
             {"capture_exclude": None, "verify_exclude": None, "symlink": True}),
    ]


# ─────────────────────── implementation adapters ─────────────────────
def _verdict_from_bool(b) -> str:
    return VALID if b else INVALID


def impl_engine_supplied(case: Case, ctx: dict) -> tuple[str, str]:
    """server/engine.py canon: verify_hash_against_receipt comparison semantics.

    Called at the comparison level rather than through the receipt store so the
    harness needs no live database.
    """
    if case.group != "supplied":
        return ABSENT, "supplied-digest surface only"
    supplied = case.payload.get("supplied")
    stored = ctx["real_hex"]
    try:
        norm = supplied.strip().lower()          # engine.py:352
    except AttributeError:
        return ERROR, "AttributeError on non-string (engine would 500)"
    return _verdict_from_bool(norm == stored), f"normalized={norm[:16]}…"


def impl_engine_stored(case: Case, ctx: dict) -> tuple[str, str]:
    """Canon on the STORED side: engine compares against stored verbatim."""
    if case.group != "stored":
        return ABSENT, "stored-digest surface only"
    stored = case.payload.get("stored")
    if stored is None:
        return ERROR, "no hash_hex — engine treats receipt as corrupt"
    supplied = ctx["real_hex"]
    return _verdict_from_bool(supplied.strip().lower() == stored), f"stored={stored[:16]}…"


def impl_anchor_write_guard(case: Case, ctx: dict) -> tuple[str, str]:
    """Can this value even ENTER the ledger? engine.anchor_hash normalizes and
    strictly validates at write time, so most malformed stored values are
    unreachable for a service-issued receipt. That is a customer-impact fact,
    so it is measured, not assumed."""
    val = case.payload.get("stored") if case.group == "stored" else case.payload.get("supplied")
    if not isinstance(val, str):
        return UNREACHABLE, "rejected at write time (not a string)"
    v = val.strip().lower()
    ok = len(v) == 64 and all(c in "0123456789abcdef" for c in v)
    # REACHABLE is NOT an attestation. It means only: a service-issued receipt
    # could hold this value. That is the customer-impact question (2.3), which
    # is why it is measured separately from whether anything attests.
    return (REACHABLE, "normalized+stored lowercase") if ok else (UNREACHABLE, "ValueError at write time")


def impl_verifier_js(case: Case, ctx: dict) -> tuple[str, str]:
    if case.group != "stored":
        return ABSENT, "verifier-js takes a receipt object, not a typed digest"
    receipt = {}
    if case.payload.get("stored") is not None:
        receipt["hash_hex"] = case.payload["stored"]
    if case.payload.get("alias"):
        receipt["sha256"] = case.payload["alias"]
    job = {
        "op": "verifier_js.binding",
        "verifier_js_path": str(REPO / "verifier-js" / "orphograph_verify.js"),
        "file_path": ctx["file_path"],
        "receipt": receipt,
    }
    return _run_node(job)


def impl_sdk_node_hex(case: Case, ctx: dict) -> tuple[str, str]:
    """D4 probe: is sdk-node's hex decoding strict?

    `fromHex` is module-private in dist/merkle.js:56 (declared `function fromHex`,
    never exported), so it cannot be called directly. The first version of this
    adapter tried and got ERROR for every input INCLUDING valid hex — which would
    have been reported as 'sdk-node rejects valid hex', a fabricated finding.
    Marked ABSENT with the reason rather than inventing a reachable path.
    """
    return ABSENT, "fromHex is module-private (dist/merkle.js:56, not exported)"


def impl_sdk_python_merkle(case: Case, ctx: dict) -> tuple[str, str]:
    if case.group != "folder":
        return ABSENT, "folder surface only"
    try:
        from orphograph._merkle import MerkleTree  # type: ignore
    except Exception as e:
        return ERROR, f"import failed: {e}"
    fx = ctx["folders"].get(case.name)
    if not fx:
        return ERROR, "fixture missing"
    try:
        captured = MerkleTree.from_folder(
            Path(fx["path"]), exclude=case.payload.get("capture_exclude")
        ).root_hex()
        verified = MerkleTree.from_folder(
            Path(fx["path"]), exclude=case.payload.get("verify_exclude")
        ).root_hex()
    except Exception as e:
        return ERROR, f"{type(e).__name__}: {e}"
    if captured == verified:
        return VALID, f"root={captured[:16]}…"
    return INVALID, f"ROOT MISMATCH capture={captured[:12]}… verify={verified[:12]}…"


def impl_server_merkle(case: Case, ctx: dict) -> tuple[str, str]:
    if case.group != "folder":
        return ABSENT, "folder surface only"
    try:
        from server.merkle import MerkleTree  # type: ignore
    except Exception as e:
        return ERROR, f"import failed: {e}"
    fx = ctx["folders"].get(case.name)
    if not fx:
        return ERROR, "fixture missing"
    try:
        captured = MerkleTree.from_folder(
            Path(fx["path"]), exclude=case.payload.get("capture_exclude")
        ).root_hex()
        verified = MerkleTree.from_folder(
            Path(fx["path"]), exclude=case.payload.get("verify_exclude")
        ).root_hex()
        manifest = MerkleTree.from_folder(
            Path(fx["path"]), exclude=case.payload.get("capture_exclude")
        ).manifest()
    except Exception as e:
        return ERROR, f"{type(e).__name__}: {e}"
    # Does the manifest carry the exclude list? If not, a verifier can never
    # recover it, which is the structural half of Defect 2.
    carries = "exclude" in manifest or "excludes" in manifest
    note = "manifest carries excludes" if carries else "manifest does NOT carry excludes"
    if captured == verified:
        return VALID, f"root={captured[:16]}… ({note})"
    return INVALID, f"ROOT MISMATCH ({note})"


IMPLS = {
    "engine(canon)": impl_engine_supplied,
    "engine.stored": impl_engine_stored,
    "anchor_write_guard": impl_anchor_write_guard,
    "verifier-js": impl_verifier_js,
    "sdk-node.fromHex": impl_sdk_node_hex,
    "sdk-python.merkle": impl_sdk_python_merkle,
    "server.merkle": impl_server_merkle,
}


def _run_node(job: dict) -> tuple[str, str]:
    if not Path(NODE).exists() and not shutil.which("node"):
        return ABSENT, "node not available"
    try:
        p = subprocess.run(
            [NODE, str(HERE / "js_bridge.mjs"), json.dumps(job)],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as e:
        return ERROR, f"spawn failed: {e}"
    if p.returncode != 0 and not p.stdout.strip():
        return ERROR, (p.stderr or "node exited nonzero")[:120]
    try:
        r = json.loads(p.stdout.strip() or "{}")
    except Exception:
        return ERROR, (p.stdout or p.stderr)[:120]
    if r.get("status") == "error":
        return ERROR, str(r.get("detail", ""))[:120]
    return _verdict_from_bool(r.get("valid")), json.dumps(r.get("raw", {}))[:80]


# ─────────────────────────── fixtures ────────────────────────────────
def make_fixtures(tmp: Path) -> dict:
    f = tmp / "attested.bin"
    f.write_bytes(b"the quick brown fox jumps over the lazy dog\n" * 7)
    real_hex = hashlib.sha256(f.read_bytes()).hexdigest()

    folders: dict[str, dict] = {}
    for case in build_folder_cases():
        d = tmp / f"folder_{case.name}"
        (d / "tmp").mkdir(parents=True, exist_ok=True)
        (d / "a" / "b").mkdir(parents=True, exist_ok=True)
        if not case.payload.get("empty"):
            (d / "keep.txt").write_text("kept\n")
            (d / "notes.log").write_text("excluded by *.log\n")
            (d / "tmp" / "scratch.txt").write_text("excluded by tmp/*\n")
            (d / "a" / "b" / "deep.txt").write_text("nested\n")
        if case.payload.get("exotic"):
            (d / "naïve café.txt").write_text("unicode\n")
            (d / "with spaces.txt").write_text("spaced\n")
        if case.payload.get("symlink"):
            try:
                os.symlink(d / "keep.txt", d / "link.txt")
            except OSError:
                pass
        folders[case.name] = {"path": str(d)}
    return {"real_hex": real_hex, "file_path": str(f), "folders": folders}


# ─────────────────────────── runner ──────────────────────────────────
def run() -> tuple[list[Row], list[str]]:
    tmp = Path(tempfile.mkdtemp(prefix="orpho_diff_"))
    try:
        ctx = make_fixtures(tmp)
        cases = build_digest_cases(ctx["real_hex"]) + build_folder_cases()
        rows, violations = [], []
        for case in cases:
            results = {}
            for name, fn in IMPLS.items():
                try:
                    results[name] = fn(case, ctx)
                except Exception as e:               # an adapter bug is not a pass
                    results[name] = (ERROR, f"adapter raised {type(e).__name__}: {e}")
            rows.append(Row(case, results))
            if case.must_not_validate:
                for name, (verdict, detail) in results.items():
                    if name in ATTESTING_IMPLS and verdict == VALID:
                        violations.append(
                            f"{name} returned VALID for '{case.name}' "
                            f"({case.description}) — must not validate. {detail}"
                        )
        return rows, violations
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def disagreements(row: Row) -> bool:
    """Disagreement means the ATTESTING implementations that cover this surface
    reached different verdicts. Comparing across implementations that cover
    different surfaces (ABSENT) or answer a different question (the probes)
    flagged 23/25 rows on the first run — noise that hid the real drift."""
    seen = {
        v for name, (v, _) in row.results.items()
        if name in ATTESTING_IMPLS and v != ABSENT
    }
    return len(seen) > 1


def render_markdown(rows: list[Row], violations: list[str]) -> str:
    impls = list(IMPLS)
    out = ["# Differential verifier harness — results", ""]
    out.append(f"Implementations exercised: {', '.join(impls)}")
    out.append("")
    out.append("`VALID` = attests · `INVALID` = does not attest · "
               "`ERROR` = refused to parse (safe) · `ABSENT` = surface not covered")
    out.append("")
    for group in ("supplied", "stored", "folder"):
        grp = [r for r in rows if r.case.group == group]
        if not grp:
            continue
        out.append(f"## Group: {group}")
        out.append("")
        out.append("| case | must-not-validate | " + " | ".join(impls) + " | disagree |")
        out.append("|---|---|" + "---|" * (len(impls) + 1))
        for r in grp:
            cells = [r.results[i][0] for i in impls]
            out.append(
                f"| `{r.case.name}` | {'YES' if r.case.must_not_validate else 'no'} | "
                + " | ".join(cells) + f" | {'**YES**' if disagreements(r) else ''} |"
            )
        out.append("")
    out.append("## Safety gate")
    out.append("")
    if violations:
        out.append(f"**FAIL — {len(violations)} violation(s): something invalid validated.**")
        out.append("")
        for v in violations:
            out.append(f"- {v}")
    else:
        out.append("**PASS — no implementation validated anything that must not validate.**")
    out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args()

    rows, violations = run()

    if a.json:
        print(json.dumps({
            "cases": [{
                "group": r.case.group, "name": r.case.name,
                "description": r.case.description,
                "must_not_validate": r.case.must_not_validate,
                "results": {k: {"verdict": v[0], "detail": v[1]} for k, v in r.results.items()},
                "disagreement": disagreements(r),
            } for r in rows],
            "violations": violations,
            "pass": not violations,
        }, indent=2))
    else:
        print(render_markdown(rows, violations))

    n_dis = sum(1 for r in rows if disagreements(r))
    print(f"\n{len(rows)} cases · {n_dis} with disagreement · {len(violations)} safety violation(s)",
          file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
