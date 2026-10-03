"""Focused tests for the OpenClaw anchoring CLI."""

import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "integrations" / "openclaw" / "orpho_agent_anchor.py"
SPEC = importlib.util.spec_from_file_location("openclaw_agent_anchor", MODULE_PATH)
anchor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(anchor)


@pytest.fixture(autouse=True)
def _no_network_no_stray_files(tmp_path, monkeypatch):
    """No test here may reach a server or write into the checkout.

    The CLI's default base URL is the live service and its receipts file lands
    in the current directory. Two tests in this module never stubbed the
    transport because a guard exits before it; with that guard broken they
    anchored on the live service and left .orphograph/ in the repo. A test
    that needs the transport replaces these stubs with its own.
    """
    def blocked(*args, **kwargs):
        raise AssertionError("the CLI reached its transport without a stub")

    # main() reads these as argparse defaults; one exported in the
    # developer's shell changed what the tests ran (review of the rescue
    # branch: ORPHO_PACK_TOKEN made the credential test exit 2).
    for name in ("ORPHO_API_KEY", "ORPHO_PACK_TOKEN", "ORPHO_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(anchor, "post_anchor", blocked)
    monkeypatch.setattr(anchor, "get_verify", blocked)
    monkeypatch.setattr(anchor.urllib.request, "urlopen", blocked)
    monkeypatch.chdir(tmp_path)


def run_main(monkeypatch, argv, *, stdin=""):
    monkeypatch.setattr(sys, "argv", [str(MODULE_PATH), *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return anchor.main()


def test_an_unstubbed_anchor_cannot_reach_a_server(tmp_path, monkeypatch):
    # Nothing listens on 127.0.0.1:9, so if the real transport ran it would
    # come back as a network_error record and exit 1 instead of raising.
    monkeypatch.chdir(tmp_path)
    with pytest.raises(AssertionError, match="without a stub"):
        run_main(monkeypatch, ["--base", "http://127.0.0.1:9", "anchor-text"],
                 stdin="payload")
    assert not (tmp_path / ".orphograph").exists()


def test_options_before_the_subcommand_still_work(tmp_path, monkeypatch, capsys):
    # The form every caller had to use before options were also accepted
    # after the subcommand. The subcommand's own defaults must not erase it.
    source = tmp_path / "artifact.bin"
    source.write_bytes(b"artifact bytes")
    monkeypatch.chdir(tmp_path)
    labels = []
    monkeypatch.setattr(
        anchor, "post_anchor",
        lambda base, sha256, sha512, **kwargs: labels.append(kwargs["label"])
        or {"receipt_id": "r-before"},
    )

    assert run_main(monkeypatch, ["--label", "build", "anchor-file", str(source)]) == 0
    assert labels == ["build"]

    capsys.readouterr()
    assert run_main(monkeypatch, ["--dry-run", "anchor-file", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert labels == ["build"], "a dry run must not anchor"


def test_anchor_file_parses_label_after_command_and_persists_receipt(
        tmp_path, monkeypatch, capsys):
    source = tmp_path / "artifact.bin"
    source.write_bytes(b"artifact bytes")
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        anchor,
        "post_anchor",
        lambda base, sha256, sha512, **kwargs: calls.append(
            (base, sha256, sha512, kwargs)) or {"receipt_id": "r-file"},
    )

    assert run_main(monkeypatch, ["anchor-file", str(source), "--label", "build"]) == 0

    expected256 = hashlib.sha256(source.read_bytes()).hexdigest()
    assert calls[0][1] == expected256
    assert calls[0][3]["label"] == "build"
    record = json.loads((tmp_path / ".orphograph" / "receipts.jsonl").read_text())
    assert record["subject"] == str(source)
    assert record["sha256"] == expected256
    assert record["response"] == {"receipt_id": "r-file"}
    assert json.loads(capsys.readouterr().out) == record


def test_anchor_text_reads_stdin_and_forwards_credentials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seen = {}

    def fake_post(base, sha256, sha512, **kwargs):
        seen.update(base=base, sha256=sha256, sha512=sha512, **kwargs)
        return {"receipt_id": "r-text"}

    monkeypatch.setattr(anchor, "post_anchor", fake_post)
    text = "sent invoice #42\n"
    assert run_main(
        monkeypatch,
        ["--base", "https://example.test", "--api-key", "secret", "anchor-text"],
        stdin=text,
    ) == 0
    assert seen["base"] == "https://example.test"
    assert seen["api_key"] == "secret"
    assert seen["sha256"] == hashlib.sha256(text.encode()).hexdigest()


def test_anchor_memory_dry_run_hashes_manifest_without_network(
        tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "MEMORY.md").write_text("remember this")
    monkeypatch.setattr(
        anchor, "post_anchor", lambda *args, **kwargs: pytest.fail("network called"))

    assert run_main(
        monkeypatch, ["anchor-memory", str(workspace), "--dry-run"]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    manifest = anchor.build_memory_manifest(str(workspace))
    assert output == {
        "dry_run": True,
        "subject": f"memory-manifest:{workspace}",
        "sha256": hashlib.sha256(manifest.encode()).hexdigest(),
    }
    assert not (workspace / ".orphograph").exists()


def test_anchor_api_error_returns_failure_and_persists_receipt(
        tmp_path, monkeypatch):
    source = tmp_path / "input.txt"
    source.write_text("payload")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        anchor, "post_anchor", lambda *args, **kwargs: {"error": "network_error"})

    assert run_main(monkeypatch, ["anchor-file", str(source)]) == 1
    record = json.loads((tmp_path / ".orphograph" / "receipts.jsonl").read_text())
    assert record["response"] == {"error": "network_error"}


@pytest.mark.parametrize("remote_key", ["hash_hex", "sha256"])
def test_verify_matching_file_returns_success(
        remote_key, tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.txt"
    source.write_text("same")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(anchor, "get_verify", lambda base, receipt: {remote_key: digest})

    assert run_main(monkeypatch, ["verify", "r-ok", "--file", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["local_match"] is True


def test_verify_mismatched_file_returns_failure(tmp_path, monkeypatch, capsys):
    source = tmp_path / "input.txt"
    source.write_text("local")
    monkeypatch.setattr(anchor, "get_verify", lambda base, receipt: {"hash_hex": "0" * 64})

    assert run_main(monkeypatch, ["verify", "r-mismatch", "--file", str(source)]) == 1
    assert json.loads(capsys.readouterr().out)["local_match"] is False


def test_verify_api_error_returns_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        anchor, "get_verify", lambda base, receipt: {"error": "http_error", "status": 404})

    assert run_main(monkeypatch, ["verify", "missing"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == 404


def test_cli_rejects_conflicting_credentials(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_main(
            monkeypatch,
            ["--api-key", "key", "--pack-token", "token", "anchor-text"],
            stdin="payload",
        )
    assert exc.value.code == 2


def test_anchor_text_rejects_empty_stdin(monkeypatch):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, ["anchor-text"], stdin=" \n")
    assert exc.value.code == 2


@pytest.mark.parametrize("given", ["", "does-not-exist.bin"], ids=["empty", "missing"])
def test_verify_with_a_file_it_cannot_read_fails(given, monkeypatch, capsys):
    # Review of the rescue branch: `--file ""` skipped the comparison (a
    # truthiness check) and the exit code read the missing local_match as a
    # pass, so `verify "$RID" --file "$UNSET"` exited 0 like a real match.
    monkeypatch.setattr(anchor, "get_verify", lambda base, receipt: {"hash_hex": "0" * 64})

    assert run_main(monkeypatch, ["verify", "r1", "--file", given]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["local_match"] is False
    assert "cannot read --file" in out["local_error"]
