"""Inline styles in SERVER-SIDE templates, under `style-src 'self'`.

`tests/test_no_inline_styles.py` reads `web/**/*.html` only. The CSP applies to
every response, including HTML built in Python. Found 2026-09-19: the live
/unsubscribe page carried an inline <style> (fixed in the same change), and
`server/verticals.py` carries a `<style>` block and inline `style=` attributes
that the live policy (`style-src 'self'`, no 'unsafe-inline') blocks. Nothing
is broken in production today only because /verticals/* is not served there
(the image ships without config/). Shipping those pages as they stand would
ship them unstyled, and no gate would have said so.

This is a RATCHET, not an allowlist: the known violator is pinned by name so
the set cannot grow, and so that fixing verticals.py forces this pin to be
deleted rather than left behind as a stale excuse.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"

# module -> why it is tolerated for now. Must shrink, never grow.
KNOWN_VIOLATORS = {
    "verticals.py": "not served in production (no config/ in the image); "
                    "ship-or-retire is an open founder decision",
}

# Not HTTP responses. A CSP is a response header and does not govern mail
# clients, which in practice REQUIRE inline styles. Exempt by category, named.
EMAIL_MODULES = {
    "mailer.py": "builds email bodies, never an HTTP response",
}

_STYLE_BLOCK = re.compile(r"<style\b", re.I)
# an HTML attribute inside a Python string: preceded by whitespace, inside a tag
_STYLE_ATTR = re.compile(r"<[a-zA-Z][^<>]*\sstyle\s*=\s*[\"'{]", re.I)


def _violations(text: str) -> int:
    return len(_STYLE_BLOCK.findall(text)) + len(_STYLE_ATTR.findall(text))


class TestServerTemplatesCarryNoInlineStyles(unittest.TestCase):
    def test_policy_still_forbids_inline_styles(self):
        """If the CSP ever allows inline styles this gate's premise is gone."""
        src = (SERVER / "app.py").read_text()
        policies = re.findall(r"style-src[^;]*;", src)
        self.assertTrue(policies, "no style-src directive found in server/app.py")
        for p in policies:
            self.assertNotIn("unsafe-inline", p)

    def test_domain_is_not_empty(self):
        self.assertGreater(len(list(SERVER.glob("*.py"))), 10)

    def test_only_the_pinned_module_has_inline_styles(self):
        found = {p.name for p in sorted(SERVER.glob("*.py"))
                 if p.name not in EMAIL_MODULES
                 and _violations(p.read_text(errors="replace"))}
        self.assertEqual(
            found, set(KNOWN_VIOLATORS),
            "server modules emitting inline styles the CSP blocks changed. "
            f"New: {sorted(found - set(KNOWN_VIOLATORS))}. "
            f"Fixed (delete the pin): {sorted(set(KNOWN_VIOLATORS) - found)}.")

    def test_the_patterns_discriminate(self):
        self.assertEqual(_violations('<p style="color:red">x</p>'), 1)
        self.assertEqual(_violations("<style>p{}</style>"), 1)
        self.assertEqual(_violations('<link rel="stylesheet" href="/u.css">'), 0)
        self.assertEqual(_violations('style = compute_style()'), 0)


if __name__ == "__main__":
    unittest.main()
