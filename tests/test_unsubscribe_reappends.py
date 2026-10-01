"""The consent ledger is only ever appended to.

0ce9aa3 made a failed write answer the same for a known and a new address by
appending blanks for a known one and cutting them off again with ftruncate.
The size check and the cut were two steps, and scripts/reply_router.py
appends without the lock, so a STOP row it wrote between them could be cut
off. Shortening a consent ledger is the wrong tool for this.

Now the unsubscribe POST appends a row for the address whether or not one is
already there. An address is suppressed if any row says so, so the extra row
changes nothing a reader sees, and the write is the same for both addresses.
These tests hold that nothing in server/unsubscribe.py can shorten or replace
the ledger, and that unsubscribes arriving at the same moment from two server
processes leave every row whole.
"""
from __future__ import annotations

import ast
import json
import threading
from pathlib import Path
from urllib.parse import quote

import _srv

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "server" / "unsubscribe.py"
ONE_CLICK = {"Content-Type": "application/x-www-form-urlencoded"}

# Calls that shorten, empty or replace a file. write_text and write_bytes are
# here because both open in "w" mode, which is the easy one to miss.
_CUTTERS = frozenset({"truncate", "ftruncate", "write_text", "write_bytes"})
# Calls that open a file and take a mode.
_OPENERS = frozenset({"open", "locked", "fdopen"})


def _mode_of(call: ast.Call):
    """The mode argument of an open call: a str, None when it is left to the
    default ("r"), or the node itself when the source does not spell it out."""
    for k in call.keywords:
        if k.arg == "mode":
            node = k.value
            break
    else:
        f = call.func
        # path.open(mode) takes the mode first; open(path, mode),
        # io.open(path, mode), os.fdopen(fd, mode) and locked(path, mode)
        # take it second.
        first = (isinstance(f, ast.Attribute) and f.attr == "open"
                 and not (isinstance(f.value, ast.Name)
                          and f.value.id in {"io", "builtins", "codecs"}))
        index = 0 if first else 1
        if len(call.args) <= index:
            return None
        node = call.args[index]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return node


