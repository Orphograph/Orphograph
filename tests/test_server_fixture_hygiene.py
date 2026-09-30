"""test_server_fixture_hygiene.py

New tests that spin a server must use tests/_srv.py (guard added 2026-08-25).

54 test modules stand up a server — some as a subprocess, some by
serving app.Handler inside the test interpreter — each with its own hand-copied
fixture. Three separate defects on 2026-08-25 traced to that
duplication:

  * `_free_port()` binds :0 and closes, so calling it twice can return the SAME
    port and the second server fails to bind. 21 files carry a copy.
  * Startup deadlines of 10s and 15s time out under full-suite load, which
    reads as a product failure rather than a slow machine.
  * `stderr=subprocess.DEVNULL` throws the server's own error away. A REAL
    crash-on-boot race in _seed_sample_receipt (FileExistsError when two
    processes share ORPHO_DATA_DIR) presented only as "server did not start",
    and was diagnosed only by capturing the output by hand.

This does not rewrite the 32. It stops the count growing: any NEW module that
starts the server must import _srv, whose one implementation lets the server
bind its own port, uses one tuned deadline, and puts the server's last words
into the failure message.

LEGACY is frozen deliberately. It may SHRINK as modules migrate — that is
enforced below, so a migration cannot be silently reverted — but a new name
cannot be added to it without a human deciding to type it here, which is the
point.
"""
from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

LEGACY = frozenset({
    "test_ab_home.py",
    "test_admin_toggles.py",
    "test_affiliate_redirect.py",
    "test_agent_discovery.py",
    "test_anchoring_disabled.py",
    "test_badge.py",
    "test_biweekly_safety_audit.py",
    "test_capability_copy.py",
    "test_card_notify_capture.py",
    "test_compliance_scan.py",
    "test_css_cache_discipline.py",
    "test_error_pages.py",
    "test_folder_anchor.py",
    "test_founder_funnel_endpoint.py",
    "test_founder_token_bruteforce.py",
    "test_harness_cleanup.py",
    "test_head_method.py",
    "test_l402_single_use.py",
    "test_lightning_l402.py",
    "test_lineage_endpoint.py",
    "test_no_qr_on_site.py",
    "test_notify_and_folder_delivery.py",
    "test_post_content_type_gate.py",
    "test_private_fails_closed.py",
    "test_private_receipt_not_discoverable.py",
    "test_private_receipts.py",
    "test_receipt_ownership_agrees.py",
    "test_renewal.py",
    "test_security_txt.py",
    "test_snark_receipt.py",
    "test_stripe_checkout.py",
    "test_subscription_inheritance.py",
    "test_vault_api_key.py",
    "test_vault_filters.py",
})


def _code_only(src: str) -> str:
    """Source with docstrings and comments stripped.

    Added 2026-08-26. The detector below used to match raw file text, so a
    module that merely NAMED server/app.py in a docstring -- explaining which
    endpoint its subject is reachable from -- was reported as spinning a
    server. That is the scanner-reads-its-own-prose defect this repo has now
    shipped six times; tests/test_no_phantom_env_knobs.py carries the same
    remedy. A guard that fires on prose trains people to add allowlist entries
    for tests that were never offenders, which is how a shrink-only list grows.
    """
    tree = ast.parse(src)
    _strip_docstrings(tree)
    return ast.unparse(tree)


def _strip_docstrings(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]


