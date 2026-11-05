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
installer = _load("install_hooks", ROOT / "tools/install_hooks.py")
HOOK_BYTES = (ROOT / "scripts/git-hooks/pre-push").read_bytes()


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
    (seed / "scripts/git-hooks").mkdir(parents=True)
    (seed / "scripts/git-hooks/pre-push").write_bytes(HOOK_BYTES)
    # The real scanner, so a push from this toy repo runs the real guard.
    # (it loads the pattern list from scripts/biweekly_safety_audit.py).
    for rel in ("tests/test_no_secrets_in_tracked_files.py", "scripts/biweekly_safety_audit.py"):
        (seed / rel).parent.mkdir(parents=True, exist_ok=True)
        (seed / rel).write_bytes((ROOT / rel).read_bytes())
    _run(seed, "git", "add", "-A")
    _run(seed, "git", "commit", "-m", "initial")
    _run(seed, "git", "remote", "add", "origin", str(remote))
    _run(seed, "git", "push", "-u", "origin", "master")
    ok, _ = installer.install(seed)
    assert ok
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


def _hook(repo: Path) -> Path:
    return installer.hooks_dir(repo) / "pre-push"


def test_start_branch_refuses_when_the_pre_push_guard_is_missing(tmp_path):
    repo, _ = _repo(tmp_path)
    _hook(repo).unlink()
    try:
        starter.start_branch(repo, "no-guard", tmp_path / "worktrees", fetch=False)
    except starter.StartError as exc:
        assert "not installed" in str(exc) and "install_hooks.py" in str(exc)
    else:
        raise AssertionError("a branch started from a clone with no pre-push guard")
    assert not (tmp_path / "worktrees" / "no-guard").exists()


def test_start_branch_refuses_a_stale_or_edited_guard(tmp_path):
    repo, _ = _repo(tmp_path)
    _hook(repo).write_bytes(HOOK_BYTES.replace(b"pre-push secret guard", b"old guard", 1))
    try:
        starter.start_branch(repo, "stale-guard", tmp_path / "worktrees", fetch=False)
    except starter.StartError as exc:
        assert "differs" in str(exc)
    else:
        raise AssertionError("a branch started with a guard that differs from origin/master's")


def test_start_branch_refuses_a_guard_that_cannot_run(tmp_path):
    repo, _ = _repo(tmp_path)
    _hook(repo).chmod(0o644)
    try:
        starter.start_branch(repo, "dead-guard", tmp_path / "worktrees", fetch=False)
    except starter.StartError as exc:
        assert "not executable" in str(exc)
    else:
        raise AssertionError("a non-executable guard was accepted")


def test_installer_will_not_clobber_a_different_hook_without_force(tmp_path):
    repo, _ = _repo(tmp_path)
    _hook(repo).write_text("#!/bin/sh\necho someone else's hook\n")
    ok, message = installer.install(repo)
    assert ok is False and "--force" in message
    assert b"someone else's hook" in _hook(repo).read_bytes()
    ok, _ = installer.install(repo, force=True)
    assert ok and _hook(repo).read_bytes() == HOOK_BYTES


def test_a_linked_worktree_shares_the_main_checkout_guard(tmp_path):
    repo, _ = _repo(tmp_path)
    target, _, _ = starter.start_branch(repo, "linked", tmp_path / "worktrees", fetch=False)
    assert installer.hooks_dir(target) == installer.hooks_dir(repo)
    assert installer.hook_problem(target, HOOK_BYTES) is None


def test_the_tracked_guard_is_the_one_this_checkout_runs():
    """The tracked copy is what CI tests; the installed one is what blocks pushes."""
    text = HOOK_BYTES.decode()
    assert text.startswith("#!/bin/sh\n")
    assert "test_no_secrets_in_tracked_files.py" in text
    assert "--not\", \"--remotes" in text, "the guard must scan only blobs no remote has"


def test_the_installed_guard_blocks_a_push_carrying_a_key(tmp_path):
    repo, _ = _repo(tmp_path)
    scanner = _load("secret_scan_for_hook_test", ROOT / "tests/test_no_secrets_in_tracked_files.py")
    planted = scanner.PLANTED["stripe_live_key"]
    (repo / "deploy.sh").write_text("KEY='" + planted + "'\n")
    _run(repo, "git", "add", "deploy.sh")
    _run(repo, "git", "commit", "-m", "leak")
    pushed = subprocess.run(["git", "push", "origin", "master"], cwd=repo, text=True, capture_output=True)
    assert pushed.returncode != 0, "the installed guard let a live-key shape through"
    assert "deploy.sh:1 stripe_live_key" in pushed.stderr
    assert planted not in pushed.stderr, "the guard printed the value"
