"""WCAG AA contrast guard for the token cascade behind customer-visible pages.

Found 2026-08-26 by the Stage 3e drift lens. `--warn` was defined twice with
divergent values (#c08a3e in web/style.css, #a65a30 in web/index.css). Pages
loading index.css *after* style.css silently got the accessible value, which
hid the failure on exactly the pages anyone was looking at. /docs/api does not
load index.css, so it shipped a warning callout whose bold lead-in rendered at
2.69:1 where WCAG AA 1.4.3 requires 4.5:1.

This module guards the property that actually matters -- the contrast a reader
gets -- rather than pinning token values, which would be a tautology on the fix.

CSS comments are stripped before parsing. A scanner that reads its own prose is
a defect this repo has shipped four times (see tests/test_no_phantom_env_knobs.py
for the ast-based equivalent on the Python side); the comments in web/style.css
and web/docs/api.css both quote the failing hex values verbatim, so an
unstripped parse would mis-resolve here immediately.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parent.parent / "web"

# WCAG 2.1 SC 1.4.3: normal-size text needs 4.5:1. Every rule these tokens
# paint on the guarded pages is below the large-text threshold (18.66px bold /
# 24px normal), so the small-text bar is the correct one for all of them.
AA_SMALL = 4.5

TEXT_TOKENS = ("--muted", "--accent", "--warn")

# Shrink-only. Pages still resolving to the legacy web/style.css palette, whose
# --muted (3.87:1) and --accent (3.27:1) fail AA. Migrating them changes 15
# live pages including the purchase flow, so it is a deliberate decision rather
# than a defect fix. Entries may be REMOVED as pages migrate; adding one back
# means a page regressed onto the legacy palette.
LEGACY_PALETTE_PAGES = frozenset({
    "about.html", "account.html", "buy.html", "gift.html", "index-legacy.html",
    "pay/btc.html", "pay/crypto.html", "privacy.html", "signin.html",
    "stats.html", "team/join.html", "terms.html", "v2/index.html",
    "verify/index.html",
})


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _root_vars(css_path: Path) -> dict[str, str]:
    """Custom properties declared on a bare :root selector."""
    if not css_path.exists():
        return {}
    src = _strip_comments(css_path.read_text(errors="replace"))
    out: dict[str, str] = {}
    for m in re.finditer(r"([^{}]*)\{([^{}]*)\}", src):
        selector = " ".join(m.group(1).split()).split("}")[-1].strip()
        if selector != ":root":
            continue
        for d in re.finditer(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", m.group(2)):
            out[d.group(1)] = d.group(2).strip()
    return out


def _resolve(page: Path) -> dict[str, str]:
    """Resolve :root tokens for a page by replaying its stylesheet order."""
    html = page.read_text(errors="replace")
    resolved: dict[str, str] = {}
    for href in re.findall(r'<link[^>]+href="([^"]+\.css)[^"]*"', html):
        rel = href.split("?")[0].lstrip("/")
        resolved.update(_root_vars(WEB / rel))
    return resolved


def _rgb(value: str) -> tuple[float, float, float] | None:
    value = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", value)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,[^)]+)?\)", value)
    if m:
        return tuple(float(g) for g in m.groups())  # type: ignore[return-value]
    return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    def chan(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _guarded_pages() -> list[Path]:
    pages = []
    for p in sorted(WEB.rglob("*.html")):
        rel = p.relative_to(WEB).as_posix()
        if "_mockups" in rel or "founder/" in rel or rel in LEGACY_PALETTE_PAGES:
            continue
        pages.append(p)
    return pages


def test_contrast_helper_matches_known_wcag_values():
    """Negative control: the helper must reproduce values computed by hand.

    Without this, a broken _rgb() or _luminance() would make every contrast
    assertion below pass vacuously.
    """
    cream = _rgb("#fdfaf3")
    assert cream is not None
    # Hand-computed, and independently corroborated by the note in
    # web/certificate.css:10-11 which cites ~3.9 and ~5.5 for the first two.
    assert contrast(_rgb("#837e75"), cream) == pytest.approx(3.87, abs=0.02)
    assert contrast(_rgb("#6b665d"), cream) == pytest.approx(5.47, abs=0.02)
    assert contrast(_rgb("#c08a3e"), cream) == pytest.approx(2.90, abs=0.02)
    assert contrast(_rgb("#a65a30"), cream) == pytest.approx(4.88, abs=0.02)
    # The failing value must actually fail, or the guard cannot discriminate.
    assert contrast(_rgb("#c08a3e"), cream) < AA_SMALL
    assert contrast(_rgb("#a65a30"), cream) >= AA_SMALL


def test_comment_stripping_is_load_bearing():
    """The guarded stylesheets quote failing hex values inside comments."""
    style = (WEB / "style.css").read_text()
    assert "#c08a3e" in style, "comment citing the old value was removed"
    assert "#c08a3e" not in _strip_comments(style), "stripper missed a comment"
    assert _root_vars(WEB / "style.css")["--warn"] == "#a65a30"


def test_warn_token_has_one_value_across_stylesheets():
    """--warn diverged across style.css and index.css; keep it unified."""
    values = {}
    for css in sorted(WEB.rglob("*.css")):
        if "founder/" in css.relative_to(WEB).as_posix():
            continue  # dark-themed internal console, separate palette
        v = _root_vars(css).get("--warn")
        if v:
            values[css.relative_to(WEB).as_posix()] = v
    assert len(set(values.values())) == 1, f"--warn diverged again: {values}"


@pytest.mark.parametrize("page", _guarded_pages(), ids=lambda p: p.name)
def test_text_tokens_meet_wcag_aa(page: Path):
    tokens = _resolve(page)
    bg = _rgb(tokens.get("--bg", "#fdfaf3"))
    if bg is None:
        pytest.skip("page background is not a flat colour")
    for name in TEXT_TOKENS:
        raw = tokens.get(name)
        if raw is None:
            continue
        fg = _rgb(raw)
        if fg is None:
            continue
        ratio = contrast(fg, bg)
        assert ratio >= AA_SMALL, (
            f"{page.relative_to(WEB)}: {name}={raw} is {ratio:.2f}:1 on "
            f"{tokens.get('--bg', '#fdfaf3')}, below WCAG AA {AA_SMALL}:1"
        )
