"""acceptance_hook.py — OPTIONAL acceptance resolver for /api/verify.

The open product is standalone: by default every receipt's `acceptance` block is
empty (all null). Anchoring validity is proven offline against Bitcoin with no
registry — "works if we vanish" is preserved. IF (and only if) a deployer sets
ORPHO_ACCEPTANCE_RESOLVER to an importable module exposing

    resolve_acceptance(receipt_id, issuer_did, profile) -> dict

this hook calls it at verify time to populate issuer_profile / issuer_trusted /
revoked / disputed — the value-layer answer ("...and a trusted issuer profile is
accepted, not revoked, not disputed") that a forker of the open verifier cannot
produce. ANY failure (unset / import error / bad return / exception) degrades
SILENTLY to the empty block: the optional value layer can never break, block, or
slow the open verify path, and this open module holds NO reference to any specific
resolver — the binding is runtime config only.

stdlib + attest_core (for the canonical empty shape). dependency-free otherwise.
"""
from __future__ import annotations

import dataclasses
import importlib
import os
from typing import Any

import attest_core

_KEYS = ("issuer_profile", "issuer_trusted", "revoked", "disputed")
_ENV = "ORPHO_ACCEPTANCE_RESOLVER"


def empty() -> dict[str, Any]:
    """The canonical empty acceptance block (all null) — the standalone default."""
    return dataclasses.asdict(attest_core.Acceptance())


def resolve(receipt_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return the acceptance block for a receipt. Empty unless a resolver is
    configured; never raises (the open verify path must not depend on the value
    layer's availability or correctness)."""
    base = empty()
    resolver = os.environ.get(_ENV, "").strip()
    if not resolver:
        return base
    try:
        mod = importlib.import_module(resolver)
        out = mod.resolve_acceptance(
            receipt_id=receipt_id,
            issuer_did=record.get("issuer"),
            profile=record.get("profile"),
        )
        if isinstance(out, dict):
            # accept only the known keys; fall back to null for anything missing
            return {k: out.get(k, base[k]) for k in _KEYS}
    except Exception:
        pass  # silent degrade — the value layer is strictly optional
    return base
