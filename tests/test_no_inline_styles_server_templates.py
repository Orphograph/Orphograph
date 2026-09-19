"""Inline styles in SERVER-BUILT HTML, under `style-src 'self'`.

`tests/test_no_inline_styles.py` reads `web/**/*.html` only. The CSP applies to
every response, including HTML assembled in Python. Found 2026-09-19: the live
unsubscribe page carried an inline style block (fixed in the same change), and
server/verticals.py still does. Nothing is broken in production today only
because /verticals/* is not served there (the image ships without config/).
Shipping those pages as they stand would ship them unstyled.

Reads STRING CONSTANTS through `ast`, never raw text:
  - the parser has already joined adjacent literals, so an attribute that
    starts a continuation line (`"<table " "style=..."`, this codebase's idiom
    for long tags) is seen. A text regex needing whitespace before `style`
    found 56 of the 65 in the mailer and missed every one of that shape;
  - comments and docstrings are not constants that reach a response, so prose
    about styles cannot turn the gate red (it did, once, on its first day).

RATCHET, not allowlist: the known violator is pinned WITH ITS COUNT, so it
cannot grow inside the file either, and fixing it forces the pin's deletion.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"

# module -> (pinned count, why tolerated for now). Counts only go down.
KNOWN_VIOLATORS = {
    "verticals.py": (10, "not served in production (no config/ in the image); "
                         "ship-or-retire is an open founder decision"),
}

# Not HTTP responses. A CSP is a response header and does not govern mail
# clients, which in practice REQUIRE inline styles. Exempt by category, named.
# Known limit, stated: the exemption is per FILE. HTML served over HTTP from
# one of these modules would not be seen here.
EMAIL_MODULES = {
    "mailer.py": "builds email bodies, never an HTTP response",
    "newsletter.py": "its only styled HTML is the double-opt-in confirmation "
                     "EMAIL body; its HTTP endpoints answer JSON",
}

_STYLE_BLOCK = re.compile(r"<style\b", re.I)
_STYLE_ATTR = re.compile(r"<[a-zA-Z][^<>]*?\sstyle\s*=", re.I)


def _html_strings(source: str) -> list[str]:
    """Every string value the module can emit, docstrings excluded. f-strings
    contribute their literal parts joined, which keeps a tag split around an
    interpolation in one piece."""
    tree = ast.parse(source)
    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_ids.add(id(first.value))
    out, inside_fstring = [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = [v.value for v in node.values
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            inside_fstring.update(id(v) for v in node.values)
            out.append("{}".join(parts))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in doc_ids and id(node) not in inside_fstring):
            out.append(node.value)
    return out


def _violations(source: str) -> int:
    return sum(len(_STYLE_BLOCK.findall(s)) + len(_STYLE_ATTR.findall(s))
               for s in _html_strings(source))


def _modules():
    return sorted(SERVER.glob("*.py"))


class TestServerBuiltHtmlCarriesNoInlineStyles(unittest.TestCase):
    def test_policy_still_forbids_inline_styles(self):
        """If the CSP ever allows inline styles this gate's premise is gone."""
        policies = []
        for p in _modules():
            policies += re.findall(r"style-src[^;]*;", p.read_text(errors="replace"))
        self.assertTrue(policies, "no style-src directive found under server/")
        for pol in policies:
            self.assertNotIn("unsafe-inline", pol)

    def test_domain_is_not_empty(self):
        self.assertGreater(len(_modules()), 10)

    def test_only_pinned_modules_have_inline_styles_and_none_grew(self):
        found = {}
        for p in _modules():
            if p.name in EMAIL_MODULES:
                continue
            n = _violations(p.read_text(errors="replace"))
            if n:
                found[p.name] = n
        pinned = {k: v[0] for k, v in KNOWN_VIOLATORS.items()}
        self.assertEqual(
            found, pinned,
            "inline styles in server-built HTML changed (the CSP drops them). "
            "A new module or a higher count is a defect; a lower count means "
            f"lower the pin. found={found} pinned={pinned}")

    def test_the_scanner_sees_what_it_must_and_nothing_else(self):
        self.assertEqual(_violations('X = \'<p style="color:red">x</p>\''), 1)
        self.assertEqual(_violations('X = "<style>p{}</style>"'), 1)
        # the continuation-literal idiom a text regex missed
        self.assertEqual(_violations(
            'X = ("<table role=\\"presentation\\" "\n     "style=\\"margin:0\\">")'), 1)
        # split around an interpolation
        self.assertEqual(_violations('X = f"<td {cls} style=\\"a:b\\">"'), 1)
        # prose is not output
        self.assertEqual(_violations('# an inline <style> block\nX = 1'), 0)
        self.assertEqual(_violations('def f():\n    """uses <p style="x">"""\n    return 1'), 0)
        self.assertEqual(_violations('X = \'<link rel="stylesheet" href="/u.css">\''), 0)
        self.assertEqual(_violations('style = compute_style()'), 0)

    def test_the_scanner_can_hit_in_the_exempt_modules(self):
        """CONTROL. The exemption is a decision, not blindness: the scanner
        must SEE the mailer's styles, and see essentially all of them."""
        n = _violations((SERVER / "mailer.py").read_text(errors="replace"))
        self.assertGreaterEqual(n, 60)


if __name__ == "__main__":
    unittest.main()
