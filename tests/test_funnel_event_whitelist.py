"""test_funnel_event_whitelist.py — the client/server funnel contract.

Root cause this pins (the whole bug class, not one symptom):
  web/**/*.js emits funnel beacons via track()/orphoEvent() with a fixed
  vocabulary of event names. The server's /api/event handler rejects any name
  NOT in app.FUNNEL_EVENTS with 400. When a client name drifts out of that
  allowlist, every beacon for it is silently 400-dropped and the funnel loses
  its durable record — which is exactly what happened to the homepage funnel
  (anchor_start, anchor_done, buy_pack_click, billing_toggle, checkout_error,
  … were all firing and all being dropped).

The guard test below greps the client JS for every static event-name literal
and asserts each is whitelisted. If someone adds a new track()/orphoEvent()
call without whitelisting the name, this test fails BEFORE it ships — the whole
class of "silently-dropped funnel event" bugs cannot recur.

Also pinned here:
  - the dark /v2 homepage fires a durable, distinctly-labelled page_view;
  - every newly-whitelisted event now returns 204 end-to-end;
  - unknown events still 400 (the allowlist was not weakened).
"""
from __future__ import annotations

import json
import os
import re
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
V2_JS = WEB_DIR / "v2.js"

# Matches both `track("name"` and `window.orphoEvent("name"` (with or without
# a trailing second argument). Event names are lower_snake_case string literals
# and may include digits (e.g. scroll_25 / scroll_50 / scroll_75 / scroll_100)
# so the depth-telemetry beacons are covered by the recurrence guard too.
_EVENT_CALL_RE = re.compile(r'(?:orphoEvent|track)\(\s*"([a-z0-9_]+)"')


def _iter_client_js() -> list[Path]:
    """Every shipped client JS file that could emit a funnel beacon."""
    return sorted(p for p in WEB_DIR.rglob("*.js") if "vendor" not in p.parts)


def _client_emitted_events() -> dict[str, list[str]]:
    """name -> [relative file paths that emit it], scanned from client JS."""
    found: dict[str, list[str]] = {}
    for path in _iter_client_js():
        src = path.read_text(encoding="utf-8")
        for name in _EVENT_CALL_RE.findall(src):
            found.setdefault(name, []).append(str(path.relative_to(REPO_ROOT)))
    return found


# ── Layer 1: the recurrence guard — client names ⊆ server allowlist ─────────

def test_every_client_emitted_event_is_whitelisted():
    """THE guard: no track()/orphoEvent() name may fall out of FUNNEL_EVENTS.

    This is the test that prevents the entire 400-drop bug class from
    returning. Add a client beacon, whitelist its name here, or this fails.
    """
    import app

    emitted = _client_emitted_events()
    assert emitted, "no client event literals found — the grep regex likely broke"

    missing = {
        name: files for name, files in emitted.items()
        if name not in app.FUNNEL_EVENTS
    }
    assert not missing, (
        "client JS emits funnel events that the server will 400-drop; add them "
        "to FUNNEL_EVENTS in server/app.py: "
        + ", ".join(f"{n} (from {', '.join(f)})" for n, f in sorted(missing.items()))
    )


def test_inventory_matches_expected_set():
    """Belt-and-suspenders: pin the exact client vocabulary so a silent
    addition/removal is visible in review, not just caught in aggregate."""
    expected = {
        "page_view", "drop_zone_visible",
        "anchor_start", "anchor_done", "file_anchored",
        "buy_pack_click", "buy_personal_click", "billing_toggle",
        "pack_waitlist_join", "checkout_clicked", "checkout_error",
        "checkout_returned_success",
        "try_sample_click", "verify_sample_click", "share_link_click",
        "lp_cta_clicked",
        "scroll_25", "scroll_50", "scroll_75", "scroll_100",
    }
    assert set(_client_emitted_events()) == expected


# ── Layer 2: the dark /v2 homepage fires a durable, distinct page_view ──────

def test_v2_fires_distinct_page_view_on_load():
    src = V2_JS.read_text(encoding="utf-8")
    assert '"page_view"' in src and '"landing-v2"' in src, (
        "v2.js must fire a page_view labelled page='landing-v2' on load so dark "
        "homepage visits are countable and distinct from the cream homepage"
    )
    # Idempotent so a double-loaded <script> can't double-count a visit.
    assert "__orphoV2PageView" in src, "v2 page_view fire must be idempotent-guarded"


def test_v2_page_view_label_is_whitelisted_and_fits():
    import app
    assert "page_view" in app.FUNNEL_EVENTS
    assert app.MAX_EVENT_PAGE_LEN >= len("landing-v2")


# ── Layer 3: live HTTP contract — new events 204, unknown still 400 ─────────

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


# The events that were 400-dropped before this fix — each must now 204.
_NEWLY_WHITELISTED = [
    "anchor_start", "anchor_done", "buy_pack_click", "buy_personal_click",
    "billing_toggle", "pack_waitlist_join", "checkout_error",
    "try_sample_click", "verify_sample_click", "share_link_click",
]


@pytest.mark.parametrize("event", _NEWLY_WHITELISTED)
def test_newly_whitelisted_event_returns_204(live_server, event):
    base, _ = live_server
    status, raw = _post_json(base + "/api/event", {"event": event, "page": "landing"})
    assert status == 204, f"{event} must be accepted (204), got {status}: {raw[:200]!r}"


def test_v2_page_view_landing_v2_is_durable(live_server):
    base, data_dir = live_server
    status, raw = _post_json(
        base + "/api/event", {"event": "page_view", "page": "landing-v2"}
    )
    assert status == 204, f"landing-v2 page_view must 204, got {status}: {raw[:200]!r}"
    events_path = data_dir / "events.jsonl"
    rows = [json.loads(ln) for ln in events_path.read_text().splitlines() if ln.strip()]
    assert any(
        r.get("event") == "page_view" and r.get("page") == "landing-v2" for r in rows
    ), "durable landing-v2 page_view row missing from events.jsonl"


def test_unknown_event_still_rejected(live_server):
    """Widening the allowlist must not weaken it."""
    base, _ = live_server
    status, _ = _post_json(base + "/api/event", {"event": "not_a_real_event", "page": "/"})
    assert status == 400, f"unknown event must still 400, got {status}"


def test_extra_field_still_rejected(live_server):
    """The strict {event, page} shape must still reject smuggled fields."""
    base, _ = live_server
    status, _ = _post_json(
        base + "/api/event",
        {"event": "page_view", "page": "/", "ua": "sneaky"},
    )
    assert status == 400, f"extra field must still 400, got {status}"
