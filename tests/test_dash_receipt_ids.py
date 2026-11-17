"""Receipt ids that start with "-" (2026-11-17).

Ids are secrets.token_urlsafe(12): 16 base64url characters, so about 1 in 64
starts with "-". argparse reads such a token as an option flag, so
`orpho_agent_anchor.py verify -AbC...` failed with "the following arguments
are required: receipt_id" (found when tests/test_openclaw_agent_anchor_e2e.py
failed in CI on PR #317 by chance). Two fixes: the server no longer mints such
ids, and the CLIs accept the ones that already exist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import engine  # noqa: E402

DASH_ID = "-AbCdEfGhIjKlMnO"  # the shape token_urlsafe(12) produces


def test_new_receipt_ids_never_start_with_a_dash(monkeypatch):
    draws = iter([DASH_ID, "--bCdEfGhIjKlMnO", "QbCdEfGhIjKlMnOp"])
    monkeypatch.setattr(engine.secrets, "token_urlsafe", lambda n: next(draws))
    assert engine._new_receipt_id() == "QbCdEfGhIjKlMnOp"


def test_openclaw_cli_verify_accepts_a_dash_led_id(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    cli = ROOT / "integrations" / "openclaw" / "orpho_agent_anchor.py"
    out = subprocess.run([sys.executable, str(cli), "verify", DASH_ID, "--file", str(f)],
                         capture_output=True, text=True, timeout=60,
                         env={"PATH": "/usr/bin:/bin", "ORPHO_BASE_URL": "http://127.0.0.1:9"})
    assert "required: receipt_id" not in out.stderr, out.stderr
    assert out.returncode != 2, out.stderr      # 2 = argparse usage error
    # Parsing succeeded and the CLI went on to make its request (the base URL is
    # a closed port), so the result is a network error, not a usage error.
    assert '"network_error"' in out.stdout, out.stdout
