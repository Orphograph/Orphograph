"""test_event_ip_source.py — real-visitor IP resolution for funnel analytics.

Regression cover for the 2026-07-25 defect: the funnel event log recorded the
address of whatever connected to the origin socket. The site sits behind
Cloudflare, so that address was always a Cloudflare egress node — and
Cloudflare rotates egress per request, so a single visitor's successive events
were logged under several different /24s and the distinct-IP count overstated
unique visitors.

These tests pin:
  1. header precedence — CF-Connecting-IP → first X-Forwarded-For → socket;
  2. the ip_src provenance label emitted alongside every address;
  3. that the trust-proxy gate still disables header trust entirely;
  4. that truncation is unchanged (/24 for IPv4, /48 for IPv6) — this is a
     correctness fix, NOT a data-collection expansion.
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

import app
from rate_limit import truncate_ip

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------

def test_cf_connecting_ip_wins_over_xff_and_socket():
    ip, src = app._resolve_analytics_ip(
        "203.0.113.7",                  # CF-Connecting-IP: the real visitor
        "198.51.100.9, 172.68.1.1",     # XFF
        "162.158.5.4",                  # socket peer = Cloudflare egress
        True,
    )
    assert (ip, src) == ("203.0.113.7", "cf")


def test_falls_back_to_first_xff_entry_when_cf_header_absent():
    """LEFTMOST entry: the first proxy in the chain records the original
    client there. Deliberately the opposite of `_resolve_peer_ip`, which
    takes the rightmost entry because it buckets rate limits."""
    ip, src = app._resolve_analytics_ip(
        "",
        "198.51.100.9, 172.68.1.1, 10.0.0.1",
        "162.158.5.4",
        True,
    )
    assert (ip, src) == ("198.51.100.9", "xff")


def test_falls_back_to_socket_peer_when_no_headers():
    ip, src = app._resolve_analytics_ip("", "", "203.0.113.44", True)
    assert (ip, src) == ("203.0.113.44", "socket")


def test_blank_and_whitespace_headers_are_ignored():
    ip, src = app._resolve_analytics_ip("   ", " , ,  ", "203.0.113.44", True)
    assert (ip, src) == ("203.0.113.44", "socket")


def test_header_values_are_stripped():
    ip, src = app._resolve_analytics_ip("  203.0.113.7  ", "", "10.0.0.1", True)
    assert (ip, src) == ("203.0.113.7", "cf")

    ip, src = app._resolve_analytics_ip("", "  198.51.100.9 , 10.0.0.1", "10.0.0.2", True)
    assert (ip, src) == ("198.51.100.9", "xff")


# --------------------------------------------------------------------------
# Trust gate — client-supplied headers must be ignored with no proxy in front
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cf,xff", [
    ("203.0.113.7", ""),
    ("", "198.51.100.9"),
    ("203.0.113.7", "198.51.100.9"),
])
def test_untrusted_proxy_always_uses_socket(cf, xff):
    ip, src = app._resolve_analytics_ip(cf, xff, "192.0.2.55", False)
    assert (ip, src) == ("192.0.2.55", "socket")


# --------------------------------------------------------------------------
# Truncation — privacy posture is unchanged
# --------------------------------------------------------------------------

def test_resolver_never_returns_a_truncated_value():
    """Truncation is the caller's job; keeping the resolver pure means the
    /24 boundary lives in exactly one place (`truncate_ip`)."""
    ip, _ = app._resolve_analytics_ip("203.0.113.7", "", "", True)
    assert "/" not in ip


def test_resolved_ipv4_truncates_to_slash_24():
    ip, src = app._resolve_analytics_ip("203.0.113.7", "", "162.158.5.4", True)
    assert truncate_ip(ip) == "203.0.113.0/24"
    assert src == "cf"


def test_resolved_ipv6_truncates_to_slash_48():
    """Cloudflare hands back IPv6 for a large share of real visitors — a
    hand-rolled dotted-quad truncation would silently mangle those."""
    ip, src = app._resolve_analytics_ip("2606:4700:3031:abcd:1:2:3:4", "", "10.0.0.1", True)
    assert truncate_ip(ip) == "2606:4700:3031::/48"
    assert src == "cf"


def test_compressed_ipv6_yields_a_stable_if_ugly_slash_48_label():
    """Documents existing `truncate_ip` behaviour, newly exercised by this fix.

    Cloudflare sends RFC 5952 compressed IPv6. `truncate_ip` is string-based,
    so a compressed run can leave an empty group and produce a malformed-
    looking label ("2001:db8:::/48"). This is COSMETIC, not count-affecting:
    the mapping is deterministic, so one address always lands in one bucket,
    and two addresses only collide when their third group is genuinely zero
    in both — i.e. when they really do share a /48.

    Deliberately NOT fixed here: `truncate_ip` is the shared rate-limit helper
    and is pinned by tests/test_security_hardening.py. Changing it belongs in
    its own PR, outside the analytics path.
    """
    # Realistic Cloudflare visitor addresses truncate cleanly.
    ip, _ = app._resolve_analytics_ip("2606:4700:3031::ac43:cfd5", "", "", True)
    assert truncate_ip(ip) == "2606:4700:3031::/48"

    # Degenerate short form: ugly label, but stable and correctly grouped.
    assert truncate_ip("2001:db8::1") == "2001:db8:::/48"
    assert truncate_ip("2001:db8::2") == truncate_ip("2001:db8::1")   # same /48, truly
    assert truncate_ip("2001:db8:1::1") != truncate_ip("2001:db8::1")  # different /48

    # Whatever the label looks like, no full address survives.
    assert "db8::1" not in truncate_ip("2001:db8::1").replace("2001:db8:::/48", "")


def test_truncated_output_never_contains_the_host_octet():
    for host in ("1", "7", "254"):
        ip, _ = app._resolve_analytics_ip(f"203.0.113.{host}", "", "", True)
        assert truncate_ip(ip) == "203.0.113.0/24"


# --------------------------------------------------------------------------
# The defect itself, stated as a test
# --------------------------------------------------------------------------

def test_one_visitor_behind_rotating_cloudflare_egress_yields_one_bucket():
    """The regression: four events from ONE visitor arrive over four
    different Cloudflare egress nodes. Pre-fix these produced four distinct
    /24s; the visitor's own address is constant, so the fix must collapse
    them to one."""
    visitor = "203.0.113.7"
    rotating_cf_egress = ["162.158.5.4", "172.68.9.10", "104.23.1.2", "162.159.44.7"]
    buckets = {
        truncate_ip(app._resolve_analytics_ip(visitor, "", egress, True)[0])
        for egress in rotating_cf_egress
    }
    assert buckets == {"203.0.113.0/24"}

    # Sanity: the pre-fix behaviour really did scatter — this is what the
    # 59-distinct-IP reading was counting.
    pre_fix = {truncate_ip(e) for e in rotating_cf_egress}
    assert len(pre_fix) == 4


# --------------------------------------------------------------------------
# Rate limiting must NOT change: it still buckets on the least-forgeable value
# --------------------------------------------------------------------------

def test_rate_limit_resolver_is_untouched_and_still_prefers_rightmost_xff():
    """Guard rail. If someone later 'unifies' these two resolvers, the
    limiter would start trusting a client-rotatable leftmost XFF token and
    become trivially bypassable."""
    assert app._resolve_peer_ip("", "198.51.100.9, 172.68.1.1", "10.0.0.1", True) == "172.68.1.1"
    assert app._resolve_peer_ip("172.68.1.1", "198.51.100.9", "10.0.0.1", True) == "172.68.1.1"


def test_the_two_resolvers_disagree_by_design():
    xff = "198.51.100.9, 172.68.1.1"
    analytics_ip, _ = app._resolve_analytics_ip("", xff, "10.0.0.1", True)
    limiter_ip = app._resolve_peer_ip("", xff, "10.0.0.1", True)
    assert analytics_ip != limiter_ip


# --------------------------------------------------------------------------
# End-to-end: the wiring, not just the resolver
# --------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Server with the production proxy posture (ORPHO_TRUST_PROXY_HEADERS=1)."""
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("data")
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_TRUST_PROXY_HEADERS": "1",
        "RATE_LIMIT_PER_DAY": "100000",
    }
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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


