"""HEAD must never change state (RFC 9110 §9.2.1: HEAD is a safe method).

`do_HEAD` answers by running the ordinary GET routing and discarding the body.
That is right for status and headers, and it means every GET handler with a
SIDE EFFECT also fired on HEAD. Two routes had one (2026-09-19):

  * `/a/<token>` redeemed the one-time sign-in token and minted a session.
    Mail security gateways and link checkers probe links with HEAD, so the
    token was spent before the person clicked, and their click answered
    "link expired or already used".
  * `/api/unsubscribe?e=` recorded the suppression, so a scanner that only
    looked at the link unsubscribed the recipient.

GET keeps both behaviours; they are deliberate single-click flows. HEAD now
reports what GET WOULD answer, from read-only lookups, and writes nothing.

Drives a real server over HTTP and reads the server's own ledgers afterwards:
the assertion is on recorded state, not on a handler's return value.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("head_safe_data")
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        yield base, data_dir


def _mint_token(data_dir: Path, email: str) -> str:
    """Issue a real one-time link token against the server's own ledger."""
    code = (
        "import os,sys;"
        f"os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});"
        "import auth;"
        f"print(auth.issue_link_token({email!r})[0])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _events_for(data_dir: Path, email: str, ledger: str) -> list[str]:
    return [r.get("event", "") for r in _rows(data_dir / ledger)
            if r.get("email") == email]


# --- /a/<token> -------------------------------------------------------------

def test_head_does_not_spend_the_sign_in_link(server):
    """THE DEFECT: a scanner's HEAD, then the person's click."""
    base, data_dir = server
    email = "scanned-first@example.test"
    token = _mint_token(data_dir, email)

    status, body, headers = _srv.request(base, f"/a/{token}", method="HEAD", timeout=15)
    assert status == 303, "HEAD must report the status GET would answer"
    assert body == b""
    assert not headers.get("Set-Cookie"), "HEAD handed out a session cookie"

    assert _events_for(data_dir, email, "auth_tokens.jsonl") == ["issued"], (
        "HEAD redeemed the one-time token")
    assert _events_for(data_dir, email, "auth_sessions.jsonl") == [], (
        "HEAD minted a session")

    status, _body, headers = _srv.request(base, f"/a/{token}", timeout=15)
    assert status == 303, "the link was dead by the time the person clicked it"
    assert "orpho_sid=" in headers.get("Set-Cookie", "")


def test_head_reports_the_same_redirect_get_would(server):
    base, data_dir = server
    token = _mint_token(data_dir, "same-location@example.test")
    _s, _b, head = _srv.request(base, f"/a/{token}?next=/pricing", method="HEAD", timeout=15)
    _s, _b, get = _srv.request(base, f"/a/{token}?next=/pricing", timeout=15)
    assert head.get("Location") == get.get("Location") == "/pricing"
    assert head.get("Cache-Control") == get.get("Cache-Control") == "no-store"


def test_head_on_a_hostile_next_falls_back_like_get(server):
    base, data_dir = server
    token = _mint_token(data_dir, "hostile-next@example.test")
    _s, _b, head = _srv.request(base, f"/a/{token}?next=/%5Cevil.example",
                                method="HEAD", timeout=15)
    assert head.get("Location") == "/account"


def test_head_on_a_spent_or_unknown_link_answers_like_get(server):
    base, data_dir = server
    token = _mint_token(data_dir, "spent@example.test")
    assert _srv.request(base, f"/a/{token}", timeout=15)[0] == 303
    assert _srv.request(base, f"/a/{token}", method="HEAD", timeout=15)[0] == 404
    assert _srv.request(base, f"/a/{token}", timeout=15)[0] == 404
    unknown = "A" * len(token)
    assert _srv.request(base, f"/a/{unknown}", method="HEAD", timeout=15)[0] == 404


# --- /api/unsubscribe -------------------------------------------------------

def test_head_does_not_unsubscribe_anyone(server):
    base, data_dir = server
    email = "only-looked@example.test"
    path = f"/api/unsubscribe?e={email}"

    status, body, head_before = _srv.request(base, path, method="HEAD", timeout=15)
    assert status == 200 and body == b""
    assert [r for r in _rows(data_dir / "suppressions.jsonl")
            if r.get("email") == email] == [], "HEAD recorded an unsubscribe"

    # The person's own click still works in one action, and HEAD described
    # that response exactly (same length: the "Confirmed." page).
    status, get_body, _h = _srv.request(base, path, timeout=15)
    assert status == 200 and b"Confirmed." in get_body
    assert head_before.get("Content-Length") == str(len(get_body))
    assert [r.get("email") for r in _rows(data_dir / "suppressions.jsonl")] .count(email) == 1

    # Once recorded, HEAD and GET agree on the "already" page too.
    _s, _b, head_after = _srv.request(base, path, method="HEAD", timeout=15)
    _s, get_again, _h = _srv.request(base, path, timeout=15)
    assert b"Already on the suppression list" in get_again
    assert head_after.get("Content-Length") == str(len(get_again))
    assert [r.get("email") for r in _rows(data_dir / "suppressions.jsonl")].count(email) == 1


def test_head_on_a_refused_address_answers_like_get(server):
    base, _data_dir = server
    assert _srv.request(base, "/api/unsubscribe?e=not-an-address", method="HEAD",
                        timeout=15)[0] == 400


# --- the class, not the two instances ---------------------------------------

def _snapshot(data_dir: Path) -> dict[str, tuple[int, str]]:
    """Every file the server keeps, except request ACCOUNTING: the rate
    limiter's counters and the harness's server log record that a request
    arrived, whatever its method, and they should."""
    import hashlib
    import re
    accounting = re.compile(r"^(rate_limit_state\.json|server-\d+\.log)$")
    return {str(p.relative_to(data_dir)): (p.stat().st_size,
                                           hashlib.sha256(p.read_bytes()).hexdigest())
            for p in sorted(data_dir.rglob("*"))
            if p.is_file() and not p.name.endswith(".lock")
            and not accounting.match(p.name)}


def test_head_on_every_get_route_leaves_the_data_dir_untouched(server):
    """Observed, not inferred: HEAD each enumerated GET route on a real server
    (plus the two known writers with VALID input, which a synthesized probe
    path never supplies) and compare the server's data directory byte for byte.
    Ends with a control: the same snapshot must see a GET that does write."""
    import importlib.util

    base, data_dir = server

    def load(name):
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    es, rs = load("enumerate_surface"), load("route_sweep")
    tracked = {str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "web").rglob("*") if p.is_file()}
    report = es.routes_report((REPO_ROOT / "server" / "app.py").read_text(),
                              (REPO_ROOT / "scripts" / "all_endpoints_probe.py").read_text(),
                              tracked)
    assert report["ok"], "the route enumeration failed its own oracles"
    paths = [rs.probe_path(e) for e in report["elements"] if e["method"] == "GET"]
    assert len(paths) >= 50 and "/api/unsubscribe" in paths, paths

    token = _mint_token(data_dir, "class-guard@example.test")
    paths += [f"/a/{token}", "/api/unsubscribe?e=class-guard@example.test"]

    before = _snapshot(data_dir)
    answered = 0
    for path in paths:
        status, _body, _headers = _srv.request(base, path, method="HEAD", timeout=15)
        answered += status != 429
    after = _snapshot(data_dir)
    # A rate limiter answering for the routes would make "nothing changed" vacuous.
    assert answered == len(paths), f"only {answered}/{len(paths)} HEADs reached a handler"
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert changed == [], f"HEAD changed server state: {changed}"

    _srv.request(base, "/api/unsubscribe?e=class-guard@example.test", timeout=15)
    assert _snapshot(data_dir) != after, "control: the snapshot cannot see a write"


