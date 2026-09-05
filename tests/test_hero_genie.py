"""Contract tests for the genie warp (2026-09-05).

The homepage letter leaves and re-enters the envelope the way a window
leaves the macOS Dock: hero-envelope.js cuts a clone into horizontal bands and
funnels them out of the pocket mouth with the Web Animations API. These pin
the parts that a static read can pin; the behavioural probe
(scripts/probe_hero_genie.py) drives a real engine.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "web" / "index.html"
SCRIPT = ROOT / "web" / "hero-envelope.js"
STYLES = ROOT / "web" / "css" / "orpho-home.css"


def _script():
    return SCRIPT.read_text(encoding="utf-8")


def _styles():
    return STYLES.read_text(encoding="utf-8")


def test_warp_is_feature_gated_and_falls_back_to_css_keyframes():
    script = _script()
    assert "Element.prototype.animate" in script, "WAAPI must be detected, not assumed"
    assert 'CSS.supports("clip-path", "inset(0 0 0 0)")' in script
    assert "warpAvailable = !reducedMotion" in script, "reduced motion never warps"
    # The CSS path is still reachable: the keyframes and the is-genie hook stay.
    assert "@keyframes orpho-genie" in _styles()
    assert 'plate.classList.toggle("is-genie", settled)' in script


def test_warp_bands_funnel_from_the_mouth_with_a_stagger():
    script = _script()
    assert "STAGGER_MS" in script and "TRAVEL_MS" in script and "THROAT" in script
    # Every band starts on the mouth line, squeezed to the throat.
    assert "const startY = mouthY - f * box.height" in script
    assert 'scaleX(" + THROAT + ")' in script
    # Opening: head first (delay grows with f). Closing: tail first (1 - f).
    assert "(opening ? f : 1 - f) * STAGGER_MS" in script
    assert 'direction: opening ? "normal" : "reverse"' in script


def test_warp_parks_the_real_letter_and_hands_off_at_the_end_pose():
    styles = _styles()
    script = _script()
    warp = styles.split(".orpho-hero__plate.is-warp .orpho-hero__receipt {", 1)[1].split("}", 1)[0]
    assert "visibility: hidden !important" in warp
    assert "animation: none !important" in warp and "transition: none !important" in warp
    # The stage takes the letter's computed end transform, so the last band
    # frame and the live element coincide.
    assert 'stage.style.transform = box.transform === "none" ? "" : box.transform' in script
    assert "stage.remove();" in script and 'plate.classList.remove("is-warp")' in script


def test_measurement_never_animates_and_restores_state():
    styles = _styles()
    script = _script()
    meas = styles.split(".orpho-hero__plate.is-measuring", 1)[1].split("}", 1)[0]
    assert "transition: none !important" in meas and "animation: none !important" in meas
    assert 'plate.classList.toggle("is-open", hadOpen)' in script
    assert 'plate.classList.toggle("is-closing", hadClosing)' in script


def test_stage_starts_behind_the_pocket_and_comes_forward():
    script = _script()
    assert 'stage.style.zIndex = opening ? "2" : "6"' in script
    assert '{ zIndex: opening ? "6" : "2" }' in script
    genie = _styles().split(".orpho-genie {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none" in genie, "the clone must never eat the click"


def test_phone_plate_grows_to_the_real_letter_height():
    styles = _styles()
    script = _script()
    assert 'plate.style.setProperty("--orpho-open-h"' in script
    assert "min-height: var(--orpho-open-h, 780px)" in styles
    # The envelope keeps its shape while the plate grows.
    env = styles.split(".orpho-hero__plate.is-interactive .orpho-envelope {", 1)[1].split("}", 1)[0]
    assert "height: min(400px" in env and "inset: 0 0 auto 0" in env
    assert "transition: min-height" in styles


def test_click_guard_covers_the_whole_warp():
    script = _script()
    assert "const MOTION_MS = Math.max(960, WARP_MS + 100)" in script
    assert "const WARP_MS = LEAD_MS + TRAVEL_MS + STAGGER_MS" in script


def test_genie_css_sits_above_the_terminal_block():
    styles = _styles()
    assert 0 <= styles.find("Genie warp (2026-09-05)") < styles.find("TERMINAL BLOCK")


def test_script_version_bumped_with_its_bytes():
    html = INDEX.read_text(encoding="utf-8")
    assert 'src="/hero-envelope.js?v=5"' in html
    assert re.search(r'/css/orpho-home\.css\?v=18"', html)
