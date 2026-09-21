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


# --- a ledger we can read and not write --------------------------------------

@pytest.fixture()
def read_only(server):
    """Make named ledgers read-only for one test, and always restore them."""
    import os
    _base, data_dir = server
    touched: list[Path] = []

    def make(*names: str) -> None:
        # Not meaningful as root, where permission bits do not bind. Fail
        # loudly rather than pass having tested nothing.
        assert os.geteuid() != 0, "run this suite as a non-root user"
        for name in names:
            path = data_dir / name
            assert path.exists(), f"{name} must exist before it can be made read-only"
            path.chmod(0o400)
            touched.append(path)
            assert not os.access(path, os.W_OK), f"control: {name} is still writable"

    yield make
    for path in touched:
        path.chmod(0o600)


def test_an_unwritable_suppression_ledger_is_answered_not_dropped(server, read_only):
    """GET used to get NO response (an OSError escaped the handler and the
    socket closed) while HEAD showed the success page."""
    base, data_dir = server
    _srv.request(base, "/api/unsubscribe?e=seed-the-ledger@example.test", timeout=15)
    read_only("suppressions.jsonl")
    path = "/api/unsubscribe?e=cannot-record@example.test"
    get_status, get_body, _h = _srv.request(base, path, timeout=15)
    head_status, _b, _h = _srv.request(base, path, method="HEAD", timeout=15)
    assert (get_status, head_status) == (503, 503)
    assert b"could not record" in get_body, "the person must be told it did not work"
    assert b"Done" not in get_body


def test_an_unwritable_sign_in_ledger_is_answered_and_keeps_the_link(server, read_only):
    base, data_dir = server
    token = _mint_token(data_dir, "cannot-sign-in@example.test")
    read_only("auth_tokens.jsonl")
    assert _srv.request(base, f"/a/{token}", timeout=15)[0] == 503
    assert _srv.request(base, f"/a/{token}", method="HEAD", timeout=15)[0] == 503
    (data_dir / "auth_tokens.jsonl").chmod(0o600)
    status, _b, headers = _srv.request(base, f"/a/{token}", timeout=15)
    assert status == 303 and "orpho_sid=" in headers.get("Set-Cookie", ""), (
        "the failed attempt must not have spent the link")


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


def test_head_on_every_get_route_leaves_the_data_dir_untouched(server, monkeypatch):
    """Observed, not inferred: HEAD each enumerated GET route on a real server
    (plus the two known writers with VALID input, which a synthesized probe
    path never supplies) and compare the server's data directory byte for byte.
    Ends with a control: the same snapshot must see a GET that does write."""
    import importlib.util

    base, data_dir = server

    def load(name):
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        # monkeypatch, not a bare assignment: this suite runs in ONE pytest
        # process, and a leaked `sys.modules` entry or a scripts/ directory
        # left at the front of sys.path changes what every later test imports.
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
        return mod

    monkeypatch.syspath_prepend(str(REPO_ROOT / "scripts"))
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

    # Some GET writes only happen for a visitor the server recognises: a
    # signed-in account (first read of an affiliate code registers it) or an
    # experiment cookie (a checkout view is attributed to its arm). An
    # anonymous sweep never reaches those branches, so sweep as both.
    sid = _sign_in(base, data_dir, "class-guard-member@example.test")
    # One-time bootstrap, not request state: the first email-id computation in
    # a data dir creates its HMAC key. Production has had one since day one, so
    # let a GET create it here rather than blame HEAD for initialising a dir.
    _srv.request(base, "/api/me/team", headers={"Cookie": f"orpho_sid={sid}"}, timeout=15)
    _srv.request(base, "/api/me/anchors", headers={"Cookie": f"orpho_sid={sid}"}, timeout=15)
    assert (data_dir / ".hmac_secret").exists(), "bootstrap did not happen before the snapshot"
    visitors = {
        "anonymous": {},
        "signed in, with an experiment cookie": {
            "Cookie": f"orpho_sid={sid}; orpho_ab_home=dark"},
    }

    before = _snapshot(data_dir)
    answered = 0
    for headers in visitors.values():
        for path in paths:
            status, _body, _headers = _srv.request(base, path, method="HEAD",
                                                   headers=headers, timeout=15)
            answered += status != 429
    after = _snapshot(data_dir)
    # A rate limiter answering for the routes would make "nothing changed" vacuous.
    total = len(paths) * len(visitors)
    assert answered == total, f"only {answered}/{total} HEADs reached a handler"
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert changed == [], f"HEAD changed server state: {changed}"

    # Controls: the session is real (or the signed-in sweep was anonymous in
    # disguise), and the same snapshot sees the same routes write under GET.
    member = visitors["signed in, with an experiment cookie"]
    assert _srv.request(base, "/api/me", headers=member, timeout=15)[0] == 200
    _srv.request(base, "/api/me/referral-code", headers=member, timeout=15)
    _srv.request(base, "/pay/crypto", headers=member, timeout=15)
    seen = _snapshot(data_dir)
    wrote = sorted(k for k in seen.keys() | after.keys() if seen.get(k) != after.get(k))
    assert "affiliate_codes.jsonl" in wrote and "ab_home.jsonl" in wrote, (
        f"control: GET on the writer routes changed only {wrote}")


