"""Tests for scripts/compliance_scan.py.

Each test builds an isolated TemporaryDirectory fixture so the live repo
is never scanned by the test suite.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import compliance_scan  # noqa: E402


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _run(tmp_root: Path, out_path: Path) -> tuple[int, dict]:
    rc = compliance_scan.main([
        "--root", str(tmp_root),
        "--out", str(out_path),
        "--quiet",
    ])
    report = json.loads(out_path.read_text())
    return rc, report


def test_clean_repo_exits_zero():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "README.md", "# Project\n\nThis is a plain text file.\n")
        _write(root / "server" / "app.py", "def hello():\n    return 1\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        assert rc == 0
        assert report["high_severity_hits"] == []
        assert report["files_scanned"] >= 2


def test_planted_competitor_name_is_high_severity():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Intentional planted name for the scanner under test.
        _write(root / "docs" / "leak.md", "We are competing with companycam in the field.\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        assert rc == 1
        assert any(
            h["match"].lower() == "companycam"
            for h in report["high_severity_hits"]
        )


def test_planted_dollar_figure_recorded():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "docs" / "pitch.md", "We raised $1.5M last quarter.\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        # Spec says: planted "$1.5M" string → exit 1 with dollar hit.
        assert rc == 1
        assert report["dollar_hits"], "expected at least one dollar hit"
        matches = [h["match"] for h in report["dollar_hits"]]
        assert any("$1.5M" in m or m.startswith("$") for m in matches)
        assert report["valuation_hits"], "a $1.5M valuation must be a valuation_hit"


def test_product_price_tags_do_not_trip_exit():
    # The 2026-06-22 fix: bare price tags are recorded but do NOT fail the build.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "web" / "pricing.html",
               "Writer Pack $19. Standing Order $9/mo. Pack of Fifty $29. Annual $60/yr.\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        assert rc == 0, "product price tags must not fail the build"
        assert report["dollar_hits"], "price tags should still be recorded for the report"
        assert report["valuation_hits"] == [], "no price tag is a valuation hit"


def test_valuation_scale_amount_trips_exit():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "docs" / "deck.md", "FMV today is $350K; 5y EV ~$3M.\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        assert rc == 1
        assert report["valuation_hits"], "magnitude-suffixed amounts are valuation hits"


def test_vendored_min_js_is_excluded():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "web" / "vendor" / "lottie.min.js", "var a=$1000000,b=$25;\n")
        _write(root / "README.md", "plain\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        assert rc == 0, "vendored minified libs must not trip the build"
        assert all("lottie.min.js" not in h["path"] for h in report["dollar_hits"])


def test_stripe_webhook_reference_is_low_severity_only():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(
            root / "server" / "webhook.py",
            "# Verifies the Stripe webhook signature for billing events.\n",
        )
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        # No high-severity hit — Stripe is in the tech-name carveout.
        assert rc == 0
        assert report["high_severity_hits"] == []
        assert any(
            h["match"].lower() == "stripe"
            for h in report["low_severity_hits"]
        )


def test_external_strategic_analysis_whitelisted():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # The rule-declaration file itself is allowed to literally
        # discuss the rules — both company names and the word "valuation"
        # may appear here as part of declaring what is forbidden elsewhere.
        _write(
            root / "outbox" / "EXTERNAL_STRATEGIC_ANALYSIS_2026-05-20.md",
            "Rule: never publish a dollar valuation publicly.\n"
            "Rule: never name a third-party brand on any surface.\n",
        )
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        # No dollar hits should be reported for the rule-declaration file.
        for h in report["dollar_hits"]:
            assert "EXTERNAL_STRATEGIC_ANALYSIS_" not in h["path"]
        assert rc == 0


def test_output_json_shape():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write(root / "README.md", "Hello.\n")
        out = root / "outbox" / "report.json"
        _run(root, out)
        report = json.loads(out.read_text())
        for key in (
            "scanned_at_utc",
            "files_scanned",
            "high_severity_hits",
            "low_severity_hits",
            "dollar_hits",
        ):
            assert key in report, f"missing key: {key}"
        assert isinstance(report["files_scanned"], int)
        assert isinstance(report["high_severity_hits"], list)
        assert isinstance(report["low_severity_hits"], list)
        assert isinstance(report["dollar_hits"], list)


def test_excluded_paths_are_not_scanned():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # An offending string in an excluded directory should not register.
        _write(root / "node_modules" / "leak.md", "Mentioning companycam here.\n")
        # And one in a tracked directory should still register.
        _write(root / "src" / "leak.md", "Mentioning companycam here.\n")
        out = root / "outbox" / "report.json"
        rc, report = _run(root, out)
        assert rc == 1
        for h in report["high_severity_hits"]:
            assert not h["path"].startswith("node_modules/")
