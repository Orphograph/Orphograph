"""Repo-root conftest: the no-green-by-skip gate for EVERY suite.

The deploy gate runs `pytest tests/ capture/ tools/test_gate_read.py
zk-provenance/test_zk_provenance.py` and CI runs `sdk-python/tests/` in a
second interpreter. A gate that lives in tests/conftest.py covers one of
those trees. This file, with the empty pytest.ini beside it that pins the
rootdir here, covers all of them.
"""
# --- no green-by-skip -------------------------------------------------------
# A skipped test is not a passed test. 21 tests in this suite skipped in CI for
# weeks (missing local receipt, snarkjs not installed) and the gate stayed
# green. Every skip must now match a budgeted reason, or the session fails.
# Local runs without the tooling can opt out: PYTEST_ALLOW_SKIPS=1. (Harness
# knob, deliberately outside the ORPHO_* product namespace that
# test_no_phantom_env_knobs.py polices — nothing shipped reads it.)
import os as _os
import re as _re

ALLOWED_SKIP_REASONS = (
    # (regex, why it is acceptable) — keep this list short and justified.
)
_SKIPS: list = []


def pytest_runtest_logreport(report):
    if report.skipped and report.when in ("setup", "call"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else str(report.longrepr)
        _SKIPS.append((report.nodeid, reason))


def pytest_sessionfinish(session, exitstatus):
    if _os.environ.get("PYTEST_ALLOW_SKIPS") == "1":
        return
    unbudgeted = [
        (nid, reason) for nid, reason in _SKIPS
        if not any(_re.search(rx, reason) for rx, _why in ALLOWED_SKIP_REASONS)
    ]
    if unbudgeted:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        if tr:
            tr.write_line("")
            tr.write_line(f"GREEN-BY-SKIP: {len(unbudgeted)} skip(s) outside the budget "
                          "(set PYTEST_ALLOW_SKIPS=1 for local runs without the tooling):", red=True)
            for nid, reason in unbudgeted:
                tr.write_line(f"  {nid}: {reason}", red=True)
        session.exitstatus = 1
