"""A row written inside file_lock.locked() is on disk before the lock is let
go (bundle review round 1). The lock was released in `finally` BEFORE the
file was closed, and closing is where a buffered row is written: a second
writer could take the lock, see no trailing row, and append before it."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import file_lock  # noqa: E402


def _size_at_unlock(monkeypatch, path: Path, opener):
    seen = []
    real = file_lock.fcntl.flock

    def spy(fd, op):
        if op == file_lock.fcntl.LOCK_UN:
            seen.append(path.stat().st_size if path.exists() else -1)
        return real(fd, op)

    monkeypatch.setattr(file_lock.fcntl, "flock", spy)
    with opener(path) as f:
        f.write("one row\n")
    return seen


def test_locked_flushes_before_it_unlocks(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    assert _size_at_unlock(monkeypatch, path, lambda p: file_lock.locked(p, mode="a")) == [len("one row\n")]


def test_try_locked_flushes_before_it_unlocks(tmp_path, monkeypatch):
    path = tmp_path / "ledger.jsonl"
    assert _size_at_unlock(monkeypatch, path, lambda p: file_lock.try_locked(p, mode="a")) == [len("one row\n")]
