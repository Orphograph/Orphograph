"""test_capability_copy.py — pin the honesty scope of the 2026-08 capability copy.

Three shipped-capability claims went onto the public web surface (homepage,
/docs/api, /lp/agent-receipts, /anchor-output, /pricing, /llms.txt):

  1. Edit-lineage / version history — the copy may claim commitment ORDER only,
     never what changed, when the edit happened, or who made it.
  2. Zero-knowledge execution proof on agent-output receipts — every page that
     mentions it must carry BOTH scope caveats (fixed hash-chain procedure, not
     a specific AI model; development-grade proving-key ceremony).
  3. Lightning pay-per-anchor is BUILT but NOT ARMED — "Lightning" may appear
     on the public surface only adjacent to "coming"/"soon" wording.

Text-only assertions over the shipped files; nothing here executes site code.
Pure stdlib + pytest.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# ── pinned phrases ───────────────────────────────────────────────────────────

LINEAGE_SCOPE = "never what changed, when the edit happened, or who made it"

# Both SNARK caveats, exactly as shipped (whitespace-normalised before match).
CAVEAT_PROCEDURE = (
    "proves a fixed hash-chain procedure ran — not that a specific AI model ran"
)
CAVEAT_CEREMONY = (
    "proving key ceremony is development-grade; we do not yet make this claim "
    "in any certified sense"
)

# Any of these marks a page as "mentions the execution proof".
EXEC_MARKER = re.compile(r"(?i)zero-knowledge|execution\s+proof|zk_proof|zk_provenance")

LIGHTNING = re.compile(r"(?i)\blightning\b")
COMING_ADJ = re.compile(r"(?i)\b(coming|soon)\b")

# Pages that must carry the lineage-scope sentence.
LINEAGE_PAGES = [
    WEB / "index.html",
    WEB / "docs" / "api.html",
    WEB / "lp" / "agent-receipts.html",
    WEB / "llms.txt",
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _read(p: Path) -> str:
    return _norm(p.read_text(encoding="utf-8", errors="ignore"))


def _public_surface() -> list[Path]:
    """Every served web/**/*.html (minus non-deployed artifacts) + llms.txt."""
    out: list[Path] = []
    for p in WEB.rglob("*.html"):
        rel = p.relative_to(WEB).as_posix()
        if rel.startswith("_mockups/") or rel == "index-legacy.html":
            continue
        out.append(p)
    out.append(WEB / "llms.txt")
    return sorted(out)


# ── 1 · edit-lineage scope ───────────────────────────────────────────────────

def test_lineage_scope_sentence_present_on_the_relevant_pages() -> None:
    for page in LINEAGE_PAGES:
        assert page.is_file(), f"{page} is missing"
        assert LINEAGE_SCOPE in _read(page), (
            f"{page.relative_to(ROOT)}: the lineage copy must state its scope "
            f"({LINEAGE_SCOPE!r}) — commitment order only."
        )


def test_lineage_copy_never_claims_edit_content_or_authorship() -> None:
    """The server's own receipt-page template carries the same scope line —
    keep the human copy and the server-rendered copy in agreement."""
    app_py = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    assert "what changed, when the edit happened, or who made it" in _norm(app_py), (
        "server/app.py lineage section lost its scope disclaimer; the site "
        "copy in web/ is pinned to the same scope and must not outrun it."
    )


# ── 2 · execution-proof caveats ──────────────────────────────────────────────

def test_both_snark_caveats_wherever_execution_proof_is_mentioned() -> None:
    mentioned_somewhere = False
    for page in _public_surface():
        text = _read(page)
        if not EXEC_MARKER.search(text):
            continue
        mentioned_somewhere = True
        assert CAVEAT_PROCEDURE in text, (
            f"{page.relative_to(ROOT)} mentions the execution proof without "
            f"the procedure caveat ({CAVEAT_PROCEDURE!r})."
        )
        assert CAVEAT_CEREMONY in text, (
            f"{page.relative_to(ROOT)} mentions the execution proof without "
            f"the ceremony caveat ({CAVEAT_CEREMONY!r})."
        )
    assert mentioned_somewhere, (
        "No public page mentions the execution proof at all — the capability "
        "copy this test pins has been removed; update or retire the test."
    )


def test_execution_proof_copy_never_claims_model_identity() -> None:
    """No page may flip the caveat into an affirmative model-identity claim."""
    forbidden = re.compile(r"(?i)proves\s+(?:that\s+)?a\s+specific\s+AI\s+model\s+ran")
    for page in _public_surface():
        text = _read(page)
        # The shipped caveat contains "not that a specific AI model ran";
        # strip the negated form before hunting for the affirmative one.
        text = text.replace("not that a specific AI model ran", "")
        assert not forbidden.search(text), (
            f"{page.relative_to(ROOT)} affirmatively claims a specific AI "
            "model ran — the proof does not establish that."
        )


# ── 3 · Lightning is "coming", never "available" ─────────────────────────────

def test_lightning_only_ever_appears_adjacent_to_coming_wording() -> None:
    found = False
    for page in _public_surface():
        text = _read(page)
        for m in LIGHTNING.finditer(text):
            found = True
            window = text[max(0, m.start() - 160):m.end() + 160]
            assert COMING_ADJ.search(window), (
                f"{page.relative_to(ROOT)}: 'Lightning' appears without "
                "'coming'/'soon' wording nearby — the endpoint is not armed "
                "and must not be presented as available "
                f"(context: …{window[:200]}…)"
            )
    assert found, (
        "No public page mentions Lightning — the coming-soon copy this test "
        "pins has been removed; update or retire the test."
    )
