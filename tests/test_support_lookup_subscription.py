"""The founder support lookup must see a subscription the ledger says is active.

Found 2026-08-26. `server/subscriptions.py` moved to an append-only
`subscriptions.jsonl` event ledger, but `server/support_tools.py` still read a
`subscriptions.json` snapshot that nothing writes any more. Its guard was
`if subs_path.exists():` -- so the read silently fell through and EVERY paying
customer came back as `subscription: None` from the founder support endpoint
(`server/app.py` -> `support_tools.lookup_customer`). No error, no log: a
missing file and "this person has no subscription" are indistinguishable at
that call site, which is why it survived.

Driven as a subprocess because both modules resolve DATA_DIR at import time
from the environment; patching after import would test a fiction.
"""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DRIVER = textwrap.dedent("""
    import json, sys, time
    sys.path.insert(0, {server!r})
    import subscriptions, support_tools

    EMAIL = "paying.customer@example.com"
    subscriptions.record_customer_email("cus_TEST", EMAIL)
    subscriptions.record_subscription_event(
        stripe_customer="cus_TEST",
        status="active",
        current_period_end=time.time() + 86400,
        sub_id="sub_TEST",
        event_type="customer.subscription.created",
    )
    profile = support_tools.lookup_customer(EMAIL)
    print(json.dumps({{
        "ledger_says_active": subscriptions.is_active(EMAIL),
        "subscription": (profile or {{}}).get("subscription"),
    }}))
""")


def _run(tmp_path: Path) -> dict:
    out = subprocess.run(
        [sys.executable, "-c", DRIVER.format(server=str(ROOT / "server"))],
        cwd=ROOT, env={"ORPHO_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, f"driver failed:\n{out.stdout}\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_active_subscriber_is_visible_to_support_lookup(tmp_path):
    result = _run(tmp_path)

    # Guard the guard: if the writer did not record an active subscription,
    # the assertion below would pass for the wrong reason.
    assert result["ledger_says_active"] is True, (
        "the subscription writer did not record an active sub; this test "
        "cannot discriminate and proves nothing"
    )

    sub = result["subscription"]
    assert sub is not None, (
        "support lookup reports subscription=None for a customer the "
        "subscription ledger reports as ACTIVE -- the reader and writer "
        "disagree about the on-disk format again"
    )
    assert sub.get("status") == "active"
    assert sub.get("stripe_sub") == "sub_TEST"


def test_no_subscription_still_reads_as_none(tmp_path):
    """The opposite polarity: absence must still be reported as absence."""
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import json, sys
            sys.path.insert(0, {str(ROOT / 'server')!r})
            import support_tools
            p = support_tools.lookup_customer("nobody@example.com")
            print(json.dumps({{"subscription": (p or {{}}).get("subscription")}}))
        """)],
        cwd=ROOT, env={"ORPHO_DATA_DIR": str(tmp_path), "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1])["subscription"] is None
