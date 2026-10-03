"""What the certificate and receipt pages say about Bitcoin, held against what happens.

Review round 4 of the rescue branch (#283):
  * A receipt no calendar accepted (calendars_ok 0) has no Bitcoin commitment
    and never gets one. The dataset certificate page still called it
    "Pending Bitcoin confirmation" and promised block-pinning "within ~1 hour".
  * "Within ~1 hour" is not what happens even for a good receipt: launch-week
    receipts were pinned after 1.3 h, 34 h, 46 h and 128 h. The homepage, the
    receipt page and the certificate page all said it.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
CERT_JS = WEB / "certificate.js"
RECEIPT_JS = WEB / "receipt.js"

# Promises that a receipt is pinned in about an hour. Review round 5: the
# first pattern missed "within roughly an hour" and "~1 hour" forms. Not
# matched, because they are true: "6 subsequent blocks (~1 hour)" (block
# depth), "batch-broadcast roughly hourly" (calendar cadence), a payment
# confirming "~1 hour on-chain".
HOUR_PROMISE = re.compile(
    r"\b(within|in) (about |roughly |around |~ ?|approximately )?(1|one|an) hour"
    r"|pending \(~ ?1 ?hour\)|~ ?1 ?hour after anchoring|wait ~ ?1 ?hour"
    r"|upgrade after (1|one|an) hour"
    # Cycle 9 (post-merge review): "About an hour." as an answer, "over the
    # following hour", "≈1 hour", "~ one hour to the block", "block-pinned
    # after one hour", and "within (a few) hours" / "usually within hours".
    r"|>\s*about an hour\.|over the following hour|≈ ?1 ?hour|~ ?one hour to the block"
    r"|block-pinned after one hour|(confirms|committed[^.]{0,60}|lands|arrives) within (a few )?hours"
    r"|usually within hours|by approximately one hour")


def _friendly_status(rec: dict) -> str:
    """Run the real friendlyStatus from certificate.js under node."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real certificate.js")
    src = CERT_JS.read_text(encoding="utf-8")
    m = re.search(r"^function friendlyStatus\(rec\) \{.*?^\}", src, re.S | re.M)
    assert m, "friendlyStatus not found in certificate.js"
    helper = re.search(r"^function noCommitment\(rec\) \{.*?\}$", src, re.M)
    assert helper, "noCommitment not found in certificate.js"
    driver = (helper.group(0) + "\n" + m.group(0)
              + "\nprocess.stdout.write(friendlyStatus(JSON.parse(process.argv[1])));\n")
    proc = subprocess.run([node, "-e", driver, json.dumps(rec)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_a_root_no_calendar_accepted_is_not_called_pending():
    text = _friendly_status({"status": "pending", "calendars_ok": 0, "calendars_total": 0})
    assert "pending" not in text.lower(), text
    assert "no bitcoin commitment" in text.lower(), text


def test_a_root_one_calendar_accepted_is_still_pending():
    # The honest record on the same path.
    text = _friendly_status({"status": "pending", "calendars_ok": 1, "calendars_total": 5})
    assert text.startswith("Pending Bitcoin confirmation"), text


def test_no_page_or_script_promises_pinning_within_an_hour():
    offenders = []
    # content/blog/*.md too: /blog/<slug> renders it when no static page
    # exists, and /blog/atom.xml is always built from it.
    # server/mailer.py too: the customer emails carry the same promise.
    for p in sorted(list(WEB.rglob("*.js")) + list(WEB.rglob("*.html"))
                    + list((ROOT / "content").rglob("*.md")) + list(WEB.rglob("*.md"))
                    + [ROOT / "server" / "mailer.py"]):
        rel = p.relative_to(ROOT).as_posix()
        if "/_mockups/" in rel or rel.endswith("index-legacy.html") or "/dist/" in rel:
            continue
        text = re.sub(r"\s+", " ", p.read_text(encoding="utf-8", errors="replace")).lower()
        if HOUR_PROMISE.search(text):
            offenders.append(rel)
    assert not offenders, ("Bitcoin pinning took 1.3 h to 128 h at launch; these still "
                           "promise it within an hour: " + ", ".join(offenders))


def test_the_hour_scan_sees_the_sentence_it_was_written_for():
    # Positive control on the shipped wording.
    for old in ("Bitcoin confirmation arrives within ~1 hour.",
                "pending — block-pinning happens within ~1 hour",
                "a real Bitcoin transaction (usually within ~1 hour)",
                "Within roughly an hour, it is committed inside a Bitcoin block.",
                "receipts upgrade to a Bitcoin commitment in roughly one hour.",
                "Bitcoin commitment: pending (~1 hour)",
                "# Wait ~1 hour for the calendar to publish its Bitcoin tx.",
                "# upgrade after 1 hour to get the full Bitcoin merkle proof:",
                "(~1 hour after anchoring)",
                "<p>About an hour. Orphograph fetches the upgraded proof",
                "The block-pinning upgrade happens automatically over the following hour;",
                "Bitcoin commitment expected within ≈1 hour.",
                "~ one hour to the block",
                'timestamping receipts say "block-pinned after one hour."',
                "commitment to Bitcoin typically confirms within a few hours,",
                "typically committed to the Bitcoin chain within hours;",
                "The seal is in place; the Bitcoin anchor usually lands within hours.",
                "Pending — usually within hours",
                "this follows issuance by approximately one hour,",
                "will batch it into a Bitcoin transaction within ~1 hour."):
        assert HOUR_PROMISE.search(old.lower()), old



def test_the_hour_scan_leaves_true_hour_statements_alone():
    for true in ("the block has accumulated 6 subsequent blocks (~1 hour)",
                 "Calendars batch-broadcast roughly hourly.",
                 "The claim code is emailed the moment your payment confirms (~1 hour on-chain)."):
        assert not HOUR_PROMISE.search(true.lower()), true



def _receipt_status(rec: dict) -> str:
    """Run the real receiptStatusLine from receipt.js under node, with the
    constants and helpers above it (the top of the file is declarations only)."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real receipt.js")
    src = RECEIPT_JS.read_text(encoding="utf-8")
    head = src[: src.index("function el(tag, attrs")]
    assert "function receiptStatusLine(rec)" in head, "receiptStatusLine moved below function el"
    driver = head + "\nprocess.stdout.write(receiptStatusLine(JSON.parse(process.argv[1])));\n"
    proc = subprocess.run([node, "-e", driver, json.dumps(rec)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_a_receipt_with_no_calendar_proof_is_not_called_pending():
    # Cycle 9: the certificate page was fixed in #283; the single-file receipt
    # page still read "Pending Bitcoin confirmation" for it.
    text = _receipt_status({"status": "pending", "calendars_ok": 0, "calendars_submitted_total": 5})
    assert "pending" not in text.lower() and "no bitcoin commitment" in text.lower(), text


def test_a_receipt_with_one_calendar_proof_is_still_pending():
    text = _receipt_status({"status": "pending", "calendars_ok": 1, "calendars_submitted_total": 5})
    assert text.startswith("Pending Bitcoin confirmation"), text


def test_a_receipt_without_a_calendars_ok_field_is_still_pending():
    # An older answer with no calendars_ok field is not a refusal.
    assert _receipt_status({"status": "pending"}).startswith("Pending Bitcoin confirmation")
    assert _friendly_status({"status": "pending"}).startswith("Pending Bitcoin confirmation")


WRITERS_JS = WEB / "writers.js"

# Loads the real writers.js in a node vm with a fake DOM, adds one version, and
# anchors the chain once per calendars_ok value in argv[2] ("0,1"), reporting
# what the page shows and stores after each attempt.
_WRITERS_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const [src, seq] = process.argv.slice(2);
function mk(id) {
  const cls = new Set();
  return { id, hidden: true, disabled: false, textContent: "", value: "", href: "",
    classList: { add: (c) => cls.add(c), remove: (...c) => c.forEach((x) => cls.delete(x)), has: (c) => cls.has(c) },
    addEventListener() {}, removeAttribute() {}, appendChild() {}, removeChild() {}, click() {} };
}
const els = {}, docL = {}, store = {};
const document = { getElementById: (id) => (els[id] = els[id] || mk(id)), querySelector: () => null,
  addEventListener: (ev, fn) => { (docL[ev] = docL[ev] || []).push(fn); }, createElement: () => mk("x"),
  body: { appendChild() {}, removeChild() {} } };
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; } };
const answers = seq.split(",").map(Number);
let call = 0;
const fetch = async () => { const ok = answers[call++];
  return { ok: true, status: 200, statusText: "OK",
    json: async () => ({ receipt_id: "RWRITERS0000001", hash_hex: "x", calendars_ok: ok, calendars_total: 5 }) }; };
const ctx = { document, localStorage, fetch, console, setTimeout, TextEncoder, crypto: globalThis.crypto, btoa,
  confirm: () => true, URL, Blob: function () {} };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(src, "utf8") +
  "\n;globalThis.__anchor = anchorChain; globalThis.__state = () => state; globalThis.__manifest = buildManifest;", ctx);
(async () => {
  (docL.DOMContentLoaded || []).forEach((f) => f());
  ctx.__state().versions.push({ sha256: "a".repeat(64), sha512: "b".repeat(128), length: 3,
    timestamp_local: new Date().toISOString() });
  const out = [];
  for (let i = 0; i < answers.length; i++) {
    await ctx.__anchor();
    const stored = JSON.parse(localStorage.getItem("orpho_writer_sessions") || "{}");
    out.push({ status: els["writers-status"].textContent, anchored: ctx.__state().anchored,
      button_disabled: els["anchor-chain-btn"].disabled, manifest_anchored: ctx.__manifest().anchored,
      stored_anchored: Object.values(stored).map((m) => m.anchored) });
  }
  process.stdout.write(JSON.stringify({ posts: call, out }));
})();
"""


def _writers(seq: str, tmp_path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real writers.js")
    driver = tmp_path / "writers_driver.js"
    driver.write_text(_WRITERS_DRIVER)
    proc = subprocess.run([node, str(driver), str(WRITERS_JS), seq], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_writers_leaves_a_chain_no_calendar_accepted_retryable(tmp_path):
    """Cycle 9 (post-merge review of #283): with calendars_ok 0 the page said
    "Not anchored ... try again" but had already marked the chain anchored,
    disabled the button and stored anchored: true. Now it stays un-anchored,
    and pressing Anchor again anchors the same chain."""
    run = _writers("0,1", tmp_path)
    first, second = run["out"]
    assert "not anchored" in first["status"].lower() and "anchor again" in first["status"].lower(), first
    assert first["anchored"] is False and first["button_disabled"] is False, first
    assert first["manifest_anchored"] is False and True not in first["stored_anchored"], first
    assert run["posts"] == 2 and second["anchored"] is True, run
    assert second["status"].startswith("Anchored."), second


V2_JS = WEB / "v2.js"

# Loads the real homepage script in a node vm with a fake DOM, drops one file,
# answers /api/anchor with calendars_ok = argv[2], and reports the banner, the
# stored recent-receipt status and how many polling intervals were started.
_V2_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const [src, cal] = process.argv.slice(2);
class El {
  constructor(tag, id) { this.tagName = tag; this.id = id || ""; this.children = []; this._t = ""; this.style = {};
    this.dataset = {}; this.hidden = false; this.className = ""; this._l = {}; this.files = null; this.value = "";
    const s = new Set(); this.classList = { add: (c) => s.add(c), remove: (c) => s.delete(c), contains: (c) => s.has(c), toggle() {} }; }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._t = String(v); this.children = []; }
  appendChild(c) { this.children.push(c); return c; } removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  addEventListener(ev, fn) { (this._l[ev] = this._l[ev] || []).push(fn); }
  setAttribute() {} removeAttribute() {} getAttribute() { return null; } querySelector() { return null; }
  querySelectorAll() { return []; } closest() { return null; } click() {} focus() {}
  insertBefore(c) { return this.appendChild(c); } prepend(c) { this.children.unshift(c); }
}
const known = {};
["drop", "drop-input", "drop-btn", "status", "sticky-status"].forEach((id) => { known[id] = new El("div", id); });
const document = { getElementById: (id) => known[id] || null, querySelector: () => null, querySelectorAll: () => [],
  createElement: (t) => new El(t), createTextNode: (t) => ({ textContent: String(t), children: [] }),
  addEventListener() {}, body: new El("body"), documentElement: new El("html") };
const store = {};
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); },
  removeItem: (k) => { delete store[k]; } };
