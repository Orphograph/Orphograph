#!/usr/bin/env python3
"""prospect_helper.py — bio text → vertical match + opener + CSV row.

Founder pastes a LinkedIn bio (or any short prose describing a person), the
script classifies them into one of the three live verticals (accounting /
construction / legal_solos), extracts one specific public-footprint detail,
and emits a single CSV row that appends to data/prospects.csv.

Pure-stdlib, pure-heuristic — no LinkedIn API, no ML model. Designed to cut
per-prospect prep time from ~5 minutes to ~30 seconds.

Usage:
  # Interactive mode: paste bio, press Ctrl-D
  python3 scripts/prospect_helper.py --email alice@cpa-firm.com --name Alice

  # Pipe a bio file
  cat bio.txt | python3 scripts/prospect_helper.py --email bob@example.com --name Bob

  # Force a vertical (skip auto-classify)
  python3 scripts/prospect_helper.py --email c@x.com --name C --vertical legal_solos < bio.txt

  # Append directly to data/prospects.csv
  python3 scripts/prospect_helper.py --email d@x.com --name D --append < bio.txt
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PROSPECTS_CSV = REPO / "data" / "prospects.csv"
CSV_FIELDS = ["email", "first_name", "vertical", "public_detail", "added_iso"]

# Vertical keyword maps — accumulate matches, highest score wins ties.
# Keywords drawn from the buyer-profile sections in outbox/COLD_OUTREACH_*.md.
VERTICAL_KEYWORDS: dict[str, list[str]] = {
    "accounting": [
        # roles
        "controller", "bookkeeper", "bookkeeping", "cpa", "accountant",
        "accounting", "audit", "auditor", "auditing", "vp finance",
        "vp of finance", "chief financial officer", "cfo", "treasurer",
        # firm types
        "outsourced accounting", "fractional cfo", "tax practice",
        "tax firm", "advisory firm",
        # work signals
        "month-end", "close package", "trial balance", "general ledger",
        "tax season", "10-k", "10-q", "audit committee", "lender review",
        "diligence", "quickbooks", "xero", "netsuite", "sage",
        # associations
        "aicpa", "aipb", "nacpb", "state society of cpas",
    ],
    "construction": [
        # roles
        "owner", "operator", "contractor", "subcontractor", "foreman",
        "project manager", "site supervisor", "estimator",
        # trades
        "roofing", "roofer", "restoration", "remediation", "remediator",
        "general contractor", "gc", "framer", "siding",
        # work signals
        "storm damage", "hail", "wildfire", "insurance claim",
        "supplement", "scope of work", "xactimate", "job site",
        "field documentation", "photo documentation",
        # associations
        "nrca", "rcat", "raca", "iicrc", "rrt", "asa",
    ],
    "legal_solos": [
        # roles
        "attorney", "lawyer", "esq", "esquire", "partner",
        "named partner", "of counsel", "solo practitioner",
        "managing partner", "general counsel",
        # practice areas
        "personal injury", "family law", "criminal defense",
        "estate planning", "estates and trusts", "estates & trusts",
        "small claims", "appellate", "litigation",
        # work signals
        "exhibit", "evidence", "chain of custody", "deposition",
        "motion to compel", "discovery", "case file", "subpoena",
        # associations
        "aba", "state bar", "trial lawyers association",
    ],
}

# Vertical-specific public-detail templates (heuristic — pick first matching).
DETAIL_PATTERNS: list[tuple[str, str]] = [
    (r"(\d+\+?\s*(?:years|yrs))[^.]*", "your {match}"),
    (r"(speaking at|spoke at|panel at)[^.]*?([A-Z][\w&-]+(?:\s+[A-Z][\w&-]+){0,3})", "your panel at {2}"),
    (r"(founded|co-founded|started)[^.]*?(in\s+\d{4}|in\s+[A-Z]\w+)", "founding the firm {2}"),
    (r"(LinkedIn newsletter|substack|blog)[^.]*", "your {match}"),
    (r"(award|honor|recognized)[^.]*", "the {match}"),
    (r"(podcast|interview|guest on)[^.]*", "your {match}"),
]


def _classify_vertical(text: str) -> tuple[str, dict[str, int]]:
    """Return (best_vertical, scores_per_vertical)."""
    text_lc = text.lower()
    scores: dict[str, int] = {}
    for vertical, keywords in VERTICAL_KEYWORDS.items():
        s = 0
        for kw in keywords:
            # word-boundary match for short keywords, substring for long phrases
            if " " in kw or "-" in kw:
                if kw in text_lc:
                    s += 2
            else:
                if re.search(rf"\b{re.escape(kw)}\b", text_lc):
                    s += 1
        scores[vertical] = s
    if max(scores.values()) == 0:
        return "", scores
    best = max(scores, key=lambda k: scores[k])
    return best, scores


def _extract_public_detail(text: str) -> str:
    """Heuristic: pick one distinctive concrete fact from the bio."""
    for pat, template in DETAIL_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                if "{2}" in template:
                    return template.format(m.group(0), m.group(2)).strip()
                return template.format(match=m.group(0)).strip()
            except (IndexError, KeyError):
                continue
    # Fallback: the first complete sentence under 80 chars.
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for s in sentences:
        s = s.strip()
        if 20 <= len(s) <= 80:
            return s.rstrip(".")
    return ""


def _suggested_opener(first_name: str, vertical: str, detail: str) -> str:
    if not first_name:
        return ""
    if detail:
        return f"{first_name}, noticed {detail} — that's why I wrote."
    return f"{first_name},"


def _append_to_csv(row: dict[str, str]) -> None:
    PROSPECTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not PROSPECTS_CSV.exists()
    with PROSPECTS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True, help="recipient first name")
    ap.add_argument(
        "--vertical",
        choices=sorted(VERTICAL_KEYWORDS.keys()),
        help="force a vertical; default auto-classify from bio",
    )
    ap.add_argument("--append", action="store_true", help="append row to data/prospects.csv")
    ap.add_argument("bio_file", nargs="?", help="path to bio file; default reads stdin")
    args = ap.parse_args()

    if args.bio_file:
        bio = pathlib.Path(args.bio_file).read_text(encoding="utf-8", errors="replace")
    else:
        if sys.stdin.isatty():
            print("paste bio, then Ctrl-D:", file=sys.stderr)
        bio = sys.stdin.read()

    if not bio.strip():
        print("no bio text provided", file=sys.stderr)
        return 1

    auto_vertical, scores = _classify_vertical(bio)
    chosen_vertical = args.vertical or auto_vertical
    detail = _extract_public_detail(bio)
    opener = _suggested_opener(args.name, chosen_vertical, detail)

    print("--- classification ---")
    for v, s in sorted(scores.items(), key=lambda kv: -kv[1]):
        marker = "  <- chosen" if v == chosen_vertical else ""
        print(f"  {v:15s} score={s}{marker}")
    if not chosen_vertical:
        print("  no vertical matched — bio is too short, or wrong audience", file=sys.stderr)
        return 1

    print(f"\n--- extracted detail ---\n  {detail or '(none — bio too generic)'}")
    print(f"\n--- suggested opener (line 1 of email) ---\n  {opener}")

    row = {
        "email": args.email.strip().lower(),
        "first_name": args.name.strip(),
        "vertical": chosen_vertical,
        "public_detail": detail,
        "added_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    }

    if args.append:
        _append_to_csv(row)
        print(f"\nappended to {PROSPECTS_CSV.relative_to(REPO)}")
    else:
        print("\n--- CSV row (use --append to write) ---")
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
        if not PROSPECTS_CSV.exists():
            writer.writeheader()
        writer.writerow(row)
        print(buf.getvalue().rstrip())

    return 0


if __name__ == "__main__":
    sys.exit(main())