def _sign_in(base: str, data_dir: Path, email: str) -> str:
    token = _mint_token(data_dir, email)
    _s, _b, headers = _srv.request(base, f"/a/{token}", timeout=15)
    cookie = headers.get("Set-Cookie", "")
    assert "orpho_sid=" in cookie, f"could not sign in: {cookie!r}"
    return cookie.split("orpho_sid=", 1)[1].split(";", 1)[0]


def test_head_reports_the_same_affiliate_code_without_registering_it(server):
    base, data_dir = server
    member = {"Cookie": f"orpho_sid={_sign_in(base, data_dir, 'affiliate-head@example.test')}"}
    registry = data_dir / "affiliate_codes.jsonl"
    before = len(_rows(registry))
    for path in ("/api/me/referral-code", "/api/me/affiliate"):
        status, _b, _h = _srv.request(base, path, method="HEAD", headers=member, timeout=15)
        assert status == 200, f"HEAD {path} never reached the signed-in branch ({status})"
    assert len(_rows(registry)) == before, "HEAD registered an affiliate code"

    _s, _b, head = _srv.request(base, "/api/me/referral-code", method="HEAD",
                                headers=member, timeout=15)
    status, body, _h = _srv.request(base, "/api/me/referral-code", headers=member, timeout=15)
    code = json.loads(body)["ref_code"]
    assert status == 200 and code.startswith("ref_")
    assert head.get("Content-Length") == str(len(body)), "HEAD described a different code"
    # Control: the ledger stores a hash, never the email, so the only honest
    # observation is the row itself. GET must have written exactly this one.
    assert [r["ref_code"] for r in _rows(registry)][before:] == [code]


@pytest.fixture(scope="module")
def experiment_server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("head_safe_ab")
    for base in _srv.server_processes(data_dir, stub_calendars=True, ORPHO_AB_HOME="0.5"):
        yield base, data_dir


def test_head_is_not_a_visitor_to_the_homepage_experiment(experiment_server):
    """A cookieless HEAD is a probe. It must not be assigned an arm, logged as
    a view, or handed the arm cookie; a returning visitor's HEAD is described
    from their arm and still logs nothing."""
    base, data_dir = experiment_server
    ua = {"User-Agent": "uptime-check/1.0"}
    status, _b, headers = _srv.request(base, "/", method="HEAD", headers=ua, timeout=15)
    assert status == 200
    assert not headers.get("Set-Cookie"), "HEAD was assigned an experiment arm"
    assert not (data_dir / "ab_home.jsonl").exists(), "HEAD was logged as a homepage view"

    returning = {**ua, "Cookie": "orpho_ab_home=dark"}
    _s, _b, head = _srv.request(base, "/", method="HEAD", headers=returning, timeout=15)
    assert not (data_dir / "ab_home.jsonl").exists(), "a returning visitor's HEAD was logged"
    _s, body, get = _srv.request(base, "/", headers=returning, timeout=15)
    assert head.get("Content-Length") == get.get("Content-Length") == str(len(body))
    assert head.get("Cache-Control") == get.get("Cache-Control") == "no-store"

    # Control: the experiment is really on in this server, and GET is a visitor.
    _s, _b, first = _srv.request(base, "/", headers=ua, timeout=15)
    assert "orpho_ab_home=" in first.get("Set-Cookie", "")
    events = [json.loads(l)["event"] for l in (data_dir / "ab_home.jsonl").read_text().splitlines()]
    assert events.count("home_view") == 2, events


