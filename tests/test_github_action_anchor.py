"""The release-anchoring action, run the way a workflow runs it.

integrations/github-action/ had no tests. Its script is driven here as a
process, configured only through the environment variables action.yml sets,
against a real local server.

The published usage pins the action at a moving ref, so a changed default
reaches every workflow on its next run. The defaults a workflow relies on
without naming them are pinned below.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import _srv

ROOT = Path(__file__).resolve().parent.parent
ACTION_DIR = ROOT / "integrations" / "github-action"
SCRIPT = ACTION_DIR / "anchor_artifacts.py"


def run_action(base, workspace, **env_extra):
    # The script's own default base URL is the live service.
    assert base.startswith("http://127.0.0.1:"), base
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("ORPHO_", "GITHUB_"))}
    env.update({
        "ORPHO_BASE_URL": base,
        "GITHUB_OUTPUT": str(workspace / "github_output.txt"),
        "GITHUB_STEP_SUMMARY": str(workspace / "step_summary.md"),
    })
    env.update(env_extra)
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=workspace, env=env,
                          capture_output=True, text=True, timeout=120)


def outputs(workspace):
    text = (workspace / "github_output.txt").read_text()
    return dict(line.split("=", 1) for line in text.splitlines())


def make_workspace(tmp_path, names):
    workspace = tmp_path / "workspace"
    (workspace / "dist").mkdir(parents=True)
    for name in names:
        (workspace / "dist" / name).write_bytes(("bytes of " + name).encode())
    data = tmp_path / "data"
    data.mkdir()
    return workspace, data


def test_receipt_links_point_at_the_server_that_issued_them(tmp_path):
    workspace, data = make_workspace(tmp_path, ["app.tar.gz"])
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        out = run_action(base, workspace)
        assert out.returncode == 0, (out.stdout, out.stderr)

        rows = json.loads((workspace / "orphograph-receipts.json").read_text())
        assert [r["file"] for r in rows] == ["dist/app.tar.gz"]
        row = rows[0]
        assert row["receipt_url"] == base + "/r/" + row["receipt_id"]
        assert json.loads(outputs(workspace)["receipts"]) == rows
        assert row["receipt_url"] in (workspace / "step_summary.md").read_text()

        # The link has to resolve where the receipt lives.
        path = row["receipt_url"][len(base):]
        status, _, _ = _srv.request(base, path)
        assert status == 200


def test_a_trailing_slash_on_the_base_url_does_not_double_up(tmp_path):
    workspace, data = make_workspace(tmp_path, ["app.tar.gz"])
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        out = run_action(base, workspace, ORPHO_BASE_URL=base + "/")
        assert out.returncode == 0, (out.stdout, out.stderr)
        row = json.loads((workspace / "orphograph-receipts.json").read_text())[0]
        assert row["receipt_url"] == base + "/r/" + row["receipt_id"]


def test_incomplete_anchoring_does_not_fail_the_job_unless_asked(tmp_path):
    # One anchor allowed, two files matched: the second is rate limited.
    workspace, data = make_workspace(tmp_path, ["a.bin", "b.bin"])
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="1"):
        out = run_action(base, workspace)
        assert out.returncode == 0, (out.stdout, out.stderr)
        rows = json.loads((workspace / "orphograph-receipts.json").read_text())
        assert [r["file"] for r in rows] == ["dist/a.bin"]
        summary = (workspace / "step_summary.md").read_text()
        assert "Not anchored" in summary and "dist/b.bin" in summary

    workspace, data = make_workspace(tmp_path / "strict", ["a.bin", "b.bin"])
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="1"):
        out = run_action(base, workspace, ORPHO_FAIL_ON_ERROR="true")
        assert out.returncode == 1, (out.stdout, out.stderr)
        rows = json.loads((workspace / "orphograph-receipts.json").read_text())
        assert [r["file"] for r in rows] == ["dist/a.bin"], "partial results are still written"


def _receipt_ids(data):
    return {p.parent.name for p in (data / "receipts").glob("*/receipt.json")}


def test_a_run_costs_one_anchor_per_matched_file(tmp_path):
    # Any further anchoring request of the action's own would cost a paying
    # user a credit they did not ask to spend. The allowance here is wide, so
    # such a request succeeds and leaves a receipt behind; counting receipts
    # on the server catches it even when the action ignores the answer.
    workspace, data = make_workspace(tmp_path, ["a.bin", "b.bin"])
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        before = _receipt_ids(data)
        out = run_action(base, workspace, ORPHO_FAIL_ON_ERROR="true")
        assert out.returncode == 0, (out.stdout, out.stderr)
        rows = json.loads((workspace / "orphograph-receipts.json").read_text())
        assert [r["file"] for r in rows] == ["dist/a.bin", "dist/b.bin"]
        assert "Not anchored" not in (workspace / "step_summary.md").read_text()
        assert _receipt_ids(data) - before == {r["receipt_id"] for r in rows}


def _declared(section):
    """{name: default-or-None} for the inputs or outputs of action.yml."""
    text = (ACTION_DIR / "action.yml").read_text()
    block = re.search(rf"^{section}:\n((?:  .*\n|\n)+)", text, re.M).group(1)
    found = {}
    for name, body in re.findall(r"^  (\w+):\n((?:    .*\n)+)", block, re.M):
        default = re.search(r'^    default: "(.*)"$', body, re.M)
        found[name] = default.group(1) if default else None
    return found


def test_the_action_interface_a_pinned_workflow_relies_on():
    inputs = _declared("inputs")
    assert inputs["fail_on_error"] == "false"
    assert inputs["paths"] == "dist/*"
    assert inputs["base_url"] == "https://orphograph.com"
    assert inputs["api_key"] == "" and inputs["pack_token"] == ""
    assert "receipts" in _declared("outputs")