def _spins_a_server(text: str) -> bool:
    """A module starts a server if it launches app.py OR serves app.Handler
    in-process. The in-process form is included because it is WORSE, not
    exempt: test_c2pa_roundtrip.py ran that way, popped 32 modules out of
    sys.modules to do it, and failed under full-suite load three times before
    being moved to a subprocess.

    Judged on CODE only -- naming app.py in prose is not launching it."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Unparseable: fall back to raw text. A scan that cannot read the file
        # reports "maybe", never "clean".
        return ("server/app.py" in text or '"app.py"' in text
                or "app.Handler" in text)

    _strip_docstrings(tree)
    for node in ast.walk(tree):
        # A string CONSTANT naming app.py -- quote style is irrelevant, which
        # is the point: an earlier version of this matched the literal text
        # '"app.py"' and silently stopped seeing 24 real offenders the moment
        # the source was normalised to single quotes.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v == "app.py" or v.endswith("/app.py") or "server/app.py" in v:
                return True
        # In-process: app.Handler
        if (isinstance(node, ast.Attribute) and node.attr == "Handler"
                and isinstance(node.value, ast.Name) and node.value.id == "app"):
            return True
    return False


def _modules():
    for p in sorted(TESTS.glob("test_*.py")):
        yield p, p.read_text(encoding="utf-8", errors="replace")


def test_new_server_spinning_modules_use_the_shared_helper() -> None:
    """THE GUARD. A module that starts the server and is not in LEGACY must
    import _srv, or it has just re-created the port race, the short deadline,
    and the swallowed stderr."""
    offenders = [
        p.name for p, t in _modules()
        if _spins_a_server(t) and "import _srv" not in t and p.name not in LEGACY
        and p.name != "test_server_fixture_hygiene.py"
    ]
    assert not offenders, (
        "These modules start server/app.py with their own fixture. Use "
        "tests/_srv.py instead — the server binds its own port (no race for "
        "it), one tuned startup deadline, and the server's OUTPUT when "
        "startup fails instead of a bare 'did not start':\n  "
        + "\n  ".join(offenders)
    )


def test_legacy_list_only_shrinks() -> None:
    """A name in LEGACY that no longer needs to be there means the module was
    migrated — good, and the list must be trimmed so the debt is visible and
    honest. A name that vanished from disk must also go."""
    stale = []
    on_disk = {p.name: t for p, t in _modules()}
    for name in sorted(LEGACY):
        text = on_disk.get(name)
        if text is None:
            stale.append(f"{name} (module no longer exists)")
        elif "import _srv" in text:
            stale.append(f"{name} (migrated to _srv — remove from LEGACY)")
        elif not _spins_a_server(text):
            stale.append(f"{name} (no longer starts a server — remove from LEGACY)")
    assert not stale, "LEGACY is out of date:\n  " + "\n  ".join(stale)


def test_the_helper_never_discards_server_output() -> None:
    """The expensive lesson, pinned. _srv must not send the server's stderr to
    DEVNULL: that is what turned a real FileExistsError crash-on-boot into an
    opaque 'server did not start'."""
    srv_raw = (TESTS / "_srv.py").read_text(encoding="utf-8")
    # Check CODE, not prose. _srv.py's docstring quotes the exact hazardous
    # line to explain why it is not used, so a naive substring search on the
    # whole file flags its own documentation. Strip docstrings and comments.
    tree = ast.parse(srv_raw)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    srv = ast.unparse(tree)
    assert "stderr=subprocess.DEVNULL" not in srv, (
        "_srv.py discards server output — a crash on boot will present as a "
        "timeout and cost hours to diagnose"
    )
    assert "stderr=subprocess.STDOUT" in srv
    assert "--- server output ---" in srv_raw, (
        "_srv.py no longer surfaces the server log on startup failure"
    )


def test_the_scan_can_actually_see_a_server_fixture() -> None:
    """NEGATIVE CONTROL. If _spins_a_server() ever stops matching, every
    assertion above passes over an empty set and reports clean forever."""
    spinning = [p.name for p, t in _modules() if _spins_a_server(t)]
    # Floor was 54 while the detector matched raw text. Four LEGACY entries
    # turned out to mention server/app.py only in a docstring or comment and
    # never started anything (verified: zero Popen/socket/HTTPServer between
    # them), so they were false entries the textual detector had manufactured.
    # The floor moves with the corpus; it exists to catch the detector going
    # blind, not to freeze a number.
    # 2026-09-28: 54 -> 43. Fourteen modules moved to _srv, and a module that
    # starts its server through _srv no longer names app.py, so eleven of them
    # left this count. Every migration lowers it; the planted sources below
    # do not move.
    assert len(spinning) >= 40, (
        f"only {len(spinning)} modules detected as starting a server — the "
        "detector is broken, not the suite clean"
    )
    assert _spins_a_server(
        "proc = subprocess.Popen([sys.executable, str(ROOT / 'server' / 'app.py')])\n")
    assert _spins_a_server(
        "import app\nhttpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)\n")
    assert not _spins_a_server('"""Reachable from server/app.py."""\nimport os\n')


# --- request helpers (added 2026-09-18) ------------------------------------
# The same duplication, one layer up. Ten modules that already spun their server
# through _srv still carried a private urlopen/HTTPConnection wrapper, and the
# copies had drifted the way the fixtures did: one returned (0, "") on ANY
# exception, so its 234-request reflected-XSS sweep read a dead server, or a
# swept page that had started to 404, as "nothing reflected".

_OWN_CONNECTION_NAMES = frozenset({
    "urlopen", "urlretrieve", "build_opener", "OpenerDirector",
    "HTTPConnection", "HTTPSConnection", "create_connection"})
_OWN_CONNECTION_MODULES = frozenset({"requests", "httpx", "urllib3", "aiohttp"})


def _imports_srv(tree: ast.AST) -> bool:
    """`import _srv`, `import _srv as s`, `from _srv import x` — judged on the
    import statement, not on the text, so prose cannot opt a module in or out."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "_srv" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "_srv":
            return True
    return False


