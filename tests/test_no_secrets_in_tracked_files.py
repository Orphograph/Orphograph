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
import io
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The body after a prefix is matched with the characters real keys use
# (`_`, `-`, and for base64 secrets `+ / =`), and never ended with `\b`: a
# key ending in `-` or `_` has no word boundary after it.
_END = r"(?![A-Za-z0-9_-])"
# A key may follow a word character when it sits inside escaped or encoded
# text (`\nsk_live_…`, `Bearer%20sk-…`), where `\b` finds no boundary.
_PRE = r"(?:(?<![A-Za-z0-9])|(?<=\\[nrt])|(?<=%[0-9A-Fa-f]{2}))"
SHAPES = {
    "stripe_live_key": _PRE + r"[sr]k_live_[A-Za-z0-9]{16,}",
    # Stripe, Svix and Resend webhook secrets; also inside orpho_whsec_.
    "webhook_signing_secret": r"whsec_[A-Za-z0-9+/=_-]{24,}",
    "orphograph_api_key": r"\borpho_[A-Za-z0-9_-]{32}" + _END,
    "aws_access_key_id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "aws_secret_access_key": r"(?i)aws_secret_access_key\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{40}",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY(?: BLOCK)?-----",
    "github_token": _PRE + r"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})",
    "npm_token": r"\bnpm_[A-Za-z0-9]{36}" + _END,
    "pypi_token": r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{40,}",
    # Fly macaroons, with or without the `FlyV1 ` header prefix.
    "fly_token": r"\bfm[12][ra]?_[A-Za-z0-9_+/=-]{40,}",
    "slack_token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    # Often inside a URL: api.telegram.org/bot<token>/sendMessage.
    "telegram_bot_token": r"(?<![0-9])\d{8,10}:AA[A-Za-z0-9_-]{33}" + _END,
    # NOWPayments API keys are four dash-joined groups of seven.
    "nowpayments_api_key": r"\b[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}-[A-Z0-9]{7}\b",
    "resend_key": r"\bre_[A-Za-z0-9]{8}_[A-Za-z0-9_-]{20,}",
    "anthropic_key": _PRE + r"sk-ant-[A-Za-z0-9_-]{20,}",
    "openai_key": _PRE + r"sk-(?:proj-|svcacct-|admin-)?(?!ant-)[A-Za-z0-9_-]{32,}",
}

# The server's own secrets have no recognisable shape, so they are matched by
# NAME on one line, and only when the value looks random (placeholders like
# `whsec_xxxxxxxxxx` or `PASTE_HERE` in setup docs are not secrets).
NAMED = {
    "server_secret_by_name": re.compile(
        r"\b(?:ORPHO_[A-Z0-9_]*(?:SECRET|TOKEN|KEY)|NOWPAYMENTS_IPN_SECRET|NOWPAYMENTS_API_KEY"
        r"|STRIPE_WEBHOOK_SECRET|STRIPE_SECRET_KEY|FLY_API_TOKEN|RESEND_API_KEY)"
        r"[ \t]*[=:][ \t]*[\"']?([A-Za-z0-9+/=_.-]{16,})"),
}
_PLACEHOLDER_WORDS = re.compile(r"(?i)paste|here|example|your|dummy|fake|placeholder|changeme|test")


def _looks_random(value: str) -> bool:
    return (any(c.isdigit() for c in value) and any(c.isalpha() for c in value)
            and not re.search(r"(.)\1\1\1", value)
            and not _PLACEHOLDER_WORDS.search(value))


def _audit_patterns() -> dict[str, re.Pattern]:
    """The safety audit's name-bound patterns (NOWPayments key, BTC receive
    address, Stripe/Resend by name), so the two scanners cannot drift."""
    path = REPO_ROOT / "scripts" / "biweekly_safety_audit.py"
    spec = importlib.util.spec_from_file_location("_audit_for_secret_scan", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    # Stripe and Resend by name are covered by SHAPES / NAMED (the audit's
    # Stripe pattern also matches sk_test_ placeholders in docs).
    return {f"audit:{name}": rx for name, rx in mod._KEY_PATTERNS.items()
            if name not in ("STRIPE_SECRET_KEY", "RESEND_API_KEY")}


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
    "nowpayments_api_key": "A1B2C3D" + "-E4F5G6H-J7K8L9M-N0P1Q2R",
}


