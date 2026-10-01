"""The weekly job signs what it anchors, and the office key never leaks.

The Standing Record lists a new row only when it carries the office's
signature (server/standing_record.py). So the job must sign every anchor, and
must refuse to anchor at all when it cannot: an unsigned anchor would be made
and then hidden by the page, which is worse than a loud failed run.

scripts/office_key.py makes that key. It must never overwrite one and never
print the private seed. Every run here points it at a temp path and a temp
HOME, so the real key can never be touched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "scripts"))
import _ed25519  # noqa: E402
import manifest_signature  # noqa: E402
import standing_record  # noqa: E402
import weekly_anchor  # noqa: E402

OFFICE_KEY_SCRIPT = ROOT / "scripts" / "office_key.py"
SEED = hashlib.sha256(b"weekly job test office key, never pinned").digest()
LABEL = "weekly-2026-10-04-17-artifacts"
ROOT_HEX = hashlib.sha256(b"a folder root").hexdigest()


@pytest.fixture(params=["cryptography", "stdlib"])
def backend(request, monkeypatch):
    """Run once on each Ed25519 backend. Production has no `cryptography`
    (python:3.11-slim, and the macOS system Python the job runs under), so
    the stdlib one is the one that matters there."""
    if request.param == "stdlib":
        monkeypatch.setattr(manifest_signature, "_HAVE_CRYPTOGRAPHY", False)
        monkeypatch.setattr(manifest_signature, "_HAVE_REF", True)
        monkeypatch.setattr(manifest_signature, "_ref", _ed25519)
    else:
        assert manifest_signature._HAVE_CRYPTOGRAPHY, "this run needs cryptography"
    return request.param


def _record(label: str, hash_hex: str, signature: str, rid: str = "jobSigned0000000") -> dict:
    return {"receipt_id": rid, "created_at": "2026-10-04T07:30:00+00:00",
            "hash_hex": hash_hex, "client_label": label, "private": False,
            "office_signature": signature}


def test_the_jobs_signature_is_listed_by_the_servers_verifier(backend):
    sig = weekly_anchor.sign_for_standing_record(LABEL, ROOT_HEX, SEED)
    key = standing_record.public_key(SEED)
    rows = standing_record.listed([_record(LABEL, ROOT_HEX, sig)], keys=(key,))
    assert [r["receipt_id"] for r in rows] == ["jobSigned0000000"]
    # The same row against a key set without the office key: not listed.
    other = standing_record.public_key(hashlib.sha256(b"other").digest())
    assert standing_record.listed([_record(LABEL, ROOT_HEX, sig)], keys=(other,)) == []


@pytest.mark.parametrize("which", ["label-first", "label-last", "hash-first", "hash-last"])
def test_one_character_off_does_not_verify(backend, which):
    sig = bytes.fromhex(weekly_anchor.sign_for_standing_record(LABEL, ROOT_HEX, SEED))
    key = standing_record.public_key(SEED)
    assert standing_record.verifies(LABEL, ROOT_HEX, sig, (key,))

    def flip(s: str, i: int) -> str:
        repl = "0" if s[i] != "0" else "1"
        return s[:i] + repl + s[i + 1:]

    label, hash_hex = LABEL, ROOT_HEX
    if which == "label-first":
        label = "W" + LABEL[1:]
    elif which == "label-last":
        label = flip(LABEL, -1) if LABEL[-1].isdigit() else LABEL[:-1] + "x"
    elif which == "hash-first":
        hash_hex = flip(ROOT_HEX, 0)
    else:
        hash_hex = flip(ROOT_HEX, len(ROOT_HEX) - 1)
    assert (label, hash_hex) != (LABEL, ROOT_HEX)
    assert not standing_record.verifies(label, hash_hex, sig, (key,))


# ---- the job itself, with the network replaced --------------------------------

class _Reply:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def job(monkeypatch, tmp_path):
    """The real main(), with its log in tmp and urlopen recorded."""
    monkeypatch.setattr(weekly_anchor, "LOG_PATH", tmp_path / "weekly_anchor_log.jsonl")
    state = {"sent": [], "reply": {}}

    def fake_urlopen(req, timeout=None):
        state["sent"].append(json.loads(req.data.decode()))
        return _Reply(state["reply"])

    monkeypatch.setattr(weekly_anchor.urllib.request, "urlopen", fake_urlopen)
    state["log"] = lambda: [json.loads(x) for x in
                            (tmp_path / "weekly_anchor_log.jsonl").read_text().splitlines()]
    return state


def _write_key(path: Path, seed: bytes = SEED) -> None:
    path.write_bytes(seed)
    os.chmod(path, 0o600)


def test_a_missing_key_fails_loudly_and_anchors_nothing(job, monkeypatch, tmp_path, capsys):
    missing = tmp_path / "no-such-key"
    monkeypatch.setenv("ORPHO_OFFICE_KEY_PATH", str(missing))
    assert weekly_anchor.main() != 0
    assert job["sent"] == [], "an unsigned anchor was sent"
    err = capsys.readouterr().err
    assert "office signing key" in err and str(missing) in err
    assert job["log"]()[-1]["receipt_id"] is None


def test_an_unpinned_key_fails_before_anchoring(job, monkeypatch, tmp_path, capsys):
    key = tmp_path / "office_signing_key"
    _write_key(key)
    monkeypatch.setenv("ORPHO_OFFICE_KEY_PATH", str(key))
    assert weekly_anchor.main() != 0
    assert job["sent"] == []
    out = capsys.readouterr()
    assert "not pinned" in out.err
    assert SEED.hex() not in out.err + out.out


def test_a_signed_run_sends_a_signature_the_listing_accepts(job, monkeypatch, tmp_path, capsys):
    key = tmp_path / "office_signing_key"
    _write_key(key)
    monkeypatch.setenv("ORPHO_OFFICE_KEY_PATH", str(key))
    pub = standing_record.public_key(SEED)
    monkeypatch.setattr(standing_record, "PINNED_OFFICE_KEYS", (pub.hex(),))
    job["reply"] = {"receipt_id": "jobSigned0000000", "calendars_ok": 5,
                    "calendars_total": 5, "office_signed": True}
    assert weekly_anchor.main() == 0
    [body] = job["sent"]
    label, root_hex = body["client_label"], body["manifest"]["root_hex"]
    assert label.startswith("weekly-")
    rows = standing_record.listed([_record(label, root_hex, body["office_signature"])],
                                  keys=(pub,))
    assert len(rows) == 1
    out = capsys.readouterr()
    assert SEED.hex() not in out.err + out.out
    assert job["log"]()[-1]["receipt_id"] == "jobSigned0000000"


def test_a_server_that_drops_the_signature_is_a_failed_run(job, monkeypatch, tmp_path, capsys):
    """A server older than this job ignores the field: the anchor is made but
    the page will never list it. That must not pass as a good week."""
    key = tmp_path / "office_signing_key"
    _write_key(key)
    monkeypatch.setenv("ORPHO_OFFICE_KEY_PATH", str(key))
    monkeypatch.setattr(standing_record, "PINNED_OFFICE_KEYS",
                        (standing_record.public_key(SEED).hex(),))
    job["reply"] = {"receipt_id": "jobUnsigned00000", "calendars_ok": 5, "calendars_total": 5}
    assert weekly_anchor.main() != 0
    assert "office signature" in capsys.readouterr().err
    assert job["log"]()[-1]["error"]


# ---- scripts/office_key.py -----------------------------------------------------

def _office_key(args: list, home: Path, key_path: Path | None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ORPHO_OFFICE_KEY_PATH"}
    env["HOME"] = str(home)   # so even the default path lands in tmp
    if key_path is not None:
        env["ORPHO_OFFICE_KEY_PATH"] = str(key_path)
    return subprocess.run([sys.executable, str(OFFICE_KEY_SCRIPT), *args],
                          env=env, capture_output=True, timeout=60)


def _assert_seed_absent(seed: bytes, output: bytes) -> None:
    text = output.decode("utf-8", "replace")
    assert seed not in output
    for form in (seed.hex(), seed.hex().upper(),
                 base64.b64encode(seed).decode(), base64.urlsafe_b64encode(seed).decode(),
                 base64.b64encode(seed).decode().rstrip("="),
                 base64.urlsafe_b64encode(seed).decode().rstrip("=")):
        assert form not in text


def test_generate_prints_only_the_public_key_and_locks_the_file(tmp_path):
    key = tmp_path / "keys" / "office_signing_key"
    r = _office_key(["generate"], tmp_path, key)
    assert r.returncode == 0, r.stderr
    assert re.fullmatch(rb"[0-9a-f]{64}\n", r.stdout), r.stdout
    assert r.stderr == b""
    seed = key.read_bytes()
    assert len(seed) == 32
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
    assert r.stdout.decode().strip() == standing_record.public_key(seed).hex()
    _assert_seed_absent(seed, r.stdout + r.stderr)
    # `public` reads the same file back to the same public key.
    p = _office_key(["public"], tmp_path, key)
    assert p.returncode == 0 and p.stdout == r.stdout
    _assert_seed_absent(seed, p.stdout + p.stderr)


def test_generate_refuses_to_overwrite_and_still_prints_no_seed(tmp_path):
    key = tmp_path / "office_signing_key"
    first = _office_key(["generate"], tmp_path, key)
    assert first.returncode == 0
    before = key.read_bytes()
    again = _office_key(["generate"], tmp_path, key)
    assert again.returncode != 0
    assert again.stdout == b""
    assert b"refus" in again.stderr.lower()
    assert key.read_bytes() == before
    _assert_seed_absent(before, again.stdout + again.stderr)
    # A file it did not make is just as safe.
    other = tmp_path / "someone_elses_file"
    other.write_bytes(b"\x07" * 32)
    r = _office_key(["generate"], tmp_path, other)
    assert r.returncode != 0 and other.read_bytes() == b"\x07" * 32


def test_the_default_path_is_under_home_and_its_directory_is_made_private(tmp_path):
    (tmp_path / ".orphograph").mkdir(mode=0o755)
    os.chmod(tmp_path / ".orphograph", 0o755)
    r = _office_key(["generate"], tmp_path, None)
    assert r.returncode == 0, r.stderr
    key = tmp_path / ".orphograph" / "office_signing_key"
    assert len(key.read_bytes()) == 32
    assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700


def test_public_without_a_key_fails(tmp_path):
    r = _office_key(["public"], tmp_path, tmp_path / "absent")
    assert r.returncode != 0 and r.stdout == b""
