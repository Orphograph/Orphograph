"""Every page that posts an address shows the server's hint when it is refused.

The server refuses an address with an uppercase letter outside A-Z with 400
{"error": "email_needs_lowercase", "message": <hint>} (server/email_fold.py,
tests/test_email_twin_closed.py). A page that showed its generic failure, or
its success line, would leave the person with no way to know what to change:
pack.js styled the refusal as a success, recover.js and crypto.js printed the
machine code, v2.js and the notify forms threw the body away.

Each test loads the real page script in a node vm with a fake DOM and a fetch
stub, submits its form, and reads what the page shows. Each has a control: the
same page answered normally shows its own line and not the hint, so a driver
that read the wrong element could not pass.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "server"))

from email_fold import LOWERCASE_HINT  # noqa: E402

REFUSED = {"status": 400, "body": {"error": "email_needs_lowercase", "message": LOWERCASE_HINT}}
TWIN = "Karl@example.test"

# argv[2] is a JSON spec: the script, the element ids that exist, values,
# element-scoped selectors, fetch answers by URL, the form to submit and the
# elements to read. Prints what each read element shows.
_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const spec = JSON.parse(process.argv[2]);
class El {
  constructor(tag, id) { this.tagName = tag; this.id = id || ""; this.children = []; this._t = ""; this.style = {};
    this.dataset = {}; this.hidden = false; this.disabled = false; this.className = ""; this._l = {}; this.value = "";
    this._a = {}; this.parent = null; this.q = {};
    const s = new Set(); this.classList = { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle() {} };
    this._cls = s; }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._t = String(v); this.children = []; }
  get nextElementSibling() { return null; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  insertAdjacentElement(_where, c) { (this.after = this.after || []).push(c); return c; }
  addEventListener(ev, fn) { (this._l[ev] = this._l[ev] || []).push(fn); }
  setAttribute(k, v) { this._a[k] = String(v); } removeAttribute(k) { delete this._a[k]; }
  getAttribute(k) { return k in this._a ? this._a[k] : null; }
  querySelector(sel) {
    if (sel in this.q) return known[this.q[sel]] || null;
    const cls = sel.startsWith(".") ? sel.slice(1) : null;
    return (cls && this.children.find((c) => c.className.split(" ").includes(cls))) || null;
  }
  querySelectorAll() { return []; } closest() { return null; } click() {} focus() {}
  insertBefore(c) { return this.appendChild(c); } prepend(c) { this.children.unshift(c); }
}
const known = {};
spec.ids.forEach((id) => { known[id] = new El("div", id); });
Object.entries(spec.values || {}).forEach(([id, v]) => { known[id].value = v; });
Object.entries(spec.queries || {}).forEach(([id, q]) => { known[id].q = q; });
const document = {
  getElementById: (id) => known[id] || null,
  querySelector: (sel) => (sel.startsWith("#") ? known[sel.slice(1)] || null : null),
  querySelectorAll: () => [], createElement: (t) => new El(t), createTextNode: (t) => ({ textContent: String(t), children: [] }),
  addEventListener() {}, body: new El("body"), documentElement: new El("html") };
const store = {};
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; } };
const posted = [];
const fetch = async (url, opts) => {
  const path = String(url).split("?")[0];
  if (opts && opts.body && typeof opts.body === "string") posted.push({ path, body: JSON.parse(opts.body) });
  const a = spec.answers[path] || { status: 404, body: {} };
  return { ok: a.status >= 200 && a.status < 300, status: a.status, json: async () => a.body,
    text: async () => JSON.stringify(a.body) };
};
const ctx = { document, localStorage, sessionStorage: localStorage, fetch, console, setTimeout, clearTimeout,
  setInterval: () => 1, clearInterval() {}, TextEncoder, crypto: globalThis.crypto, Intl, URL, URLSearchParams,
  Blob: function () {}, navigator: {}, history: { replaceState() {} },
  location: { search: "", pathname: "/", href: "https://example.test/", hash: "", assign() {} } };
ctx.window = ctx;
ctx.addEventListener = () => {};
vm.createContext(ctx);
const settle = () => new Promise((r) => setTimeout(r, 150));
(async () => {
  vm.runInContext(fs.readFileSync(spec.script, "utf8"), ctx);
  await settle();
  (known[spec.submit]._l.submit || []).forEach((f) => f({ preventDefault() {} }));
  await settle();
  const out = { posted };
  spec.read.forEach((id) => { const e = known[id];
    out[id] = { text: e.textContent + (e.after || []).map((c) => c.textContent).join(""),
      hidden: e.hidden, className: e.className, classes: [...e._cls] }; });
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(2); });
"""


