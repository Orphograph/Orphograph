#!/usr/bin/env python3
"""office_key.py — make or show the office's Standing Record signing key.

    python3 scripts/office_key.py generate   # make a new key, print its public key
    python3 scripts/office_key.py public     # print the public key of the existing key

The key file holds a raw 32-byte Ed25519 seed. It lives at
$ORPHO_OFFICE_KEY_PATH, default ~/.orphograph/office_signing_key, on the
machine that runs scripts/weekly_anchor.py, and nowhere else. The weekly job
signs each anchor with it; the server lists a new Standing Record row only
when that signature verifies against a public key pinned in
server/standing_record.py (PINNED_OFFICE_KEYS).

Neither command ever prints the seed. `generate` refuses to replace an
existing file: a lost or overwritten key cannot be recovered, and a new one
must be pinned before the page will list anything it signs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import standing_record  # noqa: E402

DEFAULT_KEY_PATH = "~/.orphograph/office_signing_key"


def key_path() -> Path:
    return Path(os.environ.get("ORPHO_OFFICE_KEY_PATH") or DEFAULT_KEY_PATH).expanduser()


def _fail(msg: str) -> int:
    sys.stderr.write(f"office_key: {msg}\n")
    return 1


def generate(path: Path) -> int:
    parent = path.parent
    if not parent.exists():
        parent.mkdir(mode=0o700, parents=True)
    st = parent.stat()
    if st.st_uid != os.getuid():
        return _fail(f"refusing: {parent} belongs to another user")
    if st.st_mode & 0o077:
        # Only the owner should be able to list or enter the key's directory.
        os.chmod(parent, 0o700)
    try:
        # O_EXCL makes the no-overwrite rule atomic, and also refuses a
        # symlink planted at the path.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _fail(f"refusing to overwrite {path}; run 'public' to see its public key")
    seed = os.urandom(32)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(seed)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except OSError as e:
        os.close(fd)
        path.unlink()
        return _fail(f"could not write {path}: {e.strerror}")
    os.close(fd)
    print(standing_record.public_key(seed).hex())
    return 0


def public(path: Path) -> int:
    try:
        seed = path.read_bytes()
    except FileNotFoundError:
        return _fail(f"no key at {path}; run 'generate' first")
    except OSError as e:
        return _fail(f"cannot read {path}: {e.strerror}")
    if len(seed) != 32:
        return _fail(f"{path} is not a 32-byte key file")
    print(standing_record.public_key(seed).hex())
    return 0


def main(argv: list) -> int:
    if len(argv) != 1 or argv[0] not in ("generate", "public"):
        return _fail("usage: office_key.py generate | public")
    return (generate if argv[0] == "generate" else public)(key_path())


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
