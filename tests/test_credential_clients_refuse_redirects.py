"""@redirect-key-class: no client carries an Orphograph credential to another host.

urllib's default opener follows a 3xx to any host and copies every request
header except the body's, so X-Orpho-Api-Key and X-Pack-Token went with it,
and a POST turned into a GET whose answer (the other host's) was read as the
receipt. #290 fixed the Python SDK client; this is the rest of the class.

Every client below sends a credential through one function, ``_urlopen``,
backed by an opener that refuses redirects. Three checks:

  * behaviour: each client's ``_urlopen`` meets a real 3xx (301/302/303/307/
    308, GET and POST) from a local server pointing at a second local server
    on another host name; it must raise HTTPError and the second server must
    see nothing;
  * entry points: the three integrations named in the finding are driven
    through their public functions against the same pair of servers;
  * shape: no client calls urlopen, build_opener (without the refusing
    handler), requests or http.client directly, and every file in the repo
    that names a credential header and makes a urllib request is either in
    CLIENTS or in SAFE with its reason (a new client cannot slip past).
"""
from __future__ import annotations

import ast
import http.server
import importlib.util
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Clients that send X-Orpho-Api-Key / X-Pack-Token / X-Orpho-Founder to a
# server the user (or an env var) names.
CLIENTS = [
    ".github/actions/anchor/anchor_ci.py",
    "capture/orphograph_capture.py",
    "capture/orphograph_usb.py",
    "integrations/github-action/anchor_artifacts.py",
    "integrations/openclaw/orpho_agent_anchor.py",
    "integrations/watch-folder/orphograph_watch.py",
    "marketplace/orphograph-plugin/skills/orphograph-anchor/anchor.py",
    "scripts/auto_anchor_repo.py",
    "scripts/folder_watch.py",
    "scripts/publish_watcher.py",
    "scripts/usb_offline_anchor_submit.py",
    "scripts/weekly_anchor.py",
    "sdk/orphograph/__init__.py",
    "sdk-python/orphograph/_client.py",
    "tools/anchor_commit.py",
    "tools/anchor_prompt.py",
    "web/dist/orphograph-watch.py",
]

# Files that name a credential header next to a urllib request but are not
# the class: the host is a constant first-party or vendor address, not one a
# caller chooses.
SAFE = {
    "scripts/morning_check.py": "fixed host https://orphograph.com (PROD_BASE constant)",
}

# The class, not fixed here: mcp/orphograph_mcp.py is pinned by sha256 in
# mcp/server.json (tests/test_mcp_release_currency.py), so any change to it is
# a new MCP release (version, .mcpb, registry entry), which is founder-held;
# web/mcp/orphograph_mcp.py must stay byte-equal to it. The fix belongs in the
# 0.1.2 release candidate.
BLOCKED = {
    "mcp/orphograph_mcp.py": "release-pinned; fix ships with MCP 0.1.2 (founder-held)",
    "web/mcp/orphograph_mcp.py": "byte copy of mcp/orphograph_mcp.py",
}

_CRED = re.compile(r"X-Orpho-Api-Key|X-Pack-Token|X-Orpho-Founder")
_REQ = re.compile(r"urlopen|build_opener")


def _load(rel: str):
    name = "redir_" + re.sub(r"\W", "_", rel)
    # No __pycache__ next to the source: web/ is served, and a .pyc there
    # fails the web scans (tests/test_lightning_rail_is_retired.py).
    sys.dont_write_bytecode, before = True, sys.dont_write_bytecode
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    # A script imports its siblings (scripts/_usb_safety.py) the way it does
    # when run from its own directory.
    here = str((ROOT / rel).parent)
    sys.path.insert(0, here)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(here)
    sys.dont_write_bytecode = before
    return mod


