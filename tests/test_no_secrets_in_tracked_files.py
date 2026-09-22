"""No live credential sits in any tracked file.

The safety audit's inline-secrets check (scripts/biweekly_safety_audit.py) only
matches the `NAME=value` form, only reads web/ and server/, and in CI only runs
against temporary fixture trees. So nothing in CI ever scanned the real tree:
a key pasted into a script, a deploy file, a doc or a workflow would ship.

This scans every file `git ls-files` returns for the SHAPE of a credential, the
value alone, plus the audit's own name-bound patterns (one list, not two). It
never prints a matched value, only the file, line and pattern name. A test
fixture that must contain a fake key goes in KNOWN_FAKES by its exact value,
never by file.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The body after a prefix is matched with the characters real keys use
# (`_`, `-`, and for base64 secrets `+ / =`), and never ended with `\b`: a
# key ending in `-` or `_` has no word boundary after it.
_END = r"(?![A-Za-z0-9_-])"
SHAPES = {
    "stripe_live_key": r"\b[sr]k_live_[A-Za-z0-9]{16,}",
    # Stripe, Svix and Resend webhook secrets; also inside orpho_whsec_.
    "webhook_signing_secret": r"whsec_[A-Za-z0-9+/=_-]{24,}",
    "orphograph_api_key": r"\borpho_[A-Za-z0-9_-]{32}" + _END,
    "aws_access_key_id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "aws_secret_access_key": r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{40}",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{36,}|\bgithub_pat_[A-Za-z0-9_]{60,}",
    "npm_token": r"\bnpm_[A-Za-z0-9]{36}" + _END,
    "pypi_token": r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{40,}",
    # Fly macaroons, with or without the `FlyV1 ` header prefix.
    "fly_token": r"\bfm[12][ra]?_[A-Za-z0-9_+/=-]{40,}",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "telegram_bot_token": r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}" + _END,
    "resend_key": r"\bre_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{20,}",
    "anthropic_key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai_key": r"\bsk-(?:proj-|svcacct-|admin-)?(?!ant-)[A-Za-z0-9_-]{32,}",
}


def _audit_patterns() -> dict[str, re.Pattern]:
    """The safety audit's name-bound patterns (NOWPayments key, BTC receive
    address, Stripe/Resend by name), so the two scanners cannot drift."""
    path = REPO_ROOT / "scripts" / "biweekly_safety_audit.py"
    spec = importlib.util.spec_from_file_location("_audit_for_secret_scan", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return {f"audit:{name}": rx for name, rx in mod._KEY_PATTERNS.items()}


COMPILED = {name: re.compile(p) for name, p in SHAPES.items()}
COMPILED.update(_audit_patterns())

# Exact fake values that tests plant on purpose. By value, so a real key in
# the same file is still caught.
KNOWN_FAKES = frozenset({
    "sk_live_abcd1234efgh5678",  # tests/test_biweekly_safety_audit.py detector fixtures
    "re_abcdefgh12345678",  # same file
    "bc1qabcdefghijklmnopqrstuv",  # same file
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",  # BIP-173 test vector (test_direct_btc_rail_is_gone.py)
    "whsec_test_fulfils_what_was_paid",  # tests/test_stripe_webhook_fulfils_what_was_paid.py
})

# One planted example per shape, with the characters real keys contain
# (`_ - + / =`), built by concatenation so this file does not flag itself.
# An all-letter example would pass a pattern that misses most real keys.
PLANTED = {
    "stripe_live_key": "sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "webhook_signing_secret": "whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2L+aLaSw/",
    "orphograph_api_key": "orpho_" + "Ab3_dEf-Gh1jKl2mNo3pQr4sTu5vWx6_",
    "aws_access_key_id": "ASIA" + "IOSFODNN7EXAMPLE",
    "aws_secret_access_key": "aws_secret_access_key = " + "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "private_key_block": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    "github_token": "ghp_" + "16C7e42F292c6912E7710c838347Ae178B4a",
    "npm_token": "npm_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "pypi_token": "pypi-AgEIcHlwaS5vcmc" + "CJGM2YTk5ZmQ0LTNmNWEtNDU3Zi1iY2U0LWE5_Zm-x",
    "fly_token": "fm2_" + "lJPECAAAAAAAAMjKxBBA5cuVaW/Xp+f2mNb_7Qa-x",
    "slack_token": "xoxb-" + "1234567890-abcDEF",
    "telegram_bot_token": "123456789:AA" + "FtM3G_uX8kQ2xYz9pLmNoPqRsTuVwXyZ-",
    "resend_key": "re_" + "Ab3dEf9h" + "_" + "Kq2w-Er7tY_uI9oP1aS3d",
    "anthropic_key": "sk-ant-" + "api03-Ab3_dEf-Gh1jKl2mNo3p",
    "openai_key": "sk-proj-" + "Ab3_dEf-Gh1jKl2mNo3pQr4sTu5vWx6_yZ",
}


def scan_text(text: str) -> list[tuple[str, int]]:
    """(pattern name, line number) for every credential shape in `text`."""
    hits = []
    for name, rx in COMPILED.items():
        for m in rx.finditer(text):
            if m.group(0) in KNOWN_FAKES or (m.groups() and m.group(1) in KNOWN_FAKES):
                continue
            hits.append((name, text.count("\n", 0, m.start()) + 1))
    return hits


def _decode(data: bytes) -> str | None:
    """Text of a tracked file, or None for a binary. UTF-16 files (a BOM) are
    decoded, not dropped as binary for the NUL bytes they carry."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "ignore")
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", "ignore")


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO_ROOT,
                         capture_output=True, check=True).stdout
    return [REPO_ROOT / raw.decode("utf-8", "surrogateescape")
            for raw in filter(None, out.split(b"\0"))]


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_each_shape_fires_on_a_realistic_example(name):
    assert set(PLANTED) == set(SHAPES), "every shape needs a planted example"
    for quote in ("'", '"', "\n"):
        found = {n for n, _line in scan_text(f"x = {quote}{PLANTED[name]}{quote}\n")}
        assert name in found, f"{name} cannot fire on a realistic key: a leak would read as clean"


