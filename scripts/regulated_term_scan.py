#!/usr/bin/env python3
"""regulated_term_scan.py — gate against NEW regulated/legal-status claims on
the public web surface.

Orphograph's true, defensible framing is "Bitcoin-anchored proof a file existed
before a date." Words that imply a *regulated legal act/status* — notarization,
court-admissibility, legally-binding, qualified eIDAS trust services — create
UPL / false-status exposure. compliance_scan.py only gates competitor names +
dollar/valuation figures; it does NOT cover this (council finding 2026-06-21).
This is that missing gate.

Design (low-noise, so it doesn't cry wolf like a naive scanner):
  * Only AFFIRMATIVE status terms count — a match is cleared if it is negated
    nearby ("we are *not* a notary") or the page carries a disclaimer.
  * Bare technical/comparative mentions (C2PA, "unlike eIDAS") are NOT flagged —
    only self-asserted regulated status is.
  * A BASELINE (regulated_term_baseline.json) grandfathers today's accepted
    usage (e.g. the disclaimed "empirical notary" tagline). The gate fails (exit
    1) ONLY on a term/page NOT in the baseline — i.e. a genuinely NEW claim.
  * --update-baseline re-snapshots accepted usage (founder action).
  * --report-only never exits nonzero.

stdlib only. Default scans <repo>/web/*.html.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Self-asserted regulated legal act/status (NOT bare technical mentions).
REGULATED = re.compile(
    r"""(?ix)\b(
        notar(?:y|ies|ial|ize|ized|ise|ised|ization|isation)
      | court[-\s]?admissible
      | legally[-\s]+binding
      | apostille
      | sworn[-\s]+(?:affidavit|statement|declaration)
      | qualified[-\s]+(?:electronic[-\s]+)?(?:trust[-\s]+service|signature|timestamp)(?:[-\s]+provider)?
    )\b""",
)
# A negation just before the term means it's a disclaimer ("not a notary").
NEG = re.compile(r"(?i)\b(?:not|never|no|n't|without|non|isn'?t|aren'?t|won'?t|don'?t|doesn'?t|neither)\b")
# A page-level disclaimer clears every term on that page.
DISCLAIMER = re.compile(
    r"""(?ix)
        not\s+(?:a\s+)?(?:law\s+firm|notary|lawyer|attorney|qualified[^.<\n]{0,80}|financial\s+advisor)
      | not\s+court[-\s]?admissible
      | not\s+legal\s+advice
      | no\s+legal\s+(?:effect|advice|status)
      | does\s+not\s+constitute\s+legal
    """,
)
BASELINE_PATH = Path(__file__).resolve().parent / "regulated_term_baseline.json"


def _norm(term: str) -> str:
    return re.sub(r"\s+", " ", term.lower().strip())


def scan_text(text: str) -> list[str]:
    """Return the affirmative (non-negated) regulated terms on a page that is
    not globally disclaimed. Empty if the page is clean or disclaimed."""
    if DISCLAIMER.search(text):
        return []
    risky = set()
    for m in REGULATED.finditer(text):
        window = text[max(0, m.start() - 60):m.start()]
        if NEG.search(window):
            continue  # negated == disclaimed in place
        risky.add(_norm(m.group(1)))
    return sorted(risky)


def scan_dir(web_dir: Path) -> dict[str, list[str]]:
    out = {}
    for f in sorted(web_dir.glob("*.html")):
        terms = scan_text(f.read_text(encoding="utf-8", errors="replace"))
        if terms:
            out[f.name] = terms
    return out


def _load_baseline(p: Path) -> dict[str, list[str]]:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gate against NEW regulated-status web copy.")
    ap.add_argument("--web", default=None, help="web dir (default <repo>/web)")
    ap.add_argument("--baseline", default=str(BASELINE_PATH), help="accepted-usage baseline json")
    ap.add_argument("--update-baseline", action="store_true", help="re-snapshot accepted usage and exit 0")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--report-only", action="store_true", help="never exit nonzero")
    a = ap.parse_args(argv)

    web = Path(a.web).expanduser() if a.web else Path(__file__).resolve().parent.parent / "web"
    if not web.is_dir():
        print(f"ERROR: web dir not found: {web}", file=sys.stderr)
        return 2

    current = scan_dir(web)
    bl_path = Path(a.baseline).expanduser()

    if a.update_baseline:
        bl_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"baseline updated: {bl_path} ({len(current)} page(s) of accepted usage)")
        return 0

    baseline = _load_baseline(bl_path)
    # A finding is NEW if the page is absent from the baseline, or uses a term
    # not previously accepted for that page.
    new = {}
    for page, terms in current.items():
        accepted = set(baseline.get(page, []))
        fresh = [t for t in terms if t not in accepted]
        if fresh:
            new[page] = fresh

    if a.json:
        print(json.dumps({"web": str(web), "new_findings": new, "accepted_baseline": len(baseline)}, indent=2))
    elif new:
        print(f"REGULATED-TERM GATE: {len(new)} page(s) introduce a NEW regulated-status term with no disclaimer:")
        for page, terms in new.items():
            print(f"  ✗ {page}: {', '.join(terms)}")
        print("Fix: add a disclaimer to the page (e.g. 'not a law firm, not a notary, not a qualified "
              "electronic-trust-service provider'), reword to the proof-of-existence framing, or — if "
              "intentional & disclaimed — run with --update-baseline.")
    else:
        print(f"REGULATED-TERM GATE OK — no new regulated-status terms beyond the accepted baseline "
              f"({len(baseline)} page(s) grandfathered).")

    return 0 if (a.report_only or not new) else 1


if __name__ == "__main__":
    sys.exit(main())
