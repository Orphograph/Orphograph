"""@receipt-link-base-class: receipt links prefer the issuing server's receipt_url.

A client that reaches the server under an internal name (a proxy, a CI
network host, a LAN address) wrote links built from that name, which no one
outside can open. The server answers with receipt_url built from its public
SITE_URL. #290 made the GitHub release action and the watch-folder
integration use it; this is the rest of the class. The served value is taken
only when it is a plain http(s) link to /r/<this receipt id>; anything else
falls back to the base the client used.

mcp/orphograph_mcp.py has the same defect and is not changed here: it is
pinned by sha256 to its released package (tests/test_mcp_release_currency.py),
so its fix ships with the next MCP release.
"""
from __future__ import annotations

import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RID = "RIDabc_123-x"
BASE = "http://anchor-proxy.internal:8080/"
GOOD = "https://public.example/r/" + RID
FALLBACK = "http://anchor-proxy.internal:8080/r/" + RID

CASES = [
    (GOOD, GOOD),
    ("https://public.example/orpho/r/" + RID, "https://public.example/orpho/r/" + RID),
    ("http://public.example:8443/r/" + RID, "http://public.example:8443/r/" + RID),
    ("https://public.example/r/OTHER", FALLBACK),
    ("https://public.example/r/" + RID + "x", FALLBACK),
    ("javascript:alert(1)//r/" + RID, FALLBACK),
    ('https://public.example/r/' + RID + '"', FALLBACK),
    ("https://user@public.example/r/" + RID, FALLBACK),
    ("https://public.example/a b/r/" + RID, FALLBACK),
    ("https://public.example/x/r/" + RID + "?q=1", FALLBACK),
    ("ftp://public.example/r/" + RID, FALLBACK),
    ("", FALLBACK),
    (None, FALLBACK),
    (7, FALLBACK),
]

MODULES = [
    ".github/actions/anchor/anchor_ci.py",
    "capture/orphograph_capture.py",
    "capture/orphograph_usb.py",
    "marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py",
    "sdk/orphograph/__init__.py",
    "tools/anchor_commit.py",
]


def _load(rel: str):
    name = "rlink_" + re.sub(r"\W", "_", rel)
    # No __pycache__ next to the source: web/ is served, and a .pyc there
    # fails the web scans (tests/test_lightning_rail_is_retired.py).
    sys.dont_write_bytecode, before = True, sys.dont_write_bytecode
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    sys.dont_write_bytecode = before
    return mod


@pytest.mark.parametrize("rel", MODULES)
def test_receipt_link_rule(rel):
    mod = _load(rel)
    for served, want in CASES:
        assert mod.receipt_link(BASE, RID, served) == want, (rel, served)


