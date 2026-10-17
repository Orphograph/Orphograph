"""HEAD-safety is enforced once, at the shared mechanism (@head-mechanism).

Before 2026-10-17 every GET handler with a side effect had to ask
`self._is_head()` itself, and each one was found after it shipped: the
sign-in link a scanner spent, the unsubscribe a link checker recorded, the
crypto confirmation page's lookup budget a HEAD probe used up (#281). The
tests in test_head_is_a_safe_method.py pin those instances and sweep the
routes that exist today; nothing stopped the NEXT handler from writing.

Now do_HEAD runs the GET routing inside request_mode.safe_method():
  * a write under the data dir is refused (PermissionError), and
  * a rate-limit spend goes to a HEAD key of its own,
for every route, guarded or not. The tests plant a handler with NO guard and
drive it through the real do_HEAD: before the mechanism it wrote and spent;
with it, it can do neither. Each has a control (the same handler under GET
does write and spend), so a planted handler that never wrote could not pass.
"""
from __future__ import annotations

import ast
import io
import json
import os
import threading
from pathlib import Path

import pytest

import app
import request_mode
from rate_limit import TokenBucket

REPO_ROOT = Path(__file__).resolve().parent.parent
# This module serves nothing: it calls do_HEAD on a bare Handler instance with
# no socket and no port, so it has none of the fixture problems tests/_srv.py
# exists to solve (test_server_fixture_hygiene.py).
Handler = vars(app)["Handler"]
APP_SRC = Path(app.__file__)


def _planted_handler(do_get):
    """A Handler with only what do_HEAD touches, and a do_GET of the test's."""
    h = object.__new__(Handler)
    h.command = "HEAD"
    h.path = "/planted"
    h.wfile = io.BytesIO()
    h.end_headers = lambda: None  # do_HEAD wraps the bound one
    h.do_GET = do_get
    return h


def _drive(h, command):
    h.command = command
    if command == "HEAD":
        Handler.do_HEAD(h)
    else:
        h.do_GET()


# --- writes -------------------------------------------------------------------

def test_an_unguarded_get_writer_cannot_write_on_head():
    """THE DEFECT CLASS: a GET handler nobody guarded, reached by HEAD."""
    ledger = Path(app.DATA_DIR) / "planted_unguarded_writer.jsonl"
    ledger.unlink(missing_ok=True)

    def do_get():
        with open(ledger, "a") as f:  # no _is_head() check: the bypass
            f.write(json.dumps({"event": "planted"}) + "\n")

    h = _planted_handler(do_get)
    with pytest.raises(PermissionError):
        _drive(h, "HEAD")
    assert not ledger.exists(), "HEAD wrote under the data dir"

    # Control: the same handler under GET writes, so the plant is live.
    _drive(h, "GET")
    assert ledger.read_text().count("planted") == 1
    ledger.unlink()


@pytest.mark.parametrize("how", ["write_text", "replace", "remove", "mkdir"])
def test_every_way_of_changing_the_data_dir_is_refused_on_head(how, tmp_path):
    root = Path(app.DATA_DIR)
    victim = root / "planted_victim.txt"
    victim.write_text("before")
    newdir = root / "planted_newdir"
    tmp_src = tmp_path / "outside.txt"
    tmp_src.write_text("x")

    def do_get():
        if how == "write_text":
            victim.write_text("after")
        elif how == "replace":
            staged = root / "planted_staged.txt"
            os.replace(tmp_src, staged)  # landing under the data dir
        elif how == "remove":
            victim.unlink()
        elif how == "mkdir":
            newdir.mkdir()

    try:
        with pytest.raises(PermissionError):
            _drive(_planted_handler(do_get), "HEAD")
        assert victim.read_text() == "before"
        assert not (root / "planted_staged.txt").exists()
        assert not newdir.exists()
    finally:
        victim.unlink(missing_ok=True)
        (root / "planted_staged.txt").unlink(missing_ok=True)
        if newdir.exists():
            newdir.rmdir()


