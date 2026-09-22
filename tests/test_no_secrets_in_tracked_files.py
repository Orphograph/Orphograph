"""No live credential sits in any tracked file.

The safety audit's inline-secrets check (scripts/biweekly_safety_audit.py) only
matches the `NAME=value` form, only reads web/ and server/, and in CI only runs
against temporary fixture trees. So nothing in CI ever scanned the real tree:
a key pasted into a script, a deploy file, a doc or a workflow would ship.

This scans every file `git ls-files` returns (text only) for the SHAPE of a
credential, the value alone. It never prints a matched value, only the file,
line and pattern name. A test fixture that must contain a fake key goes in
KNOWN_FAKES by its exact value, never by file.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    "stripe_live_key": r"\b[sr]k_live_[A-Za-z0-9]{16,}",
    "stripe_webhook_secret": r"\bwhsec_[A-Za-z0-9]{32,}",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}",
    "npm_token": r"\bnpm_[A-Za-z0-9]{36}\b",
    "pypi_token": r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{40,}",
    "fly_token": r"\bFlyV1 fm[12]_[A-Za-z0-9_+/=-]{40,}",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "telegram_bot_token": r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b",
    "resend_key": r"\bre_[A-Za-z0-9]{8}_[A-Za-z0-9]{20,}\b",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai_key": r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b",
}
COMPILED = {name: re.compile(p) for name, p in PATTERNS.items()}

# Exact fake values that tests plant on purpose. By value, so a real key in
# the same file is still caught.
KNOWN_FAKES = frozenset({
    "sk_live_abcd1234efgh5678",  # tests/test_biweekly_safety_audit.py detector fixtures
})

# One planted example per pattern: the detector must fire on each, or a
# pattern that can never match would report the tree clean.
PLANTED = {
    "stripe_live_key": "sk_live_" + "Z" * 24,
    "stripe_webhook_secret": "whsec_" + "Z" * 32,
    "aws_access_key": "AKIA" + "Z" * 16,
    "private_key_block": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "github_token": "ghp_" + "Z" * 36,
    "npm_token": "npm_" + "Z" * 36,
    "pypi_token": "pypi-AgEIcHlwaS5vcmc" + "Z" * 40,
    "fly_token": "FlyV1 fm2_" + "Z" * 40,
    "slack_token": "xoxb-" + "1" * 12,
    "telegram_bot_token": "123456789:AA" + "Z" * 33,
    "resend_key": "re_" + "Z" * 8 + "_" + "Z" * 20,
    "anthropic_key": "sk-ant-" + "Z" * 24,
    "openai_key": "sk-proj-" + "Z" * 32,
}


def scan_text(text: str) -> list[tuple[str, int]]:
    """(pattern name, line number) for every credential shape in `text`."""
    hits = []
    for name, rx in COMPILED.items():
        for m in rx.finditer(text):
            if m.group(0) in KNOWN_FAKES:
                continue
            hits.append((name, text.count("\n", 0, m.start()) + 1))
    return hits


def _tracked_text_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                         capture_output=True, check=True).stdout
    files = []
    for raw in filter(None, out.split(b"\0")):
        path = REPO_ROOT / raw.decode("utf-8", "surrogateescape")
        try:
            head = path.read_bytes()[:8192]
        except OSError:
            continue
        if b"\0" not in head:
            files.append(path)
    return files


@pytest.mark.parametrize("name", sorted(PATTERNS))
def test_each_pattern_fires_on_its_planted_example(name):
    assert set(PLANTED) == set(PATTERNS), "every pattern needs a planted example"
    found = {n for n, _line in scan_text(f"x = '{PLANTED[name]}'\n")}
    assert name in found, f"{name} cannot fire: the scan would report a leak as clean"


def test_a_known_fake_is_skipped_but_a_real_key_beside_it_is_not():
    text = "a = 'sk_live_abcd1234efgh5678'\nb = 'sk_live_" + "Q" * 24 + "'\n"
    assert scan_text(text) == [("stripe_live_key", 2)]


def test_no_tracked_file_carries_a_credential():
    files = _tracked_text_files()
    # Control: the scan read the whole tree, not an empty or partial list.
    assert len(files) > 500, f"only {len(files)} tracked text files: git ls-files failed?"
    assert any(p.name == "app.py" and p.parent.name == "server" for p in files)
    leaks = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, line in scan_text(text):
            leaks.append(f"{path.relative_to(REPO_ROOT)}:{line} {name}")
    assert not leaks, "credential-shaped values in tracked files (values not shown):\n" + "\n".join(leaks)
