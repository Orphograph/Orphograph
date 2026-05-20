"""tests/test_usb_backup.py — additive-only invariants for usb_cold_backup."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import usb_cold_backup  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_receipts(receipts_root: Path, ids: list[str]) -> None:
    for rid in ids:
        rd = receipts_root / rid
        rd.mkdir(parents=True, exist_ok=False)
        (rd / "receipt.json").write_text(
            json.dumps({"receipt_id": rid, "hash_hex": "ab" * 32}),
            encoding="utf-8",
        )
        (rd / "alice.ots").write_bytes(b"\x00ots")


def _seed_preexisting_usb(usb: Path) -> dict[str, tuple[bytes, float]]:
    fingerprint: dict[str, tuple[bytes, float]] = {}
    files = {
        "personal/photo.jpg": b"\xff\xd8 jpeg payload" * 10,
        "old_backup_notes.txt": b"keep me intact",
        "nested/dir/data.bin": b"\x01\x02\x03" * 100,
    }
    for rel, data in files.items():
        p = usb / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        past = time.time() - 7200
        os.utime(p, (past, past))
        fingerprint[rel] = (data, p.stat().st_mtime)
    return fingerprint


def _verify_preexisting_intact(usb: Path, fingerprint: dict[str, tuple[bytes, float]]) -> None:
    for rel, (data, mtime) in fingerprint.items():
        p = usb / rel
        assert p.is_file(), f"pre-existing file vanished: {rel}"
        assert p.read_bytes() == data, f"pre-existing file modified: {rel}"
        assert p.stat().st_mtime == mtime, f"pre-existing mtime changed: {rel}"


def _snapshot_dir(root: Path) -> dict[str, tuple[int, float, bytes]]:
    snap: dict[str, tuple[int, float, bytes]] = {}
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime, p.read_bytes())
    return snap


@pytest.fixture
def usb_dir():
    with tempfile.TemporaryDirectory(prefix="fake_backup_usb_") as t:
        yield Path(t)


@pytest.fixture
def receipts_src():
    with tempfile.TemporaryDirectory(prefix="receipts_src_") as t:
        yield Path(t)


@pytest.fixture
def verifier_dir(monkeypatch):
    real = usb_cold_backup.VERIFIER_DIR
    if real.is_dir() and (real / "verify.py").is_file():
        yield real
        return
    with tempfile.TemporaryDirectory(prefix="verifier_") as t:
        vd = Path(t)
        (vd / "verify.py").write_text("# fake verify\n", encoding="utf-8")
        (vd / "merkle.py").write_text("# fake merkle\n", encoding="utf-8")
        monkeypatch.setattr(usb_cold_backup, "VERIFIER_DIR", vd)
        yield vd


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_additive_preexisting_files_untouched(usb_dir, receipts_src, verifier_dir):
    fingerprint = _seed_preexisting_usb(usb_dir)
    _seed_receipts(receipts_src, ["RIDA", "RIDB"])

    rc = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc == 0
    _verify_preexisting_intact(usb_dir, fingerprint)

    new_dirs = [p for p in usb_dir.iterdir() if p.is_dir() and p.name.startswith("orphograph_")]
    assert len(new_dirs) == 1
    target = new_dirs[0]

    for p in usb_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(usb_dir))
        if rel in fingerprint:
            continue
        # Every other file must be inside the target subdir.
        sep_a = target.name + os.sep
        sep_b = target.name + "/"
        assert rel.startswith(sep_a) or rel.startswith(sep_b), \
            f"new file outside target subdir: {rel}"


def test_empty_receipts_dir_writes_nothing(usb_dir, receipts_src, verifier_dir):
    before = _snapshot_dir(usb_dir)
    rc = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc == 0
    after = _snapshot_dir(usb_dir)
    assert before == after, "empty-receipts run wrote something to the USB"
    # And no orphograph_* subdir was created.
    assert not any(p.name.startswith("orphograph_") for p in usb_dir.iterdir())


def test_two_runs_produce_two_distinct_subdirs(usb_dir, receipts_src, verifier_dir):
    _seed_receipts(receipts_src, ["RIDX"])

    rc1 = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc1 == 0
    # Ensure timestamp granularity (UTC stamp is per-second) ticks over.
    time.sleep(1.1)
    rc2 = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc2 == 0

    subdirs = sorted(p for p in usb_dir.iterdir() if p.is_dir() and p.name.startswith("orphograph_"))
    assert len(subdirs) == 2, f"expected 2 subdirs, found {len(subdirs)}: {subdirs}"
    # Both backups must still be intact (receipts/receipt.json present in each).
    for sd in subdirs:
        assert (sd / "receipts" / "RIDX" / "receipt.json").is_file()


def test_what_was_added_lists_every_backed_up_file(usb_dir, receipts_src, verifier_dir):
    _seed_receipts(receipts_src, ["RIDM", "RIDN"])
    rc = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc == 0
    target = next(p for p in usb_dir.iterdir() if p.is_dir())
    manifest = json.loads((target / "WHAT_WAS_ADDED.json").read_text(encoding="utf-8"))
    listed = {entry["path"] for entry in manifest["files"]}
    actual = {
        str(p.relative_to(target))
        for p in target.rglob("*")
        if p.is_file()
    }
    actual_minus_manifest = actual - {"WHAT_WAS_ADDED.json"}
    listed_minus_manifest = listed - {"WHAT_WAS_ADDED.json"}
    assert actual_minus_manifest == listed_minus_manifest

    # Both receipts must appear in the manifest paths.
    joined = "\n".join(listed)
    assert "RIDM" in joined and "RIDN" in joined


def test_dry_run_makes_no_writes(usb_dir, receipts_src, verifier_dir):
    fingerprint = _seed_preexisting_usb(usb_dir)
    _seed_receipts(receipts_src, ["RIDY"])

    before = _snapshot_dir(usb_dir)
    rc = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=True, stdout=io.StringIO(),
    )
    assert rc == 0
    after = _snapshot_dir(usb_dir)
    assert before == after, "dry-run modified the USB filesystem"
    _verify_preexisting_intact(usb_dir, fingerprint)


def test_root_filesystem_refused(receipts_src, verifier_dir):
    _seed_receipts(receipts_src, ["RIDZ"])
    rc = usb_cold_backup.run_backup(
        usb=Path("/"), receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc == 2


def test_help_runs():
    import subprocess
    out = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "usb_cold_backup.py"), "--help"],
        capture_output=True, text=True, timeout=15,
    )
    assert out.returncode == 0
    assert "USB" in out.stdout or "usb" in out.stdout


def test_readme_no_banned_terms(usb_dir, receipts_src, verifier_dir):
    _seed_receipts(receipts_src, ["RIDQ"])
    rc = usb_cold_backup.run_backup(
        usb=usb_dir, receipts_dir=receipts_src,
        dry_run=False, stdout=io.StringIO(),
    )
    assert rc == 0
    target = next(p for p in usb_dir.iterdir() if p.is_dir())
    readme = (target / "BACKUP_README.txt").read_text(encoding="utf-8")
    low = readme.lower()
    # Obfuscated banlist — see test_usb_handover.py for rationale.
    banned_pairs = [
        ("compan", "ycam"), ("spec", "tora"), ("ado", "be"),
        ("goo", "gle"), ("cla", "ude"), ("anthro", "pic"),
        ("stri", "pe"), ("stam", "pery"), ("true", "pic"),
    ]
    for a, b in banned_pairs:
        assert (a + b) not in low
    import re
    assert re.search(r"\$[0-9]", readme) is None
    assert "!" not in readme
    for token in (" we ", " our ", " us "):
        assert token not in low
