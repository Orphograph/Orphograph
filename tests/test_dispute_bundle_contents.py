"""The dispute bundle must be self-sufficient for a hostile stranger.

Wedge 03 + 04, built 2026-07-26. The bundle already shipped the checker; it did
not ship the SPEC the checker implements, nor any document a non-technical
reader could act on. Both gaps are what these tests pin.

The bundle's whole claim is that it verifies without us. A recipient who has
our checker but not our spec cannot write a second implementation and compare —
which is the only way to check a checker. And an adjuster or contractor holding
a folder of hex strings has been handed a digest, not a deliverable.
"""

import importlib.util
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dispute_bundle.py"

_spec = importlib.util.spec_from_file_location("dispute_bundle", SCRIPT)
dispute_bundle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispute_bundle)


# SHA-256 of the literal bytes b"test" — the fixture receipt anchors this.
KNOWN_HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


@pytest.fixture(scope="module")
def built_bundle(tmp_path_factory):
    """Build a real bundle from a real receipt, then extract it."""
    receipt_dir = ROOT / "data" / "receipts" / "J7uAbxdxcmwXx88m"
    if not (receipt_dir / "receipt.json").is_file():
        pytest.skip("no local fixture receipt in this checkout")

    work = tmp_path_factory.mktemp("bundle")
    src = work / "evidence.txt"
    src.write_bytes(b"test")
    out = work / "b.tar.gz"

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(src), str(receipt_dir), "-o", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert out.is_file(), f"bundle not created: {proc.stdout}\n{proc.stderr}"
    # The fixture file genuinely matches the receipt, so no mismatch warning.
    assert "does NOT match" not in proc.stdout

    extract = work / "x"
    extract.mkdir()
    with tarfile.open(out) as tf:
        tf.extractall(extract)
    root = next(p for p in extract.iterdir() if p.is_dir())
    return root


REQUIRED = [
    "receipt.json",
    "verify_cli.py",
    "VERIFY.md",
    "VERIFIER_SPEC.md",   # added 2026-07-26
    "SUMMARY.txt",        # added 2026-07-26
    "RESUMEN.txt",        # added 2026-07-26
    "sha256sum.txt",
]


@pytest.mark.parametrize("name", REQUIRED)
def test_required_file_present(built_bundle, name):
    assert (built_bundle / name).is_file(), f"{name} missing from the bundle"


def test_spec_is_the_real_spec_not_a_stub(built_bundle):
    """Shipping an empty placeholder would pass a presence check and help nobody."""
    spec = (built_bundle / "VERIFIER_SPEC.md").read_text(encoding="utf-8")
    assert len(spec) > 2000, "VERIFIER_SPEC.md looks truncated"
    assert "Specification" in spec or "specification" in spec


def test_checksums_cover_every_file_except_themselves(built_bundle):
    """A checksum file cannot contain its own checksum; everything else must be in."""
    listed = {
        line.split("  ", 1)[1].strip()
        for line in (built_bundle / "sha256sum.txt").read_text().splitlines()
        if "  " in line
    }
    on_disk = {p.name for p in built_bundle.iterdir() if p.is_file()}
    missing = on_disk - listed - {"sha256sum.txt"}
    assert not missing, f"files shipped but not checksummed: {sorted(missing)}"


def test_checksums_actually_match(built_bundle):
    for line in (built_bundle / "sha256sum.txt").read_text().splitlines():
        if "  " not in line:
            continue
        digest, name = line.split("  ", 1)
        target = built_bundle / name.strip()
        assert dispute_bundle.sha256_of(target) == digest, f"{name} checksum mismatch"


@pytest.mark.parametrize("sheet", ["SUMMARY.txt", "RESUMEN.txt"])
def test_summary_states_the_ceiling_and_the_limits(built_bundle, sheet):
    """The sheet must say what it does NOT show. That is the point of it."""
    text = (built_bundle / sheet).read_text(encoding="utf-8")
    assert KNOWN_HASH in text, "the fingerprint must appear on the sheet"
    assert "evidence.txt" in text
    # A rendered sheet has no leftover placeholders.
    assert "{" not in text and "}" not in text, "unsubstituted template field"
    lowered = text.lower()
    if sheet == "SUMMARY.txt":
        for phrase in ("does not show", "who wrote", "court"):
            assert phrase in lowered, f"missing limitation language: {phrase!r}"
    else:
        for phrase in ("no demuestra", "quien escribio", "tribunal"):
            assert phrase in lowered, f"missing limitation language: {phrase!r}"


def test_spanish_sheet_is_ascii_safe(built_bundle):
    """Printed and emailed through arbitrary systems; a mangled accent in a
    legal-adjacent document reads as carelessness."""
    raw = (built_bundle / "RESUMEN.txt").read_bytes()
    assert raw.decode("ascii"), "RESUMEN.txt should survive ASCII transcoding"


def test_summary_does_not_overclaim(built_bundle):
    """Guards the claim ceiling against future edits."""
    text = (built_bundle / "SUMMARY.txt").read_text(encoding="utf-8").lower()
    for forbidden in ("tamper-proof", "admissible", "proves you wrote",
                      "proof of authorship", "legally binding"):
        assert forbidden not in text, f"claim-ceiling violation: {forbidden!r}"


def test_verifier_runs_from_inside_the_bundle(built_bundle):
    """The survivability claim, exercised: no network, no repo, no service."""
    proc = subprocess.run(
        [sys.executable, "verify_cli.py", "receipt.json"],
        cwd=built_bundle, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "hash_match=True" in proc.stdout
    rec = json.loads((built_bundle / "receipt.json").read_text())
    assert rec["hash_hex"] == KNOWN_HASH


def test_summary_text_is_pure_and_both_languages_exist():
    """Unit-level: the renderer itself, independent of the bundle build."""
    assert set(dispute_bundle._SUMMARY) == {"en", "es"}
    rec = {"hash_hex": "ab" * 32, "created_at": "2026-01-02T03:04:05+00:00"}
    for lang in ("en", "es"):
        out = dispute_bundle.summary_text(lang, "photo.jpg", rec, 5)
        assert "photo.jpg" in out
        assert "ab" * 32 in out
        assert "2026-01-02 03:04:05" in out
        assert "{" not in out


def test_missing_created_at_does_not_crash_the_sheet():
    """A malformed receipt must still yield a readable sheet, not a traceback."""
    out = dispute_bundle.summary_text("en", "f.txt", {"hash_hex": "cd" * 32}, 1)
    assert "f.txt" in out
    assert "{" not in out
