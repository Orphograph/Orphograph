"""test_offline_claims.py — pin what "offline" verification may promise.

The standalone receipt verifier (web/verify/verify.py) checks a receipt's
structure with no network call. Confirming the timestamp against Bitcoin
needs the OpenTimestamps client and a Bitcoin node. Copy that says a receipt
"verifies offline" with nothing nearby saying which check runs offline
promises more than the tool does.

Text-only assertions over the shipped files; nothing here executes site code.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# A verification verb followed within four words by "offline". The noun
# "verifier" is excluded: "the offline verifier" names a tool, it claims nothing.
VERIFY_OFFLINE = re.compile(
    r"(?i)\b(verif(?!ier)\w*|confirm\w*|checks?)\b(?:\W+\w+){0,4}?\W+offline\b"
)

# Wording that says which check runs offline, or what else it needs.
QUALIFIER = re.compile(
    r"(?i)structure|block header|bitcoin node|chain is local|no network call"
    r"|network is required|takes over"
)

# "offline" right before one of these names a tool ("the offline command
# line", "the offline path"); the claim shape is "verifies offline".
TOOL_NOUN = re.compile(r"(?i)\s*(command|cli|verifier|copy|path|kit)\b")

WINDOW = 200


def _public_surface() -> list[Path]:
    out: list[Path] = []
    for p in WEB.rglob("*.html"):
        rel = p.relative_to(WEB).as_posix()
        if rel.startswith("_mockups/") or rel == "index-legacy.html":
            continue
        out.append(p)
    out.append(WEB / "llms.txt")
    return sorted(out)


def _visible_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw))


def _claims(text: str) -> list[tuple[str, bool]]:
    """(context, qualified) for every verify-offline claim in the text."""
    found = []
    for m in VERIFY_OFFLINE.finditer(text):
        if TOOL_NOUN.match(text, m.end()):
            continue
        window = text[max(0, m.start() - WINDOW):m.end() + WINDOW]
        found.append((text[max(0, m.start() - 60):m.end() + 60],
                      bool(QUALIFIER.search(window))))
    return found


def test_every_offline_verification_claim_says_what_runs_offline() -> None:
    qualified = 0
    for page in _public_surface():
        text = _visible_text(page.read_text(encoding="utf-8", errors="ignore"))
        for context, ok in _claims(text):
            assert ok, (
                f"{page.relative_to(ROOT)}: claims offline verification without "
                "saying the offline tool checks structure only (the Bitcoin "
                f"check needs an OpenTimestamps client and a node): …{context}…"
            )
            qualified += 1
    # Floor: the accurate wording (faq, llms.txt, mcp, one-pager, terms) must
    # still match, or the pattern has stopped seeing anything at all.
    assert qualified >= 5, f"only {qualified} qualified claims matched"


def test_the_checker_flags_a_bare_offline_claim() -> None:
    bare = "Hand it over. Anyone can verify the receipt offline, forever."
    fixed = "The MIT verifier checks the receipt's structure offline."
    assert [ok for _, ok in _claims(bare)] == [False]
    assert [ok for _, ok in _claims(fixed)] == [True]
    assert _claims("Download the offline verifier kit.") == []