def scan_text(text: str) -> list[tuple[str, int]]:
    """(pattern name, line number) for every credential in `text`."""
    hits = []
    for name, rx in COMPILED.items():
        for m in rx.finditer(text):
            if m.group(0) in KNOWN_FAKES or (m.groups() and m.group(1) in KNOWN_FAKES):
                continue
            hits.append((name, text.count("\n", 0, m.start()) + 1))
    for name, rx in NAMED.items():
        for m in rx.finditer(text):
            if m.group(1) not in KNOWN_FAKES and _looks_random(m.group(1)):
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


def _archive_members(path: Path, data: bytes):
    """(member name, bytes) of a tracked zip or tar archive: the verify kit and
    press kit are served to the public, so what is packed inside them ships."""
    name = path.name.lower()
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist():
                    if not info.is_dir() and info.file_size < 5_000_000:
                        yield info.filename, z.read(info)
        elif name.endswith((".tar.gz", ".tgz", ".tar")):
            with tarfile.open(fileobj=io.BytesIO(data)) as t:
                for member in t.getmembers():
                    if member.isfile() and member.size < 5_000_000:
                        f = t.extractfile(member)
                        if f is not None:
                            yield member.name, f.read()
    except (zipfile.BadZipFile, tarfile.TarError) as e:
        raise AssertionError(f"{path}: tracked archive cannot be opened ({e})") from e


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


def test_an_archive_member_is_scanned(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("kit/.env", "KEY='" + PLANTED["github_token"] + "'\n")
    members = dict(_archive_members(tmp_path / "kit.zip", buf.getvalue()))
    assert ("github_token", 1) in scan_text(_decode(members["kit/.env"]))


def test_the_servers_own_secrets_are_caught_by_name_but_placeholders_are_not():
    real = "ORPHO_HMAC_SECRET=" + "a3f9c2e1b7d04f6a9e8c3b2a1d0f9e8c"
    assert ("server_secret_by_name", 1) in scan_text(real + "\n")
    for placeholder in ('STRIPE_WEBHOOK_SECRET="whsec_xxxxxxxxxx"',
                        'STRIPE_WEBHOOK_SECRET="whsec_PASTE_HERE"',
                        "RESEND_API_KEY=\nNEXT=1", "if not NOWPAYMENTS_IPN_SECRET:\n    sys.stderr"):
        assert scan_text(placeholder + "\n") == [], placeholder


def test_keys_inside_urls_and_escaped_text_are_caught():
    tg = "curl https://api.telegram.org/bot" + PLANTED["telegram_bot_token"] + "/sendMessage"
    assert "telegram_bot_token" in {n for n, _l in scan_text(tg)}
    assert "stripe_live_key" in {n for n, _l in scan_text("k=\\n" + PLANTED["stripe_live_key"])}
    assert "openai_key" in {n for n, _l in scan_text("Bearer%20" + PLANTED["openai_key"])}


def test_no_tracked_file_carries_a_credential():
    files = _tracked_files()
    assert len(files) > 500, f"only {len(files)} tracked files: git ls-files failed?"
    leaks, binaries, unreadable, scanned = [], [], [], set()
    archive_members = 0
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            unreadable.append(path)
            continue
        rel = path.relative_to(REPO_ROOT)
        members = list(_archive_members(path, data))
        archive_members += len(members)
        for member, content in members:
            text = _decode(content)
            if text is not None:
                leaks += [f"{rel}!{member}:{line} {name}" for name, line in scan_text(text)]
        text = _decode(data)
        if text is None:
            if not members:
                binaries.append(path)
            continue
        scanned.add(rel.as_posix())
        leaks += [f"{rel}:{line} {name}" for name, line in scan_text(text)]
    # Controls: known files were READ and decoded (not just listed), nothing
    # tracked was silently unreadable, and binaries stay a minority.
    assert {"web/index.html", "server/credits.py"} <= scanned, "the scan did not read the tree"
    assert not unreadable, f"tracked files could not be read: {unreadable[:5]}"
    assert archive_members > 0, "no tracked archive was opened (the verify/press kits ship publicly)"
    assert len(binaries) < len(files) // 4, f"{len(binaries)} files skipped as binary"
    assert not leaks, "credential-shaped values in tracked files (values not shown):\n" + "\n".join(leaks)
