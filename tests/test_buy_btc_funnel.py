"""test_buy_btc_funnel.py — the homepage frictionless-BTC purchase path.

Covers the conversion wiring added 2026-06-17: after a free-tier 429, the v2
homepage offers a one-email-field Writer Pack order that POSTs /api/buy-btc and
redirects to the wallet-ready /buy/<id> page. If the BTC rail isn't configured
server-side the endpoint returns 503 and the JS degrades to the manual
/pay/crypto.html flow — so the offer is never a dead end.

Two layers, both browserless:
  1. Static contract on web/v2.js (the JS calls the endpoint and keeps the
     manual fallback URL).
  2. HTTP contract on /api/buy-btc with the BTC rail unconfigured → 503, which
     is exactly the branch the JS fallback depends on.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
V2_JS = REPO_ROOT / "web" / "v2.js"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ── Layer 1: static contract on the homepage JS ────────────────────────────

def test_v2_js_wires_buy_btc_endpoint():
    src = V2_JS.read_text(encoding="utf-8")
    assert "/api/buy-btc" in src, "homepage must call the frictionless BTC order endpoint"
    assert "offerWriterPack" in src, "429 path must invoke the Writer-Pack offer"
    # The redirect target comes straight from the order response.
    assert "buy_page" in src, "must redirect to the server-provided /buy/<id> page"


def test_v2_js_keeps_manual_crypto_fallback():
    src = V2_JS.read_text(encoding="utf-8")
    # The manual page is the documented degrade path on a 503 (BTC not
    # configured) and a click-through safety net on fetch errors.
    assert "/pay/crypto.html?plan=writer_pack" in src
    assert "503" in src, "JS must branch on the not-configured status to degrade"


# ── Layer 2: HTTP contract — unconfigured BTC rail returns 503 ─────────────

@pytest.fixture(scope="module")
def live_server_no_btc(tmp_path_factory):
    """Server with the BTC rail explicitly disabled — exercises the 503 path."""
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("data")
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "RATE_LIMIT_PER_DAY": "100000",
    }
    # Strip any BTC HD-wallet config so btc_payments.is_configured() is False.
    for k in ("BTC_XPUB", "ORPHO_BTC_XPUB", "BTC_PAYMENTS_ENABLED"):
        env.pop(k, None)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start in 10s")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _post_json(url: str, body: dict):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_buy_btc_degrades_with_503_when_unconfigured(live_server_no_btc):
    status, raw = _post_json(
        live_server_no_btc + "/api/buy-btc", {"email": "writer@example.com"}
    )
    assert status == 503, f"expected 503 when BTC rail unconfigured, got {status}: {raw[:200]!r}"
    payload = json.loads(raw)
    assert "error" in payload
