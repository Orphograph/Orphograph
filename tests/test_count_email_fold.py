"""scripts/count_email_fold.py counts the account-id case-fold exposure and prints only integers.

It runs on the production machine over the live ledgers, so what it prints is
what leaves the box: fixed key names and integers, never an address, and it
must not create the HMAC secret or write anything. Synthetic data dir only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "count_email_fold.py"
KELVIN = "K"
LINE = re.compile(r"[A-Za-z0-9_:.+\-]+=\d+")


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _write(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _data(tmp_path: Path, secret: bool) -> Path:
    d = tmp_path / "data"
    (d / "receipts" / "AbcDEFghiJKLmno1").mkdir(parents=True)
    victim, twin, upper = "kate@example.test", KELVIN + "ate@example.test", "Émile@example.test"
    _write(d / "auth_sessions.jsonl", [
        {"session_hash": "s1", "event": "created", "email": victim, "expires_unix": 4e9},
        {"session_hash": "s2", "event": "created", "email": twin, "expires_unix": 4e9}])
    _write(d / "auth_tokens.jsonl", [{"token_hash": "t1", "event": "issued", "email": upper, "expires_unix": 4e9}])
    _write(d / "credit_ledger.jsonl", [{"email": victim}, {"email": "plain@example.test"}])
    (d / "receipts" / "AbcDEFghiJKLmno1" / "receipt.json").write_text(json.dumps(
        {"receipt_id": "AbcDEFghiJKLmno1", "notify_email": victim}))
    if secret:
        (d / ".hmac_secret").write_bytes(b"s" * 32)
    return d


def _run(data: Path) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORPHO_")}
    env["ORPHO_DATA_DIR"] = str(data)
    proc = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _counts(out: str) -> dict:
    return {k: int(v) for k, v in (ln.split("=", 1) for ln in out.splitlines())}


def test_prints_only_key_integer_lines_and_never_an_address(tmp_path):
    out = _run(_data(tmp_path, secret=True))
    lines = out.splitlines()
    assert lines and all(LINE.fullmatch(ln) for ln in lines), lines
    assert "@" not in out and KELVIN not in out and "example" not in out


def test_counts_the_twin_and_the_non_ascii_capital(tmp_path):
    c = _counts(_run(_data(tmp_path, secret=True)))
    assert c["spellings_with_U+212A"] == 1
    assert c["spellings_with_twin_codepoint"] == 1
    assert c["spellings_fold_ne_lower"] == 2           # the twin and the capital E-acute
    assert c["live_sessions_twin_codepoint"] == 1
    assert c["live_link_tokens_fold_ne_lower"] == 1
    assert c["old_ids_with_more_than_one_fold_class"] == 1   # victim and twin share one id
    assert c["old_ids_ambiguous_involving_twin_codepoint"] == 1


def test_writes_nothing_and_never_creates_the_hmac_secret(tmp_path):
    data = _data(tmp_path, secret=False)
    before = _tree_hash(data)
    c = _counts(_run(data))
    assert c["hmac_secret_missing_id_counts_skipped"] == 1
    assert not (data / ".hmac_secret").exists()
    assert _tree_hash(data) == before


def test_a_stored_address_with_a_lone_surrogate_does_not_crash_it(tmp_path):
    # Review of PR #285: the sign-in link stored such an address, and hashing
    # it raised UnicodeEncodeError before anything was printed.
    data = _data(tmp_path, secret=True)
    with open(data / "auth_tokens.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"token_hash": "t2", "event": "issued", "email": "a\ud800@example.test",
                            "expires_unix": 4e9}) + "\n")
    out = _run(data)
    c = _counts(out)
    assert c["spellings_not_utf8_encodable"] == 1 and c["spellings_with_U+212A"] == 1
    assert "example" not in out


def test_an_unknown_ledger_file_name_is_not_printed(tmp_path):
    data = _data(tmp_path, secret=True)
    (data / "operator-notes-Kate.jsonl").write_text(json.dumps({"email": "\u00c9ve@example.test"}) + "\n")
    out = _run(data)
    assert "operator" not in out and "Kate" not in out
    assert "fold_ne_lower_in:other=1" in out


def test_real_ledgers_are_named_and_invite_owners_are_read(tmp_path):
    """@census-ledger-names (#285 round 2): gdpr_deletions, onboarding_state,
    interested, btc_orders, stripe_cancellations and team_invites were
    counted under "other", and team_invites keeps the owner's address under
    issued_by, which KEYS skipped."""
    d = _data(tmp_path, secret=True)
    capital = "Éva@example.test"
    names = ("gdpr_deletions", "onboarding_state", "interested", "btc_orders",
             "stripe_cancellations")
    for name in names:
        _write(d / f"{name}.jsonl", [{"email": capital}])
    _write(d / "team_invites.jsonl", [{"issued_by": "Áda@example.test", "code": "x"}])
    c = _counts(_run(d))
    for name in names + ("team_invites",):
        assert c.get(f"fold_ne_lower_in:{name}.jsonl") == 1, (name, c)
    assert "fold_ne_lower_in:other" not in c, c


def test_contrived_field_types_are_counted_not_fatal(tmp_path):
    """@census-contrived-shapes (#285 round 2): an unhashable id or key hash,
    or a non-numeric expires_unix, stopped the census; the float error echoed
    the stored value to stderr. The server never writes these shapes."""
    d = _data(tmp_path, secret=True)
    canary = "ExpiryValueCanary"
    _write(d / "auth_sessions.jsonl", [
        {"session_hash": ["s9"], "event": "created", "email": "a@example.test", "expires_unix": 4e9},
        {"session_hash": "s8", "event": "created", "email": "b@example.test", "expires_unix": canary},
        {"session_hash": "s7", "event": "created", "email": "c@example.test", "expires_unix": [1]}])
    _write(d / "api_keys.jsonl", [{"key_hash": ["k"], "event": "revoked"},
                                  {"key_hash": {"k": 1}, "event": "issued", "email": "d@example.test"}])
    _write(d / "x402_ledger.jsonl", [{"owner_id": ["x"]}])
    _write(d / "suppressions.jsonl", [{"email": ["e@example.test"]}])
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORPHO_")}
    env["ORPHO_DATA_DIR"] = str(d)
    proc = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert canary not in proc.stdout + proc.stderr
    assert all(LINE.fullmatch(ln) for ln in proc.stdout.splitlines())
    c = _counts(proc.stdout)
    assert c["live_sessions_rows_unusable_id"] == 1
    assert c["live_sessions_rows_unparseable_expiry"] == 2
