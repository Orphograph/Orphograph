#!/usr/bin/env python3
"""import_prospects.py — promote rows from a source CSV into data/prospects.csv.

Use cases:
  - You ran scripts/prospect_helper.py without --append, copied rows to a
    file → run this to import.
  - You exported a CSV from Apollo/Hunter/Clay/LinkedIn Sales Navigator →
    map the columns and import.
  - You staged verified prospects to data/prospects_staging.csv → review,
    then promote.

Strict mode:
  - Rejects rows missing email, vertical, or added_iso.
  - Rejects rows whose vertical is not in the live allowlist
    (accounting / construction / legal_solos).
  - De-dupes against data/prospects.csv by email (case-insensitive).
  - De-dupes against data/suppressions.jsonl (never re-add a STOP'd contact).

Usage:
  python3 scripts/import_prospects.py data/prospects_staging.csv
  python3 scripts/import_prospects.py /path/to/apollo_export.csv \\
      --map email=Email,first_name=First Name,vertical=Industry,public_detail=Title
  python3 scripts/import_prospects.py path.csv --dry-run
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
PROSPECTS_CSV = REPO / "data" / "prospects.csv"
SUPPRESSIONS = REPO / "data" / "suppressions.jsonl"
VALID_VERTICALS = {"accounting", "construction", "legal_solos"}
CSV_FIELDS = ["email", "first_name", "vertical", "public_detail", "added_iso"]
EMAIL_RE = re.compile(r"^[a-zA-Z0-9._+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _existing_emails() -> set[str]:
    out: set[str] = set()
    if PROSPECTS_CSV.exists():
        with PROSPECTS_CSV.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                e = (row.get("email") or "").strip().lower()
                if e:
                    out.add(e)
    return out


def _suppressed_emails() -> set[str]:
    out: set[str] = set()
    if SUPPRESSIONS.exists():
        for line in SUPPRESSIONS.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if row.get("email"):
                    out.add(row["email"].strip().lower())
            except json.JSONDecodeError:
                continue
    return out


def _normalize_row(row: dict, mapping: dict[str, str]) -> dict | None:
    """Apply column mapping and produce a canonical row, or None if invalid."""
    out: dict = {}
    for canon in CSV_FIELDS:
        src = mapping.get(canon, canon)
        out[canon] = (row.get(src) or "").strip()
    if not out["added_iso"]:
        out["added_iso"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    out["email"] = out["email"].lower()
    return out


def _validate(row: dict) -> str | None:
    """Return None if valid, else a reason string."""
    if not row["email"]:
        return "missing email"
    if not EMAIL_RE.match(row["email"]):
        return f"invalid email format: {row['email']}"
    if not row["vertical"]:
        return "missing vertical"
    if row["vertical"] not in VALID_VERTICALS:
        return f"unknown vertical: {row['vertical']!r} (allowed: {sorted(VALID_VERTICALS)})"
    if not row["first_name"]:
        return "missing first_name (required for personalization)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="path to source CSV")
    ap.add_argument(
        "--map", action="append", default=[],
        help="column mapping, format: canon=source (repeat as needed). "
             "E.g., --map email=Email --map first_name='First Name'",
    )
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, don't write")
    args = ap.parse_args()

    mapping: dict[str, str] = {}
    for spec in args.map:
        if "=" not in spec:
            print(f"bad --map (missing '='): {spec}", file=sys.stderr)
            return 2
        canon, src = spec.split("=", 1)
        canon = canon.strip()
        if canon not in CSV_FIELDS:
            print(f"unknown canonical field {canon!r}; allowed: {CSV_FIELDS}", file=sys.stderr)
            return 2
        mapping[canon] = src.strip()

    src_path = pathlib.Path(args.source)
    if not src_path.exists():
        print(f"source not found: {src_path}", file=sys.stderr)
        return 2

    existing = _existing_emails()
    suppressed = _suppressed_emails()

    with src_path.open(newline="", encoding="utf-8") as f:
        source_rows = list(csv.DictReader(f))

    n_invalid = 0
    n_dup = 0
    n_suppressed = 0
    accepted: list[dict] = []
    for src_row in source_rows:
        row = _normalize_row(src_row, mapping)
        if not row:
            n_invalid += 1
            continue
        why = _validate(row)
        if why:
            print(f"  REJECT {row.get('email') or '?'}: {why}", file=sys.stderr)
            n_invalid += 1
            continue
        if row["email"] in suppressed:
            print(f"  SKIP {row['email']}: in suppressions.jsonl", file=sys.stderr)
            n_suppressed += 1
            continue
        if row["email"] in existing:
            print(f"  SKIP {row['email']}: already in prospects.csv", file=sys.stderr)
            n_dup += 1
            continue
        accepted.append(row)
        existing.add(row["email"])

    print(f"source={src_path.name} rows_in={len(source_rows)} "
          f"accepted={len(accepted)} duplicate={n_dup} suppressed={n_suppressed} "
          f"rejected={n_invalid}")

    if not accepted:
        return 0

    if args.dry_run:
        print("\n--- DRY RUN — would append the following rows ---")
        for r in accepted:
            print(f"  {r['email']:50s} {r['vertical']:14s} {r['first_name']}")
        print("Re-run without --dry-run to actually import.")
        return 0

    PROSPECTS_CSV.parent.mkdir(parents=True, exist_ok=True)
    write_header = not PROSPECTS_CSV.exists()
    with PROSPECTS_CSV.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for r in accepted:
            writer.writerow({k: r[k] for k in CSV_FIELDS})

    print(f"\nimported {len(accepted)} prospect(s) to {PROSPECTS_CSV.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