# Cross-module mutators do_GET can reach today ("" = a module-level function in
# app.py). A new one belongs here in the change that adds it. The behavioural
# sweep above is what finds a writer nobody listed; this pins the listed ones.
_KNOWN_GET_WRITERS = {
    ("auth", "redeem_link_token"), ("auth", "create_session"),
    ("unsubscribe", "add"),
    ("affiliate", "code_for_email"), ("affiliate", "stats"),
    ("", "_ab_log"),
}


def _unguarded_writer_calls(src: str, writers=frozenset(_KNOWN_GET_WRITERS)):
    """(seen, offenders) over do_GET, the Handler methods it calls on self, and
    the module-level functions it calls by name.

    A writer call is guarded only if THAT CALL is, in the right direction (see
    `guarded`). One mention somewhere else in a 1000-line do_GET covers
    nothing, and neither does a mention with the polarity backwards."""
    import ast

    tree = ast.parse(src)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def is_head_call(n):
        return (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_is_head")

    def not_head(n):
        """`not self._is_head()`."""
        return isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not) and is_head_call(n.operand)

    def true_implies_not_head(test):
        """When `test` is True the request is certainly not HEAD."""
        if not_head(test):
            return True
        return (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And)
                and any(true_implies_not_head(v) for v in test.values))

    def false_implies_not_head(test):
        """When `test` is False the request is certainly not HEAD."""
        if is_head_call(test):
            return True
        return (isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or)
                and any(false_implies_not_head(v) for v in test.values))

    def writer_name(call):
        f = call.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            key = (f.value.id, f.attr)
        elif isinstance(f, ast.Name):
            key = ("", f.id)
        else:
            return None
        return ".".join(filter(None, key)) if key in writers else None

    def guarded(call, fn):
        # A guard has to say "not HEAD" about THIS call: an argument that is
        # `not self._is_head()` (`register=not self._is_head()`), the body of
        # an `if` that requires `not self._is_head()`, or the else-branch of an
        # `if` that tests `self._is_head()`. A bare mention is not enough: a
        # writer in the HEAD branch, or `register=self._is_head()` with the flag
        # inverted, mentions `_is_head` and does the opposite.
        if any(not_head(a) for a in list(call.args) + [k.value for k in call.keywords]):
            return True
        child, node = call, call
        while node is not fn:
            child, node = node, parent[node]
            if isinstance(node, ast.If):
                if child in node.body and true_implies_not_head(node.test):
                    return True
                if child in node.orelse and false_implies_not_head(node.test):
                    return True
        return False

    handler = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef) and any(
                       isinstance(m, ast.FunctionDef) and m.name == "do_GET" for m in n.body))
    methods = {m.name: m for m in handler.body if isinstance(m, ast.FunctionDef)}
    module_fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    do_get = methods["do_GET"]
    reachable = [do_get]
    for n in ast.walk(do_get):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "self" and f.attr in methods):
            reachable.append(methods[f.attr])
        elif isinstance(f, ast.Name) and f.id in module_fns:
            reachable.append(module_fns[f.id])

    seen, offenders = set(), []
    for fn in dict.fromkeys(reachable):
        for node in ast.walk(fn):
            name = isinstance(node, ast.Call) and writer_name(node)
            if not name:
                continue
            seen.add(name)
            if not guarded(node, fn):
                offenders.append(f"{fn.name}:{node.lineno} {name}")
    return seen, offenders


def test_every_known_get_writer_call_is_itself_behind_the_head_check():
    seen, offenders = _unguarded_writer_calls((REPO_ROOT / "server" / "app.py").read_text())
    # The scan must reach every listed writer, or "no offenders" means "looked
    # at nothing".
    expected = {".".join(filter(None, w)) for w in _KNOWN_GET_WRITERS}
    assert seen == expected, f"the scan no longer reaches {sorted(expected - seen)}"
    assert offenders == [], f"GET-reachable writer calls with no HEAD check: {offenders}"


