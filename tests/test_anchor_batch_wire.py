#!/usr/bin/env python3
"""test_anchor_batch_wire.py — drive the batch endpoint, do not read it.

/docs/agents tells agents this about the batch API:

    "the free tier consumes a single rate-limit token for the whole batch"

Before this file, that sentence was backed by reading `_handle_anchor_batch` in
server/app.py and nothing else. No test asserted it: the only mentions of
`/api/anchor/batch` under tests/ were a CSRF route list and a docs cap check,
and greps for `free_limit_reached` and `ANCHOR_RATE_CAPACITY` across tests/
returned nothing. A 2026-09-07 claim-audit "verified" the sentence against
source and stopped there, which is precisely the failure the wire-path rule
names: engine-level green says nothing about whether a behaviour survives the
request path.

So this drives the real HTTP endpoint on a real server process and counts
tokens by EXHAUSTING the bucket, which is the only way to tell "one token for
the batch" apart from "one token per item" from the outside.

LEAKAGE NOTE. `_srv.base_env` sets RATE_LIMIT_PER_DAY=100000 by default,
deliberately, so that a 429 in an unrelated test is never mistaken for a
handler verdict. These tests need the opposite and set a small capacity
explicitly. That makes the limiter the component under test, so each test
below states which component is expected to answer, and the arithmetic only
works if it is the anchor bucket and not some other gate.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import _srv

CAPACITY = 4
# 64-hex digests. Distinct per item so a server that deduplicates cannot make
# a per-item charge look like a single charge.
HASHES = [f"{i:064x}" for i in range(1, 40)]


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    """Small bucket. For the two tests whose SUBJECT is token accounting."""
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("batchwire"),
        stub_calendars=True,
        RATE_LIMIT_PER_DAY=str(CAPACITY),
    )


@pytest.fixture(scope="module")
def roomy(tmp_path_factory):
    """Separate server, bucket effectively unlimited.

    LEAKAGE, caught on this file's first run (2026-09-08). The validation-shape
    tests below originally shared the small-bucket fixture. By the time they
    ran, the accounting tests above had drained it, so the limiter answered 429
    before the handler ever parsed the body — and the tests failed reporting
    that a 61-item batch "was not refused" when in truth it was never READ.
    Infra answering ahead of the target is the exact shape _srv.base_env's
    RATE_LIMIT_PER_DAY=100000 default exists to prevent; these tests opted out
    of that default for no reason and paid for it.
    """
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("batchroomy"),
        stub_calendars=True,
    )


def _post(base, path, payload):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body[:200]}


def test_batch_of_many_consumes_exactly_one_free_token(base):
    """The claim, on the wire.

    Capacity is 4. Send a batch of 10 in one request, then single anchors
    until the bucket refuses. If the batch cost one token, exactly 3 singles
    succeed before the 429. If it cost one per item, the batch itself would
    have been refused or would have drained the bucket, and the first single
    would already 429.
    """
    status, body = _post(base, "/api/anchor/batch",
                         {"hashes": [{"hash_hex": h} for h in HASHES[:10]]})
    assert status == 200, f"batch of 10 refused at capacity {CAPACITY}: {status} {body}"

    singles_ok = 0
    for h in HASHES[10:10 + CAPACITY + 2]:
        s, b = _post(base, "/api/anchor", {"hash_hex": h})
        if s == 429:
            break
        assert s == 200, f"unexpected {s} from /api/anchor: {b}"
        singles_ok += 1
    else:
        pytest.fail(f"bucket never refused after {singles_ok} singles; "
                    f"RATE_LIMIT_PER_DAY={CAPACITY} did not take effect, so "
                    "this test cannot tell one token from ten")

    assert singles_ok == CAPACITY - 1, (
        f"batch consumed {CAPACITY - singles_ok} token(s), not 1. "
        f"{singles_ok} singles succeeded after it; expected {CAPACITY - 1}. "
        "The /docs/agents sentence about one token per batch is wrong on the wire.")


def test_the_refusal_comes_from_the_anchor_limiter(base):
    """Guard against the leakage this suite has been burned by before.

    The arithmetic above is only meaningful if the 429 is the ANCHOR bucket's
    verdict. A 429 from any other gate would produce the same number and mean
    nothing, so assert on the body the anchor limiter is documented to send.
    """
    for h in HASHES[:CAPACITY + 3]:
        status, body = _post(base, "/api/anchor", {"hash_hex": h})
        if status == 429:
            assert "limit" in json.dumps(body).lower(), (
                f"429 body does not look like the anchor limiter's: {body}")
            assert "limit_per_day" in body or "retry_after" in body, (
                f"429 lacks the anchor limiter's fields, so some other gate "
                f"answered: {body}")
            assert body.get("limit_per_day", CAPACITY) == CAPACITY, (
                f"429 reports a different capacity than we configured: {body}")
            return
    pytest.fail("the anchor limiter never refused; the capacity knob is not wired")


def test_batch_cap_is_enforced_on_the_wire(roomy):
    """MAX_BATCH_ITEMS = 50 in server/app.py. /docs/agents publishes the cap.

    A cap that only exists in the constant is not a cap.
    """
    over = [{"hash_hex": f"{i:064x}"} for i in range(60)]
    status, body = _post(roomy, "/api/anchor/batch", {"hashes": over})
    assert status == 400, f"61-item batch was not refused: {status} {body}"
    assert "too many" in json.dumps(body).lower(), body


def test_empty_and_malformed_batches_are_refused(roomy):
    """Error map: every documented failure shape, through the real handler."""
    for payload, why in (
        ({"hashes": []}, "empty array"),
        ({"hashes": "nope"}, "non-array"),
        ({}, "missing key"),
    ):
        status, body = _post(roomy, "/api/anchor/batch", payload)
        assert status == 400, f"{why} accepted: {status} {body}"
