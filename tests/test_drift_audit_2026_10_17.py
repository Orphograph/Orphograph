"""The drift audit of 2026-10-01 (24 findings), fixed 2026-10-17 (@drift-audit-24).

Each test is named for its finding number. Copy findings are checked over the
whole CLASS of statement (page text, meta, JSON descriptors, READMEs), not
only the line the audit quoted. Behaviour findings drive the real code: the
mailer renders, the public stats reducer runs, and the page scripts run in a
node vm with a fake DOM. Every behaviour test has a control showing the probe
can see the other outcome.

Not tested here, by finding:
  10  notify_email "Pack only" was already corrected on master (#281); the
      regression check below passes on master too.
  15  auth.email_id vs fold_email: closed by #286 (founder decision: the id
      stays, every intake refuses an address that needs lowercase).
  17  docs/X402_AGENT_PAYMENTS.md: already corrected by 7aa6812 (#280).
  19  no defect: the audit flagged it so a gate would not misfire.
  22  the Payment Link after-payment redirect is a Stripe dashboard setting
      (founder-held); the buy.js copy half shipped in #281.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import mailer  # noqa: E402
import stats  # noqa: E402

EXCLUDE = ("_mockups/", "/dist/", "/vendor/", "node_modules/", "construction/")


def _public_web_text():
    """Every served text file under web/, with its path."""
    for p in sorted(WEB.rglob("*")):
        rel = "/" + str(p.relative_to(WEB))
        if not p.is_file() or any(x in rel for x in EXCLUDE):
            continue
        if p.suffix in (".html", ".txt", ".json", ".js", ".xml", ".md"):
            yield rel, p.read_text(encoding="utf-8", errors="replace")


# --- a page script in a node vm ---------------------------------------------

_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const spec = JSON.parse(process.argv[2]);
class El {
  constructor(tag, id) { this.tagName = tag; this.id = id || ""; this.children = []; this._t = "";
    this.dataset = {}; this.hidden = !!(spec.hidden || []).includes(id); this.className = "";
    this._a = {}; this.href = ""; this.target = ""; this.rel = ""; this.style = {}; this._l = {}; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._t = String(v); this.children = []; }
  appendChild(c) { this.children.push(c); return c; }
  replaceChildren() { this.children = []; this._t = ""; }
  setAttribute(k, v) { this._a[k] = String(v); if (k === "href") this.href = String(v); }
  removeAttribute(k) { delete this._a[k]; if (k === "target") this.target = ""; if (k === "href") this.href = ""; }
  getAttribute(k) { return k in this._a ? this._a[k] : null; }
  addEventListener(ev, fn) { (this._l[ev] = this._l[ev] || []).push(fn); }
  querySelector() { return null; } querySelectorAll() { return []; }
}
const known = {};
spec.ids.forEach((id) => { known[id] = new El("div", id); });
const byId = (id) => known[id] || null;
const document = { getElementById: byId,
  querySelector: (s) => (s.startsWith("#") ? byId(s.slice(1)) : null),
  querySelectorAll: () => [], createElement: (t) => new El(t), addEventListener() {} };
const served = {};
const fetch = async (url) => {
  const path = String(url).split("?")[0];
  let a = spec.answers[path] || { status: 404, body: {} };
  const i = (served[path] = (served[path] || 0) + 1) - 1;
  if (Array.isArray(a)) a = a[Math.min(i, a.length - 1)];
  return { ok: a.status >= 200 && a.status < 300, status: a.status, json: async () => a.body };
};
const fast = (f) => setTimeout(f, 0);
const ctx = { document, fetch, console, setTimeout: fast, clearTimeout() {}, setInterval: () => 1,
  clearInterval() {}, Intl, URLSearchParams, Date, Math, Number,
  location: { search: spec.search || "", pathname: "/", hash: "" } };
ctx.window = ctx;
vm.createContext(ctx);
(async () => {
  vm.runInContext(fs.readFileSync(spec.script, "utf8"), ctx);
  for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 5));
  const out = {};
  spec.ids.forEach((id) => { const e = known[id];
    out[id] = { text: e.textContent, hidden: e.hidden, className: e.className, href: e.href,
      target: e.target, items: e.children.map((c) => ({ text: c.textContent, cls: c.className,
        parts: c.children.map((g) => ({ text: g.textContent, cls: g.className })) })) }; });
  out._fetched = served;
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(2); });
"""


