"""Both import styles must work for every server module a consumer touches.

RED PRE-FIX: `import server.engine` from the repo root raised
ModuleNotFoundError on the bare `import ots_timestamp` added with the upgrade
guard (no dual-context fallback like the file's merkle import) — which left
zk-provenance's tests and demo dead while CI over tests/ stayed green, because
tests/ put server/ itself on sys.path first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _import_ok(snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", snippet], cwd=REPO_ROOT,
                          capture_output=True, text=True)


def test_server_modules_import_package_style():
    r = _import_ok("import sys; sys.path.insert(0, '.'); "
                   "import server.engine, server.upgrade_worker, server.ots_timestamp")
    assert r.returncode == 0, r.stderr


def test_server_modules_import_flat_style():
    """Negative control: the style the server itself runs under still works,
    and the re-exported constants stay identical across both homes."""
    r = _import_ok("import sys; sys.path.insert(0, 'server'); "
                   "import engine, upgrade_worker, ots_timestamp; "
                   "assert engine.OTS_HEADER_MAGIC is ots_timestamp.OTS_HEADER_MAGIC; "
                   "assert upgrade_worker.OTS_HEADER_MAGIC is ots_timestamp.OTS_HEADER_MAGIC")
    assert r.returncode == 0, r.stderr
