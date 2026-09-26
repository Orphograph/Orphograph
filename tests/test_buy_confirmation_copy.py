"""The card buyer's confirmation page must describe what the webhook does.

Founder decision 2026-09-26: a pack paid by a delayed method (bank debit) is
delivered when the payment SETTLES, days later, not when checkout completes.
The page used to tell that buyer their access would "arrive shortly … check
back in a few minutes", and it told a fully discounted buyer
(payment_status "no_payment_required", delivered at once) that payment was
pending. This runs the real web/buy.js in node against each status.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

BUY_JS = Path(__file__).resolve().parent.parent / "web" / "buy.js"

_DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const [src, status, mode] = process.argv.slice(2);
function mk() {
  return { hidden: true, textContent: "", href: "", firstChild: null,
           removeAttribute() {}, removeChild() {}, appendChild(c) { return c; },
           querySelector(sel) { return el("settled:" + sel); } };
}
const els = new Map();
function el(sel) { if (!els.has(sel)) els.set(sel, mk()); return els.get(sel); }
const document = { querySelector: el, createElement: () => mk(),
                   createTextNode: (t) => ({ textContent: t }), addEventListener() {} };
const fetch = (url) => Promise.resolve({ ok: true, json: async () => (
  { mode: mode || "payment", customer_email: "b@example.test", payment_status: status }) });
const ctx = vm.createContext({ document, fetch, console, URLSearchParams,
  location: { search: "?stripe_session=cs_test_copy&status=success" },
  window: {} });
vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
setTimeout(() => {
  process.stdout.write(JSON.stringify({
    h: el("settled:h1, h2").textContent, p: el("settled:p").textContent }));
}, 50);
"""


def _page(tmp_path: Path, status: str, mode: str = "payment") -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real buy.js")
    driver = tmp_path / "buy_driver.js"
    driver.write_text(_DRIVER)
    proc = subprocess.run([node, str(driver), str(BUY_JS), status, mode],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_a_payment_still_clearing_is_told_it_takes_days(tmp_path):
    page = _page(tmp_path, "unpaid")
    assert page["h"] == "Payment pending."
    assert "few business days" in page["p"] and "as soon as Stripe confirms" in page["p"]
    assert "few minutes" not in page["p"], page


@pytest.mark.parametrize("status", ["paid", "no_payment_required"])
def test_a_payment_delivered_at_once_says_so(tmp_path, status):
    page = _page(tmp_path, status)
    assert page["h"] == "Payment received.", page
    assert "emailed" in page["p"]


def test_a_subscription_still_clearing_is_not_promised_a_pack_code(tmp_path):
    """A subscription paid by bank debit gets its welcome email at `completed`
    and never a Pack code; the pending copy runs before the subscription copy,
    so it must not promise one (found by /code-review high 274)."""
    page = _page(tmp_path, "unpaid", mode="subscription")
    assert page["h"] == "Payment pending.", page
    assert "Pack code" not in page["p"], page
    assert "few business days" in page["p"] and "welcome email" in page["p"], page


def test_a_paid_subscription_keeps_its_own_copy(tmp_path):
    page = _page(tmp_path, "paid", mode="subscription")
    assert page["h"] == "Subscription active.", page