@pytest.mark.parametrize("rel", MODULES)
def test_no_link_is_built_from_the_base_outside_the_helper(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert not re.search(r"/r/\{", src), rel + " still formats /r/{id} from a base"


# ── each call site, through its own entry point ──────────────────────────────

def test_capture_sidecar(tmp_path):
    oc = _load("capture/orphograph_capture.py")
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x")
    side = oc._write_receipt_sidecar(f, {"receipt_id": RID, "receipt_url": GOOD}, BASE)
    assert json.loads(Path(side).read_text())["receipt_url"] == GOOD
    side = oc._write_receipt_sidecar(f, {"receipt_id": RID, "receipt_url": "https://evil/r/X"}, BASE)
    assert json.loads(Path(side).read_text())["receipt_url"] == FALLBACK


def test_anchor_commit_record(tmp_path):
    ac = _load("tools/anchor_commit.py")
    out = ac.anchor_commit(
        "HEAD", endpoint=BASE, repo_root=tmp_path,
        resolve_fn=lambda ref: ("c" * 40, "t" * 40, "subject"),
        anchor_fn=lambda *a: (True, {"receipt_id": RID, "receipt_url": GOOD, "calendars_ok": 3}))
    assert out["receipt_url"] == GOOD, out


def test_anchor_ci_result():
    ci = _load(".github/actions/anchor/anchor_ci.py")
    code, result = ci.run({"ORPHO_TEXT": "hello", "ORPHO_ENDPOINT": BASE},
                          anchor_fn=lambda *a: (True, {"receipt_id": RID, "receipt_url": GOOD,
                                                       "calendars_ok": 3}))
    assert code == 0 and result["receipt_url"] == GOOD, result


def test_marketplace_plugin_output(monkeypatch, tmp_path):
    an = _load("marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py")
    monkeypatch.setattr(an, "post_anchor", lambda *a, **k: (200, {
        "receipt_id": RID, "receipt_url": GOOD, "calendars_ok": 3, "calendars_total": 3}))
    monkeypatch.setattr(sys, "argv", ["anchor.py", "a" * 64, "--endpoint", BASE, "--json"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert an.main() == 0
    assert json.loads(buf.getvalue())["receipt_url"] == GOOD


def test_legacy_sdk_receipt():
    sdk = _load("sdk/orphograph/__init__.py")
    r = sdk.Receipt._from_response({"receipt_id": RID, "receipt_url": GOOD}, BASE, "a", "b")
    assert r.receipt_url == GOOD
    # It used to take any served value unchecked.
    r = sdk.Receipt._from_response({"receipt_id": RID, "receipt_url": "javascript:x"}, BASE, "a", "b")
    assert r.receipt_url == FALLBACK


def test_capture_usb_record():
    src = (ROOT / "capture/orphograph_usb.py").read_text(encoding="utf-8")
    assert 'receipt_link(endpoint, rid, resp.get("receipt_url"))' in src


# ── Lightroom plugins (Lua) ──────────────────────────────────────────────────

LUA_PLUGINS = [
    ("dist/lightroom-plugin/Orphograph.lrplugin", "ExportFilter.lua"),
    ("lightroom-plugin/orphograph.lrdevplugin", "ExportFilterProvider.lua"),
]


@pytest.mark.parametrize("plugin,caller", LUA_PLUGINS)
def test_lightroom_plugins_use_the_helper(plugin, caller):
    d = ROOT / plugin
    helper = (d / "ReceiptLink.lua").read_text(encoding="utf-8")
    assert "function M.link(" in helper
    src = (d / caller).read_text(encoding="utf-8")
    assert 'require "ReceiptLink"' in src
    assert not re.search(r'"/r/"\s*\.\.', src), caller + " still concatenates /r/ .. rid"


def _lua():
    for name in ("lua", "lua5.4", "lua5.3", "luajit"):
        p = shutil.which(name)
        if p:
            return p
    return None


@pytest.mark.parametrize("plugin,caller", LUA_PLUGINS)
def test_lightroom_receipt_link_rule_in_lua(plugin, caller):
    """Runs the Lua helper itself when an interpreter is present. CI has
    none, and a skip fails the suite there (conftest), so without one this
    checks the helper's text against the Python rule's pieces instead; the
    PR records the run with an interpreter."""
    lua = _lua()
    helper = ROOT / plugin / "ReceiptLink.lua"
    if lua is None:
        src = helper.read_text(encoding="utf-8")
        for piece in ('"^https?://', '"/r/" .. rid', "find(\"//\", 1, true)"):
            assert piece in src, piece
        return
    lines = ["local M = dofile(%s)" % json.dumps(str(helper))]
    for served, want in CASES:
        s = "nil" if served is None else (str(served) if isinstance(served, int) else json.dumps(served))
        lines.append("io.write(M.link(%s, %s, %s) == %s and 'ok\\n' or ('BAD ' .. tostring(%s) .. '\\n'))"
                     % (json.dumps(BASE), json.dumps(RID), s, json.dumps(want), s))
    out = subprocess.run([lua, "-"], input="\n".join(lines), capture_output=True, text=True,
                         timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["ok"] * len(CASES), out.stdout
