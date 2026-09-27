"""A referral code must not be a piece of the bearer claim code, and must
resolve only to the pack it was made for.

Found 2026-09-27 (full-cycle, @referral-code-leak), each reproduced against
the handler before any change:

1. THE CODE WAS THE CREDENTIAL. `ref_` + claim_code[3:15] put 12 of the 16
   random characters (72 of 96 bits) of the bearer claim code into a link the
   claim email tells the buyer to share. Anyone holding the link was 24 bits
   from spending the pack.
2. ANY PREFIX MATCHED. The lookup accepted a needle of any length, so the code
   `ref_` (empty needle) resolved to the first pack in the ledger: any buyer
   who typed it got the +10 bonus and credited a stranger's pack.
3. THE SELF-REFERRAL GUARD COULD NOT FIRE. It compared the referrer's claim
   code with the NEW one, which are never equal; a repeat buyer using an
   earlier pack's code collected both bonuses.

Links already sent in claim emails (legacy 12-character codes) keep working,
matched exactly.
"""
from __future__ import annotations

import json

import pytest

import auth
import credits
import mailer
import referrals
import stripe_webhook

ALICE_PACK = "pk_aliceAAAAbbbbCCCC"
BOB_PACK = "pk_bobDDDDeeeeFFFFgg"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credits.jsonl")
    monkeypatch.setattr(referrals, "REFERRAL_LEDGER", tmp_path / "referrals.jsonl")
    # A fixed secret: deterministic codes, and nothing written to disk.
    monkeypatch.setattr(auth, "_HMAC_SECRET_CACHE", b"test-referral-secret-0123456789ab")
    yield


def _windows(s: str, n: int = 6) -> set[str]:
    return {s[i:i + n] for i in range(len(s) - n + 1)}


# --- 1. the code is not a piece of the credential ------------------------------

def test_a_ref_code_carries_no_piece_of_the_claim_code():
    code = referrals.code_for(ALICE_PACK)
    assert code.startswith("ref_"), code
    assert not (_windows(code[4:]) & _windows(ALICE_PACK[3:])), (
        f"{code} repeats part of the bearer claim code")
    assert referrals.code_for(ALICE_PACK) == code, "must be stable: it is printed in an email"
    assert referrals.code_for(BOB_PACK) != code


def test_the_claim_email_shares_the_new_code_not_a_slice(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html, *a, **k:
                        sent.append(text + html) or True)
    mailer.send_pack_claim_email("alice@example.test", ALICE_PACK, 10)
    assert len(sent) == 1
    body = sent[0]
    assert "?ref=" + referrals.code_for(ALICE_PACK) in body, "the share link must carry the new code"
    assert "ref_" + ALICE_PACK[3:15] not in body, "the old slice-of-the-credential code is still mailed"


# --- 2. a code resolves to exactly one pack ----------------------------------------

def test_the_new_code_credits_its_own_pack():
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    result = referrals.apply(referrals.code_for(ALICE_PACK), "bob@example.test", BOB_PACK)
    assert result["ok"] is True, result
    assert credits.balance(ALICE_PACK) == 20 and credits.balance(BOB_PACK) == 20


def test_a_legacy_code_already_emailed_still_works():
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    legacy = "ref_" + ALICE_PACK[3:15]
    result = referrals.apply(legacy, "bob@example.test", BOB_PACK)
    assert result["ok"] is True, result
    assert credits.balance(ALICE_PACK) == 20


@pytest.mark.parametrize("partial", [
    "ref_",                                  # empty needle: matched the first pack
    "ref_a",                                 # one character
    "ref_" + ALICE_PACK[3:14],               # 11 of the legacy 12
    "ref_" + ALICE_PACK[3:16],               # 13: longer than any legacy code
    "ref_" + ALICE_PACK[3:],                 # the whole bearer tail
])
def test_a_partial_code_matches_nothing(partial):
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    result = referrals.apply(partial, "bob@example.test", BOB_PACK)
    assert result == {"ok": False, "reason": "unknown referral code"}, (partial, result)
    assert credits.balance(ALICE_PACK) == 10 and credits.balance(BOB_PACK) == 10


