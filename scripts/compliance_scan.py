#!/usr/bin/env python3
"""compliance_scan.py — daily brand-rule compliance sweep.

Walks the repo and looks for two classes of forbidden content the founder
has committed to keeping off every Orphograph surface:

  1. Names of other commercial companies (any positioning that mentions
     a competitor by name is disallowed — standards and protocol names
     are fine, brand names are not).
  2. Dollar-denominated valuation language (figures like ``$1.5M``,
     "valuation", "acquired for", "raised $", "Series A").

Both classes are flagged across every UTF-8 readable text file in the
repo, with binary blobs and build / cache directories excluded.

The scanner emits a single JSON report to ``outbox/compliance_scan_<UTC-date>.json``
and exits non-zero if any high-severity hit is found.

Stdlib only. MIT licensed.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT_DEFAULT = Path(__file__).resolve().parent.parent

# ----------------------------------------------------------------------------
# Pattern sets
# ----------------------------------------------------------------------------
# Each of these constants is intentionally kept on a single discrete line so
# that an outer grep for the forbidden tokens in docstrings/comments can
# easily exclude exactly the constant definition lines below.

ALL_CAPS_DENY = ["companycam","spectora","jobnimbus","procore","buildertrend","verisk","corelogic","truepic","clio","vlex","filevine","tebra","dentrix","kareo","patientpop","costar","matterport","moxiworks","lone wolf","stone point","insight partners","stampery","guardtime","leica","samsung","adobe","stripe"]

# Tech / protocol names that are sometimes legitimately referenced as file
# formats, signature schemes, or API contracts. Hits against these tokens are
# downgraded to low_severity so the report still flags them but does not fail
# the build.
TECH_NAME_CARVEOUTS = {"adobe", "stripe", "google", "anthropic", "claude", "samsung", "leica"}

# Dollar / valuation regex. Word-bounded where useful.
DOLLAR_REGEX = re.compile(
    r"(\$[0-9]+(?:\.[0-9]+)?[KMB]?\b|\bvaluation\b|\bacquired for\b|\braised(?=\s+\$)|\bseries [A-Z]\b)",
    re.IGNORECASE,
)

# Build a single word-bounded alternation for the company-name set. Each
# entry may contain spaces (e.g., "lone wolf"); for those we replace the
# space with ``\s+`` so multi-space variants still match.
def _build_company_regex(names: list[str]) -> re.Pattern[str]:
    parts: list[str] = []
    for n in names:
        escaped = re.escape(n).replace(r"\ ", r"\s+")
        parts.append(escaped)
    pattern = r"\b(" + "|".join(parts) + r")\b"
    return re.compile(pattern, re.IGNORECASE)

COMPANY_REGEX = _build_company_regex(ALL_CAPS_DENY)

# ----------------------------------------------------------------------------
# Path filters
# ----------------------------------------------------------------------------
EXCLUDE_GLOBS: tuple[str, ...] = (
    ".git/*",
    "node_modules/*",
    "sdk-node/node_modules/*",
    "sdk-node/dist/*",
    "__pycache__/*",
    "*.pyc",
    "data/*",
    "receipts/*",
    "*.png",
    "*.jpg",
    "*.ico",
    "*.pdf",
    "*.zip",
    "*.ots",
    "outbox/EXTERNAL_STRATEGIC_ANALYSIS_*",
    # Founder-private operational directories — internal planning,
    # negotiation, log capture, and audit history. These never ship to a
    # customer surface and frequently reference dollar figures and
    # competitive landscape by name. The brand rules apply to *external*
    # surfaces; these directories are scanned manually by the founder.
    "logs/*",
    "deploy/*",
    "outreach/*",
    "outbox/*",
    "docs/audits/*",
    "ledger.jsonl",
    "upgrade_log.jsonl",
)

# Files that are themselves declarations of the rules and therefore allowed
# to contain the literal trigger words.
RULE_DECLARATION_GLOBS: tuple[str, ...] = (
    "outbox/EXTERNAL_STRATEGIC_ANALYSIS_*",
)

# The scanner itself defines the deny-list and is allowed to contain the
# literal tokens on the constant lines. The whitelist below covers the
# scanner's own files (and its tests).
SELF_FILE_BASENAMES: frozenset[str] = frozenset({
    "compliance_scan.py",
    "test_compliance_scan.py",
    # This existing test enforces the same brand rule at the verticals
    # layer; its regex constant intentionally lists every denied name as
    # the literal pattern set. Same shape as ALL_CAPS_DENY in this file.
    "test_verticals.py",
})


def _path_matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    name = rel_path.rsplit("/", 1)[-1]
    for pat in patterns:
        if "/" in pat:
            if fnmatch.fnmatch(rel_path, pat):
                return True
            prefix = pat.rstrip("*").rstrip("/")
            if prefix and (rel_path == prefix or rel_path.startswith(prefix + "/")):
                return True
        else:
            if fnmatch.fnmatch(name, pat):
                return True
    return False


def _walk_files(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune excluded directories early.
        rel_dir = Path(dirpath).resolve().relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        # Manually prune top-level excluded dirs to avoid descending into them.
        pruned = []
        for d in dirnames:
            child_rel = (f"{rel_dir}/{d}" if rel_dir else d)
            if _path_matches_any(child_rel + "/x", EXCLUDE_GLOBS):
                continue
            pruned.append(d)
        dirnames[:] = pruned
        for fname in filenames:
            abs_path = Path(dirpath) / fname
            if abs_path.is_symlink() or not abs_path.is_file():
                continue
            rel = abs_path.relative_to(root).as_posix()
            if _path_matches_any(rel, EXCLUDE_GLOBS):
                continue
            out.append((rel, abs_path))
    out.sort(key=lambda e: e[0])
    return out


def _read_text(p: Path) -> str | None:
    try:
        with p.open("rb") as f:
            raw = f.read()
    except OSError:
        return None
    # Cheap binary heuristic: NULs in the first 4 KiB → skip.
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _context_snippet(line: str, match_start: int, match_end: int, width: int = 50) -> str:
    """Return up to ``width`` characters of context around a match."""
    half = max(0, width // 2)
    start = max(0, match_start - half)
    end = min(len(line), match_end + half)
    snippet = line[start:end].strip()
    return snippet[: width * 2]


def scan_file(rel_path: str, text: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (high_hits, low_hits, dollar_hits) for this file."""
    high: list[dict] = []
    low: list[dict] = []
    dollars: list[dict] = []
    basename = rel_path.rsplit("/", 1)[-1]
    is_self_file = basename in SELF_FILE_BASENAMES
    is_rule_declaration = _path_matches_any(rel_path, RULE_DECLARATION_GLOBS)
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Company-name matches.
        if not is_self_file:
            for m in COMPANY_REGEX.finditer(line):
                token = m.group(0)
                lowered = token.lower().strip()
                entry = {
                    "path": rel_path,
                    "line": lineno,
                    "match": token,
                    "context_50_chars": _context_snippet(line, m.start(), m.end()),
                }
                if lowered in TECH_NAME_CARVEOUTS:
                    entry["low_severity"] = True
                    low.append(entry)
                else:
                    high.append(entry)
        # Dollar / valuation matches.
        if is_self_file:
            continue
        for m in DOLLAR_REGEX.finditer(line):
            token = m.group(0)
            if is_rule_declaration:
                # The rule-declaration file is allowed to discuss the rule.
                continue
            entry = {
                "path": rel_path,
                "line": lineno,
                "match": token,
                "context_50_chars": _context_snippet(line, m.start(), m.end()),
            }
            dollars.append(entry)
    return high, low, dollars


