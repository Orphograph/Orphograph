#!/usr/bin/env python3
"""file_lock.py — advisory cross-process file locking via fcntl.

Linux + macOS use fcntl (always available). Windows would need msvcrt
which we don't support — fly machines are Linux, dev is macOS, fine.

Used by credits.py and stripe_webhook.py to serialize ledger writes
across multiple fly machines / processes that share a mounted volume.

Usage:
    with locked(path, exclusive=True) as f:
        f.write(...)
"""
from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from typing import IO, Iterator


@contextlib.contextmanager
def locked(path: Path, *, mode: str = "a", exclusive: bool = True) -> Iterator[IO]:
    """Open path in `mode` and hold an fcntl lock for the duration of the with-block.

    The lock is released on file close (Python's `with open(...)` semantics)
    even if the body raises. Exclusive (write) by default; pass exclusive=False
    for a shared (read) lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    f = open(path, mode)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield f
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        f.close()