def _run_page(tmp_path, script: Path, ids, answers, **extra) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real page script")
    driver = tmp_path / "drift_driver.js"
    driver.write_text(_DRIVER)
    spec = {"script": str(script), "ids": list(ids), "answers": answers, **extra}
    proc = subprocess.run([node, str(driver), json.dumps(spec)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# --- 1 (HIGH) the referral promise ------------------------------------------

def _claim_email(monkeypatch) -> str:
    sent = []
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html, *a, **k:
                        sent.append(text + "\n" + html) or True)
    assert mailer.send_pack_claim_email("buyer@example.test", "pk_driftAAAAbbbbCCCC", 10) is True
    assert len(sent) == 1
    return sent[0]


def test_f01_the_claim_email_promises_no_referral_bonus(monkeypatch):
    """No page we serve carries ?ref= into a purchase, so the +10/+10 the
    claim email promised could never be credited (reproduced 2026-10-17: the
    live "/?ref=..." page is web/index.html byte for byte, and its scripts
    never read ref)."""
    body = _claim_email(monkeypatch)
    assert "pk_driftAAAAbbbbCCCC" in body, "control: the claim code itself is mailed"
    for phrase in ("?ref=", "Referral instrument", "complimentary anchors", "equivalent ten"):
        assert phrase not in body, phrase


def test_f01_the_retired_endpoint_no_longer_says_referral_links_work():
    import app
    assert "still work" not in app._AFFILIATE_RETIRED["detail"]


def test_f01_no_served_page_reads_a_referral_code_so_none_may_promise_one():
    """The class: a promise of a referral bonus anywhere public, while no
    served page reads ?ref=. The served homepage's scripts are the readers
    that would have to exist first."""
    index = (WEB / "index.html").read_text()
    scripts = re.findall(r'src="(/[^"?]+\.js)', index)
    assert scripts, "control: the homepage loads scripts"
    for s in scripts:
        src = (WEB / s.lstrip("/")).read_text()
        assert not re.search(r"""get\(\s*["']ref["']\s*\)""", src), f"{s} reads ?ref= now: revisit finding 1"
    for rel, text in _public_web_text():
        low = text.lower()
        assert "orpho_ref_code" not in low or rel == "/app.js", f"{rel} still describes a referral code"
        assert "referral links in pack claim emails" not in low, rel


# --- 2 calendars shown down when they were never probed ----------------------

_UNPROBED = {"calendars": [{"url": f"https://{n}.pool.opentimestamps.org", "reachable": None, "checked": False}
                           for n in ("a", "b", "c", "d", "e")]}


def test_f02_unprobed_calendars_are_not_reported_unreachable():
    out = stats._calendars_public(_UNPROBED)
    assert out["reachable"] is None and out["checked"] is False, out
    assert all(i["reachable"] is None for i in out["items"]), out


def test_f02_control_probed_calendars_still_count():
    rows = {"calendars": [{"url": "https://a.x", "reachable": True, "checked": True},
                          {"url": "https://b.x", "reachable": False, "checked": True}]}
    out = stats._calendars_public(rows)
    assert (out["reachable"], out["total"], out["checked"]) == (1, 2, True)
    assert [i["reachable"] for i in out["items"]] == [True, False]


_STATS_IDS = ["cal-summary", "cal-list", "anchors-total", "anchors-24h", "anchors-7d", "uptime",
              "boot-at", "last-anchor-rel", "last-anchor-abs", "checked-at"]


def _stats_answer(cals):
    return {"/api/stats": {"status": 200, "body": {
        "anchors": {"total": 1, "last_24h": 1, "last_7d": 1, "last_anchor_at": None},
        "calendars": cals, "uptime_sec": 5, "checked_at": "now"}}}


def test_f02_the_stats_page_says_not_probed_not_unreachable(tmp_path):
    shown = _run_page(tmp_path, WEB / "stats.js", _STATS_IDS,
                      _stats_answer(stats._calendars_public(_UNPROBED)))
    assert shown["cal-summary"]["text"] == "not probed", shown["cal-summary"]
    labels = [p["text"] for i in shown["cal-list"]["items"] for p in i["parts"][1:]]
    assert labels == ["not probed"] * 5, labels


def test_f02_control_the_stats_page_still_shows_a_real_outage(tmp_path):
    cals = {"reachable": 0, "total": 1, "checked": True, "items": [{"name": "a", "reachable": False}]}
    shown = _run_page(tmp_path, WEB / "stats.js", _STATS_IDS, _stats_answer(cals))
    assert shown["cal-summary"]["text"] == "0/1"
    assert [p["text"] for i in shown["cal-list"]["items"] for p in i["parts"][1:]] == ["unreachable"]


# --- 3 verify described as a chain check ------------------------------------

def test_f03_no_descriptor_says_verify_checks_the_chain():
    card = json.loads((WEB / ".well-known/mcp/server-card.json").read_text())
    desc = next(t["description"] for t in card["tools"] if t["name"] == "orphograph_verify_receipt")
    assert "does not independently verify" in desc, desc
    for rel, text in _public_web_text():
        low = text.lower()
        for phrase in ("against the calendars and bitcoin chain", "bitcoin block commitment time"):
            assert phrase not in low, (rel, phrase)
    readme = (ROOT / "sdk" / "README.md").read_text().lower()
    assert "against the calendars + bitcoin" not in readme


# --- 4 the 503 row ------------------------------------------------------------

def test_f04_the_error_table_does_not_promise_a_503_for_a_calendar_outage():
    api = (WEB / "docs" / "api.html").read_text()
    row = re.search(r"<td><code>503</code></td>.*?</tr>", api, re.S).group(0)
    assert "oracle" not in row.lower()
    assert "calendars_ok" in row and "200" in row, row


# --- 5 the crypto success page ------------------------------------------------

def test_f05_the_static_success_page_claims_no_confirmation(tmp_path):
    html = (WEB / "pay" / "success.html").read_text()
    lede = re.search(r'<p id="ok-lede">(.*?)</p>', html, re.S).group(1)
    for phrase in ("confirmed on-chain", "We've emailed", "Payment received"):
        assert phrase not in lede and f"<h1>{phrase}" not in html, phrase


_OK_IDS = ["ok-title", "ok-lede", "ok-order", "ok-order-id"]


def test_f05_only_a_credited_order_turns_the_page_into_received(tmp_path):
    not_yet = _run_page(tmp_path, WEB / "pay" / "success.js", _OK_IDS,
                        {"/api/nowpayments/order/ord_1": {"status": 200, "body": {"credited": False}}},
                        search="?order=ord_1")
    assert not_yet["_fetched"].get("/api/nowpayments/order/ord_1", 0) >= 6, "control: it polled to the end"
    assert not_yet["ok-title"]["text"] != "Payment received"
    assert "confirmed" not in not_yet["ok-lede"]["text"]
    credited = _run_page(tmp_path, WEB / "pay" / "success.js", _OK_IDS,
                         {"/api/nowpayments/order/ord_1": {"status": 200, "body": {"credited": True}}},
                         search="?order=ord_1")
    assert credited["ok-title"]["text"] == "Payment received", credited  # control
    assert "Payment confirmed" in credited["ok-lede"]["text"]


# --- 6 folder emails ----------------------------------------------------------

def _render(monkeypatch, fn, receipt):
    sent = []
    monkeypatch.setattr(mailer, "_send", lambda to, subject, text, html, *a, **k:
                        sent.append((text, html)) or True)
    monkeypatch.setattr(mailer, "_receipt_pdf", None)
    fn("owner@example.test", receipt)
    return sent[0]


_FOLDER = {"receipt_id": "R1", "kind": "folder", "leaf_count": 12, "hash_hex": "ab" * 32,
           "created_at": "2026-10-17T00:00:00Z", "calendars_ok": 4, "calendars_total": 5,
           "btc_pinned_at": "2026-10-17T02:00:00Z", "pinned_count": 4, "pinned_total": 5}


def test_f06_folder_receipt_html_names_the_manifest(monkeypatch):
    text, html = _render(monkeypatch, mailer.send_receipt_email, _FOLDER)
    assert "Merkle root" in html and "manifest" in html, html
    assert "original file" not in html and "SHA-256</td>" not in html
    assert "manifest" in text  # the text part was already right


def test_f06_folder_pin_email_names_the_manifest(monkeypatch):
    text, html = _render(monkeypatch, mailer.send_pin_email, _FOLDER)
    for part in (text, html):
        assert "manifest" in part and "original file" not in part, part


def test_f06_control_a_single_file_still_names_the_original_file(monkeypatch):
    single = {k: v for k, v in _FOLDER.items() if k not in ("kind", "leaf_count")}
    for fn in (mailer.send_receipt_email, mailer.send_pin_email):
        text, html = _render(monkeypatch, fn, single)
        assert "original file" in html and "manifest" not in html


# --- 7 8 9 10 11 the API docs and the pack page ---------------------------------

def test_f07_the_429_body_field_is_named_as_the_server_sends_it():
    api = (WEB / "docs" / "api.html").read_text()
    assert "limit_per_hour" not in api and "limit_per_day" in api
    import app
    assert '"limit_per_day": ANCHOR_RATE_CAPACITY' in Path(app.__file__).read_text()  # control


def test_f08_llms_txt_does_not_say_packs_get_an_api_key():
    llms = (WEB / "llms.txt").read_text()
    assert "credit packs raise the limit via an API key" not in llms
    assert "X-Pack-Token" in llms


def test_f09_no_priority_queue_or_unenforced_ceiling_is_advertised():
    for rel, text in _public_web_text():
        low = text.lower()
        assert "priority queue" not in low, rel
        assert "fair-use ceiling" not in low, rel


def test_f10_notify_email_is_not_documented_as_pack_only():
    api = (WEB / "docs" / "api.html").read_text()
    assert "Pack only" not in api


def test_f11_the_pack_code_is_not_said_to_arrive_at_purchase():
    for rel, text in _public_web_text():
        assert "emailed at purchase" not in text, rel


# --- 12 the unsubscribe page ----------------------------------------------------

def test_f12_the_unsubscribe_page_does_not_promise_re_enrolment():
    import app
    src = Path(app.__file__).read_text()
    assert "re-enrolled" not in src
    assert "def remove" not in (SERVER / "unsubscribe.py").read_text(), (
        "control: if the ledger can now remove an entry, re-read this finding")


# --- 13 the legacy SDK README --------------------------------------------------

def test_f13_the_legacy_sdk_readme_installs_what_it_documents():
    readme = (ROOT / "sdk" / "README.md").read_text()
    assert not re.search(r"pip install orphograph(\s|$)", readme), "installs sdk-python, which has no anchor_file"
    called = set(re.findall(r"orphograph\.(\w+)\(", readme))
    assert called, "control: the README documents calls"
    tree = ast.parse((ROOT / "sdk" / "orphograph" / "__init__.py").read_text())
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    defined |= {a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom) for a in n.names}
    assert called <= defined, called - defined


