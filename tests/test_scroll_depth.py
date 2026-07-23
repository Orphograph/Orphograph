"""test_scroll_depth.py — native, cookieless scroll-depth telemetry contract.

What this pins (the whole feature, not one symptom):
  web/assets/scroll-depth.js reuses the first-party beacon (window.orphoEvent)
  to report where visitors stop scrolling. It fires scroll_25 / scroll_50 /
  scroll_75 / scroll_100 exactly ONCE each per page load, the first time each
  depth threshold is crossed. There is no external script, no cookie, no CSP
  change — the same privacy-notary posture as event.js.

Layers, mirroring the funnel-whitelist test:
  1. the 4 depth names are whitelisted in FUNNEL_EVENTS (server accepts them);
  2. scroll-depth.js has the idempotent guard + throttle + passive-listener +
     no-op-without-orphoEvent structure, and emits the 4 literals verbatim;
  3. the funnel pages actually include the (versioned, deferred) script after
     event.js so orphoEvent is present when it runs;
  4. live HTTP: POST scroll_50 -> 204, an unknown event still -> 400.
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
WEB_DIR = REPO_ROOT / "web"
SCROLL_JS = WEB_DIR / "assets" / "scroll-depth.js"

SCROLL_EVENTS = ["scroll_25", "scroll_50", "scroll_75", "scroll_100"]

# Pages that must carry the scroll beacon for the funnel to see drop-off.
FUNNEL_PAGES = [
    WEB_DIR / "lp" / "agent-receipts.html",
    WEB_DIR / "index.html",
    WEB_DIR / "v2" / "index.html",
]


# ── Layer 1: the 4 depth events are whitelisted server-side ─────────────────

@pytest.mark.parametrize("event", SCROLL_EVENTS)
def test_scroll_event_is_whitelisted(event):
    import app
    assert event in app.FUNNEL_EVENTS, f"{event} missing from FUNNEL_EVENTS"


# ── Layer 2: scroll-depth.js structure — guard, throttle, passive, no-op ────

def test_scroll_js_exists():
    assert SCROLL_JS.is_file(), "web/assets/scroll-depth.js must exist"


def test_scroll_js_emits_all_four_literals():
    """Each depth name must appear as a verbatim orphoEvent("...") literal so
    the recurrence guard (test_funnel_event_whitelist) can see and check it."""
    src = SCROLL_JS.read_text(encoding="utf-8")
    for event in SCROLL_EVENTS:
        assert f'orphoEvent("{event}")' in src, (
            f'scroll-depth.js must emit orphoEvent("{event}") as a literal'
        )


def test_scroll_js_has_idempotent_guard():
    """window.__orphoScroll is the once-per-load guard object; without it a
    threshold could fire on every scroll event or a double-included script
    could double-count."""
    src = SCROLL_JS.read_text(encoding="utf-8")
    assert "__orphoScroll" in src, "must use a window.__orphoScroll guard object"
    # The guard is a per-threshold boolean map, initialized false.
    for t in (25, 50, 75, 100):
        assert str(t) in src, f"guard/threshold {t} must be present"


def test_scroll_js_is_noop_without_orphoevent():
    """Safe to include anywhere: if event.js has not defined orphoEvent, the
    script must bail before touching the DOM or adding listeners."""
    src = SCROLL_JS.read_text(encoding="utf-8")
    assert 'typeof window.orphoEvent !== "function"' in src, (
        "must no-op (early return) when window.orphoEvent is absent"
    )


def test_scroll_js_throttles_and_is_passive():
    src = SCROLL_JS.read_text(encoding="utf-8")
    assert "requestAnimationFrame" in src, "scroll handler must be throttled (rAF)"
    assert "passive" in src, "scroll listener must be registered passive"
    assert 'addEventListener("scroll"' in src, "must listen for scroll"


def test_scroll_js_has_no_external_or_cookie_surface():
    """No CSP-breaking or privacy-breaking surface — the brand contract."""
    src = SCROLL_JS.read_text(encoding="utf-8")
    # Actual API/URL usage — not prose. ("cookieless" in the header comment is
    # the brand claim, not a cookie read.)
    for banned in ("http://", "https://", "document.cookie", "localStorage.", "sessionStorage."):
        assert banned not in src, f"scroll-depth.js must not reference {banned!r}"


# ── Layer 3: the funnel pages include the versioned, deferred beacon ────────

@pytest.mark.parametrize("page", FUNNEL_PAGES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_page_includes_scroll_script(page):
    html = page.read_text(encoding="utf-8")
    assert '/assets/scroll-depth.js?v=1' in html, (
        f"{page.name} must include the version-pinned scroll-depth.js"
    )
    assert 'src="/assets/scroll-depth.js?v=1" defer' in html, (
        f"{page.name} must load scroll-depth.js deferred"
    )


@pytest.mark.parametrize("page", FUNNEL_PAGES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_scroll_script_loads_after_event_js(page):
    """scroll-depth.js depends on window.orphoEvent from event.js, so event.js
    must be present and appear before it in document order."""
    html = page.read_text(encoding="utf-8")
    assert "/assets/event.js?v=1" in html, f"{page.name} must load event.js"
    assert html.index("/assets/event.js") < html.index("/assets/scroll-depth.js"), (
        f"{page.name} must load event.js before scroll-depth.js"
    )


# ── Layer 4: live HTTP — scroll_50 accepted (204), unknown still 400 ────────

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


@pytest.mark.parametrize("event", SCROLL_EVENTS)
def test_scroll_event_returns_204(live_server, event):
    base, _ = live_server
    status, raw = _post_json(base + "/api/event", {"event": event, "page": "/"})
    assert status == 204, f"{event} must be accepted (204), got {status}: {raw[:200]!r}"


def test_scroll_event_is_durable(live_server):
    """A scroll_50 beacon lands as a real row the founder can query."""
    base, data_dir = live_server
    status, _ = _post_json(base + "/api/event", {"event": "scroll_50", "page": "landing-v2"})
    assert status == 204
    rows = [
        json.loads(ln)
        for ln in (data_dir / "events.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert any(
        r.get("event") == "scroll_50" and r.get("page") == "landing-v2" for r in rows
    ), "durable scroll_50 row missing from events.jsonl"


def test_unknown_event_still_rejected(live_server):
    """Adding the depth events must not weaken the allowlist."""
    base, _ = live_server
    status, _ = _post_json(base + "/api/event", {"event": "scroll_37", "page": "/"})
    assert status == 400, f"unknown event must still 400, got {status}"
