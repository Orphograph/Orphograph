"""test_dependency_audit_workflow.py — the dependency audit covers what CI installs.

.github/workflows/dependency-audit.yml lists the Python packages and npm
lockfiles it audits. Those lists are copies, so they drift: a new
`pip install foo` in test.yml or a new package-lock.json would go unscanned
while the audit stayed green. These tests fail when that happens.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
AUDIT = WF / "dependency-audit.yml"

# Installed to run the audit or to manage pip itself, not dependencies of the code.
TOOLING = {"pip", "pip-audit", "--upgrade"}


def _audit() -> dict:
    return yaml.safe_load(AUDIT.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    return doc.get(True, doc.get("on", {}))


def _run_text(doc: dict) -> str:
    return "\n".join(s.get("run", "") for j in doc["jobs"].values() for s in j.get("steps", []))


def _pip_installed_by(path: Path) -> set[str]:
    names = set()
    for line in re.findall(r"pip install ([^\n&|;]+)", path.read_text(encoding="utf-8")):
        for tok in line.split():
            if tok.startswith("-"):
                continue
            names.add(re.split(r"[=<>\[]", tok)[0].lower())
    return names - TOOLING


def _audited_python(doc: dict) -> set[str]:
    m = re.search(r"printf '([^']+)' > /tmp/ci-reqs.txt", _run_text(doc))
    assert m, "the pip-audit step no longer writes its requirements list"
    return {re.split(r"[=<>\[]", x)[0].lower() for x in m.group(1).split("\\n") if x}


def test_runs_on_prs_master_and_a_schedule() -> None:
    trig = _triggers(_audit())
    for key in ("pull_request", "push", "schedule"):
        assert key in trig, f"dependency audit lost its {key} trigger"
    assert _audit().get("permissions") == {"contents": "read"}


def test_every_python_package_ci_installs_is_audited() -> None:
    audited = _audited_python(_audit())
    for wf in ("test.yml", "deploy.yml"):
        missing = _pip_installed_by(WF / wf) - audited
        assert not missing, f"{wf} installs {sorted(missing)} but the audit does not scan them"


def test_every_tracked_npm_lockfile_is_audited() -> None:
    tracked = subprocess.run(["git", "ls-files", "*package-lock.json"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.split()
    lock_dirs = {str(Path(p).parent) for p in tracked if "node_modules" not in p}
    assert lock_dirs, "found no tracked package-lock.json; the scan would be vacuous"
    matrix = set(_audit()["jobs"]["npm-audit"]["strategy"]["matrix"]["dir"])
    assert lock_dirs <= matrix, f"lockfiles not audited: {sorted(lock_dirs - matrix)}"


def test_drift_checks_can_fire() -> None:
    # Control: the parsers see a real install line and a real omission.
    fake = ROOT / "tests" / "_tmp_fake_wf.yml"
    fake.write_text("run: pip install pytest leftpad==1.0\n", encoding="utf-8")
    try:
        assert _pip_installed_by(fake) == {"pytest", "leftpad"}
    finally:
        fake.unlink()
    assert "leftpad" not in _audited_python(_audit())
