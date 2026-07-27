"""Regression: internal-only pages under web/ must never be publicly served.

Audited 2026-07-26 — all four of these returned 200 to the open internet:

    /_mockups/A_pure          /_mockups/B_broadsheet
    /_mockups/C_instrument    /index-legacy

web/** ships wholesale, so anything placed there is served by default. These
carried claim wording that contradicts the canonical pages (index-legacy.html
discusses "guarantee admissibility"; the mockups carry their own legal framing).

The guard is checked against the path with .html stripped, because pages resolve
at clean extensionless URLs — both forms must 404.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.app import _is_private_path, WEB_DIR  # noqa: E402


PRIVATE = [
    "_mockups/A_pure.html",
    "_mockups/A_pure",
    "_mockups/B_broadsheet.html",
    "_mockups/B_broadsheet",
    "_mockups/C_instrument.html",
    "_mockups/C_instrument",
    "index-legacy.html",
    "index-legacy",
    "/index-legacy",          # leading slash
    "_mockups/",              # the directory itself
    "_mockups/anything_added_later.html",
]

PUBLIC = [
    "index.html",
    "",
    "buy.html",
    "verify.html",
    "certificate.html",
    "method/bitcoin-attestation.html",   # contains "test" — must NOT be caught
    "method/folder-merkle.html",         # contains "old"  — must NOT be caught
    "blog/prove-a-photo-was-not-edited.html",
    "assets/lp-cta.js",
    "seal-display.png",
    "lp/agent-receipts.html",
    "legacy-of-proof.html",              # merely CONTAINS "legacy"
]


@pytest.mark.parametrize("path", PRIVATE)
def test_private_paths_are_blocked(path):
    assert _is_private_path(path) is True, f"{path} would still be served publicly"


@pytest.mark.parametrize("path", PUBLIC)
def test_public_paths_are_not_blocked(path):
    assert _is_private_path(path) is False, f"{path} was wrongly blocked"


def test_every_mockup_on_disk_is_covered():
    """If a new mockup is added to web/_mockups/, it is blocked automatically."""
    mock_dir = WEB_DIR / "_mockups"
    if not mock_dir.is_dir():
        pytest.skip("no _mockups directory in this checkout")
    found = list(mock_dir.glob("*.html"))
    assert found, "expected mockups on disk; the fixture list may be stale"
    for f in found:
        rel = f.relative_to(WEB_DIR).as_posix()
        assert _is_private_path(rel) is True, f"{rel} is not covered by the guard"
        assert _is_private_path(rel[: -len('.html')]) is True


def test_index_legacy_exists_and_is_covered():
    """Guards against the file being renamed without updating the deny list."""
    if not (WEB_DIR / "index-legacy.html").is_file():
        pytest.skip("index-legacy.html not in this checkout")
    assert _is_private_path("index-legacy") is True


def test_canonical_homepage_still_serves():
    """The obvious catastrophic over-block."""
    assert _is_private_path("index.html") is False
    assert _is_private_path("") is False