def _parse_code(text: str) -> ast.AST | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    _strip_docstrings(tree)
    return tree


def _opens_its_own_connection(text: str) -> list[str]:
    """Connection-opening names REFERENCED in code: called, aliased, imported
    under another name, or passed as a value (`pool.map(urlopen, urls)`)."""
    tree = _parse_code(text)
    if tree is None:
        # Unparseable: a scan that cannot read the file reports "maybe".
        return sorted(n for n in _OWN_CONNECTION_NAMES if n in text)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _OWN_CONNECTION_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in _OWN_CONNECTION_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.ImportFrom):
            found += [a.name for a in node.names if a.name in _OWN_CONNECTION_NAMES]
            if (node.module or "").split(".")[0] in _OWN_CONNECTION_MODULES:
                found.append(node.module)
        elif isinstance(node, ast.Import):
            found += [a.name for a in node.names
                      if a.name.split(".")[0] in _OWN_CONNECTION_MODULES]
    return found


def _on_the_shared_helper(text: str) -> bool:
    tree = _parse_code(text)
    return "_srv" in text if tree is None else _imports_srv(tree)


def test_modules_on_the_shared_helper_do_not_open_their_own_connections() -> None:
    """THE GUARD. A module that imports _srv talks to its server through
    _srv.request / get_json / post_json / anchor: one place that never follows
    a redirect, keeps a header sent twice, and reports a dead server with the
    server's own last words instead of an empty body."""
    offenders = {
        p.name: sorted(set(calls)) for p, t in _modules()
        if p.name != "test_server_fixture_hygiene.py" and _on_the_shared_helper(t)
        and (calls := _opens_its_own_connection(t))
    }
    assert not offenders, (
        "These modules import _srv and still open their own connections. Use "
        f"_srv.request / get_json / post_json / anchor:\n  {offenders}")


def test_the_connection_scan_can_actually_see_a_private_helper() -> None:
    """NEGATIVE CONTROL. Every spelling below is one a guard matching only
    `urlopen(...)` calls would have missed."""
    sees = _opens_its_own_connection
    assert sees("import urllib.request\ndef g(u):\n    return urllib.request.urlopen(u)\n")
    assert sees("from http.client import HTTPConnection as Conn\nc = Conn('h')\n")
    assert sees("import urllib.request\nfetch = urllib.request.urlopen\n")
    assert sees("import urllib.request as r\npool.map(r.urlopen, urls)\n")
    assert sees("import socket\ns = socket.create_connection(('h', 1))\n")
    assert sees("import requests\n")
    assert sees("def broken(:\n    urlopen(x)\n"), "unparseable must read as maybe"
    assert sees('def f():\n    """urlopen(x) in prose must not count."""\n') == []

    assert _on_the_shared_helper("from _srv import request\n")
    assert _on_the_shared_helper("import _srv as s\n")
    assert not _on_the_shared_helper('"""see: import _srv"""\nimport os\n')

    # The corpus, while it still has any: every LEGACY fixture carries a private
    # helper today, so a detector gone blind shows up as a corpus with none.
    # Not a fixed floor — LEGACY is meant to shrink to nothing.
    seen = [p.name for p, t in _modules() if _opens_its_own_connection(t)]
    assert seen or not LEGACY, "no module detected opening a connection — detector is blind"


