"""test_sha512_claim_honesty.py

The SHA-512 sibling is OPTIONAL, and two things must stay true about that
(guard added 2026-08-25 for a defect fixed 2026-08-17 in commit 13ab538).

`server/engine.py` declares `sha512_hex: str | None = None` and its docstring
calls it "an optional sibling witness"; `/api/anchor` reads it with
`payload.get(...)`. So a third-party API or MCP client that omits it produces a
receipt with no sibling, and older receipts predate the field — the site's own
JS already renders "(none — receipt predates SHA-512 sibling)".

Eleven pages once asserted the opposite. They never used the word "every";
they made bare containment claims — "records the SHA-256 alongside a sibling
SHA-512", "with a SHA-512 sibling witness", "together with its SHA-512
sibling" — which read as universal to a customer. 13ab538 qualified all of them.

WHY THIS FILE IS NARROW, deliberately: a first attempt scanned prose for
unqualified containment claims. It could not discriminate at any granularity —
sentence-level flagged four honestly-qualified passages; paragraph-level
flagged nineteen; page-level failed to catch the original defect at all,
because the pre-fix architecture.html already contained the word "optional"
elsewhere (in an unrelated JSON-envelope caption). A guard that cannot separate
the defect from correct copy is not a guard, so it was dropped rather than
tuned until green. What remains is the part that is actually decidable.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"
ENGINE = REPO_ROOT / "server" / "engine.py"


def test_sha512_is_still_optional_in_the_engine() -> None:
    """THE PREMISE. Every copy decision below rests on this being optional.
    If the field ever becomes required, the honest copy changes too — this
    failing is a prompt to revisit the wording, not merely to update a regex."""
    src = ENGINE.read_text(encoding="utf-8")
    assert re.search(r"sha512_hex:\s*str\s*\|\s*None\s*=\s*None", src), (
        "sha512_hex is no longer an optional parameter in engine.py — the "
        "site's qualified wording ('when the client supplies one') may now be "
        "wrong in the other direction. Re-read the copy before changing this."
    )


# The exact claims that shipped false, normalised. Verbatim pins: zero false
# positives, and they are what a careless copy edit would most plausibly
# reintroduce.
RETIRED_CLAIMS = (
    "records the sha-256 alongside a sibling sha-512",
    "only the sha-256 (with a sha-512 sibling witness) is",
    "together with its sha-512 sibling",
    "your orphograph receipts also include a sha-512 sibling witness",
    "receipts include a sha-512 sibling hash",
    "embed a sha-512 sibling witness in every receipt",
    "every receipt carries a sha-512 sibling",
    "anchors the sha-256 and sha-512 fingerprints",
)


def _visitor_pages() -> list[Path]:
    # _mockups/ and index-legacy are in the server's own private-path blocklist
    # (server/app.py _PRIVATE_PATH_PREFIXES / _PRIVATE_PATH_EXACT) and 404
    # publicly, so they are not the visitor surface.
    return [
        p for p in sorted(WEB.rglob("*.html"))
        if "_mockups" not in p.parts and p.stem != "index-legacy"
    ]


def _normalise(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&mdash;", "—").replace("&nbsp;", " ")
    return " ".join(text.split()).lower()


def test_no_retired_sha512_claim_has_returned() -> None:
    """Verbatim regression pin on the wording 13ab538 removed."""
    hits = []
    for page in _visitor_pages():
        text = _normalise(page.read_text(encoding="utf-8", errors="replace"))
        for claim in RETIRED_CLAIMS:
            if claim in text:
                hits.append(f"{page.relative_to(REPO_ROOT).as_posix()}: {claim!r}")
    assert not hits, (
        "A retired SHA-512 claim is back on the visitor surface. The sibling is "
        "optional (engine.py), so these read false to any customer whose client "
        "omits it:\n  " + "\n  ".join(hits)
    )


def test_the_pins_actually_matched_the_shipped_defect() -> None:
    """NEGATIVE CONTROL / can-this-test-fail check. Each pin must match the real
    pre-fix sentence it was derived from. Pins that match nothing would make the
    test above pass vacuously forever."""
    samples = {
        "records the sha-256 alongside a sibling sha-512":
            "The Orphograph receipt records the SHA-256 alongside a sibling SHA-512 of the same file.",
        "only the sha-256 (with a sha-512 sibling witness) is":
            "Only the SHA-256 (with a SHA-512 sibling witness) is submitted and committed to Bitcoin.",
        "together with its sha-512 sibling":
            "a 32-byte SHA-256 output — together with its SHA-512 sibling and a static label.",
        "anchors the sha-256 and sha-512 fingerprints":
            "Orphograph anchors the SHA-256 and SHA-512 fingerprints of the file as it stands.",
    }
    for pin, sentence in samples.items():
        assert pin in _normalise(sentence), f"pin {pin!r} no longer matches its source sentence"


def test_the_sibling_is_never_described_as_anchored() -> None:
    """The sibling is RECORDED on the receipt, never committed to Bitcoin —
    faq.html states this explicitly ("not itself anchored"). vs/c2pa.html
    contradicted it until 2026-08-25 by saying Orphograph 'anchors the SHA-256
    and SHA-512 fingerprints'. Self-contradiction on a trust product."""
    bad = []
    for page in _visitor_pages():
        text = _normalise(page.read_text(encoding="utf-8", errors="replace"))
        for m in re.finditer(r"anchors? the sha-256 and sha-512|sha-512[^.]{0,40}committed to bitcoin", text):
            bad.append(f"{page.relative_to(REPO_ROOT).as_posix()}: …{m.group(0)}…")
    assert not bad, "SHA-512 described as anchored/committed:\n  " + "\n  ".join(bad)
