from __future__ import annotations

import pytest

import credits
import referrals


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credits.jsonl")
    monkeypatch.setattr(referrals, "REFERRAL_LEDGER", tmp_path / "referrals.jsonl")
    yield


def test_code_for_returns_predictable_prefix():
    assert referrals.code_for("pk_abc123def456ghi") == "ref_abc123def456"


def test_code_for_handles_bad_input():
    assert referrals.code_for("") == ""
    assert referrals.code_for("not_a_pack_code") == ""


def test_apply_credits_both_parties(tmp_path):
    referrer = "pk_alice12345678"
    credits.add_credits(referrer, "alice@b.com", 10, "stripe:cs_alice")
    new_buyer = "pk_bob9876543210"
    credits.add_credits(new_buyer, "bob@b.com", 10, "stripe:cs_bob")

    ref_code = referrals.code_for(referrer)
    # ref_code is "ref_" + claim_code[3:15] = "ref_alice1234567" (12-char slice)
    assert ref_code.startswith("ref_alice")
    assert len(ref_code) == 4 + 12

    result = referrals.apply(ref_code, "bob@b.com", new_buyer)
    assert result["ok"] is True
    assert result["bonus_credits"] == 10

    # Both balances bumped by 10.
    assert credits.balance(referrer) == 20  # 10 original + 10 reward
    assert credits.balance(new_buyer) == 20  # 10 original + 10 bonus


def test_apply_is_idempotent_on_replay():
    referrer = "pk_alice12345678"
    credits.add_credits(referrer, "alice@b.com", 10, "stripe:cs")
    new_buyer = "pk_bob9876543210"
    credits.add_credits(new_buyer, "bob@b.com", 10, "stripe:cs2")
    ref_code = referrals.code_for(referrer)

    first = referrals.apply(ref_code, "bob@b.com", new_buyer)
    second = referrals.apply(ref_code, "bob@b.com", new_buyer)
    assert first["ok"] is True
    assert second["ok"] is False
    assert "already credited" in second["reason"]
    # Balances must NOT be double-credited.
    assert credits.balance(referrer) == 20
    assert credits.balance(new_buyer) == 20


def test_apply_blocks_self_referral():
    code = "pk_alice12345678"
    credits.add_credits(code, "alice@b.com", 10, "stripe:cs")
    ref_code = referrals.code_for(code)
    result = referrals.apply(ref_code, "alice@b.com", code)
    assert result["ok"] is False
    assert "self-refer" in result["reason"]


def test_apply_rejects_unknown_ref_code():
    result = referrals.apply("ref_nonexistent", "bob@b.com", "pk_bob123456")
    assert result["ok"] is False
    assert result["reason"] == "unknown referral code"


def test_apply_rejects_empty_inputs():
    assert referrals.apply("", "", "")["ok"] is False
    assert referrals.apply("ref_x", "", "pk_y")["ok"] is False
    assert referrals.apply("ref_x", "a@b.com", "")["ok"] is False


def test_apply_credits_different_buyers_with_same_ref():
    referrer = "pk_alice12345678"
    credits.add_credits(referrer, "alice@b.com", 10, "stripe:cs")
    ref_code = referrals.code_for(referrer)

    buyer1 = "pk_bob1234567890"
    credits.add_credits(buyer1, "bob@b.com", 10, "stripe:cs2")
    buyer2 = "pk_carol987654321"
    credits.add_credits(buyer2, "carol@b.com", 10, "stripe:cs3")

    r1 = referrals.apply(ref_code, "bob@b.com", buyer1)
    r2 = referrals.apply(ref_code, "carol@b.com", buyer2)
    assert r1["ok"] is True and r2["ok"] is True

    # Referrer credited twice: +10 + 10.
    assert credits.balance(referrer) == 30  # 10 + 10 + 10
    assert credits.balance(buyer1) == 20
    assert credits.balance(buyer2) == 20
