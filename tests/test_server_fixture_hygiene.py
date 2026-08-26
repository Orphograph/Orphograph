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
starts the server must import _srv, whose one implementation reserves ports
together, uses one tuned deadline, and puts the server's last words into the
failure message.

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
    "test_access_hub.py",
    "test_admin_toggles.py",
    "test_affiliate_redirect.py",
    "test_agent_discovery.py",
    "test_anchoring_disabled.py",
    "test_attacks.py",
    "test_badge.py",
    "test_biweekly_safety_audit.py",
    "test_blog_static_css.py",
    "test_buy_btc_funnel.py",
    "test_capability_copy.py",
    "test_card_notify_capture.py",
    "test_compliance_scan.py",
    "test_css_cache_discipline.py",
    "test_docs_hub.py",
    "test_error_pages.py",
    "test_event_ip_source.py",
    "test_folder_anchor.py",
    "test_founder_funnel_endpoint.py",
    "test_founder_token_bruteforce.py",
    "test_funnel_event_whitelist.py",
    "test_harness_cleanup.py",
    "test_head_method.py",
    "test_l402_single_use.py",
    "test_lightning_l402.py",
    "test_lineage_endpoint.py",
    "test_lp_pageview_beacon.py",
    "test_money_surface_hardening_2026_05_29.py",
    "test_no_qr_on_site.py",
    "test_notify_and_folder_delivery.py",
    "test_nowpayments_create.py",
    "test_order_status.py",
    "test_pack_access.py",
    "test_pay_btc_same_origin.py",
    "test_post_content_type_gate.py",
    "test_private_fails_closed.py",
    "test_private_receipt_not_discoverable.py",
    "test_private_receipts.py",
    "test_receipt_ownership_agrees.py",
    "test_recover_crypto.py",
    "test_renewal.py",
    "test_scroll_depth.py",
    "test_security_txt.py",
    "test_snark_receipt.py",
    "test_stripe_checkout.py",
    "test_subscription_inheritance.py",
    "test_ui.py",
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
        "tests/_srv.py instead — it reserves ports together (no reuse race), "
        "uses one tuned startup deadline, and reports the server's OUTPUT when "
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
    assert len(spinning) >= 50, (
        f"only {len(spinning)} modules detected as starting a server — the "
        "detector is broken, not the suite clean"
    )
