"""Repo-root conftest: the no-green-by-skip gate for EVERY suite.

The deploy gate runs `pytest tests/ capture/ tools/test_gate_read.py
zk-provenance/test_zk_provenance.py` and CI runs `sdk-python/tests/` in a
second interpreter. A gate that lives in tests/conftest.py covers one of
those trees. This file, with the empty pytest.ini beside it that pins the
rootdir here, covers all of them.

A skipped test is not a passed test. 21 tests skipped in CI for weeks
(missing local receipt, snarkjs not installed) and the gate stayed green.
Any skip — call-level, marker, OR module/collection-level (importorskip,
pytest.skip(allow_module_level=True)) — fails the session. Local runs
without the tooling can opt out with PYTEST_ALLOW_SKIPS=1 (a harness knob,
deliberately outside the ORPHO_* product namespace that
tests/test_no_phantom_env_knobs.py polices). tests/test_skip_gate.py drives
this file through a real pytest subprocess for each of those cases.

Three more protections live here for the same reason (one file, every
suite): the data directory defaults to a throwaway one, a test that leaves
that setting unset or pointing into the checkout fails, and a run that leaves
a file created, changed or removed inside the checkout fails.
tests/test_checkout_write_guard.py drives all three.
"""
import atexit as _atexit
import os as _os
import pathlib as _pl
import shutil as _shutil
import sys as _sys
import tempfile as _tempfile

# --- in-process tests keep their ledgers out of the checkout -------------------
# Server modules work out their ledger paths from ORPHO_DATA_DIR when they are
# first imported, and without it they fall back to <repo>/data (or the repo
# root). In the founder's checkout that is the real data directory, and test
# rows were appended to its session, refund and fulfillment ledgers. This runs
# before any test module is imported, so the first import of a server module
# already sees a throwaway directory. A value inherited from the shell is
# replaced too: it would be the real directory. A test that gives its own
# server subprocess a data directory passes it in that process's environment,
# which this does not touch.
_DATA_DIR = _tempfile.mkdtemp(prefix="orpho-test-data-")
_os.environ["ORPHO_DATA_DIR"] = _DATA_DIR
_atexit.register(_shutil.rmtree, _DATA_DIR, ignore_errors=True)

import pytest as _pytest  # noqa: E402

_SKIPS: list = []

# sdk/orphograph (tests/test_sdk.py) and sdk-python/orphograph share the
# import name `orphograph`, so one process can only hold one of them: with
# sdk-python/tests first, test_sdk.py fails 10 tests on a missing Client;
# with test_sdk.py first, sdk-python/tests hits 2 collection errors. CI
# runs them in two interpreters (scripts/run_gate_tests.sh). A run that
# would collect both stops here with one honest error instead.
_ROOT = _pl.Path(__file__).resolve().parent
_SDK_PAIR = (_ROOT / "tests" / "test_sdk.py", _ROOT / "sdk-python" / "tests")


def pytest_configure(config):
    inv = _pl.Path(config.invocation_params.dir)
    args = [(inv / a.split("::")[0]).resolve() for a in (config.args or [str(inv)])]
    ignored = [(inv / i).resolve() for i in (config.getoption("ignore") or [])]

    def _in_scope(target):
        if any(target == ig or ig in target.parents for ig in ignored):
            return False
        return any(target == a or a in target.parents for a in args)

    if all(_in_scope(t) for t in _SDK_PAIR):
        raise _pytest.UsageError(
            "tests/test_sdk.py and sdk-python/tests both import a package named "
            "`orphograph` and cannot share one pytest process. Run "
            "scripts/run_gate_tests.sh, or pass --ignore=sdk-python "
            "(or --ignore=tests/test_sdk.py)."
        )


def _reason(report) -> str:
    lr = report.longrepr
    if isinstance(lr, tuple) and len(lr) == 3:
        return str(lr[2])
    return str(lr)


