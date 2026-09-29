"""Three ledgers were written to <repo>/data whatever ORPHO_DATA_DIR said.

The manual fulfillment queue (mailer), the refund request ledger and the
recovery gap log (app) each built their path by hand from the source tree. In
production the two directories are the same one (/app/data), so nothing
showed. Anywhere else a server told to keep its state in one directory kept
these three in another: a test server given a temp directory wrote them into
the checkout, which in the founder's checkout is the real data directory.

Each test drives the real entry point with ORPHO_DATA_DIR set and reads the
row back from that directory.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
FOUNDER_TOKEN = "founder-token-for-tests-0123456789"
OVERRIDES = ("ORPHO_MANUAL_FULFILL_QUEUE", "ORPHO_REFUND_LEDGER", "ORPHO_RECOVERY_GAP_LOG")


def _rows(path: Path) -> list[dict]:
    assert path.is_file(), f"nothing was written to {path}"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _python(data_dir: Path, code: str, **env_extra: str) -> str:
    """Run `code` in a fresh interpreter whose server modules see data_dir."""
    env = {k: v for k, v in os.environ.items() if k not in OVERRIDES}
    env.update({"ORPHO_DATA_DIR": str(data_dir)}, **env_extra)
    prog = f"import sys\nsys.path.insert(0, {str(REPO_ROOT / 'server')!r})\n" + code
    out = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                         text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


# A send that Resend refuses three times. Nothing leaves the machine: the one
# function that would open the connection is replaced, and so is the wait
# between attempts.
FAILED_SEND = (
    "import time, urllib.error, urllib.request\n"
    "time.sleep = lambda s: None\n"
    "def refuse(*a, **k): raise urllib.error.URLError('stubbed outage')\n"
    "urllib.request.urlopen = refuse\n"
    "import mailer\n"
    "print(mailer.send_pack_claim_email('buyer@example.test', 'claim-code-for-tests', 10))\n"
)


def test_the_manual_fulfillment_queue_follows_the_data_dir(tmp_path):
    sent = _python(tmp_path, FAILED_SEND, RESEND_API_KEY="re_not_a_real_key")
    assert sent == "False"
    rows = _rows(tmp_path / "manual_fulfillment_queue.jsonl")
    assert [(r["to"], r["category"]) for r in rows] == [("buyer@example.test", "transactional")]


def test_the_queue_override_still_wins(tmp_path):
    """Control: a deployment that names the queue file keeps its file."""
    named = tmp_path / "elsewhere" / "queue.jsonl"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sent = _python(data_dir, FAILED_SEND, RESEND_API_KEY="re_not_a_real_key",
                   ORPHO_MANUAL_FULFILL_QUEUE=str(named))
    assert sent == "False"
    assert [r["to"] for r in _rows(named)] == ["buyer@example.test"]
    assert not (data_dir / "manual_fulfillment_queue.jsonl").exists()


@pytest.fixture()
def clean_env(monkeypatch):
    """A spun server inherits this process's environment; an override left in
    the shell would send the ledger somewhere this test is not looking."""
    for name in OVERRIDES:
        monkeypatch.delenv(name, raising=False)


def test_a_refund_request_is_registered_in_the_data_dir(tmp_path, clean_env):
    sid = _python(tmp_path, "import auth;print(auth.create_session('refund@example.test')[0])")
    for base in _srv.server_processes(tmp_path, stub_calendars=True,
                                      ORPHO_FOUNDER_TOKEN=FOUNDER_TOKEN):
        status, _, _ = _srv.request(
            base, "/api/me/refund-request", "POST",
            json.dumps({"reason": "changed my mind"}).encode(),
            {"Cookie": f"orpho_sid={sid}", "Content-Type": "application/json"})
        assert status == 200
        rows = _rows(tmp_path / "refund_requests.jsonl")
        assert [(r["email"], r["reason"]) for r in rows] == [
            ("refund@example.test", "changed my mind")]
        # The founder's summary counts requests from the same file.
        status, summary = _srv.get_json(base, "/api/founder/morning-summary",
                                        headers={"X-Orpho-Founder": FOUNDER_TOKEN})
        assert status == 200
        assert summary["feedback"]["refund_requests_pending"] == 1


def test_a_recovery_gap_is_logged_in_the_data_dir(tmp_path, clean_env):
    """Stripe says the session is paid and nothing was minted for it: the gap
    is written down for the founder."""
    (tmp_path / "stub_stripe_answers.json").write_text(json.dumps({
        "/checkout/sessions/cs_test_gap1": {"status": 200, "data": {
            "payment_status": "paid", "mode": "payment",
            "customer_details": {"email": "gap@example.test"}}}}))
    for base in _srv.server_processes(tmp_path, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key"):
        status, _, _ = _srv.request(
            base, "/api/recover", "POST",
            json.dumps({"stripe_session_id": "cs_test_gap1",
                        "email": "gap@example.test"}).encode(),
            {"Content-Type": "application/json"})
        assert status == 202
        rows = _rows(tmp_path / "recovery_gaps.jsonl")
        assert [(r["session_id"], r["email"]) for r in rows] == [
            ("cs_test_gap1", "gap@example.test")]