# --- 3. a buyer cannot refer themselves ------------------------------------------

@pytest.mark.parametrize("buyer", ["alice@example.test", "Alice@Example.TEST"])
def test_a_buyer_cannot_refer_themselves_with_an_earlier_pack(buyer):
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    second = "pk_aliceSECONDpack01"
    credits.add_credits(second, buyer, 10, "stripe:cs_alice2")
    for code in (referrals.code_for(ALICE_PACK), "ref_" + ALICE_PACK[3:15]):
        result = referrals.apply(code, buyer, second)
        assert result["ok"] is False and "self-refer" in result["reason"], (code, result)
    assert credits.balance(ALICE_PACK) == 10 and credits.balance(second) == 10


# --- the real entry point: a Stripe event carrying the code -----------------------

def _completed(event_id: str, session_id: str, email: str, ref: str) -> bytes:
    return json.dumps({"id": event_id, "type": "checkout.session.completed", "data": {"object": {
        "id": session_id, "mode": "payment", "payment_status": "paid",
        "customer_email": email, "metadata": {"ref_code": ref}}}}).encode()


def test_the_webhook_applies_the_new_code_and_refuses_the_empty_one(tmp_path, monkeypatch):
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", tmp_path / "pi_map.jsonl")
    codes: list[str] = []
    monkeypatch.setattr(stripe_webhook.mailer, "send_pack_claim_email",
                        lambda to, code, n: codes.append(code) or True)
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")

    stripe_webhook.handle_event(_completed("evt_r1", "cs_bob1", "bob@example.test",
                                           referrals.code_for(ALICE_PACK)))
    assert credits.balance(ALICE_PACK) == 20, "the referrer was not credited"
    assert credits.balance(codes[-1]) == 20, "the referred buyer did not get the bonus"

    stripe_webhook.handle_event(_completed("evt_r2", "cs_carol1", "carol@example.test", "ref_"))
    assert credits.balance(codes[-1]) == 10, "an empty code still earned the bonus"
    assert credits.balance(ALICE_PACK) == 20, "an empty code credited a stranger's pack"


# --- review round 1 (/code-review high 275) ----------------------------------------

_APP_JS_DRIVER = r"""
const fs = require("fs");
const [src, search] = process.argv.slice(2);
const text = fs.readFileSync(src, "utf8");
const start = text.indexOf("function readReferralCode()");
const end = text.indexOf("\nfunction ", start + 1);
const fnSrc = text.slice(start, end);
const localStorage = { setItem() {}, getItem() { return ""; } };
const location = { search, hash: "" };
const readReferralCode = new Function("location", "localStorage", "URLSearchParams",
  fnSrc + "\nreturn readReferralCode();");
process.stdout.write(readReferralCode(location, localStorage, URLSearchParams));
"""