# --- pre-picked ports (added 2026-09-28) -----------------------------------
# A fixture that binds :0, closes the socket and hands the number to a server
# leaves a window in which another process takes the port. It failed the
# deploy of #275 on 2026-09-27. _srv.spin() closed it for its callers: the
# server is started with PORT=0 and the harness reads the port that server
# bound. This keeps every other fixture from picking one again.
#
# Picking a port is still right for one thing: a port that must be CLOSED, to
# point a proxy at. So the scan follows the picked number to where it goes and
# only objects when that is a server.

# getsockname is the picking itself, done inline with no helper to name.
_PORT_PICKERS = frozenset({"_free_port", "free_port", "reserve_ports", "getsockname"})


def _called(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _picks_a_port(fn: ast.AST) -> bool:
    """A function that binds a socket and returns a value read from
    getsockname() picks a port, whatever it is called."""
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    returns = any(isinstance(n, ast.Return) and n.value is not None
                  for n in ast.walk(fn))
    return returns and {"bind", "getsockname"} <= attrs


def _pickers_defined_in(text: str) -> set[str]:
    tree = _parse_code(text)
    if tree is None:
        return set()
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _picks_a_port(n)}


def _picked_ports_handed_to_a_server(
        text: str, shared: frozenset[str] = frozenset()) -> list[str]:
    """Where this source gives a picked port to a server, as "line N: what".

    `shared` names the pickers other modules define, since a module that
    imports one defines nothing to judge it by.

    A name is followed through assignments, loops and calls to functions of
    the same module, by NAME across the whole module: a name that holds a
    picked port anywhere counts as holding one everywhere. That can only
    over-report, and an over-report is loud."""
    tree = _parse_code(text)
    if tree is None:
        # Unparseable: a scan that cannot read the file reports "maybe".
        maybe = any(p in text for p in _PORT_PICKERS | shared)
        return ["unparseable, and it picks a port"] if maybe else []

    defs = {n.name: n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    pickers = _PORT_PICKERS | shared | _pickers_defined_in(text)
    held: set[str] = set()

    def picked(expr: ast.AST) -> bool:
        return any(
            (isinstance(n, ast.Call) and _called(n) in pickers)
            or (isinstance(n, ast.Name) and n.id in held)
            for n in ast.walk(expr))

    while True:
        before = len(held)
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and picked(n.value):
                held.update(*(_names(t) for t in n.targets))
            elif (isinstance(n, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr))
                  and n.value is not None and picked(n.value)):
                held |= _names(n.target)
            elif isinstance(n, (ast.For, ast.comprehension)) and picked(n.iter):
                held |= _names(n.target)
            elif isinstance(n, ast.Call) and _called(n) in defs:
                # The callee's parameter now holds what the caller passed.
                spec = defs[_called(n)].args
                params = [a.arg for a in spec.posonlyargs + spec.args]
                held.update(p for p, a in zip(params, n.args) if picked(a))
                held.update(k.arg for k in n.keywords if k.arg and picked(k.value))
        if len(held) == before:
            break

    found = []
    for n in ast.walk(tree):
        line = getattr(n, "lineno", 0)
        if isinstance(n, ast.Dict):
            for key, value in zip(n.keys, n.values):
                if (isinstance(key, ast.Constant) and key.value == "PORT"
                        and picked(value)):
                    found.append(f"line {value.lineno}: PORT is a picked port")
        elif isinstance(n, ast.Assign) and picked(n.value):
            for t in n.targets:
                if (isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "PORT"):
                    found.append(f"line {line}: PORT is a picked port")
        elif isinstance(n, ast.Call):
            if any(k.arg == "PORT" and picked(k.value) for k in n.keywords):
                found.append(f"line {line}: PORT is a picked port")
            # setenv("PORT", n), setdefault("PORT", n), putenv("PORT", n)
            if (len(n.args) >= 2 and isinstance(n.args[0], ast.Constant)
                    and n.args[0].value == "PORT" and picked(n.args[1])):
                found.append(f"line {line}: PORT is a picked port")
            # In-process: HTTPServer((host, n), ...) or sock.bind((host, n)).
            name = _called(n)
            if (name.endswith("Server") or name == "bind") and n.args and picked(n.args[0]):
                found.append(f"line {line}: {name}() binds a picked port")
    return found


def _port_offenders(sources: dict[str, str]) -> dict[str, list[str]]:
    """{module: where it hands a picked port to a server}, over a set of
    modules given as {name: source}. A picker defined in any of them is a
    picker in all of them."""
    shared = frozenset().union(*(_pickers_defined_in(t) for t in sources.values()))
    return {name: found for name, text in sorted(sources.items())
            if (found := _picked_ports_handed_to_a_server(text, shared))}


