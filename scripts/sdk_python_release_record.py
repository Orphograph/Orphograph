#!/usr/bin/env python3
"""sdk_python_release_record.py — pin what PyPI actually ships for `orphograph`.

Found 2026-11-06: PyPI's orphograph 0.1.2 differed from sdk-python@master in
3 of 5 modules. Master was NEWER (the redirect refusal that keeps
X-Orpho-Api-Key from following a 3xx to another host, the calendars_ok 0
verdict, the broken-answer exit 2) but still said 0.1.1, so `pip install
orphograph` users missed those fixes, and the next upload would have carried
a version number below the one already live. Nothing compared the tree to
the published artifact.

sdk-python/RELEASED.json records the last PUBLISHED release: its version,
the sha256 of the sdist PyPI serves, and the sha256 of every file inside the
`orphograph/` package directory of that sdist.
sdk-python/tests/test_release_currency.py then requires:

  * the tree's version is never below the released one, and
  * if any package file differs from the released bytes (or a file was
    added or removed), the tree's version is strictly above the released one.

Roll the record AFTER an upload, from the sdist PyPI serves (download it;
do not record the local dist/ build — the point is to pin what users get):

    python3 scripts/sdk_python_release_record.py --sdist orphograph-X.Y.Z.tar.gz
    python3 scripts/sdk_python_release_record.py --check     # tree vs record

The sdist is read in memory; nothing is extracted to disk.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SDK = REPO / "sdk-python"
RECORD = SDK / "RELEASED.json"
PACKAGE = "orphograph"

_SDIST_NAME = re.compile(r"^orphograph-(\d+\.\d+\.\d+)\.tar\.gz$")


def version_tuple(v: str) -> tuple[int, ...]:
    """'0.1.3' -> (0, 1, 3). Only plain X.Y.Z is accepted: the SDK has never
    published a pre-release, and a comparison that guesses is worse than one
    that refuses."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", v or ""):
        raise ValueError(f"not an X.Y.Z version: {v!r}")
    return tuple(int(p) for p in v.split("."))


def tree_version(sdk: Path = SDK) -> str:
    text = (sdk / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    if not m:
        raise ValueError("sdk-python/pyproject.toml has no version line")
    return m.group(1)


def tree_files(sdk: Path = SDK) -> dict[str, str]:
    """sha256 of every file under sdk-python/orphograph/, keyed
    'orphograph/<relative path>'. Bytecode caches are not package files."""
    out: dict[str, str] = {}
    pkg = sdk / PACKAGE
    for p in sorted(pkg.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        if p.name.startswith("."):  # .DS_Store and friends are not shipped
            continue
        rel = p.relative_to(sdk).as_posix()
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def record_from_sdist(sdist_path: Path) -> dict:
    """Build the record from a published sdist, in memory."""
    name = sdist_path.name
    m = _SDIST_NAME.match(name)
    if not m:
        raise ValueError(f"not an orphograph sdist file name: {name}")
    version = m.group(1)
    data = sdist_path.read_bytes()
    prefix = f"orphograph-{version}/{PACKAGE}/"
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            rel = member.name[len(f"orphograph-{version}/"):]
            if "__pycache__" in rel.split("/") or rel.endswith(".pyc"):
                continue
            fh = tar.extractfile(member)
            if fh is None:
                continue
            files[rel] = hashlib.sha256(fh.read()).hexdigest()
    if not files:
        raise ValueError(f"{name} has no {PACKAGE}/ package files")
    return {
        "version": version,
        "sdist": name,
        "sdist_sha256": hashlib.sha256(data).hexdigest(),
        "files": dict(sorted(files.items())),
    }


def load_record(path: Path = RECORD) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def problems(record: dict, version: str, files: dict[str, str]) -> list[str]:
    """Empty list = the tree is current with respect to the released record."""
    out: list[str] = []
    released = record["version"]
    local_t, released_t = version_tuple(version), version_tuple(released)
    if local_t < released_t:
        out.append(f"tree version {version} is below the released {released}: "
                   "the next upload would publish an older number than PyPI's")
    changed = sorted(
        set(files) ^ set(record["files"])
        | {k for k in files if k in record["files"] and files[k] != record["files"][k]}
    )
    if changed and local_t <= released_t:
        out.append(f"package files differ from released {released} but the tree "
                   f"still says {version}: bump the version (pyproject.toml, "
                   f"orphograph/__init__.py, _client.USER_AGENT). Changed: "
                   + ", ".join(changed))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sdist", type=Path, help="published sdist to record")
    g.add_argument("--check", action="store_true", help="compare the tree to the record")
    args = ap.parse_args(argv)
    if args.sdist:
        rec = record_from_sdist(args.sdist)
        RECORD.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        print(f"recorded {rec['sdist']} ({len(rec['files'])} package files) -> {RECORD}")
        return 0
    found = problems(load_record(), tree_version(), tree_files())
    for p in found:
        print("FAIL:", p)
    if not found:
        print("OK: tree is current with the released record")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
