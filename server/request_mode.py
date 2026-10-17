#!/usr/bin/env python3
"""request_mode.py — HEAD is read-only, enforced in one place.

HEAD is a safe method (RFC 9110 §9.2.1). `do_HEAD` answers by running the
ordinary GET routing with the body thrown away, so every GET handler's side
effects used to fire on HEAD too. Each one was found and guarded at its own
call site: the sign-in link spent by a mail scanner (2026-09-19), the
unsubscribe a link checker recorded (same day), and the crypto confirmation
page's lookup budget that a HEAD probe on the buyer's network used up
(cycle 7). A guard per call site protects only the call sites someone
already found.

This module is the one switch instead. `do_HEAD` runs the GET routing inside
`safe_method()`, and two things follow for every route, listed or not:

  * Writes under the guarded data directory are refused. An audit hook
    (sys.addaudithook) sees every open-for-write, rename, remove, mkdir,
    rmdir and truncate the process makes; on a HEAD thread one that lands
    under the data directory raises PermissionError instead of happening. A
    handler that has its own HEAD branch never gets there; a handler nobody
    guarded fails loudly on HEAD instead of changing state quietly.
  * Rate-limit budgets are kept apart: rate_limit.TokenBucket spends a HEAD
    from a key of its own (`head:<key>`), so a probe cannot use up the budget
    the GET it mirrors polls with. Failure limiters (a wrong founder token)
    opt out with method_neutral=True: a guess is a guess whatever the method.

Two kinds of write stay allowed under HEAD, both request ACCOUNTING rather
than state: lock files (`*.lock`, opened to coordinate a read) and anything
written inside `accounting()` (the rate limiter's own snapshot, which
records that a request arrived, whatever its method).

The flag is per thread. ThreadingHTTPServer serves each request on its own
thread, and the background workers never set it. A thread a handler starts
itself does not inherit it either, so a GET handler that hands a write to a
new thread is not covered here; the route sweep in
tests/test_head_is_a_safe_method.py (data dir byte-compared around a HEAD on
every GET route) is what sees that.
"""
from __future__ import annotations

import errno
import os
import sys
import threading
from contextlib import contextmanager

_state = threading.local()
_guarded_roots: list[str] = []
_hook_installed = False
_hook_lock = threading.Lock()

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
# Events whose first argument (and, for rename, second) is a path the call
# changes. os.replace raises "os.rename"; os.unlink raises "os.remove".
_PATH_EVENTS = {"os.remove": 1, "os.mkdir": 1, "os.rmdir": 1,
                "os.truncate": 1, "os.rename": 2, "os.link": 2, "os.symlink": 2}


def is_safe_method() -> bool:
    """True while this thread is answering a HEAD."""
    return getattr(_state, "safe", False)


@contextmanager
def safe_method():
    """Run the enclosed request handling as a safe method (HEAD)."""
    prev = is_safe_method()
    _state.safe = True
    try:
        yield
    finally:
        _state.safe = prev


@contextmanager
def accounting():
    """Allow the enclosed writes under HEAD: they record that a request
    arrived (the rate limiter's snapshot), which is true of a HEAD too."""
    prev = getattr(_state, "accounting", False)
    _state.accounting = True
    try:
        yield
    finally:
        _state.accounting = prev


def _abs(path) -> str | None:
    if isinstance(path, bytes):
        try:
            path = os.fsdecode(path)
        except (UnicodeDecodeError, ValueError):
            return None
    if not isinstance(path, str):
        try:
            path = os.fspath(path)
        except TypeError:
            return None  # a file descriptor: no path to judge
        if not isinstance(path, str):
            return None
    return os.path.abspath(path)


def _is_guarded(path) -> bool:
    p = _abs(path)
    if p is None or p.endswith(".lock"):
        return False
    return any(p == root or p.startswith(root + os.sep) for root in _guarded_roots)


def _refuse(path) -> None:
    sys.stderr.write(f"[head] refused a write under the data dir during HEAD: "
                     f"{os.path.basename(str(path))}\n")
    raise PermissionError(errno.EROFS, "HEAD is a safe method: no writes", str(path))


def _audit(event: str, args) -> None:
    # Cheapest test first: the hook sees every audited event in the process.
    if not getattr(_state, "safe", False) or getattr(_state, "accounting", False):
        return
    if event == "open":
        path, _mode, flags = args[0], args[1], args[2]
        if isinstance(flags, int) and flags & _WRITE_FLAGS and _is_guarded(path):
            _refuse(path)
        return
    n = _PATH_EVENTS.get(event)
    if n:
        # mkdir(exist_ok=True) on a directory that is already there changes
        # nothing (the audit event fires before the call fails), and read
        # paths do it to make sure their ledger's directory exists.
        if event == "os.mkdir" and _abs(args[0]) and os.path.isdir(_abs(args[0])):
            return
        for path in args[:n]:
            if _is_guarded(path):
                _refuse(path)


def guard_writes_under(root) -> None:
    """Refuse writes under `root` on any thread answering a HEAD. Idempotent;
    the audit hook is installed once per process and cannot be removed."""
    global _hook_installed
    for form in {os.path.abspath(os.fspath(root)), os.path.realpath(os.fspath(root))}:
        if form not in _guarded_roots:
            _guarded_roots.append(form)
    with _hook_lock:
        if not _hook_installed:
            sys.addaudithook(_audit)
            _hook_installed = True
