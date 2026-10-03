"""Founder decision 2026-09-28 (option A): with no active subscription, a
PAST-DUE subscription is what the account sees and what Cancel acts on.

What was wrong, each reproduced over HTTP before the fix:

  1. With nothing active the account read the newest row of ALL its
     subscriptions. Subscription A past due and subscription B cancelled
     afterwards: the account said "canceled", and Cancel sent B to Stripe, the
     one that had already ended, while Stripe kept retrying A's charge. An
     abandoned attempt (`incomplete`) landing after A did the same.
  2. The account page offered Cancel only to an active subscription, so even
     a lone past-due one read "Not active" with nothing to press.
  3. Checkout refused a second subscription only to an ACTIVE account. A
     past-due one is still being retried, so a second checkout bills twice
     the day the retry succeeds.

Past due gives no access: every entitlement check still reads is_active.
Only `past_due` is preferred. `incomplete` is not: such a row never ages out
here, and preferring it let an abandoned attempt speak for the account (tried
and removed 2026-09-27).

Found in review of that change (ff7cbd5), each reproduced over HTTP first:

  4. A past-due row whose subscription Stripe had since deleted stranded the
     account: checkout refused it until it cancelled, and Cancel answered 503
     because Stripe said it had no such subscription. That answer now ends
     the subscription here.
  5. With nothing active or past due, the newest row of all still spoke, so
     an abandoned attempt after a cancellation named the attempt. An attempt
     (`incomplete`, or `incomplete_expired` a day later) now never names the
     account's subscription.

Everything goes through the real routes of one real server process, with
Stripe replaced inside it (tests/_run_server.py --stub-stripe records each
call and sends nothing). The page is the real web/account.js run in node
against the answers that server gave.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import _srv

WEB = Path(__file__).resolve().parent.parent / "web"
FUTURE = time.time() + 20 * 86400

# name -> (the address the session signed in with, the address on the rows)
ACCOUNTS = {
    "pat": ("pat@example.test", "pat@example.test"),    # A past due, B cancelled later
    "ira": ("ira@example.test", "ira@example.test"),    # A past due, an abandoned attempt later
    "ana": ("ana@example.test", "ana@example.test"),    # one active, one past due
    "inc": ("inc@example.test", "inc@example.test"),    # an abandoned attempt, nothing else
    "cal": ("cal@example.test", "cal@example.test"),    # cancelled, nothing else
    "mia": ("mia@example.test", "Mia@Example.test"),    # pat's rows, stored in another case
    "leg": ("leg@example.test", "leg@example.test"),    # a past-due row naming no subscription
    "new": ("new@example.test", "new@example.test"),    # never subscribed
    "old": ("old@example.test", "old@example.test"),    # past due, but Stripe no longer has it
    "url": ("url@example.test", "url@example.test"),    # past due, Stripe refuses: wrong URL
    "itm": ("itm@example.test", "itm@example.test"),    # past due, Stripe refuses: some other object
    "abe": ("abe@example.test", "abe@example.test"),    # cancelled, then an abandoned attempt
    "exp": ("exp@example.test", "exp@example.test"),    # cancelled, then an attempt that expired
    "ixp": ("ixp@example.test", "ixp@example.test"),    # an expired attempt, nothing else
    "due": ("due@example.test", "due@example.test"),    # past due, then an attempt that expired
}
# name -> its rows in ledger order, as (subscription, status)
HISTORY = {
    "pat": [("sub_pat_due", "active"), ("sub_pat_other", "active"),
            ("sub_pat_due", "past_due"), ("sub_pat_other", "canceled")],
    "ira": [("sub_ira_due", "active"), ("sub_ira_due", "past_due"),
            ("sub_ira_try", "incomplete")],
    "ana": [("sub_ana_paid", "active"), ("sub_ana_due", "active"),
            ("sub_ana_due", "past_due")],
    "inc": [("sub_inc_try", "incomplete")],
    "cal": [("sub_cal_old", "active"), ("sub_cal_old", "canceled")],
    "mia": [("sub_mia_due", "active"), ("sub_mia_other", "active"),
            ("sub_mia_due", "past_due"), ("sub_mia_other", "canceled")],
    "leg": [("", "past_due")],
    "new": [],
    "old": [("sub_old_due", "active"), ("sub_old_due", "past_due")],
    "url": [("sub_url_due", "active"), ("sub_url_due", "past_due")],
    "itm": [("sub_itm_due", "active"), ("sub_itm_due", "past_due")],
    "abe": [("sub_abe_paid", "active"), ("sub_abe_paid", "canceled"),
            ("sub_abe_try", "incomplete")],
    "exp": [("sub_exp_paid", "active"), ("sub_exp_paid", "canceled"),
            ("sub_exp_try", "incomplete"), ("sub_exp_try", "incomplete_expired")],
    "ixp": [("sub_ixp_try", "incomplete"), ("sub_ixp_try", "incomplete_expired")],
    "due": [("sub_due_due", "active"), ("sub_due_due", "past_due"),
            ("sub_due_try", "incomplete"), ("sub_due_try", "incomplete_expired")],
}


def _append(path: Path, *rows: dict) -> None:
    with path.open("a") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")


def _seed(data: Path) -> None:
    for name, (signed_in, on_rows) in ACCOUNTS.items():
        _append(data / "auth_sessions.jsonl", {
            "event": "created", "session_hash": hashlib.sha256(f"sess-{name}".encode()).hexdigest(),
            "email": signed_in, "expires_unix": time.time() + 86400})
        for sub, status in HISTORY[name]:
            # Stripe makes a new customer for every checkout.
            customer = f"cus_{sub[4:]}" if sub else f"cus_{name}_by_hand"
            if sub:
                _append(data / "stripe_customer_emails.jsonl", {
                    "ts": "2026-09-01T00:00:00+00:00", "stripe_customer": customer,
                    "email": on_rows})
            _append(data / "subscriptions.jsonl", {
                "ts": "2026-09-01T00:00:00+00:00",
                "event_type": "customer.subscription.updated",
                "stripe_customer": customer, "stripe_sub": sub, "email": on_rows,
                "status": status, "current_period_end": FUTURE,
                "cancel_at_period_end": False})


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    data = tmp_path_factory.mktemp("past_due")
    _seed(data)
    for base in _srv.server_processes(data, stub_calendars=True, stub_stripe=True,
                                      STRIPE_SECRET_KEY="sk_test_not_a_real_key",
                                      STRIPE_PRICE_SUB="price_test_sub",
                                      STRIPE_PRICE_PACK="price_test_pack",
                                      # every test here shares one address
                                      CHECKOUT_RATE_PER_HOUR="1000"):
        yield base, data


def _cookie(name: str) -> dict:
    return {"Cookie": f"orpho_sid=sess-{name}"}


def _me(base: str, name: str) -> dict:
    status, me = _srv.get_json(base, "/api/me", headers=_cookie(name))
    return _srv.ok_json(status, me)


def _stripe_calls(data: Path) -> list[dict]:
    path = data / "stub_stripe_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _script_stripe(data: Path, answers: dict) -> None:
    """Have the stub answer a path with a real Stripe error, e.g.
    {"/subscriptions/sub_x": (404, "resource_missing", "No such subscription")}.
    An empty dict puts every path back to success."""
    (data / "stub_stripe_answers.json").write_text(json.dumps({
        path: {"status": status, "error": {"type": "invalid_request_error",
                                           "code": code, "message": message}}
        for path, (status, code, message) in answers.items()}))


def _cancel(base: str, data: Path, name: str) -> tuple[int, dict, list[dict]]:
    """POST the cancel as `name`: (status, answer, the Stripe calls it made)."""
    before = len(_stripe_calls(data))
    status, raw, _h = _srv.request(base, "/api/me/cancel-subscription", "POST", b"",
                                   _cookie(name))
    return status, json.loads(raw), _stripe_calls(data)[before:]


def _checkout(base: str, data: Path, name: str | None, plan: str) -> tuple[int, dict, list[dict]]:
    before = len(_stripe_calls(data))
    status, answer = _srv.post_json(base, "/api/stripe/checkout", {"plan": plan},
                                    _cookie(name) if name else None)
    return status, answer, _stripe_calls(data)[before:]


# The real web/account.js in node. The DOM stub starts each element hidden or
# shown as web/account.html writes it, so "Cancel is offered" means the script
# un-hid it. /api/me and the cancel answer are the ones the running server
# gave; any other request never answers, which leaves main() idle after it
# has wired the account card.
_PAGE_JS = r"""
const fs = require("fs");
const vm = require("vm");
const [src, me, cancelStatus, cancelBody, hiddenIds] = process.argv.slice(2);
const startsHidden = new Set(JSON.parse(hiddenIds));
function mk(id) {
  const handlers = {};
  return {
    hidden: startsHidden.has(id), textContent: "", value: "", disabled: false,
    style: {}, dataset: {}, className: "", type: "",
    handlers,
    addEventListener(ev, fn) { handlers[ev] = fn; },
    replaceChildren() { this.textContent = ""; },
    appendChild(c) { this.textContent += c.textContent || ""; return c; },
    querySelector() { return mk(""); },
    querySelectorAll() { return []; },
  };
}
const els = new Map();
const el = (sel) => {
  if (!els.has(sel)) els.set(sel, mk(sel.startsWith("#") ? sel.slice(1) : ""));
  return els.get(sel);
};
const document = {
  querySelector: el,
  querySelectorAll: () => [],
  getElementById: (id) => el("#" + id),
  createElement: () => mk(""),
  addEventListener() {},
};
const sent = [];
const asked = [];
const answer = (status, body) => Promise.resolve({
  ok: status >= 200 && status < 300, status,
  json: async () => JSON.parse(body), text: async () => body });
