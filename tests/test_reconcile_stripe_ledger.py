"""test_reconcile_stripe_ledger.py — unit tests for the Stripe/ledger reconciler.

All Stripe calls are mocked via unittest.mock.patch on urllib.request.urlopen
inside the reconcile module — we NEVER hit the real Stripe API.

Run with:  pytest -p no:anchorpy tests/test_reconcile_stripe_ledger.py
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "reconcile_stripe_ledger.py"


def _load_module():
    """Load scripts/reconcile_stripe_ledger.py as an importable module."""
    spec = importlib.util.spec_from_file_location(
        "reconcile_stripe_ledger", str(SCRIPT_PATH),
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_stripe_ledger"] = mod
    spec.loader.exec_module(mod)
    return mod


reconcile = _load_module()


def _mock_resp(payload: dict) -> MagicMock:
    """Build a context-manager mock that mimics urlopen's response."""
    body = json.dumps(payload).encode("utf-8")
    m = MagicMock()
    m.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=body)))
    m.__exit__ = MagicMock(return_value=False)
    return m


def _make_event(event_type: str, session_id: str, event_id: str = "evt_x") -> dict:
    if event_type == "checkout.session.completed":
        return {
            "id": event_id,
            "type": event_type,
            "data": {"object": {"id": session_id}},
        }
    # charge.refunded / charge.dispute.created — put session id on metadata
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "metadata": {"checkout_session_id": session_id},
            }
        },
    }


def _build_responses(events_by_type: dict[str, list[dict]]) -> list[MagicMock]:
    """One non-paginated response per event-type fetched, in fetch_events order."""
    responses: list[MagicMock] = []
    for et in reconcile.EVENT_TYPES:
        responses.append(_mock_resp({
            "data": events_by_type.get(et, []),
            "has_more": False,
        }))
    return responses


def _write_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ----------------------------------------------------------- test 1: clean


def test_clean_state_exit_zero(tmp_path: Path) -> None:
    ledger = tmp_path / "credit_ledger.jsonl"
    report_dir = tmp_path / "out"

    _write_ledger(ledger, [
        {"claim_code": "pk_a", "credits_delta": 10, "source": "stripe:cs_1"},
        {"claim_code": "pk_b", "credits_delta": 10, "source": "stripe:cs_2"},
        {"claim_code": "pk_c", "credits_delta": 10, "source": "stripe:cs_3"},
    ])

    responses = _build_responses({
        "checkout.session.completed": [
            _make_event("checkout.session.completed", "cs_1", "evt_1"),
            _make_event("checkout.session.completed", "cs_2", "evt_2"),
            _make_event("checkout.session.completed", "cs_3", "evt_3"),
        ],
    })

    with patch.object(reconcile.urllib.request, "urlopen", side_effect=responses):
        code, report_path = reconcile.run(
            secret_key="sk_test_dummy",
            ledger_path=ledger,
            report_dir=report_dir,
        )

    assert code == 0
    text = report_path.read_text()
    assert "OK — no drift" in text
    assert "LOST credits (paid, no grant): 0" in text
    assert "GHOST credits (granted, no payment): 0" in text
    assert "LEAK credits (refund/dispute, no revoke): 0" in text


# ----------------------------------------------------------- test 2: lost


def test_lost_credit_flagged(tmp_path: Path) -> None:
    ledger = tmp_path / "credit_ledger.jsonl"
    report_dir = tmp_path / "out"

    # Empty ledger.
    _write_ledger(ledger, [])

    responses = _build_responses({
        "checkout.session.completed": [
            _make_event("checkout.session.completed", "cs_LOST", "evt_lost"),
        ],
    })

    with patch.object(reconcile.urllib.request, "urlopen", side_effect=responses):
        code, report_path = reconcile.run(
            secret_key="sk_test_dummy",
            ledger_path=ledger,
            report_dir=report_dir,
        )

    assert code == 1
    text = report_path.read_text()
    assert "DRIFT DETECTED" in text
    assert "LOST credits (paid, no grant): 1" in text
    assert "cs_LOST" in text


# ----------------------------------------------------------- test 3: ghost


def test_ghost_credit_flagged(tmp_path: Path) -> None:
    ledger = tmp_path / "credit_ledger.jsonl"
    report_dir = tmp_path / "out"

    # Ledger claims a stripe session that Stripe never returns.
    _write_ledger(ledger, [
        {"claim_code": "pk_g", "credits_delta": 10, "source": "stripe:cs_GHOST"},
    ])

    responses = _build_responses({})  # Stripe returns zero events for all types.

    with patch.object(reconcile.urllib.request, "urlopen", side_effect=responses):
        code, report_path = reconcile.run(
            secret_key="sk_test_dummy",
            ledger_path=ledger,
            report_dir=report_dir,
        )

    assert code == 1
    text = report_path.read_text()
    assert "DRIFT DETECTED" in text
    assert "GHOST credits (granted, no payment): 1" in text
    assert "stripe:cs_GHOST" in text


# ----------------------------------------------------------- test 4: leak


def test_refund_without_revocation_flagged(tmp_path: Path) -> None:
    ledger = tmp_path / "credit_ledger.jsonl"
    report_dir = tmp_path / "out"

    # 1 grant entry, no revoke entry.
    _write_ledger(ledger, [
        {"claim_code": "pk_r", "credits_delta": 10, "source": "stripe:cs_REFUND"},
    ])

    responses = _build_responses({
        "checkout.session.completed": [
            _make_event("checkout.session.completed", "cs_REFUND", "evt_pay"),
        ],
        "charge.refunded": [
            _make_event("charge.refunded", "cs_REFUND", "evt_ref"),
        ],
    })

    with patch.object(reconcile.urllib.request, "urlopen", side_effect=responses):
        code, report_path = reconcile.run(
            secret_key="sk_test_dummy",
            ledger_path=ledger,
            report_dir=report_dir,
        )

    assert code == 1
    text = report_path.read_text()
    assert "DRIFT DETECTED" in text
    assert "LEAK credits (refund/dispute, no revoke): 1" in text
    assert "stripe-refund:cs_REFUND" in text
    assert "evt_ref" in text


# ----------------------------------------------------------- test 5: leak resolved


def test_refund_with_revocation_clean(tmp_path: Path) -> None:
    """Bonus sanity: when a matching revoke row exists, the leak clears."""
    ledger = tmp_path / "credit_ledger.jsonl"
    report_dir = tmp_path / "out"

    _write_ledger(ledger, [
        {"claim_code": "pk_r", "credits_delta": 10, "source": "stripe:cs_REFUND"},
        {"claim_code": "pk_r", "credits_delta": -10, "source": "stripe-refund:cs_REFUND"},
    ])

    responses = _build_responses({
        "checkout.session.completed": [
            _make_event("checkout.session.completed", "cs_REFUND", "evt_pay"),
        ],
        "charge.refunded": [
            _make_event("charge.refunded", "cs_REFUND", "evt_ref"),
        ],
    })

    with patch.object(reconcile.urllib.request, "urlopen", side_effect=responses):
        code, _ = reconcile.run(
            secret_key="sk_test_dummy",
            ledger_path=ledger,
            report_dir=report_dir,
        )

    assert code == 0
