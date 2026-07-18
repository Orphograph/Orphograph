"""Tests for attest_core — the canonical Attestation Record (5-tuple)."""
from __future__ import annotations

import attest_core as ac


def _live_engine_record() -> dict:
    # Shape mirrors engine.verify_receipt() output (server/engine.py:309-320).
    return {
        "receipt_id": "abc123",
        "created_at": "2026-06-22T00:00:00Z",
        "hash_hex": "a" * 64,
        "sha512_hex": "b" * 128,
        "private": False,
        "owner_id": None,
        "calendars_ok": 4,
        "calendars_total": 5,
        "status": "confirmed",
        "btc_pinned_at": "2026-06-22T01:00:00Z",
    }


def test_live_engine_receipt_conforms_to_canonical_record():
    rec = ac.from_engine_record(_live_engine_record())
    assert ac.validate(rec) == [], "a real engine receipt must be a valid AttestationRecord"
    assert rec.claimed_state == "existed_at_or_before_anchor"
    assert rec.subject.digest_sha256 == "a" * 64
    assert rec.time_anchor.protocol == "opentimestamps" and rec.time_anchor.chain == "bitcoin"


def test_acceptance_seam_is_empty_in_the_open_module():
    # The whole moat thesis: the open module NEVER asserts acceptance/trust.
    rec = ac.from_engine_record(_live_engine_record())
    assert rec.acceptance.issuer_trusted is None
    assert rec.acceptance.revoked is None
    assert rec.acceptance.disputed is None
    assert rec.acceptance.issuer_profile is None


def test_predictive_claim_is_rejected_by_schema():
    # Keeps observer-only issuers honest BY SCHEMA: no forecast/edge claim validates.
    rec = ac.AttestationRecord(
        receipt_id="x",
        subject=ac.Subject(digest_sha256="c" * 64, kind="json-state"),
        claimed_state="predicts_price_up",  # forbidden
        time_anchor=ac.TimeAnchor(),
    )
    errs = ac.validate(rec)
    assert any("claimed_state" in e for e in errs)


def test_state_snapshot_claim_is_valid_for_observer_issuer_class():
    # An observer-only state issuer slots in: "system was in state R at T".
    rec = ac.AttestationRecord(
        receipt_id="state-1",
        subject=ac.Subject(digest_sha256="d" * 64, kind="json-state"),
        claimed_state="state_snapshot_at_anchor",
        time_anchor=ac.TimeAnchor(calendars_ok=5, calendars_total=5, status="confirmed"),
        issuer_identity="did:key:zObserver",
    )
    assert ac.validate(rec) == []


def test_bad_digest_and_calendar_math_rejected():
    rec = ac.AttestationRecord(
        receipt_id="y",
        subject=ac.Subject(digest_sha256="not-hex"),
        claimed_state="existed_at_or_before_anchor",
        time_anchor=ac.TimeAnchor(calendars_ok=6, calendars_total=5),
    )
    errs = ac.validate(rec)
    assert any("digest_sha256" in e for e in errs)
    assert any("calendars_ok exceeds" in e for e in errs)


def test_roundtrip_to_dict():
    rec = ac.from_engine_record(_live_engine_record())
    d = rec.to_dict()
    assert d["schema"] == ac.SCHEMA_VERSION
    assert d["subject"]["digest_sha256"] == "a" * 64
    assert d["acceptance"]["issuer_trusted"] is None
