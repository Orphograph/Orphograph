#!/usr/bin/env python3
"""qr_geometry.py — measure what the BROWSER draws, not what the CSS says.

Why this exists (2026-08-17). The first attempt at the QR-scannability fix
set `width: 160px` and shipped a passing test suite. The browser drew 146px.
`* { box-sizing: border-box }` in style.css plus the wrapper's 6px padding
and 1px border ate 14px of symbol, and every test in the suite grepped the
stylesheet — so all four passed while the defect was still live. Production
was worse: 74px, which is why a real camera failed on it.

A source-text assertion cannot see that class of bug. This renders the real
page in a real engine and reads each QR's bounding box out of the DOM in CSS
pixels.

The measured page is assembled in memory and served from a virtual
`/__measure/<page>` path — nothing is ever written into web/, because the
site's CSP forbids inline script and a stray injected page in the web root
would be a worse defect than the one being fixed.
"""
from __future__ import annotations

import http.server
import json
import os
import re
import socketserver
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

BRAVE = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
CANONICAL_RECEIPT = "XwTULwlh76PcCst9"   # feedback_orpho_canonical_sample_receipt

# Placeholders in the server-rendered templates, filled with values that are
# shaped like the real ones so the layout matches what a visitor gets.
TEMPLATE_FILL = {
    "{{RECEIPT_ID}}": CANONICAL_RECEIPT,
    "{{HASH_HEX}}": "2f" * 32,
    "{{CREATED_AT}}": "2026-05-20 14:37:22 UTC",
    "{{FILENAME}}": "whitepaper.pdf",
    "{{SIZE}}": "1.42 MB",
}

_INJECT = """
<div id="__geo"></div>
<script>
window.addEventListener('load', function () { setTimeout(function () {
  function b(e) { var r = e.getBoundingClientRect(); return {
    x: Math.round(r.x), y: Math.round(r.y + scrollY),
    w: Math.round(r.width), h: Math.round(r.height),
    bottom: Math.round(r.bottom + scrollY) }; }
  // The SYMBOL, not the box. getBoundingClientRect() includes padding and
  // border, so it happily reports 160 for a box whose QR is 146 — the very
  // bug being fixed. clientWidth excludes the border; subtract padding and
  // what is left is the modules.
  function sym(e) {
    var st = getComputedStyle(e);
    var px = parseFloat(st.paddingLeft) + parseFloat(st.paddingRight);
    var py = parseFloat(st.paddingTop) + parseFloat(st.paddingBottom);
    return { w: Math.round(e.clientWidth - px), h: Math.round(e.clientHeight - py) };
  }
  // receipt.js hides every .needs-record section when its API fetch fails,
  // which it always does in this offline harness — the QR lives inside one.
  // Reveal them first, or the measurement reports 0x0 and calls the page
  // broken when only the harness is.
  // receipt.js hides #card and every .needs-record section when its API
  // fetch fails, which it always does in this offline harness — the QR lives
  // inside both. Walk up from each QR and clear `hidden` on its ancestors,
  // or the measurement reports 0x0 and calls the page broken when only the
  // harness is.
  document.querySelectorAll('img').forEach(function (e) {
    if (!/qr/i.test(e.getAttribute('src') || '')) return;
    var p = e;
    while (p) { p.removeAttribute && p.removeAttribute('hidden'); p = p.parentElement; }
  });
  var o = { dpr: window.devicePixelRatio, vw: innerWidth, qrs: [] };
  document.querySelectorAll('img').forEach(function (e) {
    if (!/qr/i.test(e.getAttribute('src') || '')) return;
    var g = b(e);
    var sm = sym(e);
    g.sym_w = sm.w; g.sym_h = sm.h;
    g.sel = (e.closest('a') || {}).className || '';
    // clipped: any ancestor with overflow:hidden that cuts the box off
    var cut = null, p = e.parentElement;
    while (p) {
      var st = getComputedStyle(p);
      if (st.overflow === 'hidden' || st.overflowY === 'hidden') {
        var pr = b(p);
        if (g.bottom > pr.bottom + 1 || g.y < pr.y - 1) {
          cut = { by: p.className || p.tagName,
                  hidden_px: Math.round(g.bottom - pr.bottom) };
          break;
        }
      }
      p = p.parentElement;
    }
    g.clipped = cut;
    o.qrs.push(g);
  });
  document.getElementById('__geo').textContent = 'GEO' + JSON.stringify(o);
}, 900); });
</script>
"""


def browser_available() -> bool:
    return Path(BRAVE).exists()


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(WEB), **k)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/__measure/"):
            name = self.path.split("/__measure/", 1)[1].split("?")[0]
            src = WEB / name
            if not src.is_file():
                self.send_error(404)
                return
            html = src.read_text()
            for k, v in TEMPLATE_FILL.items():
                html = html.replace(k, v)
            # the receipt template points its QR at a server-rendered route;
            # the static symbol is the same generator's output.
            html = html.replace(f"/r/{CANONICAL_RECEIPT}/qr.svg",
                                "/qr-receipt.svg")
            html = html.replace("</body>", _INJECT + "</body>")
            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()


class _Server:
    def __init__(self, port: int = 0):
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", port), _Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def measure(page: str, width: int = 1440, height: int = 1000,
            timeout: int = 180) -> dict:
    """Render web/<page> and return the DOM-reported geometry of every QR.

    DPR is deliberately 1: this measures CSS pixels, which is the unit the
    threshold is expressed in. Raster/decode passes vary DPR separately.

    NOTE: headless Chromium/Brave clamps the viewport at ~500px wide on this
    machine (feedback_headless_chromium_500px_min) — ask for 390 and you get
    500, and the returned `vw` says so. Narrower-than-500 layout cannot be
    measured this way; do not claim it was.
    """
    if not browser_available():
        raise RuntimeError(f"no headless browser at {BRAVE}")
    srv = _Server()
    try:
        proc = subprocess.run(
            [BRAVE, "--headless", "--disable-gpu", "--no-sandbox",
             f"--window-size={width},{height}",
             "--force-device-scale-factor=1",
             "--virtual-time-budget=6000", "--dump-dom",
             f"http://127.0.0.1:{srv.port}/__measure/{page}"],
            capture_output=True, text=True, timeout=timeout)
        m = re.search(r"GEO(\{.*?\})</div>", proc.stdout, re.S)
        if not m:
            raise RuntimeError(
                f"{page}: the measuring script did not report — the render "
                f"failed or the page has no </body>. This is UNAVAILABLE, "
                f"not a pass.")
        return json.loads(m.group(1))
    finally:
        srv.close()


if __name__ == "__main__":
    import sys
    pages = sys.argv[1:] or ["index.html", "receipt.html", "certificate.html"]
    for pg in pages:
        for w in (1440, 900, 500):
            try:
                g = measure(pg, width=w)
            except Exception as e:                       # noqa: BLE001
                print(f"{pg} @{w}: UNAVAILABLE — {e}")
                continue
            print(f"{pg} @{w} (vw {g['vw']}): "
                  + (", ".join(f"symbol {q['sym_w']}x{q['sym_h']}px (box {q['w']}x{q['h']})"
                               + (f" CLIPPED {q['clipped']}" if q['clipped'] else "")
                               for q in g["qrs"]) or "no QR on this page"))