def _run(spec: dict, tmp_path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real page scripts")
    driver = tmp_path / "hint_driver.js"
    driver.write_text(_DRIVER)
    proc = subprocess.run([node, str(driver), json.dumps(spec)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _posted_email(run: dict, path: str) -> list:
    return [p["body"].get("email") for p in run["posted"] if p["path"] == path]


# Per page: the script, its elements, the endpoint it posts to, the element
# that shows the answer, and what an ordinary answer looks like.
PAGES = {
    "signin": dict(
        spec=dict(script=str(WEB / "signin.js"), ids=["signin-form", "signin-msg", "submit-btn", "email"],
                  values={"email": TWIN}, submit="signin-form", read=["signin-msg"]),
        path="/api/auth/email-link", shows="signin-msg",
        ok={"status": 200, "body": {"ok": True, "message": "Check your inbox for a sign-in link."}},
        ok_text="Check your inbox for a sign-in link."),
    "pack": dict(
        spec=dict(script=str(WEB / "assets" / "pack.js"),
                  ids=["pk-code-form", "pk-code", "pk-code-submit", "pk-balance", "pk-anchor", "pk-drop",
                       "pk-file", "pk-anchor-status", "pk-receipt", "pk-r-name", "pk-r-hash", "pk-r-ts",
                       "pk-r-link", "pk-recover-form", "pk-email", "pk-recover-submit", "pk-recover-result"],
                  values={"pk-email": TWIN}, submit="pk-recover-form", read=["pk-recover-result"]),
        path="/api/pack/recover", shows="pk-recover-result",
        ok={"status": 200, "body": {"ok": True, "message":
                                    "If a pack is associated with that email, we've sent the code(s)."}},
        ok_text="If a pack is associated with that email, we've sent the code(s).", err_class="err"),
    "recover": dict(
        spec=dict(script=str(WEB / "recover.js"),
                  ids=["rec-form", "rec-session", "rec-email", "rec-submit", "rec-result"],
                  values={"rec-session": "cs_test_abc123", "rec-email": TWIN},
                  submit="rec-form", read=["rec-result"]),
        path="/api/recover", shows="rec-result",
        ok={"status": 200, "body": {"ok": True, "mode": "payment", "message": "Re-sent."}},
        ok_text="Re-sent.", err_class="err"),
    "crypto": dict(
        spec=dict(script=str(WEB / "pay" / "crypto.js"),
                  ids=["crypto-form", "coin", "email", "pay-submit", "pay-msg", "pack-pick"],
                  values={"coin": "btc", "email": TWIN}, submit="crypto-form", read=["pay-msg"]),
        path="/api/nowpayments/create", shows="pay-msg",
        ok={"status": 400, "body": {"error": "unknown plan"}}, ok_text="unknown plan", err_class="error"),
    "agent-receipts": dict(
        spec=dict(script=str(WEB / "lp" / "agent-receipts.js"),
                  ids=["lp-notify-form", "lp-notify-email", "lp-notify-submit", "lp-notify-msg", "lp-notify-done"],
                  values={"lp-notify-email": TWIN}, submit="lp-notify-form", read=["lp-notify-msg"]),
        path="/api/waitlist", shows="lp-notify-msg",
        ok={"status": 400, "body": {"error": "body must be a JSON object"}},
        ok_text="That did not go through. Try again in a moment."),
    "checkout-cta": dict(
        spec=dict(script=str(WEB / "checkout-cta.js"),
                  ids=["notify-pack", "notify-pack-email", "notify-pack-btn", "notify-pack-row",
                       "notify-pack-note", "notify-pack-done"],
                  values={"notify-pack-email": TWIN},
                  queries={"notify-pack": {'input[type="email"]': "notify-pack-email", "button": "notify-pack-btn",
                                           ".card-notify-row": "notify-pack-row",
                                           ".card-notify-note": "notify-pack-note",
                                           ".card-notify-done": "notify-pack-done"}},
                  submit="notify-pack", read=["notify-pack", "notify-pack-done"]),
        path="/api/waitlist", shows="notify-pack",
        ok={"status": 200, "body": {"ok": True, "message": "On the list."}}, ok_text=""),
    "v2-demand": dict(
        spec=dict(script=str(WEB / "v2.js"),
                  ids=["drop", "drop-input", "drop-btn", "status", "sticky-status", "demand-pack-v1",
                       "demand-pack-email", "demand-pack-msg", "demand-pack-btn"],
                  values={"demand-pack-email": TWIN},
                  queries={"demand-pack-v1": {"button[type=submit]": "demand-pack-btn"}},
                  submit="demand-pack-v1", read=["demand-pack-msg"]),
        path="/api/waitlist", shows="demand-pack-msg",
        ok={"status": 200, "body": {"ok": True, "message": "On the list."}},
        ok_text="Recorded. You were not charged."),
}
CONFIG = {"status": 200, "body": {"stripe": {"card_charges_enabled": False}}}


def _answers(page: str, answer: dict) -> dict:
    return {PAGES[page]["path"]: answer, "/api/config": CONFIG}


@pytest.mark.parametrize("page", sorted(PAGES))
def test_the_page_shows_the_servers_hint_when_the_address_needs_lowercase(page, tmp_path):
    p = PAGES[page]
    run = _run({**p["spec"], "answers": _answers(page, REFUSED)}, tmp_path)
    assert _posted_email(run, p["path"]) == [TWIN], ("the page never posted the address", run["posted"])
    shown = run[p["shows"]]
    assert shown["text"].strip() == LOWERCASE_HINT, (page, shown)
    assert "email_needs_lowercase" not in shown["text"]
    if "err_class" in p:
        assert p["err_class"] in (shown["className"].split() + shown["classes"]), (
            "the refusal is styled as something other than an error", shown)


@pytest.mark.parametrize("page", sorted(PAGES))
def test_control_an_ordinary_answer_shows_the_pages_own_line(page, tmp_path):
    p = PAGES[page]
    run = _run({**p["spec"], "answers": _answers(page, p["ok"])}, tmp_path)
    assert _posted_email(run, p["path"]) == [TWIN]
    shown = run[p["shows"]]
    assert LOWERCASE_HINT not in shown["text"], (page, shown)
    assert shown["text"].strip() == p["ok_text"], (page, shown)


def test_signin_no_longer_says_check_your_inbox_when_it_was_refused(tmp_path):
    """A 429 carries no message. The page used to fall back to "Check your
    inbox." for it: a link that was never sent."""
    p = PAGES["signin"]
    run = _run({**p["spec"], "answers": _answers("signin", {
        "status": 429, "body": {"error": "too many requests"}})}, tmp_path)
    assert run["signin-msg"]["text"] == "Could not send a link just now. Try again in a moment."