def test_no_get_handler_writes_without_asking_which_method_it_is():
    """Every state-changing call reachable from do_GET must sit behind the
    HEAD check. Reads app.py's AST: a call to one of the known writers inside
    do_GET, or inside a handler do_GET dispatches to by name, fails unless the
    enclosing function also consults `_is_head()`.

    The writer list is the set of cross-module mutators do_GET can reach
    today. A new one belongs here in the change that adds it."""
    import ast

    src = (REPO_ROOT / "server" / "app.py").read_text()
    tree = ast.parse(src)
    writers = {("auth", "redeem_link_token"), ("auth", "create_session"),
               ("unsubscribe", "add")}

    def calls(fn, want):
        found = []
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and (node.func.value.id, node.func.attr) in want):
                found.append(f"{node.func.value.id}.{node.func.attr}")
        return found

    def consults_head(fn):
        return any(isinstance(n, ast.Attribute) and n.attr == "_is_head"
                   for n in ast.walk(fn))

    handler = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and any(
                       isinstance(m, ast.FunctionDef) and m.name == "do_GET" for m in n.body))
    methods = {m.name: m for m in handler.body if isinstance(m, ast.FunctionDef)}
    do_get = methods["do_GET"]
    dispatched = {n.func.attr for n in ast.walk(do_get)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                  and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"
                  and n.func.attr in methods}

    # Control: the scan must be able to see the writers at all, or "no
    # offenders" would mean "looked at nothing".
    reachable = [do_get] + [methods[name] for name in sorted(dispatched)]
    seen = {c for fn in reachable for c in calls(fn, writers)}
    assert seen == {f"{m}.{f}" for m, f in writers}, (
        f"the scan no longer reaches every known writer (saw {sorted(seen)})")

    offenders = [f"{fn.name}: {', '.join(calls(fn, writers))}"
                 for fn in reachable if calls(fn, writers) and not consults_head(fn)]
    assert offenders == [], f"GET-reachable writers with no HEAD check: {offenders}"
