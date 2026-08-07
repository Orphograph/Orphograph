#!/usr/bin/env python3
"""build_verifier_dist.py — regenerate the downloadable offline-verifier bundles.

The repo ships three committed distributables that users download to verify
receipts offline. They are BUILT ARTIFACTS: their contents must always be
byte-identical to the tracked sources, or a fix to the sources silently
never reaches downloaders (exactly what happened with AUDIT_VERIFIER_DRIFT
D1/D2 — the served /verify-js page was fixed while the zips kept shipping
the pre-fix verifier).

  dist/orphograph-verify.zip           built from dist/orphograph-verify/
  web/dist/orphograph-verify.zip       byte-identical copy of the above;
                                       served at /dist/orphograph-verify.zip
  web/verify/orphograph-verify-0.1.tar.gz
                                       built from web/verify/ sources;
                                       served at /verify/orphograph-verify-0.1.tar.gz

Pure stdlib (zipfile/tarfile). Deterministic: fixed timestamp, sorted
member order, no uid/gid — rebuilding from unchanged sources reproduces
the same archives bit-for-bit, so `git status` stays clean.

Run after ANY change to dist/orphograph-verify/ or web/verify/ sources:

    python3 scripts/build_verifier_dist.py

tests/test_dist_verifier.py contains sync guards that fail the suite when
the committed archives drift from the sources.
"""
from __future__ import annotations

import gzip
import io
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST_SRC = ROOT / "dist" / "orphograph-verify"
ZIP_PATH = ROOT / "dist" / "orphograph-verify.zip"
WEB_ZIP_PATH = ROOT / "web" / "dist" / "orphograph-verify.zip"
TAR_SRC = ROOT / "web" / "verify"
TAR_PATH = TAR_SRC / "orphograph-verify-0.1.tar.gz"

# Fixed timestamp for reproducible archives (date of the D1/D2 dist fix).
ZIP_DATE = (2026, 7, 21, 0, 0, 0)
TAR_MTIME = 1784952000  # 2026-07-21T00:00:00-04:00

# (source file under dist/orphograph-verify/, archive member name)
ZIP_MEMBERS: list[tuple[str, str]] = [
    ("LICENSE", "LICENSE.txt"),
    ("QUICKSTART.txt", "QUICKSTART.txt"),
    ("README.md", "README.md"),
    ("merkle.py", "merkle.py"),
    # verify.py imports otscheck at module scope, so omitting it would ship a
    # bundle that dies on `import otscheck` before doing any work.
    ("otscheck.py", "otscheck.py"),
    ("verify.py", "verify.py"),
]

# Files/dirs under web/verify/ that go into the tarball (archive-root relative).
TAR_MEMBERS: list[str] = [
    "verify.py",
    "README.md",
    "LICENSE",
    "examples",
]


def build_zip() -> None:
    missing = [src for src, _ in ZIP_MEMBERS if not (DIST_SRC / src).is_file()]
    if missing:
        raise FileNotFoundError(f"missing zip sources: {missing}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for src, arcname in ZIP_MEMBERS:
            zi = zipfile.ZipInfo(arcname, date_time=ZIP_DATE)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zf.writestr(zi, (DIST_SRC / src).read_bytes())
    ZIP_PATH.write_bytes(buf.getvalue())
    WEB_ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ZIP_PATH, WEB_ZIP_PATH)


def _tar_filter(ti: tarfile.TarInfo) -> tarfile.TarInfo:
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    ti.mtime = TAR_MTIME
    ti.mode = 0o755 if ti.isdir() else 0o644
    return ti


def build_tarball() -> None:
    missing = [m for m in TAR_MEMBERS if not (TAR_SRC / m).exists()]
    if missing:
        raise FileNotFoundError(f"missing tarball sources: {missing}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for member in TAR_MEMBERS:
            src = TAR_SRC / member
            if src.is_dir():
                paths = sorted(p for p in src.rglob("*"))
                tf.add(src, arcname=member, recursive=False, filter=_tar_filter)
                for p in paths:
                    tf.add(p, arcname=str(Path(member) / p.relative_to(src)),
                           recursive=False, filter=_tar_filter)
            else:
                tf.add(src, arcname=member, recursive=False, filter=_tar_filter)
    # gzip with mtime=0 and no filename for byte-reproducibility.
    gz = io.BytesIO()
    with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as gzf:
        gzf.write(buf.getvalue())
    TAR_PATH.write_bytes(gz.getvalue())


def main() -> int:
    try:
        build_zip()
        build_tarball()
    except Exception as exc:  # noqa: BLE001 — top-level reporting
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    for path in (ZIP_PATH, WEB_ZIP_PATH, TAR_PATH):
        print(f"Built: {path.relative_to(ROOT)}  ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