def _scan(source: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Every place in `source` that can shorten or replace a file, and every
    open it read the mode of. A mode it cannot read counts as unsafe: a
    variable would let "w" through unseen."""
    unsafe: list[str] = []
    opens: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        # A reference, not only a call, so `cut = os.ftruncate` is seen too.
        if isinstance(node, ast.Attribute) and node.attr in _CUTTERS | {"O_TRUNC"}:
            unsafe.append(f"{node.lineno} {node.attr}")
        elif isinstance(node, ast.Name) and node.id in _CUTTERS | {"O_TRUNC"}:
            unsafe.append(f"{node.lineno} {node.id}")
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
              and node.value in _CUTTERS):
            unsafe.append(f"{node.lineno} {node.value!r}")   # getattr(os, "ftruncate")
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "os" and f.attr in {"replace", "rename"}):
            unsafe.append(f"{node.lineno} os.{f.attr}")       # a rewrite moved over it
            continue
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name not in _OPENERS:
            continue
        if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "os" and f.attr == "open"):
            continue    # flags, not a mode; O_TRUNC is caught above
        mode = _mode_of(node)
        if mode is None:
            mode = "r"
        if not isinstance(mode, str):
            unsafe.append(f"{node.lineno} {name} with a mode the scan cannot read")
            continue
        opens.append((name, mode))
        # "w" empties the file. "r+" writes over it in place. "a" and "a+"
        # only ever add at the end.
        if "w" in mode or ("+" in mode and "a" not in mode):
            unsafe.append(f"{node.lineno} {name} mode {mode!r}")
    return unsafe, opens


def test_nothing_in_unsubscribe_can_shorten_the_ledger():
    unsafe, opens = _scan(SOURCE.read_text())
    # The scan has to have looked at the writer, or "nothing found" means
    # nothing was read.
    assert ("locked", "a") in opens, opens
    assert unsafe == [], f"server/unsubscribe.py can shorten or replace a file: {unsafe}"


def test_the_scan_sees_every_way_to_shorten_a_file():
    """Control: plant each form; each must be found, on its own line. And the
    forms the module uses to read and append must pass."""
    planted = '''
import os
from pathlib import Path
os.ftruncate(f.fileno(), before)
f.truncate()
cut = os.ftruncate
open(path, "w")
path.open("w+")
locked(SUPPRESS_PATH, mode="w")
locked(SUPPRESS_PATH, "r+")
SUPPRESS_PATH.write_text("")
SUPPRESS_PATH.write_bytes(b"")
fd = os.open(path, os.O_WRONLY | os.O_TRUNC)
open(path, mode_from_somewhere)
os.replace(tmp, SUPPRESS_PATH)
getattr(os, "ftruncate")(fd, 0)
'''
    unsafe, _opens = _scan(planted)
    lines = sorted({int(u.split()[0]) for u in unsafe})
    assert lines == list(range(4, 17)), unsafe
    clean = '''
with open(path, "a"):
    pass
with path.open("rb") as f:
    pass
with SUPPRESS_PATH.open() as f:
    pass
with locked(SUPPRESS_PATH, mode="a", exclusive=True) as f:
    f.write(row)
with open(path, "a+") as f:
    pass
email.strip().replace("x", "y")
'''
    unsafe, opens = _scan(clean)
    assert unsafe == [], unsafe
    assert len(opens) == 5, opens


def _row(email: str) -> str:
    return json.dumps({"ts": "2026-09-27T00:00:00+00:00", "email": email,
                       "source": "test"}, separators=(",", ":")) + "\n"


def test_concurrent_unsubscribes_of_one_address_keep_every_row_whole(tmp_path, monkeypatch):
    """Two server processes on one data directory, each taking several
    one-click POSTs for the same address, all let go at the same moment, and
    then again. The first round races to be first for a new address; in the
    second the address is certainly there, which is the case 0ce9aa3 padded
    and cut. Every answer is 200, every line of the ledger still parses, the
    rows that were there before are all still there, and each POST answered
    200 left its row: the address is suppressed, and nothing was cut."""
    import unsubscribe
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ledger = data_dir / "suppressions.jsonl"
    # Other people's rows. A writer that rewrote or shortened the file would
    # lose these even if the address under test stayed suppressed.
    others = [f"before-{i}@example.test" for i in range(3)]
    seed = "".join(_row(e) for e in others)
    ledger.write_text(seed)
    email = "both-at-once@example.test"
    per_server = 4
    rounds = 2
    for bases in _srv.server_processes(data_dir, n=2, stub_calendars=True):
        jobs = [b for b in bases for _ in range(per_server)]
        for _round in range(rounds):
            _all_at_once(jobs, email)
    text = ledger.read_text()
    assert text.startswith(seed), "a row that was already in the ledger is gone"
    lines = [line for line in text.splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]          # every line is whole
    assert [r["email"] for r in rows].count(email) == rounds * len(jobs), \
        "a POST was answered 200 and its row is not in the ledger"
    monkeypatch.setattr(unsubscribe, "SUPPRESS_PATH", ledger)
    assert unsubscribe.is_unsubscribed(email)
    assert all(unsubscribe.is_unsubscribed(e) for e in others)
    assert not unsubscribe.is_unsubscribed("nobody@example.test")


def _all_at_once(jobs: list[str], email: str) -> None:
    """One one-click POST for `email` to each base in `jobs`, all let go
    together. Each must be answered 200."""
    start = threading.Barrier(len(jobs))
    answers: list = [None] * len(jobs)

    def post(i: int, base: str) -> None:
        try:
            start.wait(timeout=30)
            status, body, _h = _srv.request(
                base, "/api/unsubscribe?e=" + quote(email), method="POST",
                body=b"List-Unsubscribe=One-Click", headers=ONE_CLICK, timeout=30)
            answers[i] = (status, json.loads(body))
        except BaseException as e:      # reported below, not lost with the thread
            answers[i] = e

    threads = [threading.Thread(target=post, args=(i, b), daemon=True)
               for i, b in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert not any(t.is_alive() for t in threads), "a POST never came back"
    assert answers == [(200, {"ok": True})] * len(jobs), answers


def test_one_address_posted_in_a_loop_is_rate_limited_but_other_addresses_are_not(tmp_path):
    """Review of the re-append change (cycle 8): every POST now appends a row,
    and every marketing send scans the whole suppression ledger, so a
    stranger looping the POST for one address grew the file without bound
    and slowed every send. The limit is per (client prefix, address): a
    person presses unsubscribe once or twice, and a mailbox provider's
    one-click POSTs for MANY different recipients from one server stay
    unthrottled (RFC 8058 unsubscribes must keep working)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ledger = data_dir / "suppressions.jsonl"
    email = "looped@example.test"
    for base in _srv.server_processes(data_dir, stub_calendars=True):
        answers = []
        for _ in range(21):
            status, body, headers = _srv.request(
                base, "/api/unsubscribe?e=" + quote(email), method="POST",
                body=b"List-Unsubscribe=One-Click", headers=ONE_CLICK, timeout=30)
            answers.append((status, headers.get("Retry-After")))
        assert [s for s, _ in answers[:20]] == [200] * 20, answers
        assert answers[20][0] == 429 and answers[20][1], answers   # honest: retry later
        # many recipients from the same server are not throttled
        for i in range(12):
            status, _b, _h = _srv.request(
                base, "/api/unsubscribe?e=" + quote(f"other-{i}@example.test"), method="POST",
                body=b"List-Unsubscribe=One-Click", headers=ONE_CLICK, timeout=30)
            assert status == 200, (i, status)
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert [r["email"] for r in rows].count(email) == 20, "the limited POST still wrote a row"