const fetch = async (url) => String(url) === "/api/anchor"
  ? { ok: true, status: 200, json: async () => ({ receipt_id: "RHOMEPAGE000001", calendars_ok: Number(cal), calendars_total: 5 }), text: async () => "" }
  : { ok: false, status: 404, json: async () => ({}), text: async () => "" };
let intervals = 0;
const ctx = { document, localStorage, sessionStorage: localStorage, fetch, console, setTimeout, clearTimeout,
  setInterval: () => { intervals += 1; return intervals; }, clearInterval() {}, TextEncoder, crypto: globalThis.crypto,
  Intl, URL, URLSearchParams, Blob: function () {}, navigator: {}, history: { replaceState() {} },
  location: { search: "", pathname: "/", href: "https://example.test/", hash: "" } };
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
(async () => {
  const input = known["drop-input"];
  const bytes = new TextEncoder().encode("hello");
  input.files = [{ name: "f.txt", size: bytes.length, arrayBuffer: async () => bytes.buffer }];
  input._l.change.forEach((f) => f());
  await new Promise((r) => setTimeout(r, 300));
  const recent = JSON.parse(store["orpho_recent_receipts"] || "[]");
  process.stdout.write(JSON.stringify({ banner: known["sticky-status"].textContent, status: known.status.textContent,
    intervals, recent_status: recent.length ? recent[0].status : null }));
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(2); });
"""


def _homepage(calendars_ok: int, tmp_path) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real v2.js")
    driver = tmp_path / "v2_driver.js"
    driver.write_text(_V2_DRIVER)
    proc = subprocess.run([node, str(driver), str(V2_JS), str(calendars_ok)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_homepage_does_not_watch_for_a_pin_that_never_comes(tmp_path):
    """Cycle 9 (post-merge review of #283): with calendars_ok 0 the homepage
    still showed "Watching for Bitcoin confirmation…", started polling for a
    pin that can never come, and stored the receipt as "pending"."""
    run = _homepage(0, tmp_path)
    assert "watching" not in run["banner"].lower() and "no calendar accepted" in run["banner"].lower(), run
    assert run["intervals"] == 0, run
    assert run["recent_status"] == "no commitment", run


def test_homepage_still_watches_a_committed_receipt(tmp_path):
    run = _homepage(1, tmp_path)
    assert "watching for bitcoin confirmation" in run["banner"].lower(), run
    assert run["intervals"] == 1 and run["recent_status"] == "pending", run


def _receipt_js_call(expr: str, rec: dict) -> str:
    """Evaluate expr(rec) with the declarations at the top of the real receipt.js."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real receipt.js")
    src = RECEIPT_JS.read_text(encoding="utf-8")
    head = src[: src.index("function el(tag, attrs")]
    driver = head + f"\nconst rec = JSON.parse(process.argv[1]);\nprocess.stdout.write(JSON.stringify({expr}));\n"
    proc = subprocess.run([node, "-e", driver, json.dumps(rec)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("calendars_ok,expect_none", [(0, True), (1, False), (None, False)],
                         ids=["no-calendar", "one-calendar", "field-absent"])
def test_the_receipt_verdict_and_facts_tell_a_root_with_no_commitment_so(calendars_ok, expect_none):
    """Cycle 9 (review of PR #284): the verdict banner said "confirmation in
    progress … Check back shortly" and the facts strip "Pending — an hour to
    several days" for a receipt no calendar accepted."""
    rec = {"status": "pending", "created_at": "2026-10-03T05:00:00Z", "calendars_submitted_total": 5}
    if calendars_ok is not None:
        rec["calendars_ok"] = calendars_ok
    verdict = _receipt_js_call('receiptVerdictCopy(rec, "October 3, 2026")', rec)
    fact = _receipt_js_call("receiptBtcFact(rec, (d) => d.toISOString())", rec)
    if expect_none:
        assert "no bitcoin commitment" in (verdict["headline"] + verdict["sub"]).lower(), verdict
        assert "check back" not in verdict["sub"].lower() and fact.startswith("None"), (verdict, fact)
    else:
        assert verdict["kind"] == "pending" and "no bitcoin commitment" not in verdict["headline"].lower(), verdict
        # Round 2: the facts strip read "None" for a record with no
        # calendars_ok field while the banner kept "in progress". Only an
        # explicit 0 means no commitment, on every line of the page.
        assert fact.startswith("Pending"), fact



FOLDER_JS = WEB / "folder.js"

_FOLDER_DRIVER = r"""
const fs = require("fs"), vm = require("vm");
const [src, cal] = process.argv.slice(2);
class El {
  constructor() { this.children = []; this._t = ""; this.hidden = true; this.className = ""; this.style = {}; this.dataset = {}; }
  get textContent() { return this._t + this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this._t = String(v); this.children = []; }
  appendChild(c) { this.children.push(c); return c; } replaceChildren() { this.children = []; this._t = ""; }
  addEventListener() {} setAttribute() {} removeAttribute() {}
}
const document = { querySelector: () => null, getElementById: () => null, createElement: () => new El(),
  createTextNode: (t) => ({ _t: String(t), textContent: String(t), children: [] }), addEventListener() {} };
const ctx = { document, console, TextEncoder, crypto: globalThis.crypto, URL, setTimeout };
ctx.window = ctx; ctx.addEventListener = () => {};
vm.createContext(ctx);
const code = fs.readFileSync(src, "utf8").replace(/^export /gm, "").replace(/^import .*$/gm, "");
vm.runInContext(code + "\n;globalThis.__render = _renderReceipt;", ctx);
const host = new El();
ctx.__render(host, { receipt_id: "RFOLDER0000001", root_hex: "ab".repeat(32), calendars_ok: Number(cal),
  calendars_total: 5, kind: "folder" }, [{ path: "a.csv", digest: "cd".repeat(32) }], 0);
process.stdout.write(JSON.stringify(host.textContent));
"""


@pytest.mark.parametrize("calendars_ok", [0, 1])
def test_homepage_folder_card_tells_a_root_with_no_commitment_so(tmp_path, calendars_ok):
    """Cycle 9 (review of PR #284): the folder card always said "A folder receipt
    has been issued" and "Bitcoin commitment expected …", also for a root no
    calendar accepted."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not on PATH: this check runs the real folder.js")
    driver = tmp_path / "folder_driver.js"
    driver.write_text(_FOLDER_DRIVER)
    proc = subprocess.run([node, str(driver), str(FOLDER_JS), str(calendars_ok)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    text = json.loads(proc.stdout).lower()
    if calendars_ok == 0:
        assert "no bitcoin commitment" in text and "commitment expected" not in text, text[:400]
    else:
        assert "commitment expected" in text and "no bitcoin commitment" not in text, text[:400]


# Round 3 of PR #284: the #btc row and the Bitcoin card had no test, so the
# lines could drift apart again. Each page now reads calendars_ok only through
# its helpers; any other comparison of it (or of a count derived from it, the
# forms earlier versions used) fails here.
_CAL_COMPARE = re.compile(
    r"calendars_ok\s*\)?\s*([!=]==?|[<>]=?)|\(\s*rec\.calendars_ok\s*\|\|\s*0\s*\)\s*[<>]"
    r"|serversOk\s*([!=]==?|[<>]=?)\s*\d|\bcok\s*([!=]==?|[<>]=?)\s*\d")
_CAL_HELPERS = re.compile(r"^function (noCommitment|someCalendarAccepted)\(rec\) \{.*\}$", re.M)


def _calendar_comparisons_outside_helpers(src: str) -> list:
    body = _CAL_HELPERS.sub("", src)
    return [m.group(0) for m in _CAL_COMPARE.finditer(body)]


@pytest.mark.parametrize("page", ["receipt.js", "certificate.js"])
def test_every_line_of_the_page_reads_calendars_ok_through_one_helper(page):
    src = (WEB / page).read_text(encoding="utf-8")
    assert "function noCommitment(rec) { return rec.calendars_ok === 0; }" in src
    assert _calendar_comparisons_outside_helpers(src) == [], page


def test_the_helper_check_sees_the_forms_earlier_versions_used():
    for old in ('textContent: rec.calendars_ok > 0 ? "Pending" : "None"',
                'else if (!(rec.calendars_ok > 0)) $("#btc")',
                '$("#btc").textContent = _c.serversOk > 0',
                'if (calendarCounts(rec).serversOk === 0) return "None"',
                'if (raw === "pending" && cok === 0) return',
                'if ((rec.calendars_ok || 0) > 0) {',
                'textContent: rec.calendars_ok !== 0'):
        assert _calendar_comparisons_outside_helpers(old), old
    # Not comparisons: counts printed on the page.
    for fine in ('`${rec.calendars_ok || 0} of ${rec.calendars_total || 5} OTS proofs valid`',
                 'serversOk: num(rec.calendars_ok, okFiles.length),',
                 '`${c.serversOk} of ${c.serversTotal} calendar servers · `'):
        assert not _calendar_comparisons_outside_helpers(fine), fine
