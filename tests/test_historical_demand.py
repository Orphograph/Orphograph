from __future__ import annotations

import json

from tools.classify_historical_demand import classify, report


def test_closed_confidence_bands_do_not_guess_free_or_api_demand():
    office = ("api:office123",)
    assert classify("api:office123-more", office) == "confirmed_office"
    assert classify("pack:customer", office) == "confirmed_external_paid"
    assert classify("sub:customer", office) == "confirmed_external_paid"
    assert classify("free", office) == "unknown"
    assert classify("api:unrecognized", office) == "unknown"


def test_report_is_read_only_and_surfaces_malformed_receipts(tmp_path):
    root = tmp_path / "receipts"
    before = {}
    for name, source in (("a", "api:office123"), ("b", "ln:paid"), ("c", "free")):
        path = root / name / "receipt.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"source": source}))
        before[path] = path.read_bytes()
    bad = root / "bad" / "receipt.json"
    bad.parent.mkdir()
    bad.write_text("not json")
    before[bad] = bad.read_bytes()

    result = report(root, ("api:office123",))

    assert result == {
        "data_quality": "degraded",
        "receipts_scanned": 3,
        "confidence_bands": {
            "confirmed_office": 1,
            "confirmed_external_paid": 1,
            "unknown": 1,
        },
        "malformed_receipts": 1,
        "mutations_performed": 0,
    }
    assert {path: path.read_bytes() for path in before} == before
