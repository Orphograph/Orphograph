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
"""
import os as _os

_SKIPS: list = []


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


def pytest_sessionfinish(session, exitstatus):
    if _os.environ.get("PYTEST_ALLOW_SKIPS") == "1" or not _SKIPS:
        return
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    if tr:
        tr.write_line("")
        tr.write_line(f"GREEN-BY-SKIP: {len(_SKIPS)} skip(s) "
                      "(set PYTEST_ALLOW_SKIPS=1 for local runs without the tooling):", red=True)
        for nid, reason in _SKIPS:
            tr.write_line(f"  {nid}: {reason}", red=True)
    session.exitstatus = 1