class _Trap(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def _any(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        type(self).hits.append((self.command, self.path, dict(self.headers)))
        body = json.dumps({"receipt_id": "TRAPPED", "calendars_ok": 3,
                           "receipt_url": "http://localhost/r/TRAPPED"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_HEAD = _any

    def log_message(self, *a):
        pass


def _redirector(target: str, code_box: list):
    class _Redirect(http.server.BaseHTTPRequestHandler):
        def _any(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            self.send_response(code_box[0])
            self.send_header("Location", target + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = do_HEAD = _any

        def log_message(self, *a):
            pass
    return _Redirect


@pytest.fixture()
def servers():
    _Trap.hits = []
    trap = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Trap)
    # Another host name for the same loopback server: a cross-host redirect.
    target = "http://localhost:%d" % trap.server_address[1]
    code_box = [302]
    redir = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _redirector(target, code_box))
    for s in (trap, redir):
        threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % redir.server_address[1], code_box
    finally:
        for s in (trap, redir):
            s.shutdown()
            s.server_close()


def test_the_trap_is_reachable_by_a_client_that_follows(servers):
    """Control for the fixture: urllib's default opener does follow this
    redirect to the other host name and hands it the key, so a client that
    reaches nothing there is refusing, not failing to connect."""
    base, code_box = servers
    code_box[0] = 302
    req = urllib.request.Request(base + "/api/anchor", data=b"{}", method="POST",
                                 headers={"X-Orpho-Api-Key": "k-secret",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert json.loads(resp.read())["receipt_id"] == "TRAPPED"
    assert len(_Trap.hits) == 1
    assert _Trap.hits[0][2].get("X-Orpho-Api-Key") == "k-secret"


@pytest.mark.parametrize("rel", CLIENTS)
def test_client_urlopen_refuses_every_redirect(rel, servers):
    base, code_box = servers
    mod = _load(rel)
    assert callable(getattr(mod, "_urlopen", None)), rel + " has no _urlopen"
    for code in (301, 302, 303, 307, 308):
        code_box[0] = code
        for method, data in (("GET", None), ("POST", b"{}")):
            req = urllib.request.Request(
                base + "/api/anchor", data=data, method=method,
                headers={"X-Orpho-Api-Key": "k-secret", "X-Pack-Token": "p-secret",
                         "X-Orpho-Founder": "f-secret",
                         "Content-Type": "application/json"})
            with pytest.raises(urllib.error.HTTPError) as ei:
                mod._urlopen(req, timeout=5)
            assert ei.value.code == code, (rel, code, method)
    assert _Trap.hits == [], (rel, _Trap.hits[:1])


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            yield name, node


@pytest.mark.parametrize("rel", CLIENTS)
def test_client_has_one_request_path(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for name, node in _calls(tree):
        assert name != "urlopen", "%s:%d calls urlopen directly" % (rel, node.lineno)
        if name == "build_opener":
            args = [ast.unparse(a) for a in node.args]
            assert "_RefuseRedirects" in args, "%s:%d builds an opener that follows" % (rel, node.lineno)
    for banned in ("import requests", "import httpx", "http.client"):
        assert banned not in src, (rel, banned)
    assert "class _RefuseRedirects" in src, rel


def test_sweep_is_complete():
    """Every tracked .py file that names a credential header and makes a
    urllib request is classified. Tests and the server are out of scope (the
    server receives the headers; it does not send them)."""
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    # Untracked new files are swept too (git ls-files misses them).
    out += subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "*.py"],
                          cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    found = set()
    for rel in out:
        p = Path(rel)
        if p.parts[0] in ("tests", "server") or p.name.startswith("test_"):
            continue
        try:
            src = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _CRED.search(src) and _REQ.search(src):
            found.add(rel)
    unclassified = found - set(CLIENTS) - set(SAFE) - set(BLOCKED)
    assert not unclassified, sorted(unclassified)
    assert set(CLIENTS) <= found, sorted(set(CLIENTS) - found)


def test_blocked_mcp_finding_is_still_open():
    """Tripwire: when the MCP release ships the fix, this fails and the two
    files move from BLOCKED to CLIENTS."""
    for rel in BLOCKED:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "urllib.request.urlopen(" in src and "_RefuseRedirects" not in src, (
            rel + " now refuses redirects: move it from BLOCKED to CLIENTS")


# ── the three integrations named in the finding, through their entry points ──

def test_openclaw_agent_anchor_entry_point(servers):
    base, code_box = servers
    mod = _load("integrations/openclaw/orpho_agent_anchor.py")
    for code in (301, 302, 303):
        code_box[0] = code
        out = mod.post_anchor(base, "a" * 64, "b" * 128, api_key="k-secret")
        assert out.get("receipt_id") != "TRAPPED" and out.get("error"), out
        out = mod.post_anchor(base, "a" * 64, "b" * 128, pack_token="p-secret")
        assert out.get("receipt_id") != "TRAPPED" and out.get("error"), out
    assert _Trap.hits == []


def test_watch_folder_entry_point(servers):
    base, code_box = servers
    for rel in ("integrations/watch-folder/orphograph_watch.py", "web/dist/orphograph-watch.py"):
        mod = _load(rel)
        for code in (301, 302, 303):
            code_box[0] = code
            with pytest.raises(urllib.error.HTTPError):
                mod.post_anchor(base, "a" * 64, "b" * 128, api_key="k-secret")
            with pytest.raises(urllib.error.HTTPError):
                mod.post_anchor(base, "a" * 64, "b" * 128, pack_token="p-secret")
    assert _Trap.hits == []


def test_github_action_entry_point(servers, monkeypatch):
    base, code_box = servers
    mod = _load("integrations/github-action/anchor_artifacts.py")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("ORPHO_API_KEY", "k-secret")
    for code in (301, 302, 303):
        code_box[0] = code
        receipt, _fatal = mod.anchor_one(base, mod.build_headers(), "a" * 64, "b" * 128)
        assert not receipt or receipt.get("receipt_id") != "TRAPPED", receipt
    assert _Trap.hits == []
