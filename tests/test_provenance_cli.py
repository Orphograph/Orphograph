#!/usr/bin/env python3
"""Tests for the dataset-provenance CLI (dataset-provenance/provenance.py).

Drives the offline paths via subprocess (no network): `anchor` emits a
certificate + PDF + manifest with the honesty disclaimer; `verify` passes on
an intact bundle and fails (exit 1) with a per-file diff on a tampered one.
Plus a direct unit test of _categorise. Mirrors tests/test_verify_cli.py.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "dataset-provenance" / "provenance.py"


def _bundle() -> Path:
    d = Path(tempfile.mkdtemp()) / "bundle"
    (d / "data").mkdir(parents=True)
    (d / "data" / "a.txt").write_text("alpha")
    (d / "data" / "b.txt").write_text("beta")
    (d / "licenses").mkdir()
    (d / "licenses" / "LICENSE.txt").write_text("MIT")
    (d / "acquisition_log.json").write_text('{"sources": []}')
    return d


def _run(*args):
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True)


def test_anchor_offline_emits_certificate_pdf_manifest():
    b = _bundle()
    out = b.parent / "out"
    r = _run("anchor", "--bundle", str(b), "--name", "T",
             "--offline", "--pdf", "--out", str(out))
    assert r.returncode == 0, r.stderr
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["anchor"]["status"] == "unanchored"
    # The honesty disclaimer must be present (integrity+time, not ownership).
    assert len(cert["scope"]["does_not_prove"]) == 3
    assert (out / "certificate.pdf").read_bytes()[:5] == b"%PDF-"
    assert (out / "manifest.json").exists()
    assert (out / "certificate.txt").exists()


def test_verify_pass_and_inclusion_exit_zero():
    b = _bundle()
    out = b.parent / "out"
    _run("anchor", "--bundle", str(b), "--name", "T", "--offline", "--out", str(out))
    r = _run("verify", "--cert", str(out / "certificate.json"),
             "--bundle", str(b), "--file", "data/a.txt")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "RESULT: VERIFIED" in r.stdout
    assert "PASS  inclusion" in r.stdout


def test_verify_tamper_reports_per_file_diff_exit_one():
    b = _bundle()
    out = b.parent / "out"
    _run("anchor", "--bundle", str(b), "--name", "T", "--offline", "--out", str(out))
    (b / "data" / "a.txt").write_text("CHANGED")   # changed
    (b / "data" / "c.txt").write_text("new")       # added
    (b / "data" / "b.txt").unlink()                # removed
    r = _run("verify", "--cert", str(out / "certificate.json"), "--bundle", str(b))
    assert r.returncode == 1
    assert "RESULT: FAILED" in r.stdout
    assert "changed" in r.stdout and "data/a.txt" in r.stdout
    assert "added" in r.stdout and "data/c.txt" in r.stdout
    assert "removed" in r.stdout and "data/b.txt" in r.stdout


def test_verify_requires_one_of_cert_or_receipt():
    # argparse mutually-exclusive group, one required.
    r = _run("verify", "--bundle", "/tmp")
    assert r.returncode == 2  # argparse usage error


def _load_module():
    spec = importlib.util.spec_from_file_location("provenance_cli_under_test", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_categorise_buckets_by_top_segment():
    mod = _load_module()
    leaves = [
        {"path": "data/x.jpg"},
        {"path": "licenses/cc.txt"},
        {"path": "acquisition_log.json"},
        {"path": "README.md"},
    ]
    b = mod._categorise(leaves)
    assert len(b["data"]) == 1
    assert len(b["licenses"]) == 1
    assert len(b["log"]) == 1
    assert len(b["other"]) == 1
