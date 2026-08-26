"""Cross-process invariants for the Stripe money path.

The ordinary webhook tests call ``handle_event`` twice in one interpreter, so
the module's threading lock can make a non-atomic cross-process implementation
look safe.  These tests use two independent interpreters over one data
directory.  The small delay after a negative dedupe lookup widens the real
check/effect/mark race without replacing any economic operation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def _completed_event() -> bytes:
    return json.dumps({
        "id": "evt_two_process_replay",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_two_process_replay",
            "customer_email": "race@example.test",
        }},
    }, separators=(",", ":")).encode()


def test_replayed_event_has_one_economic_effect_across_processes(tmp_path):
    payload = _completed_event()
    child = r'''import json, os, sys, time
sys.path.insert(0, os.environ["ORPHO_SERVER_DIR"])
import stripe_webhook

# Keep the test hermetic even if the developer shell has live mail credentials.
stripe_webhook.mailer.send_pack_claim_email = lambda *args, **kwargs: False

original_lookup = stripe_webhook._has_been_processed
def delayed_lookup(event_id):
    found = original_lookup(event_id)
    if not found:
        time.sleep(0.35)
    return found
stripe_webhook._has_been_processed = delayed_lookup

result = stripe_webhook.handle_event(bytes.fromhex(os.environ["ORPHO_TEST_EVENT_HEX"]))
print(json.dumps(result, sort_keys=True))
'''
    env = os.environ.copy()
    env.update({
        "ORPHO_DATA_DIR": str(tmp_path),
        "ORPHO_CREDIT_LEDGER": str(tmp_path / "credit_ledger.jsonl"),
        "ORPHO_PROCESSED_EVENTS": str(tmp_path / "stripe_processed_events.jsonl"),
        "ORPHO_STRIPE_PI_SESSION_MAP": str(tmp_path / "pi_session_map.jsonl"),
        "ORPHO_SERVER_DIR": str(REPO_ROOT / "server"),
        "ORPHO_TEST_EVENT_HEX": payload.hex(),
        "RESEND_API_KEY": "",
    })

    processes = [
        subprocess.Popen(
            [sys.executable, "-c", child],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    completed = [p.communicate(timeout=30) for p in processes]
    for process, (_stdout, stderr) in zip(processes, completed):
        assert process.returncode == 0, stderr

    results = [json.loads(stdout.strip()) for stdout, _stderr in completed]
    minted = [result for result in results if result.get("claim_code_minted")]
    duplicates = [result for result in results if result.get("duplicate")]
    assert len(minted) == 1, f"one Stripe event minted {len(minted)} Packs: {results}"
    assert len(duplicates) == 1, f"the replay was not identified: {results}"

    credit_rows = [
        json.loads(line)
        for line in (tmp_path / "credit_ledger.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert len(credit_rows) == 1
    assert credit_rows[0]["credits_delta"] == 10

    processed_rows = [
        json.loads(line)
        for line in (tmp_path / "stripe_processed_events.jsonl").read_text().splitlines()
        if line.strip()
    ]
    assert [row["event_id"] for row in processed_rows] == ["evt_two_process_replay"]
