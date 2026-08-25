"""test_pack_credit_atomicity.py

One pack credit buys exactly one anchor, even under concurrency
(guard added 2026-08-25 during the session/auth audit).

credits.consume_credit() holds an in-process lock AND a cross-process fcntl
exclusive lock on a sentinel file across the whole scan+append critical
section, so two requests cannot both observe balance>0 and each consume.

This is pinned because the SIBLING money path has broken exactly this way.
The L402 handler in app.py carries the scar in a comment: the single-use
decision used to sit 176 lines and five calendar submissions before
mark_spent, on a threading server, and "eight concurrent requests with one
paid credential produced eight receipts". A correct lock today is not
evidence the next refactor keeps it — a concurrency test is.

Driven through the real HTTP entry point with real threads. A test that
called consume_credit() directly would not exercise the handler ordering,
which is where the L402 bug actually lived.

WHAT THIS COVERS, AND WHAT IT DOES NOT — measured, not assumed.
consume_credit() takes TWO locks: an in-process `threading` lock and a
cross-process `fcntl` lock on a sentinel file. These threads all hit ONE
server process, so:

  * Removing the in-process lock -> this suite FAILS loudly: 12 of 12
    concurrent anchors consume the same single credit (verified 2026-08-25).
  * Removing ONLY the fcntl lock -> this suite still PASSES, because the
    in-process lock alone serialises threads inside one process.

So this guards single-process concurrency. The cross-process half — two Fly
machines each observing balance>0 — is NOT exercised here and would need two
server processes over a shared ORPHO_DATA_DIR. Stated so nobody reads a green
run as proof of the multi-machine property the docstring in credits.py
describes.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CONCURRENCY = 12


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("pack_race_data")
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
           "ORPHO_DATA_DIR": str(data_dir), "ORPHO_COOKIE_SECURE": "0",
           "RATE_LIMIT_PER_DAY": "100000", "ORPHO_OFFLINE_CALENDARS": "1"}
    env.pop("RESEND_API_KEY", None)
    proc = subprocess.Popen([sys.executable, str(REPO_ROOT / "server" / "app.py")],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base, str(data_dir)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _credits_call(data_dir: str, snippet: str) -> str:
    code = (
        "import os,sys;"
        f"os.environ['ORPHO_DATA_DIR']={data_dir!r};"
        f"sys.path.insert(0,{str(REPO_ROOT / 'server')!r});"
        "import credits;"
        + snippet
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _mint(data_dir: str, amount: int) -> str:
    return _credits_call(
        data_dir,
        f"c=credits.new_claim_code();credits.add_credits(c,'race@x.test',{amount},"
        "'atomicity-test');print(c)",
    )


def _balance(data_dir: str, code: str) -> int:
    return int(_credits_call(data_dir, f"print(credits.balance({code!r}))"))


def _anchor(base: str, code: str, i: int):
    body = {"hash_hex": hashlib.sha256(f"race-{code}-{i}".encode()).hexdigest()}
    req = urllib.request.Request(
        base + "/api/anchor", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Pack-Token": code},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read()).get("pack_consumed")
    except urllib.error.HTTPError:
        return "http-error"


def test_one_credit_survives_concurrent_anchors(server):
    """THE GUARD. N simultaneous anchors, one credit: exactly one consumes."""
    base, data_dir = server
    code = _mint(data_dir, 1)
    assert _balance(data_dir, code) == 1

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        results = list(ex.map(lambda i: _anchor(base, code, i), range(CONCURRENCY)))

    consumed = [r for r in results if r is True]
    assert len(consumed) == 1, (
        f"DOUBLE-SPEND: {len(consumed)} of {CONCURRENCY} concurrent anchors each "
        f"consumed the same single credit. results={results}"
    )
    assert _balance(data_dir, code) == 0, "balance went negative or was not decremented"


def test_a_multi_credit_pack_spends_exactly_its_balance(server):
    """Same race, more headroom: 3 credits against 12 concurrent anchors must
    consume exactly 3 — not 12, and not 1."""
    base, data_dir = server
    code = _mint(data_dir, 3)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        results = list(ex.map(lambda i: _anchor(base, code, i), range(CONCURRENCY)))
    consumed = [r for r in results if r is True]
    assert len(consumed) == 3, f"expected exactly 3 consumes, got {len(consumed)}: {results}"
    assert _balance(data_dir, code) == 0


def test_the_harness_can_observe_a_consume(server):
    """NEGATIVE CONTROL. If pack_consumed never came back True — a renamed
    response field, a header the server ignores — both assertions above would
    be vacuous, because 0 == 0 is not the failure they are guarding."""
    base, data_dir = server
    code = _mint(data_dir, 1)
    assert _anchor(base, code, 0) is True, (
        "a lone anchor with a funded pack token did not report pack_consumed=True; "
        "the concurrency assertions cannot detect a double-spend"
    )
