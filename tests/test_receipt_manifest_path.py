"""test_receipt_manifest_path.py — manifests are read only from inside the
receipts directory, whatever id reaches the reader.

Every route matches RECEIPT_ID_RE before it gets here, so this is defense in
depth (code scanning flagged the path built from a URL-supplied id on #304).
The reader is driven directly with ids the routes would refuse, plus a
control that a real id still reads.
"""
from __future__ import annotations

import json

import pytest

import app
import engine


@pytest.fixture()
def receipts(tmp_path, monkeypatch):
    d = tmp_path / "receipts"
    d.mkdir()
    monkeypatch.setattr(engine, "RECEIPTS_DIR", d)
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "manifest.json").write_text('{"leaked": true}')
    good = d / "AbC_123-x"
    good.mkdir()
    (good / "manifest.json").write_text(json.dumps({"root_hex": "ab" * 32}))
    return d


@pytest.mark.parametrize("rid", ["../secret", "..", "a/../../secret", "/etc", "AbC_123-x\n",
                                 "", "x" * 65, "%2e%2e"])
def test_ids_outside_the_alphabet_are_refused_before_any_read(receipts, rid):
    with pytest.raises(ValueError):
        app._read_receipt_manifest(rid)


def test_a_symlinked_receipt_dir_pointing_outside_is_refused(receipts):
    (receipts / "Linked").symlink_to(receipts.parent / "secret")
    with pytest.raises(ValueError):
        app._read_receipt_manifest("Linked")


def test_control_a_real_receipt_still_reads(receipts):
    assert app._read_receipt_manifest("AbC_123-x") == {"root_hex": "ab" * 32}


def test_control_a_missing_receipt_is_oserror_not_valueerror(receipts):
    with pytest.raises(OSError):
        app._read_receipt_manifest("NoSuchId")
