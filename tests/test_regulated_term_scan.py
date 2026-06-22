"""Tests for scripts/regulated_term_scan.py — the regulated-status web gate.

Guards the council finding (2026-06-21): regulated terms (notarize /
court-admissible / legally-binding / qualified eIDAS trust service) had NO
automated gate, so a new page could imply a regulated legal status (UPL / false
status) undetected. The gate is negation-aware, page-disclaimer-aware, and
baseline-grandfathered so it fires ONLY on genuinely new undisclaimed claims.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCANNER = REPO / "scripts" / "regulated_term_scan.py"
LIVE_BASELINE = REPO / "scripts" / "regulated_term_baseline.json"


def _run(web: Path, baseline: Path, *extra):
    return subprocess.run(
        [sys.executable, str(SCANNER), "--web", str(web), "--baseline", str(baseline), *extra],
        capture_output=True, text=True,
    )


def _empty_baseline(tmp_path: Path) -> Path:
    p = tmp_path / "bl.json"
    p.write_text("{}")
    return p


def test_new_bare_regulated_claim_is_flagged(tmp_path):
    (tmp_path / "newpage.html").write_text(
        "<p>Our receipts are court-admissible and legally binding. We will notarize your work.</p>"
    )
    r = _run(tmp_path, _empty_baseline(tmp_path))
    assert r.returncode == 1, r.stdout
    out = r.stdout.lower()
    assert "court-admissible" in out and "legally binding" in out and "notarize" in out


def test_negated_term_passes(tmp_path):
    (tmp_path / "p.html").write_text(
        "<p>An empirical record. We are not a notary and this is not legally binding.</p>"
    )
    r = _run(tmp_path, _empty_baseline(tmp_path))
    assert r.returncode == 0, r.stdout


def test_page_with_disclaimer_passes(tmp_path):
    (tmp_path / "p.html").write_text(
        "<p>Think of it as an empirical notary.</p>"
        "<footer>Orphograph is not a law firm and not a qualified electronic-trust-service provider.</footer>"
    )
    r = _run(tmp_path, _empty_baseline(tmp_path))
    assert r.returncode == 0, r.stdout


def test_bare_technical_mention_not_flagged(tmp_path):
    # C2PA / "unlike eIDAS" are technical/comparative, not self-asserted status.
    (tmp_path / "p.html").write_text(
        "<p>Unlike C2PA content credentials or eIDAS systems, we anchor a SHA-256 hash to Bitcoin.</p>"
    )
    r = _run(tmp_path, _empty_baseline(tmp_path))
    assert r.returncode == 0, r.stdout


def test_baseline_grandfathers_accepted_usage(tmp_path):
    (tmp_path / "brand.html").write_text("<h1>The empirical notary</h1>")
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps({"brand.html": ["notary"]}))
    r = _run(tmp_path, bl)
    assert r.returncode == 0, r.stdout


def test_live_surface_within_committed_baseline():
    """The live web surface must not exceed the committed baseline — i.e. no NEW
    regulated-status term has been introduced since the baseline was snapshotted."""
    assert LIVE_BASELINE.exists(), "committed baseline missing"
    r = subprocess.run(
        [sys.executable, str(SCANNER)], capture_output=True, text=True, cwd=str(REPO),
    )
    assert r.returncode == 0, f"live web surface introduced a new undisclaimed regulated term:\n{r.stdout}"