def test_control_a_guard_elsewhere_in_do_get_does_not_cover_a_new_writer():
    """The first version of this check accepted any `_is_head` mention inside
    do_GET as covering every writer in it. Plant exactly that."""
    planted = '''
class Handler:
    def do_GET(self):
        if path == "/guarded":
            if self._is_head():
                pass
            else:
                unsubscribe.add(email)
        if path == "/forgotten":
            unsubscribe.add(email)
        if path == "/by-argument":
            affiliate.code_for_email(email, register=not self._is_head())
'''
    seen, offenders = _unguarded_writer_calls(planted)
    assert seen == {"unsubscribe.add", "affiliate.code_for_email"}
    assert offenders == ["do_GET:10 unsubscribe.add"], offenders


def test_control_a_guard_with_the_wrong_polarity_does_not_count():
    """The check as first written accepted ANY enclosing `if` that mentioned
    `_is_head`, so a writer placed in the HEAD branch, or given the flag
    inverted, passed. Plant both, plus the shapes that must stay accepted."""
    planted = '''
class Handler:
    def do_GET(self):
        if path == "/head-branch":
            if self._is_head():
                unsubscribe.add(email)
        if path == "/inverted":
            affiliate.code_for_email(email, register=self._is_head())
        if path == "/not-head":
            if not self._is_head():
                unsubscribe.add(email)
        if path == "/not-head-and-more":
            if refusal is None and not self._is_head():
                unsubscribe.add(email)
        if path == "/else-of-or":
            if other or self._is_head():
                pass
            else:
                unsubscribe.add(email)
        if path == "/else-of-not":
            if not self._is_head():
                pass
            else:
                unsubscribe.add(email)
'''
    seen, offenders = _unguarded_writer_calls(planted)
    assert seen == {"unsubscribe.add", "affiliate.code_for_email"}
    # Line 6: the writer sits in the HEAD branch. Line 8: the flag is inverted.
    # Line 24: the writer sits in the else-branch of `not _is_head()`, which is
    # the HEAD case. The other three shapes are accepted.
    assert sorted(offenders) == sorted(["do_GET:6 unsubscribe.add",
                                        "do_GET:8 affiliate.code_for_email",
                                        "do_GET:24 unsubscribe.add"]), offenders


# --- review round 2 (2026-09-20) ---------------------------------------------

def test_an_unwritable_session_ledger_keeps_the_link_on_get(server, read_only):
    """GET redeemed the link (spending it) and only THEN found it could not
    record the session, and answered "try the link again" about a link that no
    longer worked. HEAD already checked both ledgers. GET now checks first."""
    base, data_dir = server
    seed = _mint_token(data_dir, "session-ledger-seed@example.test")
    assert _srv.request(base, f"/a/{seed}", timeout=15)[0] == 303, "control: sign-in works"
    token = _mint_token(data_dir, "session-ledger-blocked@example.test")
    read_only("auth_sessions.jsonl")
    assert _srv.request(base, f"/a/{token}", timeout=15)[0] == 503
    assert _srv.request(base, f"/a/{token}", method="HEAD", timeout=15)[0] == 503
    (data_dir / "auth_sessions.jsonl").chmod(0o600)
    status, _b, headers = _srv.request(base, f"/a/{token}", timeout=15)
    assert status == 303 and "orpho_sid=" in headers.get("Set-Cookie", ""), (
        "the failed attempt spent the link")
    assert _events_for(data_dir, "session-ledger-blocked@example.test",
                       "auth_tokens.jsonl").count("redeemed") == 1