def _post_event(base: str, page: str, headers: dict):
    body = json.dumps({"event": "page_view", "page": page}).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/event", data=body, method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except OSError as e:
        pytest.skip(f"network unreachable in this env: {e!r}")


def _rows_for(data_dir: Path, page: str) -> list[dict]:
    path = data_dir / "events.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("page") == page:
            out.append(rec)
    return out


@pytest.mark.parametrize("tag,spelling", [
    ("canonical", "CF-Connecting-IP"),   # canonical
    ("lower", "cf-connecting-ip"),       # HTTP/2 lowercases header names on the wire
    ("mixed", "Cf-Connecting-Ip"),
])
def test_logged_row_uses_cf_header_and_labels_the_source(live_server, tag, spelling):
    """Header lookup must be case-insensitive.

    If it were not, the fix would be a silent no-op in production — Cloudflare
    talks HTTP/2 to the origin, which lowercases header names. The failure
    would surface as ip_src="socket" on every row, i.e. the defect unchanged.
    """
    base, data_dir = live_server
    page = f"/e2e-cf-{tag}"
    assert _post_event(base, page, {spelling: "203.0.113.7"}) == 204
    rows = _rows_for(data_dir, page)
    assert len(rows) == 1
    assert rows[0]["ip_trunc"] == "203.0.113.0/24"   # NOT 127.0.0.0/24
    assert rows[0]["ip_src"] == "cf"