def _as_the_site_sends_it(tmp_path, link_ref: str) -> str:
    """Run the real readReferralCode from web/app.js on a ?ref= link: what the
    site puts into Stripe metadata is what the webhook receives."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real web/app.js")
    driver = tmp_path / "ref_driver.js"
    driver.write_text(_APP_JS_DRIVER)
    app_js = Path(__file__).resolve().parent.parent / "web" / "app.js"
    out = subprocess.run([node, str(driver), str(app_js), "?ref=" + link_ref],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_legacy_link_works_after_the_site_lowercases_it(tmp_path):
    """web/app.js lowercases every ?ref= before checkout, and a legacy code is
    mixed-case base64: matched case-sensitively, no mailed link could ever
    credit anyone (found by /code-review high 275)."""
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    sent = _as_the_site_sends_it(tmp_path, "ref_" + ALICE_PACK[3:15])
    assert sent == ("ref_" + ALICE_PACK[3:15]).lower(), sent  # control: the site did fold it
    result = referrals.apply(sent, "bob@example.test", BOB_PACK)
    assert result["ok"] is True, result
    assert credits.balance(ALICE_PACK) == 20


def test_the_new_code_survives_the_site_unchanged(tmp_path):
    code = referrals.code_for(ALICE_PACK)
    assert _as_the_site_sends_it(tmp_path, code) == code


@pytest.mark.parametrize("again_as", ["bob@example.test", "Bob@Example.TEST"])
def test_one_buyer_is_credited_once_per_referrer_whichever_code_they_use(again_as):
    """A pack has two valid codes (the legacy one already mailed, the new one in
    any re-sent email), and the dedupe keyed on the code string and the raw
    address: the same buyer took the bonus twice (found by /code-review high 275)."""
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    second = "pk_bobSECONDpack0001"
    credits.add_credits(second, again_as, 10, "stripe:cs_bob2")
    first = referrals.apply("ref_" + ALICE_PACK[3:15], "bob@example.test", BOB_PACK)
    assert first["ok"] is True, first
    again = referrals.apply(referrals.code_for(ALICE_PACK), again_as, second)
    assert again["ok"] is False and "already credited" in again["reason"], again
    assert credits.balance(ALICE_PACK) == 20 and credits.balance(second) == 10


def test_the_claim_email_goes_out_even_if_the_share_link_cannot_be_made(monkeypatch):
    """The claim code is the only way to spend a pack; the referral link is
    optional. A failure making the link must not stop the email (found by
    /code-review high 275)."""
    sent: list[str] = []
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html, *a, **k:
                        sent.append(text) or True)

    def _boom(_claim):
        raise OSError("secret unreadable")
    monkeypatch.setattr(referrals, "code_for", _boom)
    assert mailer.send_pack_claim_email("alice@example.test", ALICE_PACK, 10) is True
    assert len(sent) == 1 and ALICE_PACK in sent[0], "the claim code was not delivered"
    assert "?ref=" not in sent[0]


# --- review round 2 (adversarial review of 2d10369) ------------------------------

def test_a_credited_legacy_code_is_not_written_to_the_log(tmp_path, capsys):
    """Once legacy links matched (round 1), the success line printed the raw
    code: 12 claim-code characters in the stream the access-log fix protects.
    The line names the pack by its current code instead."""
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    capsys.readouterr()
    legacy = ("ref_" + ALICE_PACK[3:15]).lower()
    assert referrals.apply(legacy, "bob@example.test", BOB_PACK)["ok"] is True  # control
    err = capsys.readouterr().err
    assert ALICE_PACK[3:15].lower() not in err.lower(), err
    assert referrals.code_for(ALICE_PACK) in err, "the line should still say which pack"


def test_a_buyer_is_credited_once_per_referrer_not_once_per_pack():
    """The guardrail is one credit per referee per REFERRER. Keyed on the pack,
    a referrer holding two packs let the same buyer be credited twice."""
    credits.add_credits(ALICE_PACK, "alice@example.test", 10, "stripe:cs_alice")
    alice_two = "pk_aliceTWOpack00001"
    credits.add_credits(alice_two, "Alice@Example.test", 10, "stripe:cs_alice2")
    credits.add_credits(BOB_PACK, "bob@example.test", 10, "stripe:cs_bob")
    bob_two = "pk_bobTWOpack0000001"
    credits.add_credits(bob_two, "bob@example.test", 10, "stripe:cs_bob2")
    assert referrals.apply(referrals.code_for(ALICE_PACK), "bob@example.test", BOB_PACK)["ok"] is True
    again = referrals.apply(referrals.code_for(alice_two), "bob@example.test", bob_two)
    assert again["ok"] is False and "already credited" in again["reason"], again
    assert credits.balance(alice_two) == 10 and credits.balance(bob_two) == 10
    # Control: a different buyer through Alice's second pack is still credited.
    carol = "pk_carolPACK00000001"
    credits.add_credits(carol, "carol@example.test", 10, "stripe:cs_carol")
    assert referrals.apply(referrals.code_for(alice_two), "carol@example.test", carol)["ok"] is True
