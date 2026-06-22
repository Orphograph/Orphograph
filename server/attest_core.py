"""attest_core.py — the canonical Attestation Record.

The horizontal primitive behind the "$1B path" thesis (2026-06-22 panel): every
vertical, seen and unseen, reduces to ONE 5-tuple. This module is the single,
dependency-free (stdlib-only) definition of that record so this product, a sibling
observer-only state issuer, and any future vertical import ONE schema instead of
duplicating the anchoring shape across repos. It deliberately holds NO secrets and NO closed logic — the
anchoring primitive is free/open (OpenTimestamps + Bitcoin); the value/moat lives
in the separate, non-MIT acceptance layer (the `accepted-state-network` repo).

The 5-tuple invariant (the test of a true horizontal primitive — adding a vertical
must be a config + adapter, never a new engine):

    subject         — WHAT is attested: a content digest (+ optional path / kind).
    claimed_state   — what is asserted of the subject ("existed", "state-snapshot").
                      NEVER a prediction (keeps observer-only issuers honest by schema).
    time_anchor     — the independent time proof (OpenTimestamps -> Bitcoin).
    issuer_identity — WHO attests (a did:key, or None). Signing is open/free.
    acceptance      — the OPEN-CORE SEAM: who TRUSTS/ACCEPTS this receipt
                      (issuer profile, revocation, dispute). This is the ONLY
                      non-commoditized element — acceptance, not anchoring, is the
                      moat. It is ALWAYS empty in this open module; it is populated
                      at resolve-time by the closed value-layer service, or stays
                      empty when that service is absent (so the open product is
                      fully standalone — "works if we vanish").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

SCHEMA_VERSION = "asr-1"

# Claims are existence/state assertions ONLY — never predictive. The allowed set
# is closed by design so an adapter cannot smuggle a forecast/edge claim through.
CLAIMS = ("existed_at_or_before_anchor", "state_snapshot_at_anchor")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA512_RE = re.compile(r"^[0-9a-f]{128}$")


@dataclass
class Subject:
    digest_sha256: str
    kind: str = "file"  # file | folder | json-state
    path: str | None = None  # relative path or None (hash-labeled / private)
    digest_sha512: str | None = None


@dataclass
class TimeAnchor:
    protocol: str = "opentimestamps"
    chain: str = "bitcoin"
    created_at: str | None = None
    calendars_ok: int = 0
    calendars_total: int = 0
    btc_pinned_at: str | None = None
    status: str = "pending"  # pending | confirmed | ...


@dataclass
class Acceptance:
    """The open-core seam. Empty in the open product; populated only by the closed
    acceptance-network service at resolve-time. Its emptiness here is the point:
    a forker of the open verifier gets math-validity but NOT 'and a trusted issuer
    profile / regulator already accepts this'."""
    issuer_profile: str | None = None
    issuer_trusted: bool | None = None  # None = not evaluated (service absent)
    revoked: bool | None = None
    disputed: bool | None = None


@dataclass
class AttestationRecord:
    receipt_id: str
    subject: Subject
    claimed_state: str
    time_anchor: TimeAnchor
    issuer_identity: str | None = None  # did:key or None
    acceptance: Acceptance = field(default_factory=Acceptance)
    schema: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate(rec: AttestationRecord) -> list[str]:
    """Return a list of schema violations (empty == valid)."""
    errs: list[str] = []
    if not rec.receipt_id:
        errs.append("receipt_id is empty")
    if not (rec.subject and _SHA256_RE.match(rec.subject.digest_sha256 or "")):
        errs.append("subject.digest_sha256 must be 64 lowercase hex chars")
    if rec.subject and rec.subject.digest_sha512 and not _SHA512_RE.match(rec.subject.digest_sha512):
        errs.append("subject.digest_sha512 must be 128 lowercase hex chars when present")
    if rec.subject and rec.subject.kind not in ("file", "folder", "json-state"):
        errs.append(f"subject.kind invalid: {rec.subject.kind!r}")
    if rec.claimed_state not in CLAIMS:
        errs.append(f"claimed_state must be one of {CLAIMS}, got {rec.claimed_state!r} "
                    "(predictive/edge claims are forbidden by schema)")
    if not rec.time_anchor or not rec.time_anchor.protocol:
        errs.append("time_anchor.protocol is required")
    if rec.time_anchor and rec.time_anchor.calendars_ok > rec.time_anchor.calendars_total:
        errs.append("time_anchor.calendars_ok exceeds calendars_total")
    return errs


def from_engine_record(record: dict[str, Any]) -> AttestationRecord:
    """Map an Orphograph engine.verify_receipt() dict onto the canonical 5-tuple.

    Proves the existing live receipt IS an instance of the horizontal primitive
    (an existence attestation). Does not touch or import the engine — pure
    structural mapping — so this stays a clean, shared, dependency-free library.
    """
    return AttestationRecord(
        receipt_id=record.get("receipt_id", ""),
        subject=Subject(
            digest_sha256=record.get("hash_hex", ""),
            digest_sha512=record.get("sha512_hex"),
            kind="file",
            path=None,
        ),
        claimed_state="existed_at_or_before_anchor",
        time_anchor=TimeAnchor(
            protocol="opentimestamps",
            chain="bitcoin",
            created_at=record.get("created_at"),
            calendars_ok=int(record.get("calendars_ok", 0) or 0),
            calendars_total=int(record.get("calendars_total", 0) or 0),
            btc_pinned_at=record.get("btc_pinned_at"),
            status=record.get("status", "pending"),
        ),
        issuer_identity=record.get("issuer") or None,
        acceptance=Acceptance(),  # open module never populates this
    )