def test_the_audit_patterns_are_scanned_too():
    names = {n for n, _l in scan_text("NOWPAYMENTS_API_KEY=" + "ABCDEFGHJK" * 3 + "\n")}
    assert "audit:NOWPAYMENTS_API_KEY" in names


def test_a_customer_key_inside_a_webhook_secret_is_caught():
    found = {n for n, _l in scan_text("s = 'orpho_whsec_" + "Ab3_dEf-Gh1jKl2mNo3pQr4sTu5vWx6_'\n")}
    assert "webhook_signing_secret" in found


def test_placeholders_and_a_known_fake_are_skipped_but_a_real_key_beside_them_is_not():
    text = ("doc = 'orpho_" + "x" * 24 + "'\n"
            "a = 'sk_live_abcd1234efgh5678'\n"
            "b = '" + PLANTED["stripe_live_key"] + "'\n")
    assert scan_text(text) == [("stripe_live_key", 3)]


def test_a_utf16_file_is_read_not_skipped():
    data = ("x = '" + PLANTED["github_token"] + "'\n").encode("utf-16")
    assert ("github_token", 1) in scan_text(_decode(data))


def test_no_tracked_file_carries_a_credential():
    files = _tracked_files()
    # Control: the scan read the whole tree (a known file is in it).
    assert len(files) > 500, f"only {len(files)} tracked files: git ls-files failed?"
    assert any(p.name == "index.html" and p.parent.name == "web" for p in files)
    leaks, binaries = [], []
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        text = _decode(data)
        if text is None:
            binaries.append(path)
            continue
        for name, line in scan_text(text):
            leaks.append(f"{path.relative_to(REPO_ROOT)}:{line} {name}")
    # Binaries are skipped, and the count is bounded so a text file that
    # starts looking binary cannot quietly leave the scan.
    assert len(binaries) < len(files) // 4, f"{len(binaries)} files skipped as binary"
    assert not leaks, "credential-shaped values in tracked files (values not shown):\n" + "\n".join(leaks)