# --- 14 MCP tool names -----------------------------------------------------------

def test_f14_the_mcp_page_names_tools_the_server_dispatches():
    dispatched = set(re.findall(r'name == "(orphograph_\w+)"', (ROOT / "mcp" / "orphograph_mcp.py").read_text()))
    assert len(dispatched) >= 6, dispatched
    page = (WEB / "mcp.html").read_text()
    table = re.search(r'<table class="mcp-table".*?</table>', page, re.S).group(0)
    named = re.findall(r"<td>(orphograph[._]\w+)\(", table)
    assert named and set(named) <= dispatched, named


# --- 16 data dir fallbacks -------------------------------------------------------

def test_f16_lightning_does_not_fall_back_to_the_working_directory(monkeypatch, tmp_path):
    import lightning
    monkeypatch.delenv("ORPHO_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    got = lightning._data_dir().resolve()
    assert got != tmp_path.resolve(), "fell back to cwd"
    assert got in (ROOT / "data", ROOT), got
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path / "d"))  # control: the variable wins
    assert lightning._data_dir() == tmp_path / "d"


def test_f16_renewal_does_not_fall_back_to_the_working_directory(tmp_path):
    fake_root = tmp_path / "root"
    (fake_root / "server").mkdir(parents=True)
    for name in ("renewal.py", "merkle.py"):
        shutil.copy(SERVER / name, fake_root / "server" / name)
    # A stand-in: the real engine is imported before the directory check and
    # would read the real data dir's configuration.
    (fake_root / "server" / "engine.py").write_text("anchor_hash = None\n")
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "ORPHO_DATA_DIR"}
    out = subprocess.run([sys.executable, str(fake_root / "server" / "renewal.py")],
                         cwd=elsewhere, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stderr
    assert f"no receipts directory at {fake_root / 'receipts'}" in out.stderr, out.stderr


# --- 18 a second subscription --------------------------------------------------

_CTA_IDS = ["buy-pack", "buy-pack50", "buy-personal", "notify-pack", "notify-pack50", "notify-personal"]
_CFG = {"status": 200, "body": {"stripe": {"card_charges_enabled": True,
                                           "personal_monthly_url": "https://buy.stripe.test/sub",
                                           "pack_url": "https://buy.stripe.test/pack"}}}


@pytest.mark.parametrize("flag", ["subscription_own_active", "subscription_past_due"])
def test_f18_a_signed_in_subscriber_is_not_offered_a_second_subscription(tmp_path, flag):
    me = {"status": 200, "body": {"signed_in": True, "subscription_active": flag == "subscription_own_active",
                                  "subscription_own_active": False,
                                  "subscription_past_due": False, flag: True}}
    shown = _run_page(tmp_path, WEB / "checkout-cta.js", _CTA_IDS,
                      {"/api/config": _CFG, "/api/me": me}, hidden=_CTA_IDS)
    assert shown["buy-personal"]["href"] == "/account", shown["buy-personal"]
    assert "stripe" not in shown["buy-personal"]["href"]
    assert shown["buy-pack"]["href"] == "https://buy.stripe.test/pack"  # packs stay on sale


def test_f18_a_team_member_covered_by_the_owner_can_still_subscribe(tmp_path):
    """subscription_active counts the team owner's cover; the server's 409
    guard does not, and neither does the page."""
    me = {"status": 200, "body": {"signed_in": True, "subscription_active": True,
                                  "subscription_own_active": False, "subscription_past_due": False}}
    shown = _run_page(tmp_path, WEB / "checkout-cta.js", _CTA_IDS,
                      {"/api/config": _CFG, "/api/me": me}, hidden=_CTA_IDS)
    assert shown["buy-personal"]["href"] == "https://buy.stripe.test/sub"


def test_f18_api_me_reports_the_accounts_own_subscription():
    import app
    src = Path(app.__file__).read_text()
    assert '"subscription_own_active": subscriptions.is_active(email)' in src


def test_f18_control_signed_out_visitors_still_get_the_payment_link(tmp_path):
    shown = _run_page(tmp_path, WEB / "checkout-cta.js", _CTA_IDS,
                      {"/api/config": _CFG, "/api/me": {"status": 401, "body": {}}}, hidden=_CTA_IDS)
    assert shown["buy-personal"]["href"] == "https://buy.stripe.test/sub"
    assert shown["buy-personal"]["hidden"] is False


# --- 20 21 READMEs and the support script ------------------------------------------

def test_f20_the_readme_lists_files_that_exist_and_prices_that_are_on_sale():
    readme = (ROOT / "README.md").read_text()
    tree = readme[readme.index("└── deploy/"):].split("```", 1)[0]
    listed = re.findall(r"[├└]── ([\w./-]+)", tree)[1:]  # [0] is deploy/ itself
    assert listed, "control: the tree lists deploy files"
    for name in listed:
        assert (ROOT / "deploy" / name.rstrip("/")).exists(), name
    for doc in (readme, (ROOT / "marketplace" / "orphograph-plugin" / "README.md").read_text(),
                (ROOT / "capture" / "README.md").read_text()):
        assert "$60/yr" not in doc
        assert not re.search(r"\|\s*Creator\s*\|\s*\$19/mo", doc)
        assert "$19 Creator" not in doc and "$19/mo Creator" not in doc


def test_f21_the_support_script_states_the_live_limits():
    faq = (ROOT / "deploy" / "SUPPORT_FAQ.md").read_text()
    assert "1 anchor per calendar month" not in faq
    assert "10 anchors per hour" not in faq
    assert not re.search(r"Standing Order:\*\*[^\n]*planned", faq)
    assert "3 anchors per 24 hours" in faq


# --- 23 24 ---------------------------------------------------------------------------

def test_f23_no_public_page_loads_a_script_that_relies_on_prefilled_metadata():
    loaded = set()
    for rel, text in _public_web_text():
        if rel.endswith(".html") and rel != "/index-legacy.html":
            loaded |= set(re.findall(r'src="(/[^"?#]+\.js)', text))
    assert loaded, "control: pages load scripts"
    for s in sorted(loaded):
        p = WEB / s.lstrip("/")
        if p.exists():
            assert "prefilled_metadata" not in p.read_text(), s
    assert not (WEB / "gift.js").exists(), "web/gift.js is dead code no page loads"


def _provisioned_aliases() -> set:
    tree = ast.parse((ROOT / "scripts" / "setup_email.py").read_text())
    for n in tree.body:
        if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "ALIAS_ADDRESSES" for t in n.targets):
            return set(ast.literal_eval(n.value))
    raise AssertionError("ALIAS_ADDRESSES not found")


def test_f24_a_lost_gift_code_points_to_self_service_recovery_and_a_routed_inbox():
    """The audit could not confirm support@ was routed (its only use on the
    site, against 74 uses of hello@). The setup script does provision it; the
    page now points to the self-service /recover flow and to hello@, the
    inbox every other page uses."""
    gift = (WEB / "gift.html").read_text()
    assert 'href="/recover"' in gift
    assert "support@orphograph.com" not in gift
    routed = _provisioned_aliases() | {"hello"}
    assert "support" in routed  # control: the parse found the alias list
    for rel, text in _public_web_text():
        for box in re.findall(r"\b([a-z]+)@orphograph\.com", text):
            assert box in routed, (rel, box)
