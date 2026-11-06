"""/check — the receiving-side check page (2026-11-05).

web/check.js must give the same answer as the MCP tool it mirrors
(mcp/orphograph_mcp.py tool_verify_output) on every case: the browser page and
the agent tool are two doors to one rule, and a door that disagrees would tell
a human "match" where an agent hears "do not trust it". The JS runs under Node
(CI installs Node 22 for the snark tests) on the same fixtures the Python
function sees through a stubbed _http.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
CHECK_JS = ROOT / "web" / "check.js"


def _mcp():
    sys.dont_write_bytecode, before = True, sys.dont_write_bytecode
    spec = importlib.util.spec_from_file_location("mcp_for_check_page", ROOT / "mcp/orphograph_mcp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.dont_write_bytecode = before
    return mod


M = _mcp()
RID = "ChkTestReceipt01"


def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _cases() -> list[dict]:
    run3 = M._build_run_manifest(["first step", "second step", "third step"])
    run5 = M._build_run_manifest([f"s{i}" for i in range(5)])
    run1 = M._build_run_manifest(["only step"])
    tampered = json.loads(json.dumps(run3))
    tampered["leaves"][1]["leaf_hex"] = "00" * 32
    private = json.loads(json.dumps(run3))
    for leaf in private["leaves"]:
        leaf.pop("path")
    salt = bytes(range(32))
    return [
        {"name": "single match", "text": "hello agent", "rec": {"hash_hex": _h(b"hello agent")}},
        {"name": "single match, upper-case anchored hex", "text": "x",
         "rec": {"hash_hex": _h(b"x").upper()}},
        {"name": "salted single match", "text": "approved", "salt": salt.hex(),
         "rec": {"hash_hex": _h(salt + b"approved")}},
        {"name": "salted text without its salt", "text": "approved",
         "rec": {"hash_hex": _h(salt + b"approved")}, "folder": {"error": "http_error", "status": 400}},
        {"name": "single, not this output", "text": "other",
         "rec": {"hash_hex": _h(b"hello agent")}, "folder": {"error": "http_error", "status": 400}},
        {"name": "run match, middle step", "text": "second step",
         "rec": {"hash_hex": run3["root_hex"]}, "folder": {"manifest": run3}},
        {"name": "run of five, last (lone, promoted) step", "text": "s4",
         "rec": {"hash_hex": run5["root_hex"]}, "folder": {"manifest": run5}},
        {"name": "run of one", "text": "only step",
         "rec": {"hash_hex": run1["root_hex"]}, "folder": {"manifest": run1}},
        {"name": "run, served leaf_hex tampered", "text": "second step",
         "rec": {"hash_hex": run3["root_hex"]}, "folder": {"manifest": tampered}},
        {"name": "run, root is not the anchored hash", "text": "second step",
         "rec": {"hash_hex": "ab" * 32}, "folder": {"manifest": run3}},
        {"name": "run, paths kept private", "text": "second step",
         "rec": {"hash_hex": run3["root_hex"]}, "folder": {"manifest": private}},
        {"name": "run, no such step", "text": "fourth step",
         "rec": {"hash_hex": run3["root_hex"]}, "folder": {"manifest": run3}},
        {"name": "manifest unreachable", "text": "second step",
         "rec": {"hash_hex": run3["root_hex"]}, "folder": {"error": "network_error"}},
        {"name": "receipt not found", "text": "x", "rec": {"error": "http_error", "status": 404}},
        {"name": "receipt carries no hash", "text": "x", "rec": {"created_at": "2026-11-05T00:00:00Z"}},
        {"name": "manifest with no leaves", "text": "x",
         "rec": {"hash_hex": "cd" * 32}, "folder": {"manifest": {"leaves": []}}},
    ]


KEYS = ("ok", "match", "kind", "matched_paths", "digest_listed", "reason", "problems")


def _python(case: dict) -> dict:
    def fake_http(method, path, body=None):
        if path.startswith("/api/verify_folder/"):
            return case.get("folder", {"error": "http_error", "status": 400})
        return case["rec"]

    M._http = fake_http
    args = {"receipt_id": RID, "text": case["text"]}
    if case.get("salt"):
        args["salt_hex"] = case["salt"]
    out = M.tool_verify_output(args)
    return {k: out.get(k) for k in KEYS} | {"has_error": bool(out.get("error"))}


_NODE_HARNESS = r"""
const chk = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
(async () => {
  const out = [];
  for (const c of cases) {
    const target = await chk.outputDigest(new TextEncoder().encode(c.text), c.salt || "");
    const r = await chk.decide(c.rid, target, c.rec,
      () => Promise.resolve(c.folder || {error: "http_error", status: 400}));
    out.push(r);
  }
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


def _node(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    assert node, "node is required (CI installs Node 22); the browser check cannot be tested without it"
    payload = json.dumps([dict(c, rid=RID) for c in cases])
    proc = subprocess.run([node, "-e", _NODE_HARNESS, str(CHECK_JS)], input=payload,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    return [{k: r.get(k) for k in KEYS} | {"has_error": bool(r.get("error"))}
            for r in json.loads(proc.stdout)]


def test_the_page_and_the_mcp_tool_agree_on_every_case():
    cases = _cases()
    js = _node(cases)
    for case, j in zip(cases, js):
        p = _python(case)
        assert j == p, (case["name"], {"js": j, "python": p})


def test_the_cases_cover_every_answer():
    """A comparison over cases that all say the same thing proves little."""
    seen = {(r["ok"], r["match"], r["kind"], bool(r["problems"]), bool(r["digest_listed"]))
            for r in (_python(c) for c in _cases())}
    assert (True, True, "single", False, False) in seen
    assert (True, True, "run", False, False) in seen
    assert (True, False, "run", True, False) in seen       # do not trust the manifest
    assert (True, False, "run", False, True) in seen       # listed, path private
    assert (True, False, "run", False, False) in seen      # no such step
    assert (True, False, "single", False, False) in seen   # not this output
    assert any(not ok for ok, *_ in seen)                  # nothing decided


def test_matched_paths_name_the_step():
    case = next(c for c in _cases() if c["name"] == "run match, middle step")
    assert _node([case])[0]["matched_paths"] == ["step-000002"]


_FRAG = r"""
const chk = require(process.argv[1]);
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map(h => chk.parseFragment(h))));
"""


def test_the_fragment_accepts_only_a_receipt_id_and_hex_salt():
    frags = ["#r=ChkTestReceipt01", "#r=ChkTestReceipt01&s=" + "ab" * 32, "#r=../../etc",
             "#r=<script>", "#s=abc", "#r=" + "a" * 65, "#s=" + "zz" * 4, "", "#r=%41bc",
             "#r=a%ZZ"]
    proc = subprocess.run([shutil.which("node"), "-e", _FRAG, str(CHECK_JS)],
                          input=json.dumps(frags), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got[0] == {"receipt_id": "ChkTestReceipt01", "salt_hex": ""}
    assert got[1] == {"receipt_id": "ChkTestReceipt01", "salt_hex": "ab" * 32}
    for i in (2, 3, 5, 9):
        assert got[i]["receipt_id"] == "", frags[i]
    assert got[4]["salt_hex"] == "" and got[6]["salt_hex"] == "" and got[7] == {"receipt_id": "", "salt_hex": ""}
    assert got[8]["receipt_id"] == "Abc"


# ── the live surface ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def server(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("check"), stub_calendars=True)


def test_check_page_is_served_with_get_and_head_alike(server):
    g_status, g_body, g_headers = _srv.request(server, "/check")
    h_status, h_body, h_headers = _srv.request(server, "/check", "HEAD")
    assert g_status == 200 and h_status == 200 and h_body in (b"", "")
    body = g_body.decode() if isinstance(g_body, bytes) else g_body
    assert '<script src="/check.js?v=1"></script>' in body and "<script>" not in body
    for name in ("Content-Security-Policy", "Strict-Transport-Security"):
        assert g_headers.get(name) and g_headers.get(name) == h_headers.get(name), name


def test_check_script_is_the_pinned_file(server):
    status, body, _ = _srv.request(server, "/check.js?v=1")
    assert status == 200
    data = body if isinstance(body, bytes) else body.encode()
    pin = json.loads((ROOT / "web/asset_versions.json").read_text())["/check.js"]
    assert hashlib.sha256(data).hexdigest() == pin["sha256"] == hashlib.sha256(CHECK_JS.read_bytes()).hexdigest()


def test_every_receipt_page_carries_its_check_link():
    html = (ROOT / "web/receipt.html").read_text()
    assert 'href="/check#r={{RECEIPT_ID}}"' in html