def test_no_fixture_hands_a_picked_port_to_a_server() -> None:
    """THE GUARD. Every module under tests/, helpers included: a server gets
    PORT=0 (or binds port 0 in-process) and the fixture reads the port the
    server got."""
    offenders = _port_offenders({
        str(p.relative_to(TESTS)): p.read_text(encoding="utf-8", errors="replace")
        for p in TESTS.rglob("*.py")})
    assert not offenders, (
        "These modules pick a port and then hand it to a server, which loses "
        "the port to another process now and then. Start the server through "
        "_srv.server_processes (it binds port 0 and reports the port it got), "
        "or bind port 0 in-process and read server_address:\n  "
        + "\n  ".join(f"{name}: {found}" for name, found in offenders.items()))


_PICK_AND_LAUNCH = '''
import os, socket, subprocess, sys
def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
def server():
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1"}
    return subprocess.Popen([sys.executable, "server/app.py"], env=env)
'''


def test_the_port_scan_can_actually_see_a_picked_port() -> None:
    """NEGATIVE CONTROL, on planted sources. The first is the fixture fourteen
    modules carried; the rest are the same defect spelled another way."""
    sees = _picked_ports_handed_to_a_server
    assert sees(_PICK_AND_LAUNCH) == ["line 11: PORT is a picked port"]
    # Under another name: judged by what the function does.
    assert sees(_PICK_AND_LAUNCH.replace("_free_port", "_any_port"))
    # With no helper at all.
    assert sees("s = socket.socket()\ns.bind(('127.0.0.1', 0))\n"
                "port = s.getsockname()[1]\ns.close()\n"
                "subprocess.Popen(cmd, env={**os.environ, 'PORT': str(port)})\n")
    # Through a helper's parameter, and through a second name.
    assert sees("def _env(number):\n    return {'PORT': str(number)}\n"
                "chosen = _srv.reserve_ports(1)[0]\nalso = chosen\n"
                "subprocess.Popen(cmd, env=_env(also))\n")
    assert sees("env = dict(os.environ)\nenv['PORT'] = f'{_free_port()}'\n")
    assert sees("for port in _srv.reserve_ports(2):\n"
                "    _srv.spin(d, PORT=str(port))\n")
    assert sees("monkeypatch.setenv('PORT', str(_free_port()))\n")
    assert sees("port = _free_port()\n"
                "httpd = ThreadingHTTPServer(('127.0.0.1', port), app.Handler)\n"
                ) == ["line 2: ThreadingHTTPServer() binds a picked port"]
    assert sees("def broken(:\n    port = _free_port()\n"), "unparseable must read as maybe"

    # A picker kept in a shared helper, under a name no list here knows: the
    # module that calls it defines nothing, so it is judged with the others.
    shared = {"_ports.py": _PICK_AND_LAUNCH.replace("_free_port", "grab")
                                           .split("def server")[0],
              "test_x.py": "from _ports import grab\n"
                           "env = {**os.environ, 'PORT': str(grab())}\n"
                           "subprocess.Popen(cmd, env=env)\n"}
    assert _port_offenders(shared) == {"test_x.py": ["line 2: PORT is a picked port"]}

    # What must stay allowed: the server picks, or the picked port is one
    # nothing listens on.
    assert sees("env = {'PORT': '0'}\nsubprocess.Popen(cmd, env=env)\n") == []
    assert sees("httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)\n"
                "port = httpd.server_address[1]\n"
                "base = f'http://127.0.0.1:{port}'\n") == []
    assert sees("closed = _srv.reserve_ports(1)[0]\n"
                "env = {'HTTPS_PROXY': f'http://127.0.0.1:{closed}', 'PORT': '0'}\n"
                "subprocess.Popen(cmd, env=env)\n") == []
    assert sees('def f():\n    """env = {"PORT": str(_free_port())} in prose."""\n') == []

    # The corpus: _srv.reserve_ports is a picker by what it does, not only by
    # its name, so the detector that reads function bodies is seeing them.
    assert _pickers_defined_in(
        (TESTS / "_srv.py").read_text(encoding="utf-8")) == {"reserve_ports"}