def test_head_still_reads_and_still_writes_outside_the_data_dir(tmp_path):
    """Negative control for the guard: it must not refuse what HEAD needs."""
    root = Path(app.DATA_DIR)
    ledger = root / "planted_readable.jsonl"
    ledger.write_text('{"a": 1}\n')
    outside = tmp_path / "scratch.txt"
    lock = root / "planted_ledger.lock"
    seen = {}

    def do_get():
        seen["read"] = ledger.read_text()
        outside.write_text("fine")          # not the data dir
        with open(lock, "a"):              # a lock file coordinates a read
            pass
        root.mkdir(exist_ok=True)           # already there: changes nothing
        with request_mode.accounting():     # the limiter's own snapshot
            (root / "planted_accounting.json").write_text("{}")

    try:
        _drive(_planted_handler(do_get), "HEAD")
        assert seen["read"] == '{"a": 1}\n'
        assert outside.read_text() == "fine"
    finally:
        for p in (ledger, lock, root / "planted_accounting.json"):
            p.unlink(missing_ok=True)


def test_the_guard_is_per_thread_so_background_workers_keep_writing():
    """The upgrade worker writes while requests are being answered."""
    root = Path(app.DATA_DIR)
    target = root / "planted_worker.txt"
    ok = {}

    def worker():
        target.write_text("worker")
        ok["wrote"] = True

    def do_get():
        t = threading.Thread(target=worker)
        t.start()
        t.join()

    try:
        _drive(_planted_handler(do_get), "HEAD")
        assert ok.get("wrote") and target.read_text() == "worker"
    finally:
        target.unlink(missing_ok=True)


# --- rate-limit budgets -------------------------------------------------------

def test_an_unguarded_get_limiter_keeps_head_out_of_the_get_budget():
    """The #281 instance as a class: a GET route that spends a lookup budget
    with a plain check(), with no per-site HEAD handling at all."""
    bucket = TokenBucket(2, 1e-9)
    key = "orderstat:203.0.113.0"

    def do_get():
        bucket.check(key)

    h = _planted_handler(do_get)
    for _ in range(5):
        _drive(h, "HEAD")
    assert bucket.check(key)[0], "HEAD probes used up the GET budget"
    # Control: GETs do spend it.
    assert bucket.check(key)[0]
    assert not bucket.check(key)[0]
    # And HEAD is bounded the same way, on its own key.
    assert not bucket._buckets[f"head:{key}"][0] >= 1.0


def test_a_failure_limiter_counts_a_head_guess_like_a_get_guess():
    bucket = TokenBucket(3, 1e-9, method_neutral=True)
    seen = {}

    def do_get():
        bucket.check("founder-fail:x")
        seen["peek"] = bucket.peek("founder-fail:x")

    _drive(_planted_handler(do_get), "HEAD")
    assert seen["peek"] == pytest.approx(2.0)
    assert bucket.peek("founder-fail:x") == pytest.approx(2.0)


def test_the_founder_failure_limiters_are_method_neutral():
    assert app._founder_fail_limiter.method_neutral is True
    assert app._founder_fail_global_limiter.method_neutral is True


# --- nothing bypasses the mechanism -------------------------------------------

def _app_tree():
    return ast.parse(APP_SRC.read_text())


def test_do_head_runs_the_get_routing_inside_the_mechanism():
    tree = _app_tree()
    handler = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Handler")
    do_head = next(m for m in handler.body if isinstance(m, ast.FunctionDef) and m.name == "do_HEAD")
    calls = [n for n in ast.walk(do_head) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "do_GET"]
    assert calls, "do_HEAD no longer calls do_GET: re-read this test"
    parent = {c: p for p in ast.walk(do_head) for c in ast.iter_child_nodes(p)}
    for call in calls:
        node, inside = call, False
        while node is not do_head:
            node = parent[node]
            if isinstance(node, ast.With) and any(
                    isinstance(i.context_expr, ast.Call)
                    and ast.unparse(i.context_expr.func) == "request_mode.safe_method"
                    for i in node.items):
                inside = True
        assert inside, f"do_HEAD calls do_GET outside request_mode.safe_method() (line {call.lineno})"


def test_nothing_else_answers_a_request_by_calling_do_get():
    """A second path into the GET routing (a HEAD shim of its own, a retry)
    would answer outside the mechanism."""
    tree = _app_tree()
    owners = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for n in ast.walk(fn):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "do_GET"):
                    owners.append(fn.name)
    assert owners == ["do_HEAD"], owners


def test_the_data_dir_is_guarded_at_import():
    root = os.path.abspath(str(app.DATA_DIR))
    assert root in request_mode._guarded_roots or os.path.realpath(root) in request_mode._guarded_roots
