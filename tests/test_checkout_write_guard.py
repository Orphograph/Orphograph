"""Drive the repo-root conftest's checkout protections through a real pytest
subprocess, the way tests/test_skip_gate.py drives the skip gate.

In the founder's checkout <repo>/data holds real ledgers, and in-process
tests appended rows to them: server modules work out their ledger paths from
ORPHO_DATA_DIR when first imported and fall back to the checkout without it.
Three protections, all in the root conftest so they cover every suite:

  * the data directory defaults to a throwaway one for the whole session;
  * a test that leaves that setting unset, or pointing into the checkout,
    fails by name, and the default is put back for the tests after it;
  * a run that leaves a file created, changed or removed inside the checkout
    fails, and names the file.

Each case is a separate pytest run on a probe directory created inside the
repo tree (the root conftest only loads for paths under the rootdir) and
deleted after. Every file a probe writes is inside its own probe directory.
"""
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUARD = "WROTE INTO THE CHECKOUT"

LEAVES_A_FILE = (
    "from pathlib import Path\n"
    "def test_a():\n"
    "    Path(__file__).with_name('left_behind.txt').write_text('x')\n"
)
# Test modules are imported during collection, before any test runs.
WRITES_AT_IMPORT = (
    "from pathlib import Path\n"
    "Path(__file__).with_name('at_import.txt').write_text('x')\n"
    "def test_a():\n"
    "    pass\n"
)
# The same bytes written back: size and content are unchanged, only the
# modification time moves.
REWRITES_A_FILE = (
    "from pathlib import Path\n"
    "def test_a():\n"
    "    p = Path(__file__).with_name('existing.txt')\n"
    "    p.write_text(p.read_text())\n"
)
REMOVES_A_FILE = (
    "from pathlib import Path\n"
    "def test_a():\n"
    "    Path(__file__).with_name('existing.txt').unlink()\n"
)
WRITES_TO_TMP_PATH = (
    "def test_a(tmp_path):\n"
    "    (tmp_path / 'scratch.txt').write_text('x')\n"
)
# One file per thing the guard leaves out, each covered by that one rule and
# no other: with a .pyc inside __pycache__ the directory rule could be deleted
# and this would still pass.
NOT_COUNTED = ("__pycache__/kept_by_the_interpreter", "stray.pyc", ".coverage",
               ".pytest_cache/v/x", "node_modules/pkg/index.js",
               "a/b/node_modules/pkg/index.js", ".claude/local.json", ".git/x")
WRITES_ONLY_CACHES = (
    "from pathlib import Path\n"
    "def test_a():\n"
    "    here = Path(__file__).parent\n"
    f"    for name in {NOT_COUNTED!r}:\n"
    "        (here / name).parent.mkdir(parents=True, exist_ok=True)\n"
    "        (here / name).write_text('x')\n"
)
# What an in-process test does: import a server module and call it. The
# session row is written wherever auth worked out its ledger lives.
WRITES_A_SESSION_ROW = (
    "import sys\n"
    "from pathlib import Path\n"
    "ROOT = Path(__file__).resolve().parents[2]\n"
    "sys.path.insert(0, str(ROOT / 'server'))\n"
    "def test_a():\n"
    "    import auth\n"
    "    auth.create_session('probe@example.test')\n"
    "    where = auth.SESSION_LEDGER.resolve()\n"
    "    assert 'probe@example.test' in where.read_text(), where\n"
    "    assert ROOT not in where.parents, f'session row written into the checkout: {where}'\n"
)
# A teardown that pops the setting instead of putting back what it found
# (tests/test_upgrade_email.py did). Every server module first imported after
# it falls back to the checkout again.
TAKES_THE_SETTING_AWAY = (
    "import os\n"
    "def test_a():\n"
    "    os.environ.pop('ORPHO_DATA_DIR', None)\n"
    "def test_b():\n"
    "    assert os.environ.get('ORPHO_DATA_DIR')\n"
)
POINTS_THE_SETTING_AT_THE_CHECKOUT = (
    "import os\n"
    "from pathlib import Path\n"
    "def test_a():\n"
    "    os.environ['ORPHO_DATA_DIR'] = str(Path(__file__).parent)\n"
    "def test_b():\n"
    "    assert Path(__file__).parent != Path(os.environ['ORPHO_DATA_DIR'])\n"
)
# What most tests with a data directory of their own do: set it, put it back.
SETS_AND_RESTORES_THE_SETTING = (
    "import os\n"
    "def test_a(tmp_path, monkeypatch):\n"
    "    monkeypatch.setenv('ORPHO_DATA_DIR', str(tmp_path))\n"
    "def test_b(monkeypatch):\n"
    "    monkeypatch.delenv('ORPHO_DATA_DIR')\n"
)
SETTING = "left ORPHO_DATA_DIR"


