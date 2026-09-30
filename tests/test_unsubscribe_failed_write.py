"""A failed write answers the same for a known and a new address.

POST /api/unsubscribe checked that the suppression ledger could be OPENED
(ensure_writable) and then called add(). add() returned early for an address
already in the ledger, without writing. So when the file opened and the write
itself failed (a full volume), an address that had already unsubscribed was
answered 200 and a new one 503: while the volume was full, anyone could read
who had unsubscribed.

Now the POST asks add() to prove the write. With nothing to record it appends
blank padding as long as the row a new address would write, through the same
locked append, and cuts it off again. A failed write raises
SuppressionUnavailable for both, and a proof that worked leaves the ledger
byte for byte as it was. Other callers of add() write nothing, as before.

Why as long as the row, and not one byte: a full volume still has the unused
rest of the ledger's last block. One byte fits there long after a row has
stopped fitting (measured on a full 4096-byte-block volume, 2026-09-28), so a
one-byte proof answered 200 where a new address got 503.
"""
from __future__ import annotations

import contextlib
import errno
import importlib.util
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
KNOWN = "known@example.test"
FRESH = "fresh@example.test"
ONE_CLICK = {"Content-Type": "application/x-www-form-urlencoded"}
# The capped server can grow no file past this. Far above anything it writes
# while these tests run (its log, the seeded sample receipt).
CAP = 256 * 1024


class _Volume:
    """The file file_lock.locked yields, on a volume with `room` bytes left.

    A write longer than the room puts down what fits and then fails, which is
    what a full volume does (the same measurement). Everything but write is
    the real file, so nothing else can be what fails."""

    def __init__(self, real, room: int, seen: list):
        self._real, self._room, self._seen = real, room, seen

    def write(self, text: str) -> int:
        self._seen.append(len(text))
        if len(text) <= self._room:
            self._room -= len(text)
            return self._real.write(text)
        self._real.write(text[:self._room])
        self._real.flush()
        self._room = 0
        raise OSError(errno.ENOSPC, "No space left on device")

    def __getattr__(self, name):
        return getattr(self._real, name)


def _volume_with(monkeypatch, room: int) -> list:
    """Make the ledger's volume have `room` bytes left. Returns the list that
    collects the length of every write add() attempts."""
    import file_lock
    import unsubscribe
    seen: list = []

    @contextlib.contextmanager
    def locked(path, *, mode="a", exclusive=True):
        with file_lock.locked(path, mode=mode, exclusive=exclusive) as real:
            yield _Volume(real, room, seen)

    monkeypatch.setattr(unsubscribe, "locked", locked)
    return seen


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """A healthy ledger that already holds KNOWN."""
    import unsubscribe
    path = tmp_path / "suppressions.jsonl"
    monkeypatch.setattr(unsubscribe, "SUPPRESS_PATH", path)
    assert unsubscribe.add(KNOWN, source="test") is True
    return path


