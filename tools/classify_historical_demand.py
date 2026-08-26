#!/usr/bin/env python3
"""Read-only, confidence-banded classification of historical receipts."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def classify(source: str, office_prefixes: tuple[str, ...]) -> str:
    if source and any(source.startswith(prefix) for prefix in office_prefixes):
        return "confirmed_office"
    if source.startswith(("pack:", "ln:", "sub:", "nowpayments:")):
        return "confirmed_external_paid"
    return "unknown"


def report(receipt_root: Path, office_prefixes: tuple[str, ...]) -> dict:
    counts: Counter[str] = Counter()
    malformed = 0
    for path in sorted(receipt_root.rglob("receipt.json")):
        try:
            row = json.loads(path.read_text())
            if not isinstance(row, dict):
                raise ValueError("receipt must be an object")
        except (OSError, ValueError, json.JSONDecodeError):
            malformed += 1
            continue
        counts[classify(str(row.get("source") or ""), office_prefixes)] += 1
    return {
        "data_quality": "degraded" if malformed else "complete",
        "receipts_scanned": sum(counts.values()),
        "confidence_bands": {
            "confirmed_office": counts["confirmed_office"],
            "confirmed_external_paid": counts["confirmed_external_paid"],
            "unknown": counts["unknown"],
        },
        "malformed_receipts": malformed,
        "mutations_performed": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipt_root", type=Path)
    parser.add_argument("--office-source-prefix", action="append", default=[])
    args = parser.parse_args()
    if not args.receipt_root.is_dir():
        parser.error("receipt_root must be a directory")
    print(json.dumps(report(args.receipt_root, tuple(args.office_source_prefix)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
