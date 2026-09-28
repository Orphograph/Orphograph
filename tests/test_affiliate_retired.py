"""The account-level referral/affiliate program is retired (10A).

Founder decision 2026-09-27. It never worked end to end: /api/me/referral-code
handed out `ref_` + 8 hex of the account's email id, which the Stripe webhook
cannot resolve (only Pack claim-code referrals are), affiliate.register_signup
had no caller, so no signup was ever recorded and payouts could never be
owed, and no page offered any of it. Every GET also wrote the code registry.
Its three endpoints now answer 410 Gone, for GET and HEAD alike and whoever
asks, and write nothing. Pack referral links in claim emails are a different
program and keep working.

Nothing else in the server reaches the module either: server/affiliate.py
stays on disk for its ledgers, and no other server module imports it.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

REPO = Path(__file__).resolve().parent.parent
RETIRED_GET = ("/api/me/referral-code", "/api/me/affiliate")
RETIRED_MODULE = "affiliate"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("affiliate_retired")
    for base in _srv.server_processes(d, stub_calendars=True):
        yield base, d


def _account_for(path: str) -> str:
    """The account that reads `path`: a slug of the path, so each retired
    path signs in as somebody else."""
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") + "@example.test"


def _signed_in(base: str, data_dir: Path, email: str) -> dict:
    code = (f"import os,sys;os.environ['ORPHO_DATA_DIR']={str(data_dir)!r};"
            f"sys.path.insert(0,{str(REPO / 'server')!r});import auth;"
            f"print(auth.issue_link_token({email!r})[0])")
    token = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=60, check=True).stdout.strip()
    _s, _b, headers = _srv.request(base, f"/a/{token}", timeout=15)
    cookie = headers.get("Set-Cookie", "")
    assert "orpho_sid=" in cookie, "control: could not sign in"
    return {"Cookie": "orpho_sid=" + cookie.split("orpho_sid=", 1)[1].split(";", 1)[0]}


def _ledgers(data_dir: Path) -> dict[str, bytes]:
    """Every ledger file in the data dir, by name. Not the server log, which
    every request appends to, nor the rate-limit snapshot, which every
    limited route rewrites."""
    return {p.name: p.read_bytes() for p in sorted(data_dir.glob("*.jsonl"))}


@pytest.mark.parametrize("path", RETIRED_GET)
def test_the_retired_reads_answer_gone_and_write_nothing(server, path):
    base, data_dir = server
    # One account per path. The registry writer is idempotent per account: if
    # both paths read as the same account, the second write finds the row the
    # first one left, changes nothing, and passes.
    member = _signed_in(base, data_dir, _account_for(path))
    # Every ledger in the data dir, compared before and after THIS path's
    # requests (the dir is shared by both parametrized paths). Only the code
    # registry was compared before, so a retired handler writing any other
    # ledger passed (found in verification, 2026-09-27).
    before = _ledgers(data_dir)
    for headers in (member, {}):
        status, body, _h = _srv.request(base, path, headers=headers, timeout=15)
        assert status == 410, (path, headers, status, body)
        assert "still work" in json.loads(body)["detail"]
        head = _srv.request(base, path, method="HEAD", headers=headers, timeout=15)
        assert head[0] == 410
    after = _ledgers(data_dir)
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert changed == [], f"{path} wrote {changed}"


def test_each_retired_path_reads_as_its_own_account():
    """Control for the test above: two paths whose slugs collide would share
    an account again, and the second one's write would go unseen."""
    accounts = [_account_for(path) for path in RETIRED_GET]
    assert len(set(accounts)) == len(RETIRED_GET), accounts


def test_the_payout_request_answers_gone(server):
    base, _data_dir = server
    status, body, _h = _srv.request(base, "/api/me/affiliate/payout", "POST",
                                    b'{"method":"credits"}',
                                    {"Content-Type": "application/json"}, timeout=15)
    assert status == 410, (status, body)


def _retired_imports(source: str) -> list[int]:
    """Line numbers where `source` imports the retired module, in any shape:
    `import affiliate`, `import os, affiliate as a`, `from affiliate import x`,
    `from . import affiliate`, a constant handed to `import_module` or
    `__import__`, at module level or inside a function. Parsed, not grepped:
    the words in a comment or a string are not an import."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [alias.name for alias in node.names]
        elif (isinstance(node, ast.Call) and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and getattr(node.func, "attr", getattr(node.func, "id", ""))
              in ("import_module", "__import__")):
            names = [node.args[0].value]
        else:
            continue
        if any(RETIRED_MODULE in name.split(".") for name in names):
            lines.append(node.lineno)
    return sorted(lines)


def test_no_server_module_imports_the_retired_program():
    """The endpoints answering 410 is half of it. The module stays on disk, so
    one import is all it takes for new work to call it again."""
    scanned, offenders = [], []
    for module in sorted((REPO / "server").glob("*.py")):
        if module.name == f"{RETIRED_MODULE}.py":
            continue
        scanned.append(module.name)
        offenders += [f"server/{module.name}:{line}"
                      for line in _retired_imports(module.read_text(encoding="utf-8"))]
    # A glob that found nothing would make "no offenders" vacuous. These two
    # are the modules that imported it until 2026-09-27.
    assert {"app.py", "referrals.py"} <= set(scanned), scanned
    assert offenders == [], f"these still import the retired affiliate module: {offenders}"


@pytest.mark.parametrize("planted, lines", [
    ("import os\nimport affiliate\n", [2]),
    ("import os, affiliate as aff\n", [1]),
    ("from affiliate import code_for_email\n", [1]),
    ("from . import affiliate\n", [1]),
    ("def handler():\n    import affiliate\n    return affiliate.stats('a')\n", [2]),
    ("import importlib\nmod = importlib.import_module('affiliate')\n", [2]),
    ("mod = __import__('affiliate')\n", [1]),
    # Not imports of it: another module with a longer name, and the words in
    # a comment and in a string.
    ("import affiliate_report\n", []),
    ("# import affiliate\nNOTE = 'import affiliate'\n", []),
])
def test_the_import_scanner_sees_a_planted_import(planted, lines):
    """Control: the scanner that found no offender in server/ reports one
    when it is there, and only then."""
    assert _retired_imports(planted) == lines