def test_unsubscribe_creates_a_ledger_directory_that_does_not_exist_yet(tmp_path, monkeypatch):
    """`locked()` creates missing parent directories. The writability pre-check
    inside `add()` did not know that, so a valid unsubscribe was refused where it
    used to be recorded."""
    import unsubscribe

    ledger = tmp_path / "not-yet" / "deeper" / "suppressions.jsonl"
    monkeypatch.setattr(unsubscribe, "SUPPRESS_PATH", ledger)
    assert unsubscribe.would_add("fresh@example.test") is True, "HEAD must agree with GET"
    assert unsubscribe.add("fresh@example.test", source="test") is True
    assert ledger.exists() and "fresh@example.test" in ledger.read_text()
    assert unsubscribe.add("fresh@example.test", source="test") is False, "idempotent"


def test_unsubscribe_still_refuses_a_ledger_it_cannot_write(tmp_path, monkeypatch):
    """Control for the test above: relaxing the pre-check must not turn an
    unwritable location into a silent success."""
    import os
    import unsubscribe

    assert os.geteuid() != 0, "run this suite as a non-root user"
    # A read-only FILE, as in the server tests above. (A read-only directory the
    # process owns is repaired by `locked()`, which chmods it 0700, so it is not
    # an unwritable location to the real writer.)
    ledger = tmp_path / "suppressions.jsonl"
    ledger.write_text("")
    ledger.chmod(0o400)
    try:
        assert not os.access(ledger, os.W_OK), "control: the ledger is read-only"
        monkeypatch.setattr(unsubscribe, "SUPPRESS_PATH", ledger)
        with pytest.raises(unsubscribe.SuppressionUnavailable):
            unsubscribe.would_add("blocked@example.test")
        with pytest.raises(unsubscribe.SuppressionUnavailable):
            unsubscribe.add("blocked@example.test", source="test")
        assert ledger.read_text() == "", "nothing may have been recorded"
    finally:
        ledger.chmod(0o600)


def test_can_append_follows_what_the_real_writer_does(tmp_path):
    from file_lock import can_append
    assert can_append(tmp_path / "new" / "a" / "b.jsonl") is True, "parents are created"
    existing = tmp_path / "there.jsonl"
    existing.write_text("")
    assert can_append(existing) is True
    existing.chmod(0o400)
    try:
        assert can_append(existing) is False
    finally:
        existing.chmod(0o600)
    a_file = tmp_path / "plain-file"
    a_file.write_text("x")
    assert can_append(a_file / "child.jsonl") is False, "a file is not a directory"


def test_a_stripe_session_id_in_a_url_does_not_reach_the_access_log(server):
    """`/api/stripe/session?id=cs_…` answers with the buyer's email to anyone
    who holds the id, and the post-checkout landing carries it as
    `?stripe_session=`. The log already dropped tokens, claim codes and
    addresses; it still kept these."""
    base, data_dir = server
    secret = "cs_test_a1B2c3D4e5F6g7H8logcanary"
    _srv.request(base, f"/api/stripe/session?id={secret}", timeout=15)
    _srv.request(base, f"/buy?stripe_session={secret}&status=success", timeout=15)
    # An unrelated `id=` must stay readable, or the rule is redacting too much.
    _srv.request(base, "/api/health?id=harmless-id-canary", timeout=15)
    logs = list(data_dir.glob("server-*.log"))
    assert len(logs) == 1, logs
    text = logs[0].read_text(errors="replace")
    assert "/api/stripe/session" in text and "stripe_session=" in text, (
        "control: both requests reached the log at all")
    assert secret not in text, "a Stripe session id was written to the access log"
    assert "harmless-id-canary" in text, "the rule redacted an id it should not"


def test_a_cookieless_head_on_the_experiment_homepage_is_uncacheable_like_get(experiment_server):
    """It is not a visitor and gets no arm, but it must not advertise a plain,
    cacheable page while GET answers per-arm with no-store and Vary: Cookie."""
    base, _data_dir = experiment_server
    ua = {"User-Agent": "uptime-check/1.0"}  # an honest client that is not a bot the experiment skips
    _s, _b, head = _srv.request(base, "/", method="HEAD", headers=ua, timeout=15)
    _s, _b, get = _srv.request(base, "/", headers=ua, timeout=15)
    assert head.get("Cache-Control") == get.get("Cache-Control") == "no-store"
    assert "Cookie" in head.get("Vary", "") and "Cookie" in get.get("Vary", "")
    assert "Set-Cookie" not in head
