"""@x402-two-process-boot-release, reproduced 2026-10-17 with two real
processes on one data directory: process A settles and claims a payment and
is mid-anchor; process B boots and its release_stranded_claims() released
A's claim, so the identical request claimed again (one payment, two anchors).

A claim now marks its process as a claim owner (a shared flock held for the
process's life); the boot release runs only when it can take that lock
exclusively. The control: a claim whose process died is still released."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER))
import x402  # noqa: E402

pytestmark = pytest.mark.skipif(x402._fcntl is None, reason="flock needs POSIX")

P = {"x402Version": 1, "scheme": "exact", "network": "base-sepolia",
     "payload": {"signature": "0x" + "ab" * 65,
                 "authorization": {"nonce": "0x" + "cd" * 32, "value": "10000"}}}
B = {"hash_hex": "e" * 64}

HOLDER = r'''
import json, os, sys, time
sys.path.insert(0, sys.argv[5])
import x402
P, B, bar, hold = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], sys.argv[4] == "1"
ident = x402.payment_identifier(P)
x402.record_settlement(ident=ident, receipt_id="", amount_atomic="10000", asset_addr="0xA",
    network_id="base-sepolia", tx_hash="0xmock", payer="0xp", settled=True,
    digest=x402.payment_digest(P), request_digest=x402.request_digest(B))
assert x402.claim(P)[0]
open(bar + ".claimed", "w").close()
deadline = time.time() + 60
while hold and not os.path.exists(bar + ".go") and time.time() < deadline:
    time.sleep(0.05)
'''


def _holder(data_dir: Path, hold: bool):
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORPHO_")}
    env["ORPHO_DATA_DIR"] = str(data_dir)
    bar = str(data_dir / "bar")
    p = subprocess.Popen([sys.executable, "-c", HOLDER, json.dumps(P), json.dumps(B), bar,
                          "1" if hold else "0", str(SERVER)], env=env)
    deadline = time.time() + 30
    while not os.path.exists(bar + ".claimed"):
        assert time.time() < deadline and p.poll() in (None, 0), "holder did not claim"
        time.sleep(0.05)
    return p, bar


def test_a_booting_process_leaves_a_live_processes_claim_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    p, bar = _holder(tmp_path, hold=True)            # process A, mid-anchor
    try:
        ident = x402.payment_identifier(P)
        assert x402.release_stranded_claims() == 0   # process B boots
        assert x402.is_claimed(ident), "B released A's in-flight claim"
        assert x402.claim(P)[0] is False, "the identical request claimed a second anchor"
    finally:
        Path(bar + ".go").touch()
        p.wait(30)


def test_control_a_dead_processes_claim_is_still_released(tmp_path, monkeypatch):
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    p, _bar = _holder(tmp_path, hold=False)          # A claims and dies
    assert p.wait(30) == 0
    ident = x402.payment_identifier(P)
    assert x402.release_stranded_claims() == 1
    assert not x402.is_claimed(ident)
    # This process may claim afterwards (its own lock converts, never conflicts).
    assert x402.claim(P)[0] is True
    assert x402.release_stranded_claims() == 1