def _script(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{name}_reading_the_ledger", ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _rows(path: Path) -> list[str]:
    return [json.loads(l)["email"] for l in path.read_text().splitlines() if l.strip()]


# --- add(), in process ------------------------------------------------------

def test_a_failed_write_refuses_an_address_already_in_the_ledger(ledger, monkeypatch):
    """THE DEFECT: nothing was written for this address, so nothing failed."""
    import unsubscribe
    before = ledger.read_bytes()
    seen = _volume_with(monkeypatch, room=0)
    with pytest.raises(unsubscribe.SuppressionUnavailable):
        unsubscribe.add(KNOWN, source="test", prove_write=True)
    assert seen, "refused without trying the write"
    assert ledger.read_bytes() == before


def test_a_failed_write_refuses_a_new_address(ledger, monkeypatch):
    """Control: the answer the known address has to match."""
    import unsubscribe
    seen = _volume_with(monkeypatch, room=0)
    with pytest.raises(unsubscribe.SuppressionUnavailable):
        unsubscribe.add(FRESH, source="test", prove_write=True)
    assert seen
    assert not unsubscribe.is_unsubscribed(FRESH)


def test_room_for_a_byte_is_not_room_for_a_row(ledger, monkeypatch):
    """The proof is refused wherever the row would be: it tries as many bytes
    as the row. A one-byte proof passed here and the new address did not."""
    import unsubscribe
    before = ledger.read_bytes()
    seen = _volume_with(monkeypatch, room=8)
    with pytest.raises(unsubscribe.SuppressionUnavailable):
        unsubscribe.add(KNOWN, source="link_post", prove_write=True)
    tried_for_known = sum(seen)
    ledger.write_bytes(before)          # the same ledger and the same room again
    seen = _volume_with(monkeypatch, room=8)
    with pytest.raises(unsubscribe.SuppressionUnavailable):
        unsubscribe.add(FRESH, source="link_post", prove_write=True)
    # The two addresses are equally long, so their rows are.
    assert len(KNOWN) == len(FRESH)
    assert tried_for_known == sum(seen)
    assert unsubscribe.is_unsubscribed(KNOWN) and not unsubscribe.is_unsubscribed(FRESH)


def test_proving_the_write_leaves_the_ledger_as_it_was(ledger):
    """Control: on a healthy ledger add() still says "already there", and the
    ledger does not grow however often it is asked. Nothing limits how often
    a stranger may POST, so the bound is this one: no growth at all."""
    import unsubscribe
    before = ledger.read_bytes()
    for _ in range(25):
        assert unsubscribe.add(KNOWN, source="test", prove_write=True) is False
    assert ledger.read_bytes() == before
    assert unsubscribe.is_unsubscribed(KNOWN)
    assert not unsubscribe.is_unsubscribed(FRESH)


def test_a_caller_that_does_not_ask_writes_nothing(ledger, monkeypatch):
    """Control: add() without prove_write is what it was, on a healthy ledger
    and on a full volume."""
    import unsubscribe
    before = ledger.read_bytes()
    assert unsubscribe.add(KNOWN, source="test") is False
    assert ledger.read_bytes() == before
    seen = _volume_with(monkeypatch, room=0)
    assert unsubscribe.add(KNOWN, source="test") is False
    assert seen == [], "it opened the ledger and wrote"
    assert ledger.read_bytes() == before


def test_the_proof_cuts_off_nothing_it_did_not_write(ledger, monkeypatch):
    """The proof takes its padding off again by shortening the file. A writer
    that does not take the lock (scripts/reply_router.py) can append in
    between, and its row must survive: then the padding stays instead."""
    import file_lock
    import unsubscribe
    other = json.dumps({"email": "stop-reply@example.test", "reason": "STOP_REPLY"}) + "\n"

    class _Interleaved:
        def __init__(self, real):
            self._real = real

        def flush(self):
            self._real.flush()
            with ledger.open("a") as unlocked:
                unlocked.write(other)

        def __getattr__(self, name):
            return getattr(self._real, name)

    @contextlib.contextmanager
    def locked(path, *, mode="a", exclusive=True):
        with file_lock.locked(path, mode=mode, exclusive=exclusive) as real:
            yield _Interleaved(real)

    before = ledger.read_bytes()
    monkeypatch.setattr(unsubscribe, "locked", locked)
    assert unsubscribe.add(KNOWN, source="test", prove_write=True) is False
    after = ledger.read_bytes()
    assert after.startswith(before) and after.endswith(other.encode())
    assert after[len(before):-len(other)].strip() == b"", "only blank padding between them"
    assert unsubscribe.is_unsubscribed("stop-reply@example.test")
    assert unsubscribe.is_unsubscribed(KNOWN)


def test_every_reader_of_the_ledger_skips_what_a_failed_proof_leaves(ledger, monkeypatch):
    """A proof that fails part-way leaves blanks with no newline, and one cut
    short by a crash leaves a whole blank line. The server's reader and the
    two scripts that read the same file skip both."""
    import file_lock
    import unsubscribe
    _volume_with(monkeypatch, room=8)
    with pytest.raises(unsubscribe.SuppressionUnavailable):
        unsubscribe.add(KNOWN, source="test", prove_write=True)
    monkeypatch.setattr(unsubscribe, "locked", file_lock.locked)    # room again
    assert ledger.read_bytes().endswith(b" " * 8), "control: the blanks are there"
    assert unsubscribe.add(FRESH, source="test") is True        # a row after them
    with ledger.open("a") as f:
        f.write(" " * 40 + "\n" + "\n")
    assert unsubscribe.add("last@example.test", source="test") is True
    everyone = {KNOWN, FRESH, "last@example.test"}
    assert all(unsubscribe.is_unsubscribed(e) for e in everyone)
    assert not unsubscribe.is_unsubscribed("nobody@example.test")
    runner = _script("cadence_runner")
    monkeypatch.setattr(runner, "SUPPRESSIONS", ledger)
    assert runner._read_suppressions() == everyone
    importer = _script("import_prospects")
    monkeypatch.setattr(importer, "SUPPRESSIONS", ledger)
    assert importer._suppressed_emails() == everyone


def test_a_proof_on_a_torn_ledger_leaves_the_next_row_readable(tmp_path, monkeypatch):
    """The torn-line guard still holds. The proof puts the ledger back as it
    found it, torn line and all, and the next row starts on its own line."""
    import unsubscribe
    path = tmp_path / "suppressions.jsonl"
    path.write_text(
        '{"ts":"2026-09-27T00:00:00+00:00","email":"known@example.test","source":"t"}\n'
        '{"ts":"2026-09-27T00:00:01+00:00","em')                 # torn, no newline
    before = path.read_bytes()
    monkeypatch.setattr(unsubscribe, "SUPPRESS_PATH", path)
    assert unsubscribe.add(KNOWN, source="test", prove_write=True) is False
    assert path.read_bytes() == before
    assert unsubscribe.add(FRESH, source="test") is True
    assert unsubscribe.is_unsubscribed(FRESH), "recorded, and not readable back"
    assert unsubscribe.is_unsubscribed(KNOWN)
    assert path.read_text().splitlines()[-1].startswith('{"ts"'), "glued to another line"


# --- POST /api/unsubscribe, against a real server ---------------------------

def _one_click(base: str, email: str):
    status, body, _headers = _srv.request(
        base, "/api/unsubscribe?e=" + quote(email), method="POST",
        body=b"List-Unsubscribe=One-Click", headers=ONE_CLICK, timeout=15)
    return status, body


def _button(base: str, email: str):
    status, body, _headers = _srv.request(
        base, "/api/unsubscribe?e=" + quote(email), method="POST",
        body=b"via=page", headers=ONE_CLICK, timeout=15)
    return status, body


def test_a_second_unsubscribe_is_answered_like_the_first(tmp_path):
    """Control: on a healthy ledger nothing a caller sees has changed, and
    asking again, as mailbox providers do, adds nothing to the ledger."""
    email = "twice@example.test"
    ledger = tmp_path / "suppressions.jsonl"
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        first = _one_click(base, email)
        recorded = ledger.read_bytes()
        second = _one_click(base, email)
        assert first == second
        assert second[0] == 200 and json.loads(second[1]) == {"ok": True}
        pressed, again = _button(base, email), _button(base, email)
        assert pressed == again
        assert again[0] == 200 and b"Done" in again[1]
        for _ in range(12):
            assert _one_click(base, email) == first
        assert _rows(ledger).count(email) == 1
        assert ledger.read_bytes() == recorded
        status, page, _headers = _srv.request(
            base, "/api/unsubscribe?e=" + quote(email), timeout=15)
        assert status == 200 and b'<form method="post"' in page, "the confirm page"


def _capped(tmp_path: Path) -> dict:
    """Environment for a server that caps its own file size (RLIMIT_FSIZE).

    Python imports `sitecustomize` from PYTHONPATH before it runs anything
    else, so the cap is the server's alone and this process keeps its own.
    The server writes no bytecode under it: a .pyc longer than the cap is cut
    short there without an error, and the next import of that module fails."""
    hook = tmp_path / "cap"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        "import resource\n"
        "_soft, _hard = resource.getrlimit(resource.RLIMIT_FSIZE)\n"
        f"resource.setrlimit(resource.RLIMIT_FSIZE, ({CAP}, _hard))\n")
    path = os.pathsep.join(p for p in (str(hook), os.environ.get("PYTHONPATH", "")) if p)
    return {"PYTHONPATH": path, "PYTHONDONTWRITEBYTECODE": "1"}


