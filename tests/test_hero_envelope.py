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
    assert 'src="/hero-envelope.js?v=4"' in html


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
    assert "polygon(6% 100%, 16% 5%, 84% 5%, 94% 100%)" in interactive, (
        "the bottom flap is a TRAPEZOID: its slanted edges are the corner "
        "creases and must run out to the side seams, not meet at a floating "
        "apex (pass 12, real-envelope fidelity)"
    )
    assert 'class="orpho-receipt__crest"' in html
    assert "orpho-receipt__crest" in styles


def test_genie_animation_is_gated_and_agrees_with_fallback():
    """The genie keyframes only run under no-preference, and their 100%
    frame must equal the is-open fallback transform so both paths settle on
    identical geometry (an animation that disagrees would mask the
    transition and freeze the receipt elsewhere)."""
    styles = STYLES.read_text(encoding="utf-8")
    idx = styles.find("@keyframes orpho-genie")
    assert idx > 0
    assert "prefers-reduced-motion: no-preference" in styles[max(0, idx - 700):idx], (
        "genie keyframes must sit inside the no-preference media block"
    )
    gate_slice = styles[idx:idx + 2200]
    assert "animation: orpho-genie" in gate_slice, (
        "the animation rule must live in the same gated block as its keyframes"
    )
    assert ".is-genie" in gate_slice, (
        "genie runs only when JS marks a settled-state open (is-genie)"
    )
    genie = styles.split("@keyframes orpho-genie", 1)[1].split("}\n  .", 1)[0]
    final = "translate3d(0, -40%, 34px) scale(1.02) rotate(-.35deg)"
    assert final in genie, "genie 100% frame must equal the fallback pose"
    open_rule = styles.split(
        ".orpho-hero__plate.is-open .orpho-hero__receipt {", 1
    )[1].split("}", 1)[0]
    assert final in open_rule


def test_genie_emerges_from_inside_and_clicks_are_guarded():
    """Founder pass 2 (2026-08-23 night): the receipt must rise from INSIDE
    the envelope (held behind the pocket during the rise), the strip title
    must clear the crest, and one click = one motion (mid-flight clicks were
    toggling the state back, reading as 'four clicks to close')."""
    styles = STYLES.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    genie = styles.split("@keyframes orpho-genie", 1)[1].split("  .orpho", 1)[0]
    assert "z-index: 2;" in genie, (
        "the rise must hold the receipt behind the pocket (z 2) so it "
        "visibly comes out of the mouth"
    )
    assert "z-index: 6;" in genie, "the pop-forward must end above the envelope"
    assert ".orpho-hero__receipt .orpho-receipt__title" in styles, (
        "strip title needs crest clearance"
    )
    tucked = styles.split(
        ".orpho-hero__plate.is-interactive .orpho-hero__receipt {", 1
    )[1].split("}", 1)[0]
    assert "visibility: hidden" in tucked, (
        "closed must show ONLY the envelope; a percentage clip is "
        "receipt-relative and spilled past the envelope at 560px"
    )
    assert "is-closing.is-genie" in styles, (
        "going back in must mirror the emergence"
    )
    assert "busyUntil" in script and "MOTION_MS" in script, (
        "clicks must be swallowed while a motion is in flight"
    )
    assert script.count("motionInFlight()") >= 3, (
        "every path that changes state — click, Escape, click-away — must "
        "consult the same in-flight guard"
    )
    assert "setOpen(false);" in script and "requestClose()" in script



def test_envelope_paper_is_real_and_never_a_stacking_context():
    """Pass 12. Paper needs grain, thickness and seams; and the grain must
    ride as a BACKGROUND layer, because any overlay element or any
    transform/filter/opacity on .orpho-envelope turns it into a stacking
    context, flattens the pocket/receipt z-order and silently kills the
    emerge-from-inside animation (the #185 defect)."""
    styles = STYLES.read_text(encoding="utf-8")
    assert "--orpho-grain" in styles and "feTurbulence" in styles, (
        "paper needs grain, not just gradients"
    )
    assert "background-blend-mode" in styles, "grain must blend, not overlay"

    # Every .orpho-envelope rule block: no stacking-context trigger.
    banned = ("transform:", "filter:", "will-change:", "opacity:")
    for chunk in styles.split(".orpho-envelope {")[1:]:
        body = chunk.split("}", 1)[0]
        for prop in banned:
            assert prop not in body, (
                f".orpho-envelope must never declare {prop} — it becomes a "
                "stacking context and flattens the reveal's z-order"
            )


def test_hero_receipt_title_cannot_wrap():
    """Regression for a defect shipped live in #185: symmetric 62px crest
    clearance took 124px off a ~435px title box and wrapped "Orphograph
    Receipt" onto two lines. The guarantee is structural, not a magic
    padding number."""
    styles = STYLES.read_text(encoding="utf-8")
    blocks = [c.split("}", 1)[0] for c in
              styles.split(".orpho-hero__receipt .orpho-receipt__title {")[1:]]
    assert blocks, "the hero strip title needs its own rule"
    assert any("white-space: nowrap" in b for b in blocks), (
        "the strip title must never wrap"
    )
    assert all("white-space: normal" not in b for b in blocks), (
        "no later rule may undo the nowrap guarantee"
    )
    assert any("font-size" in b for b in blocks), (
        "it must be sized to fit the reduced box"
    )
