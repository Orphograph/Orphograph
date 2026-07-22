"""test_lp_pageview_beacon.py — durable, LP-attributable page_view tracking.

Root cause this pins: `page_view` had fallen out of the server's FUNNEL_EVENTS
whitelist, so every page_view beacon POST returned 400 and nothing was written
to data/events.jsonl. The 13 page_view rows in production were stale historical
data from when it was whitelisted. This left the /lp/agent-receipts demand gate
("≥200 unique LP visits") with no durable signal — visits lived only in Fly's
ephemeral logs.

Two-sided fix pinned here:
  1. SERVER: page_view is back in FUNNEL_EVENTS → POST page_view returns 204 and
     lands a row in events.jsonl. The whitelist still rejects unknown events.
  2. CLIENT: web/assets/lp-cta.js (already loaded on the LP) fires exactly one
     window.orphoEvent('page_view') per load. event.js sends page=
     location.pathname, so an LP page_view self-attributes to
     /lp/agent-receipts — distinct from the homepage's own tracker.
  3. The LP references the cache-busted lp-cta.js?v=2.
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
LP_CTA_JS = REPO_ROOT / "web" / "assets" / "lp-cta.js"
EVENT_JS = REPO_ROOT / "web" / "assets" / "event.js"
LP_HTML = REPO_ROOT / "web" / "lp" / "agent-receipts.html"


# ── Layer 1: server whitelist contract ─────────────────────────────────────

def test_page_view_in_funnel_events():
    """page_view must be whitelisted or every beacon 400s (the root cause)."""
    import app
    assert "page_view" in app.FUNNEL_EVENTS, (
        "page_view fell out of FUNNEL_EVENTS — page_view beacons will 400 and "
        "the LP demand gate loses its durable visit signal"
    )
    # The value the LP will carry stays well under the length cap.
    assert app.MAX_EVENT_PAGE_LEN >= len("/lp/agent-receipts")


# ── Layer 2: client contract — lp-cta.js fires a guarded page_view ─────────

def test_lp_cta_js_fires_page_view_once():
    src = LP_CTA_JS.read_text(encoding="utf-8")
    assert 'orphoEvent("page_view")' in src, (
        "lp-cta.js must fire a page_view beacon on load"
    )
    # Idempotent guard so a double-loaded script can't double-count a visit.
    assert "__orphoLpPageView" in src, "page_view fire must be idempotent-guarded"
    # The existing CTA-click beacon must still be present.
    assert 'orphoEvent("lp_cta_clicked")' in src


def test_event_js_attributes_by_pathname():
    """LP attribution relies on event.js sending page=location.pathname."""
    src = EVENT_JS.read_text(encoding="utf-8")
    assert "location.pathname" in src, (
        "event.js must send location.pathname so an LP page_view carries "
        "page='/lp/agent-receipts' — distinct from the homepage"
    )


def test_lp_references_bumped_lp_cta_version():
    html = LP_HTML.read_text(encoding="utf-8")
    assert "/assets/lp-cta.js?v=2" in html, "LP must load the cache-busted lp-cta.js?v=2"
    assert "/assets/lp-cta.js?v=1" not in html, "stale ?v=1 pin must be gone"
    # event.js still loaded (defines window.orphoEvent that lp-cta.js depends on).
    assert "/assets/event.js" in html


# ── Layer 3: live HTTP contract — page_view now 204s and is durable ────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("data")
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "RATE_LIMIT_PER_DAY": "100000",
    }
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
    yield base, Path(data_dir)
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
    except OSError as e:
        pytest.skip(f"network unreachable in this env: {e!r}")


def test_lp_page_view_returns_204_and_is_durable(live_server):
    base, data_dir = live_server
    status, raw = _post_json(
        base + "/api/event", {"event": "page_view", "page": "/lp/agent-receipts"}
    )
    assert status == 204, f"page_view must be accepted (204), got {status}: {raw[:200]!r}"
    # Durable: a row lands in events.jsonl carrying the LP path — the exact
    # signal the founder tallies instead of ephemeral Fly logs.
    events_path = data_dir / "events.jsonl"
    assert events_path.exists(), "events.jsonl must be written on a successful beacon"
    rows = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    lp_views = [
        r for r in rows
        if r.get("event") == "page_view" and r.get("page") == "/lp/agent-receipts"
    ]
    assert lp_views, "durable LP-attributable page_view row missing from events.jsonl"


def test_unknown_event_still_rejected(live_server):
    """Re-enabling page_view must not weaken the whitelist."""
    base, _ = live_server
    status, _ = _post_json(base + "/api/event", {"event": "not_a_real_event", "page": "/"})
    assert status == 400, f"unknown event must still 400, got {status}"