def pytest_runtest_logreport(report):
    if report.skipped and report.when in ("setup", "call"):
        _SKIPS.append((report.nodeid, _reason(report)))


def pytest_collectreport(report):
    # importorskip / module-level skip never produce a TestReport; they
    # arrive here as a skipped CollectReport and used to bypass the gate.
    if report.skipped:
        _SKIPS.append((getattr(report, "nodeid", "<collect>"), _reason(report)))


def _inside_checkout(path: str) -> bool:
    there = _pl.Path(path).resolve()
    return there == _ROOT or _ROOT in there.parents


@_pytest.fixture(autouse=True)
def _the_data_dir_stays_set(request):
    """The default above only holds while the variable does. A teardown that
    pops it, instead of putting back what it found, sends every server module
    imported afterwards to the checkout again, and the file that turns up
    there names a later test. So the test that did it fails, by name, and the
    default is put back for the ones after it."""
    yield
    left = _os.environ.get("ORPHO_DATA_DIR")
    if left and not _inside_checkout(left):
        return
    _os.environ["ORPHO_DATA_DIR"] = _DATA_DIR
    _pytest.fail(
        f"{request.node.nodeid} left ORPHO_DATA_DIR "
        + (f"pointing into the checkout ({left})" if left else "unset")
        + ": restore the value the test found (monkeypatch.setenv does).",
        pytrace=False)


# --- the run writes nothing into the checkout ---------------------------------
# Every file under the repo root is recorded as (size, mtime_ns) before
# collection, because importing a test module can already write, and again
# when the session ends. A file that was created, changed or removed in
# between fails the run and is named. Caches the interpreter and pytest keep
# are not counted, nor is .git (a directory in a checkout, a file in a
# worktree). A file a test creates and deletes again leaves no difference.
_NOT_COUNTED_DIRS = frozenset({".git", "node_modules", "__pycache__", ".pytest_cache", ".claude"})
_NOT_COUNTED_FILES = frozenset({".git", ".coverage"})
_BEFORE: dict | None = None


def _checkout_files() -> dict:
    seen = {}
    for folder, dirs, files in _os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in _NOT_COUNTED_DIRS]
        for name in files:
            if name in _NOT_COUNTED_FILES or name.endswith(".pyc"):
                continue
            path = _os.path.join(folder, name)
            try:
                st = _os.lstat(path)
            except OSError:
                continue  # removed between the listing and the stat
            seen[_os.path.relpath(path, _ROOT)] = (st.st_size, st.st_mtime_ns)
    return seen


def _checkout_changes() -> list:
    if _BEFORE is None:
        return []
    after = _checkout_files()
    changes = []
    for path in sorted(set(_BEFORE) | set(after)):
        if path not in _BEFORE:
            changes.append(("created", path))
        elif path not in after:
            changes.append(("removed", path))
        elif _BEFORE[path] != after[path]:
            changes.append(("changed", path))
    return changes


def pytest_sessionstart(session):
    global _BEFORE
    _BEFORE = _checkout_files()


def pytest_sessionfinish(session, exitstatus):
    tr = session.config.pluginmanager.get_plugin("terminalreporter")

    def say(line):
        if tr:
            tr.write_line(line, red=True)
        else:
            print(line, file=_sys.stderr)

    skips = [] if _os.environ.get("PYTEST_ALLOW_SKIPS") == "1" else _SKIPS
    if skips:
        say("")
        say(f"GREEN-BY-SKIP: {len(skips)} skip(s) "
            "(set PYTEST_ALLOW_SKIPS=1 for local runs without the tooling):")
        for nid, reason in skips:
            say(f"  {nid}: {reason}")
    changes = _checkout_changes()
    if changes:
        say("")
        say(f"WROTE INTO THE CHECKOUT: {len(changes)} file(s) differ from when the run "
            "started (tests keep their files under tmp_path):")
        for what, path in changes:
            say(f"  {what}  {path}")
    if skips or changes:
        session.exitstatus = 1