def _ledger_with_room_for(data_dir: Path, room: int) -> Path:
    """A ledger holding KNOWN that the capped server can grow by `room`."""
    ledger = data_dir / "suppressions.jsonl"
    row = json.dumps({"ts": "2026-09-27T00:00:00+00:00", "email": KNOWN,
                      "source": "test"}, separators=(",", ":")) + "\n"
    ledger.write_text(row + "\n" * (CAP - room - len(row)))
    assert ledger.stat().st_size == CAP - room
    return ledger


@pytest.mark.parametrize("room", [0, 8], ids=["no-room", "room-for-a-byte"])
def test_a_ledger_that_cannot_take_a_row_answers_both_addresses_alike(tmp_path, room):
    """THE DEFECT, through the door. The server may not grow a file past CAP
    and the ledger is that long, or 8 bytes short of it, so it opens for
    appending and a row written to it fails, as on a full volume. The known
    address asks first: whatever room there is, is there when it asks."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    ledger = _ledger_with_room_for(data_dir, room)
    before = ledger.read_bytes()
    for base in _srv.server_processes(data_dir, stub_calendars=True, **_capped(tmp_path)):
        status, _page, _headers = _srv.request(
            base, "/api/unsubscribe?e=" + quote(KNOWN), timeout=15)
        assert status == 200, "control: the server is otherwise working"
        known, fresh = _one_click(base, KNOWN), _one_click(base, FRESH)
        assert fresh[0] == 503, "control: this ledger cannot take a row"
        assert known == fresh
        known_page, fresh_page = _button(base, KNOWN), _button(base, FRESH)
        assert fresh_page[0] == 503
        assert known_page == fresh_page
    after = ledger.read_bytes()
    assert after.startswith(before) and len(after) <= CAP
    assert FRESH.encode() not in after, "nothing may have been recorded"
