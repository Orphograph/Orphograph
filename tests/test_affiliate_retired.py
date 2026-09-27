"""The account-level referral/affiliate program is retired (10A).

Founder decision 2026-09-27. It never worked end to end: /api/me/referral-code
handed out `ref_` + 8 hex of the account's email id, which the Stripe webhook
cannot resolve (only Pack claim-code referrals are), affiliate.register_signup
had no caller, so no signup was ever recorded and payouts could never be
owed, and no page offered any of it. Every GET also wrote the code registry.
Its three endpoints now answer 410 Gone, for GET and HEAD alike and whoever
asks, and write nothing. Pack referral links in claim emails are a different
program and keep working.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

REPO = Path(__file__).resolve().parent.parent
RETIRED_GET = ("/api/me/referral-code", "/api/me/affiliate")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("affiliate_retired")
    for base in _srv.server_processes(d, stub_calendars=True):
        yield base, d


def _signed_in(base: str, data_dir: Path) -> dict:
    code = (f"import os,sys;os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
            f"sys.path.insert(0,{str(REPO / 'server')!r});import auth;"
            "print(auth.issue_link_token('affiliate-retired@example.test')[0])")
    token = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60, check=True).stdout.strip()
    _s, _b, headers = _srv.request(base, f"/a/{token}", timeout=15)
    cookie = headers.get("Set-Cookie", "")
    assert "orpho_sid=" in cookie, "control: could not sign in"
    return {"Cookie": "orpho_sid=" + cookie.split("orpho_sid=", 1)[1].split(";", 1)[0]}


@pytest.mark.parametrize("path", RETIRED_GET)
def test_the_retired_reads_answer_gone_and_write_nothing(server, path):
    base, data_dir = server
    member = _signed_in(base, data_dir)
    registry = data_dir / "affiliate_codes.jsonl"
    # Compared before and after THIS path's requests: the data dir is shared
    # by both parametrized paths, and a write by one must not be pinned on
    # the other.
    before = registry.read_text() if registry.exists() else ""
    for headers in (member, {}):
        status, body, _h = _srv.request(base, path, headers=headers, timeout=15)
        assert status == 410, (path, headers, status, body)
        assert "still work" in json.loads(body)["detail"]
        head = _srv.request(base, path, method="HEAD", headers=headers, timeout=15)
        assert head[0] == 410
    after = registry.read_text() if registry.exists() else ""
    assert after == before, f"{path} wrote the code registry"


def test_the_payout_request_answers_gone(server):
    base, _data_dir = server
    status, body, _h = _srv.request(base, "/api/me/affiliate/payout", "POST",
                                    b'{"method":"credits"}',
                                    {"Content-Type": "application/json"}, timeout=15)
    assert status == 410, (status, body)