const fetch = (url, opts) => {
  sent.push({ url, method: (opts && opts.method) || "GET" });
  if (url === "/api/me") return answer(200, me);
  if (url === "/api/me/cancel-subscription") return answer(Number(cancelStatus), cancelBody);
  return new Promise(() => {});
};
const confirm = (text) => { asked.push(text); return true; };
const ctx = vm.createContext({ document, fetch, confirm, console, setTimeout, clearTimeout,
                               location: { reload() {} }, navigator: {}, window: {},
                               URLSearchParams, Date });
vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
(async () => {
  // A timer runs only after main() has gone as far as it can without one.
  await new Promise((r) => setTimeout(r, 0));
  const out = {
    status: el("#sub-status").textContent,
    plan: el("#plan-label").textContent,
    cancel_offered: !el("#cancel-sub").hidden,
    reactivate_offered: !el("#reactivate-sub").hidden,
    api_section_shown: !el("#api-section").hidden,
  };
  if (out.cancel_offered) {
    await el("#cancel-sub").handlers.click();
    out.asked = asked;
    out.message = el("#sub-action-msg").hidden ? "" : el("#sub-action-msg").textContent;
  }
  out.posted = sent.filter((s) => s.method === "POST").map((s) => s.url);
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error((e && e.stack) || String(e)); process.exit(1); });
"""


def _hidden_in_the_markup() -> list[str]:
    html = (WEB / "account.html").read_text(encoding="utf-8")
    hidden = re.findall(r'<[a-z]+\b(?=[^>]*\shidden[\s>])[^>]*\bid="([^"]+)"', html)
    # Without these two the page check below could not tell a button the
    # script offered from one that was never hidden.
    assert {"cancel-sub", "reactivate-sub", "api-section"} <= set(hidden), hidden
    return hidden


def _page(tmp_path: Path, me: dict, cancel_status: int = 200, cancel_answer: dict | None = None) -> dict:
    node = shutil.which("node")
    assert node, "node is not on PATH: the account page check runs the real web/account.js"
    driver = tmp_path / "account_page.js"
    driver.write_text(_PAGE_JS)
    proc = subprocess.run(
        [node, str(driver), str(WEB / "account.js"), json.dumps(me), str(cancel_status),
         json.dumps(cancel_answer or {}), json.dumps(_hidden_in_the_markup())],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# --- (a) past due speaks when nothing is active, and Cancel reaches it ---------------

LATER = pytest.mark.parametrize("name", ["pat", "ira"],
                                ids=["another-cancelled-later", "an-abandoned-attempt-later"])


@LATER
def test_the_account_reads_the_past_due_subscription(srv, name):
    base, _data = srv
    me = _me(base, name)
    assert (me["subscription_status"] or {}).get("status") == "past_due", me
    assert me["subscription_status"]["stripe_sub"] == f"sub_{name}_due", me
    assert me.get("subscription_past_due") is True, me


@LATER
def test_cancel_reaches_the_past_due_subscription(srv, name):
    base, data = srv
    status, answer, calls = _cancel(base, data, name)
    assert status == 200, answer
    assert calls == [{"method": "POST", "path": f"/subscriptions/sub_{name}_due",
                      "form": {"cancel_at_period_end": "true"}}], calls
    # Past due gives no access, so the words must not promise any.
    assert "access" not in answer["message"].lower(), answer


@LATER
def test_the_account_page_says_past_due_and_offers_cancel(srv, tmp_path, name):
    base, data = srv
    me = _me(base, name)
    status, answer, _calls = _cancel(base, data, name)
    page = _page(tmp_path, me, status, answer)
    assert "past due" in page["status"].lower(), page
    assert page["cancel_offered"] is True, page
    assert page["posted"] == ["/api/me/cancel-subscription"], page
    assert page["message"] == answer["message"], page
    assert len(page["asked"]) == 1 and "access" not in page["asked"][0].lower(), page


# --- past due grants nothing ----------------------------------------------------------

@LATER
def test_past_due_gives_no_access(srv, tmp_path, name):
    """Control. The account is on the free tier while the payment is behind."""
    base, _data = srv
    me = _me(base, name)
    assert me["subscription_active"] is False and me["plan"] is None, me
    status, raw, _h = _srv.request(base, "/api/me/anchors.zip", headers=_cookie(name))
    assert status == 402, (status, raw[:200])
    page = _page(tmp_path, me)
    assert page["plan"] == "Free tier" and page["api_section_shown"] is False, page


# --- (b) active wins ------------------------------------------------------------------

def test_an_active_subscription_still_wins_over_a_past_due_one(srv, tmp_path):
    """Control. The past-due row is the newer of the two."""
    base, data = srv
    me = _me(base, "ana")
    assert me["subscription_active"] is True, me
    assert me["subscription_status"]["stripe_sub"] == "sub_ana_paid", me
    assert not me.get("subscription_past_due"), me
    status, answer, calls = _cancel(base, data, "ana")
    assert status == 200 and [c["path"] for c in calls] == ["/subscriptions/sub_ana_paid"], calls
    assert "keep access" in answer["message"], answer
    page = _page(tmp_path, me, status, answer)
    assert page["status"] == "Active" and page["cancel_offered"] is True, page
    assert "keep access" in page["asked"][0], page


# --- (c) an abandoned attempt is not shown ---------------------------------------------

def test_an_abandoned_attempt_is_not_shown_as_a_subscription(srv, tmp_path):
    """Control. `incomplete` is not past due: nothing to cancel is offered,
    and the account can still subscribe."""
    base, data = srv
    me = _me(base, "inc")
    assert me["subscription_active"] is False, me
    assert not me.get("subscription_past_due"), me
    page = _page(tmp_path, me)
    assert page["status"] == "Not active", page
    assert page["cancel_offered"] is False and page["posted"] == [], page
    status, answer, calls = _checkout(base, data, "inc", "pro")
    assert status != 409 and len(calls) == 1, (status, answer, calls)


# --- (d) only cancelled ----------------------------------------------------------------

def test_a_cancelled_subscription_reads_as_before(srv, tmp_path):
    """Control."""
    base, data = srv
    me = _me(base, "cal")
    assert me["subscription_active"] is False, me
    assert me["subscription_status"]["status"] == "canceled", me
    assert me["subscription_status"]["stripe_sub"] == "sub_cal_old", me
    assert not me.get("subscription_past_due"), me
    page = _page(tmp_path, me)
    assert page["status"] == "Not active", page
    assert page["cancel_offered"] is False and page["reactivate_offered"] is True, page
    status, answer, calls = _checkout(base, data, "cal", "pro")
    assert status != 409 and len(calls) == 1, (status, answer, calls)


def test_a_past_due_row_that_names_no_subscription_offers_nothing(srv, tmp_path):
    """Control. A hand-written row with no subscription id names nothing
    Cancel could reach, so the account is not told to cancel it and is not
    kept from subscribing."""
    base, data = srv
    me = _me(base, "leg")
    assert not me.get("subscription_past_due"), me
    page = _page(tmp_path, me)
    assert page["cancel_offered"] is False, page
    status, answer, calls = _checkout(base, data, "leg", "pro")
    assert status != 409 and len(calls) == 1, (status, answer, calls)


# --- (e) no second subscription while one is past due ---------------------------------

def test_a_past_due_account_is_not_sold_a_second_subscription(srv):
    base, data = srv
    status, answer, calls = _checkout(base, data, "pat", "pro")
    assert status == 409, (status, answer)
    # The buy page shows `error` and nothing else, so it has to say it all.
    said = answer["error"].lower()
    assert "past due" in said and "cancel" in said and "account page" in said, answer
    assert calls == [], "Stripe was asked for a session anyway"


def test_a_pack_is_still_sold_to_a_past_due_account(srv):
    """Control: only a second SUBSCRIPTION is refused."""
    base, data = srv
    status, answer, calls = _checkout(base, data, "pat", "pack")
    assert status != 409, (status, answer)
    assert [c["form"].get("mode") for c in calls] == ["payment"], calls


def test_an_account_with_no_subscription_can_subscribe(srv):
    """Control, signed in and signed out."""
    base, data = srv
    for name in ("new", None):
        status, answer, calls = _checkout(base, data, name, "pro")
        assert status != 409, (name, status, answer)
        assert [c["form"].get("mode") for c in calls] == ["subscription"], (name, calls)


def test_the_active_refusal_keeps_its_words(srv):
    """Control: the message an active subscriber gets is unchanged."""
    base, data = srv
    status, answer, calls = _checkout(base, data, "ana", "pro")
    assert status == 409 and calls == [], (status, answer, calls)
    assert answer == {"error": "This account already has an active subscription.",
                      "detail": "Manage it from your account page; nothing was charged."}


# --- (f) the case of the address does not matter --------------------------------------

def test_a_mixed_case_address_reaches_the_same_rows(srv, tmp_path):
    """The rows carry the address as it was typed at checkout; the session
    carries it as typed at sign-in."""
    base, data = srv
    me = _me(base, "mia")
    assert (me["subscription_status"] or {}).get("stripe_sub") == "sub_mia_due", me
    assert me.get("subscription_past_due") is True, me
    status, answer, calls = _cancel(base, data, "mia")
    assert status == 200 and [c["path"] for c in calls] == ["/subscriptions/sub_mia_due"], calls
    page = _page(tmp_path, me, status, answer)
    assert "past due" in page["status"].lower() and page["cancel_offered"] is True, page
    status, answer, calls = _checkout(base, data, "mia", "pro")
    assert status == 409 and calls == [], (status, answer, calls)


# --- (g) a subscription Stripe no longer has is ended ---------------------------------

def test_cancel_ends_a_past_due_subscription_stripe_no_longer_has(srv):
    """A past-due row whose subscription Stripe has since deleted (the webhook
    that would have said so was missed). Checkout refuses until it is
    cancelled, so Cancel answering 503 left the account with no way to
    subscribe again. Stripe's "no such subscription" means nothing can be
    billed, so it is recorded as ended."""
    base, data = srv
    status, answer, calls = _checkout(base, data, "old", "pro")
    assert status == 409 and calls == [], (status, answer, calls)  # where it starts
    _script_stripe(data, {"/subscriptions/sub_old_due": (
        404, "resource_missing", "No such subscription: 'sub_old_due'")})
    try:
        status, answer, calls = _cancel(base, data, "old")
    finally:
        _script_stripe(data, {})
    assert status == 200, answer
    assert [c["path"] for c in calls] == ["/subscriptions/sub_old_due"], calls
    # The shape a normal cancel answers, so the page shows it as done.
    _status, normal, _calls = _cancel(base, data, "pat")
    assert answer["ok"] is True and set(answer) == set(normal) == {"ok", "message"}, (answer, normal)
    # Nothing is billed any more: no access, and no retry, is promised.
    said = answer["message"].lower()
    assert "access" not in said and "retried" not in said, answer
    me = _me(base, "old")
    assert me.get("subscription_past_due") is False and me["subscription_active"] is False, me
    assert me["subscription_status"]["stripe_sub"] == "sub_old_due", me
    assert me["subscription_status"]["status"] == "canceled", me
    status, answer, calls = _checkout(base, data, "old", "pro")
    assert status != 409, (status, answer)
    assert [c["form"].get("mode") for c in calls] == ["subscription"], calls


@pytest.mark.parametrize("name, http_status, code, message", [
    # A 404 that is not Stripe saying "no such subscription": a wrong base
    # URL or a proxy answers this way, and the subscription may be billing.
    ("url", 404, "", "Unrecognized request URL (POST: /v1/subscriptions/sub_url_due)."),
    # resource_missing about something that is not the subscription.
    ("itm", 400, "resource_missing", "No such subscription item: 'si_itm'"),
], ids=["404-without-the-code", "the-code-without-404"])
def test_cancel_still_fails_when_stripe_refuses_for_another_reason(srv, name, http_status,
                                                                   code, message):
    """Control. Only both together read as ended; anything else Stripe
    refuses may still be billing, so nothing is recorded."""
    base, data = srv
    ledger = data / "subscriptions.jsonl"
    before = ledger.read_bytes()
    _script_stripe(data, {f"/subscriptions/sub_{name}_due": (http_status, code, message)})
    try:
        status, answer, calls = _cancel(base, data, name)
    finally:
        _script_stripe(data, {})
    assert status == 503 and len(calls) == 1, (status, answer, calls)
    assert ledger.read_bytes() == before
    assert _me(base, name).get("subscription_past_due") is True
    status, answer, calls = _checkout(base, data, name, "pro")
    assert status == 409 and calls == [], (status, answer, calls)


# --- (h) an abandoned attempt is never named ------------------------------------------

ABANDONED = pytest.mark.parametrize("name", ["abe", "exp"],
                                    ids=["an-attempt-after-a-cancel", "an-expired-attempt-after-a-cancel"])


@ABANDONED
def test_an_abandoned_attempt_never_names_the_subscription(srv, name):
    """With nothing active or past due, the newest row of all could be a
    sign-up whose first payment never went through. It named itself as the
    account's subscription, so Cancel (and the refund request and the support
    lookup, which read the same choice) reached the attempt and not the
    subscription that was paid for. Stripe moves such an attempt from
    `incomplete` to `incomplete_expired` a day later; it is the same attempt."""
    base, data = srv
    me = _me(base, name)
    assert (me["subscription_status"] or {}).get("stripe_sub") == f"sub_{name}_paid", me
    assert me["subscription_status"]["status"] == "canceled", me
    _status, _answer, calls = _cancel(base, data, name)
    assert [c["path"] for c in calls] == [f"/subscriptions/sub_{name}_paid"], calls


@pytest.mark.parametrize("name", ["inc", "ixp"], ids=["incomplete", "expired"])
def test_an_account_with_only_an_abandoned_attempt_has_no_subscription(srv, name):
    """Nothing was ever paid for, so there is nothing to show or cancel."""
    base, data = srv
    me = _me(base, name)
    assert me["subscription_status"] is None, me
    status, answer, calls = _cancel(base, data, name)
    assert status == 404 and calls == [], (status, answer, calls)


def test_a_past_due_subscription_is_still_shown_after_an_abandoned_attempt(srv, tmp_path):
    """Control. A real past-due row still speaks, with Cancel, when an
    abandoned attempt landed after it."""
    base, data = srv
    me = _me(base, "due")
    assert (me["subscription_status"] or {}).get("stripe_sub") == "sub_due_due", me
    assert me.get("subscription_past_due") is True, me
    status, answer, calls = _cancel(base, data, "due")
    assert status == 200 and [c["path"] for c in calls] == ["/subscriptions/sub_due_due"], calls
    page = _page(tmp_path, me, status, answer)
    assert "past due" in page["status"].lower() and page["cancel_offered"] is True, page
    assert page["posted"] == ["/api/me/cancel-subscription"], page


def test_cancel_never_ends_an_active_subscription_stripe_claims_not_to_have(srv):
    """Bundle review round 1 (MEDIUM, reproduced). A Stripe key from the
    wrong mode or the wrong account answers 404 resource_missing for a
    subscription that is live and billing. The "not at Stripe" branch ended
    such an ACTIVE subscriber's subscription locally: access gone, Stripe
    still charging. It applies to a past-due row only; an active row
    answers 503, records nothing, and the office gets an alert line."""
    base, data = srv
    ledger = data / "subscriptions.jsonl"
    before = ledger.read_bytes()
    _script_stripe(data, {"/subscriptions/sub_ana_paid": (
        404, "resource_missing", "No such subscription: 'sub_ana_paid'")})
    try:
        status, answer, calls = _cancel(base, data, "ana")
    finally:
        _script_stripe(data, {})
    assert status == 503, answer
    assert [c["path"] for c in calls] == ["/subscriptions/sub_ana_paid"], calls
    assert ledger.read_bytes() == before, "an active subscription was recorded as ended"
    me = _me(base, "ana")
    assert me["subscription_active"] is True, me
    logs = "".join(p.read_text(errors="replace") for p in data.glob("server-*.log"))
    assert "Stripe has no record of a subscription the ledger knows" in logs, "no alert"
    assert "sub_ana_paid" in logs
