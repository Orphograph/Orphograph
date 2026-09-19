#!/usr/bin/env python3
"""test_no_inline_script_static.py — inline <script> is dead code under our CSP.

DEFECT (2026-09-19 static-gate sweep)
--------------------------------------
Planting `<script>alert(1)</script>` directly into web/learn.html left the
whole suite (~2299 tests) green. Nothing statically scanned page markup for
a script-src violation, and the site's CSP,

    Content-Security-Policy: ... script-src 'self'; ...

carries no 'unsafe-inline', no 'nonce-…', no 'sha256-…' (confirmed live below,
not assumed — test_csp_has_no_inline_script_exception calls the real
_security_headers() and inspects the header string it sends). A browser
receiving that header silently drops any <script> element with no src= and
any inline event-handler attribute (onclick=, …): no console error most
people watch for, no server round trip, nothing — the page just quietly
ships broken JavaScript. Same failure class as test_no_inline_styles.py's
style= defect (2026-08-06): CSP-invisible, only caught by static reading.

DOMAIN (must be everything the CSP applies to)
------------------------------------------------
2026-09-12's postmortem in test_versioned_asset_bump.py is the reason this
scan does not stop at web/*.html: a scan that reads only the static pages
misses drift in server-built HTML, and that gap shipped a real defect there.
This gate's domain is:

  1. web/**/*.html EXCEPT the paths server/app.py itself refuses to serve.
     app._is_private_path() is the live production check (_mockups/** and
     index-legacy[.html] both 404 unconditionally — see app.py's
     _PRIVATE_PATH_PREFIXES / _PRIVATE_PATH_EXACT) — reused here rather than
     re-guessed, so this test tracks that logic instead of drifting from it.
     108 files pass that check (git ls-files and pathlib.rglob agree — see
     test_domain_enumeration_methods_agree).

  2. server/*.py — every module, read as literal source text for a <script>
     tag written straight into an HTML template string. Three modules build
     full documents (app.py's error page, blog.py's post shell,
     verticals.py's vertical-page template); the other 50 are scanned too,
     on purpose: reading "only the templates I already know about" is
     exactly the mistake the 2026-09-12 gap made. 53 files.

  108 + 53 = 161 files scanned. N = 161 (independently recounted via
  `git ls-files` and via `pathlib.rglob`/`glob` in
  test_domain_enumeration_methods_agree; they must produce the same set).

  Explicitly OUT of domain, with why:
    - web/_mockups/**, web/index-legacy.html: never served (see above).
    - repo-root dist/ and explainer/: not under server.app.WEB_DIR
      (`ROOT / "web"`), so the Python static server never returns them and
      this CSP is not the one they would (or, per dist/browser-extension,
      even could — a packed extension gets its CSP from its own
      manifest.json, not this HTTP server) be served under.
    - content/blog/*.md (rendered by server/blog.py's markdown renderer):
      _render_markdown() calls html.escape() on the raw text BEFORE
      re-introducing a fixed, closed set of safe tags (p/code/a/strong/em/
      h1-6/ul/ol/li/blockquote/hr) with no raw-HTML passthrough directive of
      any kind, so no markdown source can ever produce a literal <script>
      element in rendered output. Pinned by
      test_markdown_renderer_cannot_emit_script below, not just asserted in
      this docstring.
    - server/verticals.py's YAML-driven text fields (audience, FAQ, technical
      detail, disclaimer): every one of them is routed through _esc() /
      html.escape() before insertion (verified by reading verticals.py);
      pinned by test_verticals_yaml_fields_cannot_emit_script below.

ALLOWLIST
---------
A <script> element with no src= is allowed ONLY if its type= is one of the
two data-block types below — never executed as JavaScript by the browser
regardless of CSP (the HTML spec treats an unrecognized/data script type as
inert data, so script-src's execution gate never applies to it; this is why
JSON-LD under a strict CSP with no 'unsafe-inline' is standard practice).
Everything else with no src= is a defect: this CSP has no nonce/hash
mechanism for anything more, so there is nothing else CORRECTLY inline here
(pinned live by test_csp_has_no_inline_script_exception; if that ever
starts failing because the CSP gained a nonce/hash, THIS gate's allowlist
logic must be rewritten to recognise exactly that nonce/hash and nothing
broader — do not widen the allowlist to "fix" that failure).

EVENT HANDLERS
--------------
CSP Level 2+ governs inline event-handler ATTRIBUTES (onclick=, onload=, …)
under script-src the same way it governs <script> elements — with no
'unsafe-inline' on script-src, the browser drops those too. Swept: repo-wide
grep for on(click|load|...)\\s*= across web/**/*.html and the server
templates currently finds zero (test_scan_finds_the_known_tags asserts the
scan itself is not vacuous, so a zero count from a broken scan cannot pass
silently). If that count ever becomes nonzero, EVERY one of them is a live
CSP-blocked defect under the current header — there is no CSP condition
under which an inline handler would be allowed here.
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SERVER_DIR = ROOT / "server"

# tests/conftest.py puts server/ on sys.path before this module is imported.
import app  # noqa: E402  (server/app.py)
import blog  # noqa: E402  (server/blog.py)
import verticals  # noqa: E402  (server/verticals.py)

# ---------------------------------------------------------------------------
# Allowlist — small, named, one reason each. See ALLOWLIST in the docstring.
# ---------------------------------------------------------------------------
ALLOWED_INLINE_SCRIPT_TYPES: dict[str, str] = {
    "application/ld+json": (
        "schema.org structured-data blocks. Not executable script — the "
        "HTML parser never runs a data-type <script>, so script-src's "
        "execution gate does not apply to it."
    ),
    "application/json": (
        "inline JSON data islands. Same non-executable-type reasoning as "
        "application/ld+json."
    ),
}

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------
# A <script ...> opening tag, tolerant of a '>' inside a quoted attribute
# value (the same tolerant-tag technique test_no_inline_styles.py uses).
_SCRIPT_OPEN = re.compile(
    r"<script\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.IGNORECASE,
)
# Negative lookbehind, not \b: a plain \bsrc\s*= matches inside data-src=
# too (\b fires between '-' and 's'), which would let a real inline script
# disguised as `<script data-src="...">alert(1)</script>` pass as
# "external". See test_the_patterns_discriminate's data-src= case.
_SRC_ATTR = re.compile(r"(?<![-\w])src\s*=", re.IGNORECASE)
_TYPE_ATTR = re.compile(r'\btype\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)

# <textarea> is a raw-text element: the HTML parser never treats its content
# as child elements, so a literal "<script>" typed inside one is inert text,
# not a real script tag. Stripped before scanning to avoid a false positive
# on, e.g., a "paste your HTML here" demo box. (Nothing in the tree uses this
# today — test_scan_finds_the_known_tags proves the scan still finds real
# script tags with this strip in place, so the strip is not silently eating
# the whole domain.)
_TEXTAREA_BLOCK = re.compile(r"<textarea\b.*?</textarea>", re.S | re.I)

# Inline event-handler attributes. CSP governs these under script-src too
# (see EVENT HANDLERS in the docstring).
_EVENT_HANDLER_ATTR = re.compile(
    r"\son(?:click|dblclick|mouse(?:down|up|over|out|move|enter|leave)|"
    r"key(?:down|up|press)|focus|blur|change|input|submit|reset|load|"
    r"error|drag\w*|drop|scroll|touch\w*|animation\w*|transition\w*|"
    r"wheel|contextmenu|copy|cut|paste|toggle)\s*=",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Domain enumeration
# ---------------------------------------------------------------------------
def _pages() -> list[tuple[str, Path]]:
    """(relative-to-repo-root label, path) for every web/**/*.html the
    server would actually answer a GET for with this CSP header."""
    out = []
    for p in sorted(WEB.rglob("*.html")):
        rel_to_web = p.relative_to(WEB).as_posix()
        if app._is_private_path(rel_to_web):
            continue
        out.append((p.relative_to(ROOT).as_posix(), p))
    return out


def _server_modules() -> list[tuple[str, Path]]:
    """(relative-to-repo-root label, path) for every server/*.py module,
    scanned as literal source text."""
    return [(p.relative_to(ROOT).as_posix(), p) for p in sorted(SERVER_DIR.glob("*.py"))]


def _domain() -> list[tuple[str, Path]]:
    return _pages() + _server_modules()


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------
def _script_offenders(label: str, text: str) -> list[str]:
    stripped = _TEXTAREA_BLOCK.sub("", text)
    offenders = []
    for m in _SCRIPT_OPEN.finditer(stripped):
        attrs = m.group(1)
        if _SRC_ATTR.search(attrs):
            continue  # external script — not governed by this gate
        type_m = _TYPE_ATTR.search(attrs)
        script_type = type_m.group(1).strip().lower() if type_m else ""
        if script_type in ALLOWED_INLINE_SCRIPT_TYPES:
            continue
        line = stripped[: m.start()].count("\n") + 1
        offenders.append(
            f"{label}:{line}: <script{attrs}> — inline script with no src=, "
            f"type={script_type or '(none, i.e. text/javascript)'!r} is not "
            f"in ALLOWED_INLINE_SCRIPT_TYPES; blocked by script-src 'self' "
            f"with no 'unsafe-inline'"
        )
    return offenders


def _event_handler_offenders(label: str, text: str) -> list[str]:
    stripped = _TEXTAREA_BLOCK.sub("", text)
    offenders = []
    for m in _EVENT_HANDLER_ATTR.finditer(stripped):
        line = stripped[: m.start()].count("\n") + 1
        offenders.append(
            f"{label}:{line}: inline event-handler attribute "
            f"{stripped[m.start():m.end()].strip()!r} — blocked by "
            f"script-src 'self' with no 'unsafe-inline'"
        )
    return offenders


class TestNoInlineScriptStatic(unittest.TestCase):

    def test_csp_has_no_inline_script_exception(self):
        """CONTROL. Pins the assumption every other test in this file leans
        on: the live CSP has no 'unsafe-inline', no nonce, no hash on
        script-src. Calls the real _security_headers(), not a copy of the
        string. If this starts failing, the CSP grew an exception and the
        allowlist logic above must be rewritten to match it exactly — do not
        just widen ALLOWED_INLINE_SCRIPT_TYPES."""

        class _StubHandler:
            def __init__(self):
                self.path = "/"
                self.sent: list[tuple[str, str]] = []

            def send_header(self, name, value):
                self.sent.append((name, value))

        stub = _StubHandler()
        app._security_headers(stub)
        csp = dict(stub.sent).get("Content-Security-Policy")
        self.assertIsNotNone(csp, "_security_headers sent no CSP header")
        directives = {
            d.strip().split(None, 1)[0]: d.strip()
            for d in csp.split(";") if d.strip()
        }
        self.assertIn("script-src", directives)
        script_src = directives["script-src"]
        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("nonce-", script_src)
        self.assertNotIn("sha256-", script_src)
        self.assertNotIn("sha384-", script_src)
        self.assertNotIn("sha512-", script_src)
        self.assertEqual(script_src, "script-src 'self'")

    def test_domain_enumeration_methods_agree(self):
        """CONTROL on the domain itself. pathlib glob (what this test scans)
        and `git ls-files` (what actually ships) must name the same set of
        files, or the scan is silently blind to something tracked (or
        scanning something that was never checked in)."""
        glob_pages = {rel for rel, _ in _pages()}
        glob_server = {rel for rel, _ in _server_modules()}

        tracked_html = subprocess.run(
            ["git", "ls-files", "web/*.html"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        tracked_pages = {
            rel for rel in tracked_html
            if not app._is_private_path(Path(rel).relative_to("web").as_posix())
        }
        tracked_server = set(
            subprocess.run(
                ["git", "ls-files", "server/*.py"],
                cwd=ROOT, capture_output=True, text=True, check=True,
            ).stdout.splitlines()
        )

        self.assertEqual(glob_pages, tracked_pages,
                         "web/*.html domain differs between pathlib glob and "
                         "git ls-files")
        self.assertEqual(glob_server, tracked_server,
                         "server/*.py domain differs between pathlib glob and "
                         "git ls-files")
        n = len(glob_pages) + len(glob_server)
        self.assertGreater(n, 100, f"domain suspiciously small (N={n})")

    def test_scan_finds_the_known_tags(self):
        """NEGATIVE CONTROL. Zero findings would make the main assertions
        below pass vacuously — exactly how the learn.html defect shipped
        past ~2299 green tests. Proves the scan is actually reading the
        files: it must find real src= script tags, real allowlisted
        ld+json blocks, and at least one server-template reference."""
        total_script_tags = 0
        allowlisted_hits = 0
        src_hits = 0
        server_hits = 0
        for label, path in _domain():
            text = path.read_text(encoding="utf-8", errors="ignore")
            stripped = _TEXTAREA_BLOCK.sub("", text)
            for m in _SCRIPT_OPEN.finditer(stripped):
                total_script_tags += 1
                attrs = m.group(1)
                if _SRC_ATTR.search(attrs):
                    src_hits += 1
                    if label.startswith("server/"):
                        server_hits += 1
                type_m = _TYPE_ATTR.search(attrs)
                script_type = type_m.group(1).strip().lower() if type_m else ""
                if script_type in ALLOWED_INLINE_SCRIPT_TYPES:
                    allowlisted_hits += 1
        self.assertGreater(total_script_tags, 100,
                           f"scan found almost no <script> tags at all "
                           f"({total_script_tags}) — it is not reading the "
                           f"pages")
        self.assertGreater(src_hits, 100,
                           "scan found no external (src=) script tags — "
                           "the src= detector is broken")
        self.assertGreater(allowlisted_hits, 20,
                           "scan found no allowlisted ld+json blocks — the "
                           "allowlist check is broken")
        self.assertGreaterEqual(server_hits, 1,
                                "scan found no src= script reference inside "
                                "server/*.py — server templates are not "
                                "being read (this is exactly the 2026-09-12 "
                                "gap class)")

    def test_the_patterns_discriminate(self):
        """NEGATIVE CONTROL for the regexes themselves, on literal input."""
        self.assertEqual(
            _script_offenders("t", '<script>alert(1)</script>'),
            ["t:1: <script> — inline script with no src=, "
             "type='(none, i.e. text/javascript)' is not in "
             "ALLOWED_INLINE_SCRIPT_TYPES; blocked by script-src 'self' "
             "with no 'unsafe-inline'"],
        )
        self.assertEqual(
            _script_offenders("t", '<script src="/a.js"></script>'), [])
        self.assertEqual(
            _script_offenders(
                "t", '<script type="application/ld+json">{}</script>'), [])
        self.assertEqual(
            _script_offenders(
                "t", '<script type="application/json">{}</script>'), [])
        self.assertEqual(
            _script_offenders(
                "t", '<script type="text/plain">not js</script>'),
            ["t:1: <script type=\"text/plain\"> — inline script with no "
             "src=, type='text/plain' is not in ALLOWED_INLINE_SCRIPT_TYPES; "
             "blocked by script-src 'self' with no 'unsafe-inline'"],
        )
        # textarea content is inert — must not be flagged.
        self.assertEqual(
            _script_offenders(
                "t", '<textarea><script>alert(1)</script></textarea>'), [])
        # data-src= (or any -src=) must NOT satisfy the src= detector — a
        # word-boundary regex here would treat 'data-src=' as 'src=' and let
        # a real inline script through disguised as an "external" one.
        self.assertEqual(
            len(_script_offenders(
                "t", '<script data-src="/x.js">alert(1)</script>')), 1)

        self.assertEqual(
            _event_handler_offenders("t", '<button onclick="x()">go</button>'),
            ["t:1: inline event-handler attribute 'onclick=' — blocked by "
             "script-src 'self' with no 'unsafe-inline'"],
        )
        self.assertEqual(
            _event_handler_offenders("t", '<a href="/">plain link</a>'), [])
        self.assertEqual(
            _event_handler_offenders(
                "t", '<textarea>onclick="x()"</textarea>'), [])

    def test_no_served_page_has_an_inline_script(self):
        offenders: list[str] = []
        for label, path in _pages():
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders.extend(_script_offenders(label, text))
        self.assertEqual(
            offenders, [], "\n".join([
                "These <script> elements are DROPPED by the browser: the "
                "site sends script-src 'self' with no 'unsafe-inline', so "
                "the code never runs and the page silently ships broken "
                "JavaScript. Move the code into an external .js file and "
                "reference it with src=.", *offenders]))

    def test_no_server_template_has_an_inline_script(self):
        offenders: list[str] = []
        for label, path in _server_modules():
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders.extend(_script_offenders(label, text))
        self.assertEqual(
            offenders, [], "\n".join([
                "These <script> elements, written into a server-side HTML "
                "template string, are DROPPED by the browser under the "
                "site's CSP. Move the code into an external .js file and "
                "reference it with src=.", *offenders]))

    def test_no_served_page_has_an_inline_event_handler(self):
        offenders: list[str] = []
        for label, path in _pages():
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders.extend(_event_handler_offenders(label, text))
        self.assertEqual(
            offenders, [], "\n".join([
                "These inline event-handler attributes are DROPPED by the "
                "browser under script-src 'self' with no 'unsafe-inline'. "
                "Move the handler into an external .js file and attach it "
                "with addEventListener.", *offenders]))

    def test_no_server_template_has_an_inline_event_handler(self):
        offenders: list[str] = []
        for label, path in _server_modules():
            text = path.read_text(encoding="utf-8", errors="ignore")
            offenders.extend(_event_handler_offenders(label, text))
        self.assertEqual(
            offenders, [], "\n".join([
                "These inline event-handler attributes, written into a "
                "server-side HTML template string, are DROPPED by the "
                "browser under the site's CSP.", *offenders]))

    def test_markdown_renderer_cannot_emit_script(self):
        """Pins the reasoning for excluding content/blog/*.md from the scan
        domain: _render_markdown() escapes a literal <script> in the source
        text rather than passing it through."""
        out = blog._render_markdown("before\n\n<script>alert(1)</script>\n\nafter")
        self.assertNotIn("<script", out.lower())
        self.assertIn("&lt;script&gt;", out)

    def test_verticals_yaml_fields_cannot_emit_script(self):
        """Pins the same reasoning for server/verticals.py's YAML-driven
        text fields: every one is routed through _esc()/html.escape()."""
        payload = '<script>alert(1)</script>'
        self.assertNotIn("<script", verticals._esc(payload).lower())
        self.assertNotIn("<script", verticals._disclaimer_html(payload).lower())
        self.assertNotIn(
            "<script",
            verticals._audience_block({"audience": payload}).lower())
        self.assertNotIn(
            "<script",
            verticals._bullet_section("X", [payload]).lower())


if __name__ == "__main__":
    unittest.main()
