"""No QR codes on the orphograph.com visitor surface (2026-08-18).

QR codes were removed from every visitor-facing page. This test is the
regression guard: it fails if any of them come back.

Why an inverse test rather than just deleting the old ones. The QR surface
was FIVE separate insertion points across four pages, two stylesheets' worth
of geometry rules, two server routes and a static asset -- and it had just
been worked on the week before (PR #170, "QR codes the camera can actually
resolve"). Deleting the presence-tests removes the assertion that they exist
but leaves nothing asserting that they do not. A partial reintroduction --
one page, one route -- is exactly the shape of change that slips through.

ONE surface is deliberately exempt: `web/docs/api.html`, which documents
`GET /api/btc-order/<order_id>/qr.svg`. That route is public, documented API
and was intentionally kept -- removing it would break third-party callers,
which is a larger change than was asked for. It renders no QR on any page we
serve.

The scan ends with a NEGATIVE CONTROL. A grep that finds nothing is
indistinguishable from a grep that cannot reach its files; the control
proves this one can hit.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

# Directories that are not the surface we serve to visitors.
# NOTE: `construction/` is deliberately NOT excluded -- server/app.py:652
# publishes /construction/ in the sitemap at priority 0.4, so it is a
# visitor-facing surface and belongs inside the sweep.
EXCLUDE_DIRS = ("vendor/", "_mockups/", "dist/", "node_modules/")

# The one intentional, documented exception.
DOCUMENTED_API_PAGE = "docs/api.html"

# Catches `qr-container`, `.receipt-qr`, `qr.svg`, `QR code`, AND camelCase
# reintroductions like `qrCanvas` / `renderQrBadge`.
#
# Written case-SENSITIVELY on purpose. The obvious spelling,
# `re.compile(r"qr(?![a-z0-9])", re.I)`, silently misses every camelCase form:
# under IGNORECASE the class [a-z0-9] also matches uppercase, so the negative
# lookahead rejects `qrCanvas` at the `C`. Spelling the lookahead
# `(?![A-Za-z0-9])` does not fix it either, for the same reason. Matching
# [Qq][Rr] explicitly and keeping the lookahead lowercase-only is what makes
# a following capital letter a hit rather than a miss.
#
# Verified against: qrCanvas, qrImg, renderQrBadge, qr-container, QR code,
# receipt-qr, qr.svg, qrcode_svg, QRCode, .orpho-sample__qr (all match) and
# square, acquire, qualify, requirement, torque, Qatar, sqrt (none match).
QR_TOKEN = re.compile(r"[Qq][Rr](?![a-z0-9])|(?i:qrcode)")


def _visitor_sources():
    for ext in ("*.html", "*.css", "*.js"):
        for p in WEB.rglob(ext):
            rel = p.relative_to(WEB).as_posix()
            if any(rel.startswith(d) or f"/{d}" in rel for d in EXCLUDE_DIRS):
                continue
            if rel == DOCUMENTED_API_PAGE:
                continue
            yield rel, p


class TestNoQrOnVisitorSurface(unittest.TestCase):
    def test_no_qr_markup_or_asset_refs_in_web(self):
        offenders = []
        for rel, p in _visitor_sources():
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if QR_TOKEN.search(line):
                    offenders.append(f"{rel}:{n}: {line.strip()[:90]}")
        self.assertEqual(
            offenders, [],
            "QR reference(s) reintroduced on the visitor surface:\n  "
            + "\n  ".join(offenders))

    def test_scan_actually_reaches_the_files(self):
        """NEGATIVE CONTROL -- an empty result is only meaningful if the
        scan can hit. Grep the same file set for a token that is certainly
        present; zero hits here means the sweep above proved nothing."""
        seen = 0
        hits = 0
        for _rel, p in _visitor_sources():
            seen += 1
            if "orphograph" in p.read_text(encoding="utf-8").lower():
                hits += 1
        self.assertGreater(seen, 20, "the file walk collected almost nothing")
        self.assertGreater(hits, 5, "control token not found -- the scan is not reading files")

    def test_control_pattern_would_fire_on_qr_markup(self):
        """NEGATIVE CONTROL for the PATTERN, not just the walk. The regex
        must match the exact markup that was removed, or a reintroduction
        of it would pass silently."""
        removed_for_real = [
            '<a class="orpho-sample__qr" href="/r/XwTULwlh76PcCst9">',
            '<img src="/qr-receipt.svg?v=2" alt="QR code for the sample receipt">',
            '<div id="qr-container" class="qr-container"></div>',
            '<img src="/r/{{RECEIPT_ID}}/qr.svg">',
            '  .qr-wrap { margin: 18px auto; }',
            '    qr.src = "/api/btc/qr.svg?sats=" + currentSats;',
            'const qrCanvas = document.createElement("canvas");',
            'function renderQrBadge(el) {}',
        ]
        for markup in removed_for_real:
            self.assertRegex(markup, QR_TOKEN,
                             f"pattern would NOT catch a reintroduction of: {markup}")

    def test_pattern_does_not_fire_on_innocent_words(self):
        """The other half of the control. A guard that matches everything
        gets disabled by the next person who trips it."""
        for word in ("square", "acquire", "qualify", "requirement",
                     "torque", "Qatar", "sqrt", "conquer"):
            self.assertNotRegex(word, QR_TOKEN,
                                f"false positive on innocent word: {word}")

    def test_deleted_static_asset_is_gone(self):
        self.assertFalse((WEB / "qr-receipt.svg").exists(),
                         "web/qr-receipt.svg was deleted with the homepage QRs")

    def test_removed_routes_stay_removed(self):
        app_src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('path.endswith("/qr.svg")', app_src,
                         "/r/<id>/qr.svg was removed with receipt.html's QR")
        self.assertNotIn('"/api/btc/qr.svg"', app_src,
                         "/api/btc/qr.svg was removed with pay/btc.html's QR")

    def test_documented_btc_order_route_survives(self):
        """The kept route is asserted, not assumed. Removing it would be a
        public API break -- if a later cleanup deletes it, that must be a
        decision, not a side effect of a QR sweep."""
        app_src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        self.assertIn('if sub == "qr.svg":', app_src)
        docs = (WEB / DOCUMENTED_API_PAGE).read_text(encoding="utf-8")
        self.assertIn("/api/btc-order/", docs)


if __name__ == "__main__":
    unittest.main()
