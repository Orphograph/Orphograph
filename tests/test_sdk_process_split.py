"""test_sdk_process_split.py — the two `orphograph` SDKs never share a process.

sdk/orphograph and sdk-python/orphograph have the same import name. A single
pytest run over both used to report 10 AttributeErrors (or 2 collection
errors, depending on order) that look like broken SDK code and are not. The
root conftest now refuses that invocation up front. These tests drive a real
collect-only pytest subprocess, so they exercise the hook the way a person
typing `pytest` at the repo root does.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _collect(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def test_collecting_both_sdks_in_one_process_is_refused() -> None:
    out = _collect("sdk-python/tests", "tests/test_sdk.py")
    assert out.returncode == 4, (out.returncode, out.stdout[-400:], out.stderr[-400:])
    assert "run_gate_tests.sh" in out.stderr + out.stdout


def test_each_sdk_suite_still_collects_on_its_own() -> None:
    for target in ("tests/test_sdk.py", "sdk-python/tests"):
        out = _collect(target)
        assert out.returncode == 0, (target, out.stdout[-400:], out.stderr[-400:])


def test_ignoring_one_side_is_allowed() -> None:
    out = _collect("sdk-python/tests", "tests/test_sdk.py", "--ignore=sdk-python")
    assert out.returncode == 0, (out.stdout[-400:], out.stderr[-400:])
