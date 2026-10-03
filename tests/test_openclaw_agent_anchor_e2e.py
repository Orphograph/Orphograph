"""The agent anchoring CLI run as a process against a real local server.

The unit tests in test_openclaw_agent_anchor.py stub the transport, so they
cannot tell whether the fields `verify --file` compares are the ones the
service returns. Now that a mismatch exits 1, a wrong field name would fail
every honest verification, so the matching file is checked on the same path
as the mismatching one.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import _srv

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "integrations" / "openclaw" / "orpho_agent_anchor.py"


def run_cli(base, cwd, *argv, stdin=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORPHO_")}
    # The flag and the environment both name the local server. The CLI's own
    # default is the live service, and a parsing regression must not reach it.
    env["ORPHO_BASE_URL"] = base
    return subprocess.run(
        [sys.executable, str(CLI), "--base", base, *argv],
        cwd=cwd, env=env, input=stdin, capture_output=True, text=True, timeout=60)


def test_anchor_then_verify_the_same_file_and_a_changed_one(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    artifact = work / "SKILL.md"
    artifact.write_text("skill v3\n")
    changed = work / "received_skill.md"
    changed.write_text("skill v3, edited\n")

    data = tmp_path / "data"
    data.mkdir()
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        # The documented form: the option comes after the subcommand.
        out = run_cli(base, work, "anchor-file", str(artifact), "--label", "skill:my-skill v3")
        assert out.returncode == 0, (out.stdout, out.stderr)
        record = json.loads((work / ".orphograph" / "receipts.jsonl").read_text())
        rid = record["response"]["receipt_id"]
        assert record["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()

        status, receipt = _srv.get_json(base, "/api/receipt/" + rid)
        assert status == 200, receipt
        assert receipt["client_label"] == "skill:my-skill v3"

        same = run_cli(base, work, "verify", rid, "--file", str(artifact))
        assert same.returncode == 0, (same.stdout, same.stderr)
        assert json.loads(same.stdout)["local_match"] is True

        other = run_cli(base, work, "verify", rid, "--file", str(changed))
        assert other.returncode == 1, (other.stdout, other.stderr)
        assert json.loads(other.stdout)["local_match"] is False


def test_anchor_text_commits_to_stdin_as_piped_newline_included(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    # What `echo "sent invoice #42" | ... anchor-text` puts on stdin.
    piped = "sent invoice #42\n"
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        out = run_cli(base, work, "anchor-text", "--label", "action:invoice-42", stdin=piped)
        assert out.returncode == 0, (out.stdout, out.stderr)
        record = json.loads(out.stdout)
        status, receipt = _srv.get_json(
            base, "/api/receipt/" + record["response"]["receipt_id"])
        assert status == 200, receipt
        assert receipt["hash_hex"] == hashlib.sha256(piped.encode()).hexdigest()
        assert receipt["hash_hex"] != hashlib.sha256(b"sent invoice #42").hexdigest()


def test_the_recipient_specimen_states_the_byte_convention_the_cli_uses():
    # A recipient who recomputes the digest from the wrong bytes reads an
    # honest receipt as a mismatch.
    text = (ROOT / "docs" / "recipient-evidence-tests" / "agent-action"
            / "SPECIMEN.md").read_text()
    assert "include no trailing newline" not in text
    assert "followed by one newline byte" in text