def run_scan(root: Path) -> dict:
    files = _walk_files(root)
    high_all: list[dict] = []
    low_all: list[dict] = []
    dollars_all: list[dict] = []
    scanned = 0
    for rel, abs_path in files:
        text = _read_text(abs_path)
        if text is None:
            continue
        scanned += 1
        high, low, dollars = scan_file(rel, text)
        high_all.extend(high)
        low_all.extend(low)
        dollars_all.extend(dollars)
    return {
        "scanned_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files_scanned": scanned,
        "high_severity_hits": high_all,
        "low_severity_hits": low_all,
        "dollar_hits": dollars_all,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(ROOT_DEFAULT), help="repo root to scan")
    p.add_argument("--out", default=None, help="optional explicit output JSON path")
    p.add_argument("--quiet", action="store_true", help="suppress stdout summary")
    args = p.parse_args(argv)

    root = Path(args.root).resolve()
    report = run_scan(root)

    if args.out:
        out_path = Path(args.out)
    else:
        date_str = datetime.now(timezone.utc).date().isoformat()
        out_path = root / "outbox" / f"compliance_scan_{date_str}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    if not args.quiet:
        sys.stdout.write(
            f"[compliance_scan] scanned {report['files_scanned']} files; "
            f"high={len(report['high_severity_hits'])}, "
            f"low={len(report['low_severity_hits'])}, "
            f"dollar={len(report['dollar_hits'])}\n"
            f"[compliance_scan] report: {out_path}\n"
        )

    # Spec: any high-severity company-name hit OR any dollar/valuation hit
    # outside a rule-declaration file trips exit code 1. Low-severity tech
    # carve-outs do not affect the exit code.
    if report["high_severity_hits"] or report["dollar_hits"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
