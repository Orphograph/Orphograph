#!/usr/bin/env python3
"""test_no_inline_styles.py — inline style= is dead code under our CSP.

DEFECT (2026-08-06 Stage 3e drift sweep)
----------------------------------------
The site is served with

    content-security-policy: default-src 'self'; style-src 'self'; script-src 'self'

with no 'unsafe-inline'. Under CSP Level 2+ that governs style ATTRIBUTES as
well as <style> elements, so every `style="..."` attribute was silently
discarded by the browser.

Proven, not assumed: the same markup was rendered twice in headless Brave,
once with that CSP and once without. Without it the probe text was centred at
99px; with it the text rendered unstyled at the default size, left-aligned.

134 attributes across 20 live pages were dead — including the site footer on
every one of them (centring, muted colour, padding) and the uppercase
letter-spaced eyebrow on every /method and /legal page. The repo already knew
this: several stylesheets carry an "externalized … (strict CSP: no inline
styles)" banner. These were the leftovers.

They now live in web/u.css. This test stops them coming back, because the
failure mode is invisible — nothing errors, nothing logs, the page just
quietly renders wrong.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# _mockups/ are design scratch files, never served.
SKIP_PARTS = ("_mockups",)

INLINE_STYLE = re.compile(r"<[a-zA-Z][\w-]*(?:[^<>\"']|\"[^\"]*\"|'[^']*')*?>")
STYLE_ATTR = re.compile(r'\sstyle\s*=\s*"')
CODE_BLOCK = re.compile(r"<(pre|code|textarea)\b.*?</\1>", re.S | re.I)


def _pages():
    for p in sorted(WEB.rglob("*.html")):
        if any(part in str(p) for part in SKIP_PARTS):
            continue
        yield p


class TestNoInlineStyles(unittest.TestCase):

    def test_no_served_page_uses_an_inline_style_attribute(self):
        offenders = []
        for p in _pages():
            text = p.read_text(errors="ignore")
            # Documentation legitimately SHOWS markup inside code samples;
            # that text is displayed, not applied, so it is not a defect.
            stripped = CODE_BLOCK.sub("", text)
            for tag in INLINE_STYLE.findall(stripped):
                if STYLE_ATTR.search(tag):
                    offenders.append(f"{p.relative_to(ROOT)}: {tag[:110]}")
        self.assertEqual(
            offenders, [], "\n".join([
                "These inline style= attributes are DISCARDED by the browser: "
                "the site sends style-src 'self' with no 'unsafe-inline', so "
                "the declarations never apply and the page renders wrong with "
                "no error anywhere. Move them into web/u.css (or the page's "
                "own stylesheet) and reference a class.", *offenders]))

    def test_the_utility_stylesheet_exists_and_is_linked_where_used(self):
        u = WEB / "u.css"
        self.assertTrue(u.is_file(), "web/u.css is missing")
        classes = set(re.findall(r"^\.([\w-]+)\s*\{", u.read_text(), re.M))
        self.assertTrue(classes, "web/u.css defines no classes")
        missing = []
        for p in _pages():
            text = p.read_text(errors="ignore")
            used = {c for c in classes
                    if re.search(rf'class="[^"]*\b{re.escape(c)}\b', text)}
            if used and "/u.css" not in text:
                missing.append(f"{p.relative_to(ROOT)} uses {sorted(used)[:3]}")
        self.assertEqual(missing, [],
                         "pages use a u.css class without linking the "
                         "stylesheet, so the class does nothing:\n  "
                         + "\n  ".join(missing))

    def test_every_utility_class_is_actually_used(self):
        """Dead CSS is how a utility file rots into noise."""
        u = WEB / "u.css"
        classes = set(re.findall(r"^\.([\w-]+)\s*\{", u.read_text(), re.M))
        all_html = "\n".join(p.read_text(errors="ignore") for p in _pages())
        unused = sorted(c for c in classes
                        if not re.search(rf'class="[^"]*\b{re.escape(c)}\b',
                                         all_html))
        self.assertEqual(unused, [], f"unused utility classes: {unused}")


if __name__ == "__main__":
    unittest.main()