def _run(source: str, *, existing: bool = False, data_dir_in_probe: bool = False):
    """(returncode, output, probe directory name) of one pytest run."""
    probe = ROOT / "tests" / f"_write_probe_{uuid.uuid4().hex[:8]}"
    probe.mkdir()
    try:
        (probe / "test_zz_probe.py").write_text(source)
        if existing:
            (probe / "existing.txt").write_text("already here\n")
        env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
        env.pop("PYTEST_ALLOW_SKIPS", None)
        env.pop("ORPHO_DATA_DIR", None)
        env.pop("ORPHO_AUTH_SESSIONS", None)
        if data_dir_in_probe:
            # A shell that exports the real data directory, in miniature.
            (probe / "inherited_data").mkdir()
            env["ORPHO_DATA_DIR"] = str(probe / "inherited_data")
        proc = subprocess.run(
            # No colour: a caller's FORCE_COLOR would split "2 passed, 1 error".
            [sys.executable, "-m", "pytest", "-q", "--color=no", "-p", "no:cacheprovider",
             str(probe)],
            capture_output=True, text=True, timeout=120, cwd=ROOT, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr, probe.name
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_a_file_left_in_the_checkout_fails_the_session():
    rc, out, probe = _run(LEAVES_A_FILE)
    assert rc == 1 and GUARD in out, out
    assert f"tests/{probe}/left_behind.txt" in out, out
    assert "1 passed" in out, out  # the test itself passed; the guard failed the run


def test_a_file_written_while_collecting_is_seen_too():
    rc, out, probe = _run(WRITES_AT_IMPORT)
    assert rc == 1 and GUARD in out, out
    assert f"tests/{probe}/at_import.txt" in out, out


def test_a_file_written_back_unchanged_fails_the_session():
    rc, out, probe = _run(REWRITES_A_FILE, existing=True)
    assert rc == 1 and GUARD in out, out
    assert f"tests/{probe}/existing.txt" in out, out


def test_a_removed_file_fails_the_session():
    rc, out, probe = _run(REMOVES_A_FILE, existing=True)
    assert rc == 1 and GUARD in out, out
    assert f"tests/{probe}/existing.txt" in out, out


def test_writing_to_tmp_path_is_untouched():
    rc, out, _ = _run(WRITES_TO_TMP_PATH, existing=True)
    assert rc == 0 and GUARD not in out, out


def test_caches_are_not_counted():
    rc, out, _ = _run(WRITES_ONLY_CACHES)
    assert rc == 0 and GUARD not in out, out


def test_in_process_ledgers_default_to_a_throwaway_directory():
    rc, out, _ = _run(WRITES_A_SESSION_ROW)
    assert rc == 0 and GUARD not in out, out


def test_a_data_directory_inherited_from_the_shell_is_not_used():
    rc, out, _ = _run(WRITES_A_SESSION_ROW, data_dir_in_probe=True)
    assert rc == 0 and GUARD not in out, out


def test_a_test_that_takes_the_setting_away_is_named_and_the_next_one_is_safe():
    rc, out, _ = _run(TAKES_THE_SETTING_AWAY)
    assert rc == 1 and SETTING in out, out
    assert "test_zz_probe.py::test_a" in out, out
    assert "test_zz_probe.py::test_b" not in out, out  # it still had a directory
    assert "2 passed, 1 error" in out, out


def test_a_test_that_leaves_the_setting_inside_the_checkout_is_named():
    rc, out, _ = _run(POINTS_THE_SETTING_AT_THE_CHECKOUT)
    assert rc == 1 and SETTING in out, out
    assert "test_zz_probe.py::test_a" in out, out
    assert "2 passed, 1 error" in out, out


def test_a_test_that_puts_the_setting_back_is_untouched():
    rc, out, _ = _run(SETS_AND_RESTORES_THE_SETTING)
    assert rc == 0 and SETTING not in out, out
    assert "2 passed" in out, out
