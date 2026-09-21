"""Every event the webhook acts on must be in every runbook's subscription list.

A webhook handler only runs for the events its endpoint is subscribed to, and
that list lives in the payment processor's dashboard, typed in by a person
following one of these runbooks. Found 2026-09-19: all five told the founder to
subscribe to `checkout.session.completed` (some added the subscription
lifecycle), and none listed `charge.refunded` or `charge.dispute.created`. The
code that revokes a refunded buyer's credits was correct, tested, and
unreachable for anyone who followed the instructions.

The handled set is READ FROM THE HANDLER, not kept here: a second hand-kept
list would drift exactly the way the runbooks did.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

RUNBOOKS = [
    "deploy/CHECKOUT_GO_LIVE.md",
    "deploy/FLY_PREFLIGHT.md",
    "deploy/STRIPE_WEBHOOK_DEV.md",
    "scripts/deploy_orphograph.sh",
    "scripts/stripe_listen.sh",
]

_EVENT = re.compile(r"^[a-z_]+(\.[a-z_]+){1,3}$")


def handled_event_types(source: str) -> set[str]:
    """Event-type strings compared against `event_type` inside handle_event."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "handle_event")
    found: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        if not any(isinstance(s, ast.Name) and s.id == "event_type" for s in sides):
            continue
        for side in sides:
            for const in ast.walk(side):
                if (isinstance(const, ast.Constant) and isinstance(const.value, str)
                        and _EVENT.match(const.value)):
                    found.add(const.value)
    return found


def _handled() -> set[str]:
    return handled_event_types((ROOT / "server" / "stripe_webhook.py").read_text())


def test_the_scan_reads_the_real_handler():
    """Guards the guard: a refactor that hides the comparisons from the scan
    would otherwise make every runbook trivially complete."""
    handled = _handled()
    assert {"checkout.session.completed", "charge.refunded", "charge.dispute.created",
            "customer.subscription.deleted",
            "checkout.session.async_payment_failed"} <= handled, handled
    assert len(handled) >= 8, handled


@pytest.mark.parametrize("runbook", RUNBOOKS)
def test_runbook_lists_every_handled_event(runbook):
    text = (ROOT / runbook).read_text()
    # `customer.subscription.{created,updated,deleted}` is a legitimate shorthand.
    text = re.sub(r"customer\.subscription\.\{([a-z,]+)\}",
                  lambda m: " ".join(f"customer.subscription.{x}" for x in m.group(1).split(",")),
                  text)
    missing = sorted(e for e in _handled() if e not in text)
    assert missing == [], (
        f"{runbook} never tells the founder to subscribe to {missing}; "
        "the code that handles them cannot run")


def test_control_a_runbook_missing_an_event_is_caught():
    source = '''
def handle_event(payload):
    if event_type in {"charge.refunded", "charge.dispute.created"}:
        pass
    if event_type != "checkout.session.completed":
        pass
    if other == "not.an.event.we.compare":
        pass
'''
    assert handled_event_types(source) == {
        "charge.refunded", "charge.dispute.created", "checkout.session.completed"}
