"""/api/anchor_folder validates before it charges, and stays bounded anyway.

Moving validation ahead of every charge (this PR) also moved it ahead of the
only thing that bounded the work: the free limiter used to answer before the
body was read. After the move, an address with no allowance left, or one
sending manifests that fail, could make the server read, parse and re-hash up
to 8 MB each time without limit, and 25 concurrent corrupt manifests from one
fresh address reached 369 MB on a 512 MB machine. Now:

- a free caller with no allowance is answered before the body is parsed;
- a rejected manifest spends a per-address budget (10, then 1 per 6 minutes)
  that a valid manifest never touches;
- one address has one folder request in flight at a time.

Also here: review gaps on the routes this PR rewrote (a bogus pack token and
the free limiter on the single and folder routes; a subscriber whose pack is
spent, on batch) and a leaf size that overflowed int().
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

import _srv
from test_anchor_input_accounting import PATHS, TOKEN, payload, post, seed_credit

FOLDER = PATHS[2]


def _bad_root() -> bytes:
    body = payload(FOLDER)
    body["manifest"]["root_hex"] = "00" * 32
    return json.dumps(body).encode()


def _good() -> bytes:
    return json.dumps(payload(FOLDER)).encode()


def test_a_free_caller_with_no_allowance_is_answered_before_parsing(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY="1"):
        assert post(base, FOLDER, _good())[0] == 200
        # Not JSON at all: a 429 (not 400) means the answer came before the
        # body was parsed.
        status, body, headers = post(base, FOLDER, b"{" * 5000)
        assert status == 429, body
        assert headers.get("Retry-After")
        # And it did not count as a rejected manifest.
        for _ in range(12):
            assert post(base, FOLDER, b"{")[0] == 429


def test_rejected_manifests_spend_their_own_budget_only(tmp_path):
    ledger = seed_credit(tmp_path)
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        assert post(base, FOLDER, _good(), paid=True)[0] == 200  # control
        for _ in range(10):
            assert post(base, FOLDER, _bad_root(), paid=True)[0] == 400
        status, body, headers = post(base, FOLDER, _good(), paid=True)
        assert status == 429, body
        assert json.loads(body)["error"] == "too many rejected manifests"
        assert int(headers.get("Retry-After")) > 0
    # Only the one valid manifest was charged; the rejects cost nothing.
    assert sum(json.loads(l)["credits_delta"] for l in ledger.read_text().splitlines()) == 3


def test_one_folder_request_per_address_at_a_time(tmp_path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        with _srv.stalled_request(base, FOLDER, declared_length=100_000, sent=b'{"manifest"'):
            time.sleep(0.5)  # the stalled handler is inside its body read
            status, body, _ = post(base, FOLDER, _good())
            assert status == 429, body
            assert "already in progress" in json.loads(body)["error"]
        # Once the slow upload ends, the address is free again.
        deadline = time.monotonic() + 10
        while True:
            status, body, _ = post(base, FOLDER, _good())
            if status != 429 or time.monotonic() > deadline:
                break
            time.sleep(0.2)
        assert status == 200, body


@pytest.mark.parametrize("size", [b"Infinity", b"1e400", b"-Infinity"])
def test_a_leaf_size_that_overflows_is_a_400(tmp_path, size):
    body = _good().replace(b'"size_bytes": 1', b'"size_bytes": ' + size)
    assert size in body
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        status, response, _ = post(base, FOLDER, body)
        assert status == 400, response


@pytest.mark.parametrize("path", [PATHS[0], FOLDER])
def test_a_bogus_pack_token_does_not_bypass_the_free_limit(tmp_path, path):
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY="1"):
        headers = {"Content-Type": "application/json", "X-Pack-Token": "pk_not_a_real_code"}
        first = _srv.request(base, path, "POST", json.dumps(payload(path)).encode(), headers, timeout=30)
        assert first[0] == 200, first[1]
        second = _srv.request(base, path, "POST", json.dumps(payload(path)).encode(), headers, timeout=30)
        assert second[0] == 429, second[1]


SECRET = "folder-bounds-test"
EMAIL = "subscriber@example.test"
KEY = "orpho_boundsSubscriberKey123"


def _subscriber(tmp_path):
    def ledger(name, rows):
        (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    ledger("auth_sessions.jsonl", [dict(event="created", email=EMAIL,
           session_hash=hashlib.sha256(b"session-sub").hexdigest(),
           expires_unix=time.time() + 3600)])
    ledger("subscriptions.jsonl", [dict(email=EMAIL, status="active", stripe_sub="sub_b")])
    ledger("api_keys.jsonl", [dict(event="issued", email=EMAIL, key_prefix=KEY[:14],
           key_hash=hashlib.sha256(KEY.encode()).hexdigest())])
    ledger = tmp_path / "credit_ledger.jsonl"
    ledger.write_text(json.dumps({"claim_code": TOKEN, "email": "", "credits_delta": 1, "source": "t"}) + "\n"
                      + json.dumps({"claim_code": TOKEN, "credits_delta": -1, "source": "spent"}) + "\n")
    return ledger


@pytest.mark.parametrize("auth", ["api_key", "session"])
def test_a_subscriber_with_a_spent_pack_still_anchors_a_batch(tmp_path, auth):
    """The batch 402 for an unusable pack token must not reach a caller whose
    subscription pays anyway."""
    ledger = _subscriber(tmp_path)
    before = ledger.read_bytes()
    headers = {"Content-Type": "application/json", "X-Pack-Token": TOKEN}
    headers.update({"X-Orpho-Api-Key": KEY} if auth == "api_key"
                   else {"Cookie": "orpho_sid=session-sub"})
    for base in _srv.server_processes(tmp_path, stub_calendars=True, RATE_LIMIT_PER_DAY="1",
                                      ORPHO_HMAC_SECRET=SECRET):
        for _ in range(2):  # twice: the free budget (1) is not what pays
            status, body, _ = _srv.request(base, PATHS[1], "POST",
                                           json.dumps(payload(PATHS[1])).encode(), headers, timeout=30)
            assert status == 200, body
            assert json.loads(body)["succeeded"] == 1
        # Control: an anonymous caller from the same address still has its one
        # free batch, so the subscriber's batches did not spend it.
        status, body, _ = _srv.request(base, PATHS[1], "POST", json.dumps(payload(PATHS[1])).encode(),
                                       {"Content-Type": "application/json"}, timeout=30)
        assert status == 200, body
    assert ledger.read_bytes() == before
    rid = json.loads(body)["results"][0]["receipt_id"]
    assert hmac.new(SECRET.encode(), EMAIL.encode(), hashlib.sha256).hexdigest()[:16]  # id fn sanity
    assert (tmp_path / "receipts" / rid / "receipt.json").exists()