def test_logged_row_falls_back_to_first_xff_entry(live_server):
    base, data_dir = live_server
    assert _post_event(
        base, "/e2e-xff", {"X-Forwarded-For": "198.51.100.9, 172.68.1.1"}
    ) == 204
    rows = _rows_for(data_dir, "/e2e-xff")
    assert len(rows) == 1
    assert rows[0]["ip_trunc"] == "198.51.100.0/24"
    assert rows[0]["ip_src"] == "xff"


def test_logged_row_falls_back_to_socket_with_no_headers(live_server):
    base, data_dir = live_server
    assert _post_event(base, "/e2e-socket", {}) == 204
    rows = _rows_for(data_dir, "/e2e-socket")
    assert len(rows) == 1
    assert rows[0]["ip_trunc"] == "127.0.0.0/24"
    assert rows[0]["ip_src"] == "socket"


def test_row_shape_is_exactly_the_documented_five_fields(live_server):
    """No PII crept in with the new field, and ip_src is always present on
    post-fix rows so the 2026-08-06 read can partition the series."""
    base, data_dir = live_server
    assert _post_event(base, "/e2e-shape", {"CF-Connecting-IP": "203.0.113.7"}) == 204
    row = _rows_for(data_dir, "/e2e-shape")[0]
    assert set(row) == {"ts", "event", "page", "ip_trunc", "ip_src"}
    assert row["ip_src"] in {"cf", "xff", "socket"}


def test_full_visitor_address_is_never_written(live_server):
    base, data_dir = live_server
    assert _post_event(base, "/e2e-privacy", {"CF-Connecting-IP": "203.0.113.7"}) == 204
    raw = (data_dir / "events.jsonl").read_text(encoding="utf-8")
    assert "203.0.113.7" not in raw, "full visitor IP leaked into the event log"
    assert "203.0.113.0/24" in raw
