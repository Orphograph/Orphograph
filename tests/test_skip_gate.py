"""Drive the repo-root skip gate (conftest.py) through a real pytest
subprocess. A gate that has only been observed staying quiet is
indistinguishable from one that cannot fire, so each case below is a
separate pytest run on a probe file created inside the repo tree (the
root conftest only loads for paths under the rootdir) and deleted after."""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MODULE_SKIP = "import pytest\npytest.importorskip('no_such_module_zz')\ndef test_a():\n    pass\n"
CALL_SKIP = "import pytest\ndef test_a():\n    pytest.skip('probe')\n"
PASSING = "def test_a():\n    pass\n"


def _run(source: str, env_extra: dict | None = None):
    probe = ROOT / "tests" / f"_gate_probe_{uuid.uuid4().hex[:8]}"
    probe.mkdir()
    try:
        (probe / "test_zz_probe.py").write_text(source)
        env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        env.pop("PYTEST_ALLOW_SKIPS", None)
        env.update(env_extra or {})
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
            capture_output=True, text=True, timeout=120, cwd=ROOT, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_module_level_skip_fails_the_session():
    rc, out = _run(MODULE_SKIP)
    assert rc == 1 and "GREEN-BY-SKIP" in out, out


def test_call_level_skip_fails_the_session():
    rc, out = _run(CALL_SKIP)
    assert rc == 1 and "GREEN-BY-SKIP" in out, out


def test_waiver_env_lets_a_local_run_pass():
    rc, out = _run(CALL_SKIP, {"PYTEST_ALLOW_SKIPS": "1"})
    assert rc == 0 and "GREEN-BY-SKIP" not in out, out


def test_a_passing_run_is_untouched():
    rc, out = _run(PASSING)
    assert rc == 0 and "GREEN-BY-SKIP" not in out, out
