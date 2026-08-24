"""Contract tests for the homepage envelope/receipt reveal."""

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"
SCRIPT = ROOT / "web" / "hero-envelope.js"
STYLES = ROOT / "web" / "css" / "orpho-home.css"


class _ElementCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.by_id[element_id] = (tag, attributes)


def _homepage_elements():
    parser = _ElementCollector()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser.by_id


def test_reveal_has_one_native_accessible_control():
    elements = _homepage_elements()
    tag, attrs = elements["hero-envelope-toggle"]
    assert tag == "button"
    assert attrs["type"] == "button"
    assert attrs["aria-expanded"] == "false"
    assert attrs["aria-controls"] == "hero-sample-receipt"
    assert elements["hero-sample-receipt"][0] == "article"


def test_reveal_keeps_static_receipt_available_without_javascript():
    html = INDEX.read_text(encoding="utf-8")
    assert 'class="orpho-hero__plate"' in html
    assert 'aria-hidden="true"' not in html.split('id="hero-envelope"', 1)[0][-120:]
    assert ".is-interactive" in STYLES.read_text(encoding="utf-8")
    assert 'src="/hero-envelope.js?v=1"' in html


def test_reveal_covers_keyboard_and_motion_preferences():
    script = SCRIPT.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    assert 'event.key === "Escape"' in script
    assert 'setAttribute("aria-expanded"' in script
    assert 'requestAnimationFrame' in script
    assert "prefers-reduced-motion: reduce" in script
    assert "CLOSE_SETTLE_MS" in script
    assert "window.clearTimeout(settleTimer)" in script
    assert "prefers-reduced-motion: reduce" in styles


def test_reveal_stages_flap_and_receipt_motion():
    styles = STYLES.read_text(encoding="utf-8")
    assert "transition-delay: 360ms, 360ms, 360ms" in styles
    assert "transition-delay: 180ms, 120ms, 180ms" in styles


def test_reveal_javascript_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    subprocess.run([node, "--check", str(SCRIPT)], check=True)


def test_tucked_receipt_geometry_is_envelope_bound():
    """Regression for the 2026-08-23 misalignment: the tucked receipt escaped
    the envelope at mid viewport widths because its position was a
    receipt-relative translate on top of breakpoint-dependent plate geometry.
    The interactive state must pin the receipt with explicit horizontal bounds
    and reserve transform for motion only."""
    styles = STYLES.read_text(encoding="utf-8")
    tucked = styles.split(".orpho-hero__plate.is-interactive .orpho-hero__receipt", 1)[1]
    tucked = tucked.split("}", 1)[0]
    assert "left:" in tucked, "tucked receipt must set an explicit left bound"
    assert "right:" in tucked, "tucked receipt must set an explicit right bound"
    assert "animation: none" in tucked, (
        "interactive mode must disable the ambient orpho-float animation; "
        "otherwise it masks every author transform and the open/close "
        "motion never renders"
    )
    assert "translate3d(0, 0, 0)" in tucked, (
        "tucked transform must be identity; lateral drift re-opens the escape"
    )
    # The old runaway nudge must not come back anywhere in the file.
    assert "translate3d(14%" not in styles
    # The open lift is vertical-only: no lateral component.
    open_rule = styles.split(
        ".orpho-hero__plate.is-open .orpho-hero__receipt {", 1
    )[1].split("}", 1)[0]
    assert "translate3d(0, -" in open_rule


def test_envelope_reads_as_a_mailing_letter():
    """2026-08-23 founder pass: the twine must stay visible while closed
    (interactive flap z-order buried it), the pocket folds must close
    corner-to-apex instead of ending mid-air, and the receipt carries the
    letterhead crest."""
    styles = STYLES.read_text(encoding="utf-8")
    html = INDEX.read_text(encoding="utf-8")
    interactive = styles.split("Interactive envelope reveal", 1)[1]
    assert ".orpho-envelope__string { z-index: 5; }" in interactive
    assert ".orpho-envelope__closure { z-index: 6; }" in interactive
    assert "polygon(0 100%, 50% 7%, 100% 100%)" in interactive, (
        "bottom fold triangle must run corner-to-apex"
    )
    assert 'class="orpho-receipt__crest"' in html
    assert "orpho-receipt__crest" in styles


def test_genie_animation_is_gated_and_agrees_with_fallback():
    """The genie keyframes only run under no-preference, and their 100%
    frame must equal the is-open fallback transform so both paths settle on
    identical geometry (an animation that disagrees would mask the
    transition and freeze the receipt elsewhere)."""
    styles = STYLES.read_text(encoding="utf-8")
    assert "prefers-reduced-motion: no-preference" in styles
    genie = styles.split("@keyframes orpho-genie", 1)[1].split("}\n  .", 1)[0]
    final = "translate3d(0, -26%, 34px) scale(1.02) rotate(-.35deg)"
    assert final in genie, "genie 100% frame must equal the fallback pose"
    open_rule = styles.split(
        ".orpho-hero__plate.is-open .orpho-hero__receipt {", 1
    )[1].split("}", 1)[0]
    assert final in open_rule
