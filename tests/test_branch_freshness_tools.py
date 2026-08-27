from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


freshness = _load("check_branch_freshness", ROOT / "tools/check_branch_freshness.py")
starter = _load("start_branch", ROOT / "tools/start_branch.py")


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _run(tmp_path, "git", "init", "--bare", str(remote))
    _run(tmp_path, "git", "init", "-b", "master", str(seed))
    _run(seed, "git", "config", "user.email", "test@example.invalid")
    _run(seed, "git", "config", "user.name", "Test")
    (seed / "README.md").write_text("one\n")
    _run(seed, "git", "add", "README.md")
    _run(seed, "git", "commit", "-m", "initial")
    _run(seed, "git", "remote", "add", "origin", str(remote))
    _run(seed, "git", "push", "-u", "origin", "master")
    return seed, remote


def test_start_branch_uses_exact_origin_master_and_preserves_dirty_checkout(tmp_path):
    repo, _ = _repo(tmp_path)
    (repo / "README.md").write_text("dirty and preserved\n")
    target, branch, base = starter.start_branch(
        repo, "fresh-test", tmp_path / "worktrees", fetch=False
    )
    assert branch == "codex/fresh-test"
    assert _run(target, "git", "rev-parse", "HEAD") == base
    assert (repo / "README.md").read_text() == "dirty and preserved\n"
    assert (target / "README.md").read_text() == "one\n"


def test_start_branch_refuses_existing_branch(tmp_path):
    repo, _ = _repo(tmp_path)
    starter.start_branch(repo, "fresh-test", tmp_path / "worktrees", fetch=False)
    try:
        starter.start_branch(repo, "fresh-test", tmp_path / "other", fetch=False)
    except starter.StartError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("existing branch was reused")


def test_freshness_fails_after_remote_master_advances(tmp_path):
    repo, _ = _repo(tmp_path)
    target, _, _ = starter.start_branch(
        repo, "fresh-test", tmp_path / "worktrees", fetch=False
    )
    assert freshness.is_fresh(target, "origin/master")[0] is True
    (repo / "README.md").write_text("two\n")
    _run(repo, "git", "add", "README.md")
    _run(repo, "git", "commit", "-m", "advance master")
    _run(repo, "git", "push", "origin", "master")
    _run(target, "git", "fetch", "origin", "master")
    ok, message = freshness.is_fresh(target, "origin/master")
    assert ok is False
    assert "stale" in message
