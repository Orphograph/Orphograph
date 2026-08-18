#!/usr/bin/env python3
"""test_receipt_qr.py — every receipt gets a WORKING QR of its permalink.

Founder directive 2026-08-10: "add QR codes that work" — camera-scannable
AND clickable. The scannability guarantee chains through the encoder: the
endpoint must emit byte-for-byte what qrcode_svg.make_svg() produces for the
receipt URL, and test_qrcode_svg_decodes.py pins that encoder to matrices
decode-verified with Apple's Vision framework. Equality here + those pins
there = a symbol that scans, provable in CI without a camera.

Driven through the REAL HTTP entry point (wire-path rule): the /r/<id>/qr.svg
route, not the module — a route that mangles Content-Type, truncates the
body, or double-encodes would pass a module-level test and still ship broken.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

_POLLUTED = [m for m in ("app", "engine", "public_config") if m]


def _start_test_server(data_dir: Path):
    os.environ["ORPHO_DATA_DIR"] = str(data_dir)
    os.environ["HOST"] = "127.0.0.1"
    os.environ["PORT"] = "0"
    os.environ["ORPHO_COOKIE_SECURE"] = "0"
    os.environ.pop("SITE_URL", None)   # exercise the canonical default
    for m in _POLLUTED:
        sys.modules.pop(m, None)
    import app
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


SAMPLE_ID = "XwTULwlh76PcCst9"


class TestReceiptQrEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._server, cls._base = _start_test_server(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()
        cls._tmp.cleanup()

    def _get(self, path):
        req = urllib.request.Request(self._base + path)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_qr_matches_the_scan_verified_encoder(self):
        import qrcode_svg
        status, headers, body = self._get(f"/r/{SAMPLE_ID}/qr.svg")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/svg+xml")
        want = qrcode_svg.make_svg(f"https://orphograph.com/r/{SAMPLE_ID}",
                                   label=f"QR code linking to receipt {SAMPLE_ID}")
        self.assertEqual(body.decode(), want,
                         "wire QR differs from the encoder output — the "
                         "scannability guarantee is broken at the route")

    def test_qr_is_immutable_cacheable(self):
        _, headers, _ = self._get(f"/r/{SAMPLE_ID}/qr.svg")
        self.assertIn("immutable", headers.get("Cache-Control", ""),
                      "a receipt URL never changes; its QR must be "
                      "long-cacheable or every receipt view re-renders it")

    def test_invalid_id_is_rejected(self):
        status, _, _ = self._get("/r/%2e%2e%2fetc/qr.svg")
        self.assertIn(status, (400, 404))
        status, _, _ = self._get("/r/" + "A" * 65 + "/qr.svg")
        self.assertEqual(status, 400)

    def test_head_matches_get(self):
        req = urllib.request.Request(
            self._base + f"/r/{SAMPLE_ID}/qr.svg", method="HEAD")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers.get("Content-Type"), "image/svg+xml")


class TestQrIsOnTheTemplates(unittest.TestCase):
    def test_receipt_template_carries_clickable_qr(self):
        html = (ROOT / "web" / "receipt.html").read_text()
        self.assertIn('href="/r/{{RECEIPT_ID}}"', html)
        self.assertIn('src="/r/{{RECEIPT_ID}}/qr.svg"', html,
                      "receipt page lost its scannable QR")

    def test_homepage_sample_qr_is_clickable_and_canonical(self):
        html = (ROOT / "web" / "index.html").read_text()
        self.assertIn('class="orpho-sample__qr" href="/r/%s"' % SAMPLE_ID, html)

    def test_the_hero_plate_carries_no_qr(self):
        """Removed 2026-08-17, and pinned so it does not come back.

        The hero plate is `aria-hidden` — a picture of the product — and
        `.orpho-hero { overflow: hidden }` cut the receipt card off at the
        plate's bottom edge. Measured on master at three viewport widths:
        122px / 101px / 517px of the card were clipped away, which left the
        QR that used to sit there showing about 24px of itself on desktop and
        nothing at all on narrower screens. It could never have been scanned,
        while the markup comment beside it claimed a camera pointed at the
        hero would land on the live receipt.

        Fitting a 160px symbol there would mean growing the plate to ~860px
        desktop and ~1124px mobile — a hero redesign smuggled in to house a
        decorative QR. The scannable symbols live in the sample-receipt
        section and on the receipt page, where they are measured by
        tests/test_qr_scannable.py.
        """
        html = (ROOT / "web" / "index.html").read_text()
        self.assertNotIn(
            'class="orpho-receipt__qr"', html,
            "a QR is back inside the aria-hidden hero plate, which clips it "
            "— see this test's docstring for the measurements")

    def test_homepage_wax_stamp_is_gone(self):
        html = (ROOT / "web" / "index.html").read_text()
        self.assertNotIn("orpho-hero__wax", html,
                         "the red wax stamp was removed by founder "
                         "directive 2026-08-10 — do not reintroduce it")


if __name__ == "__main__":
    unittest.main()
