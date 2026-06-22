"""acceptance_resolver_http.py — generic loopback HTTP-client acceptance resolver.

The SIDECAR activation path: run the (closed) acceptance service as a loopback
sidecar and point this open product at it WITHOUT bundling any closed code into
this MIT image. Activate with:

    ORPHO_ACCEPTANCE_RESOLVER=acceptance_resolver_http
    ASN_ACCEPTANCE_URL=http://127.0.0.1:8770      (the sidecar)

This module is generic MIT plumbing — it knows only how to GET an acceptance dict
over loopback; it contains none of the value-layer logic (trust-graph / acceptance
data) which stays in the separate non-MIT service. It raises on any problem; the
caller (acceptance_hook) catches and degrades to the null block, and the short
timeout keeps a down/slow sidecar from stalling /api/verify.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

_TIMEOUT = float(os.environ.get("ASN_ACCEPTANCE_TIMEOUT", "1.5"))


def resolve_acceptance(receipt_id: str, issuer_did: str | None = None,
                       profile: str | None = None) -> dict[str, Any]:
    base = os.environ.get("ASN_ACCEPTANCE_URL", "").strip()
    if not base:
        raise RuntimeError("ASN_ACCEPTANCE_URL unset")  # caller degrades to null
    params = {"receipt_id": receipt_id}
    if issuer_did:
        params["issuer_did"] = issuer_did
    if profile:
        params["profile"] = profile
    url = base.rstrip("/") + "/acceptance?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as r:  # noqa: S310 (loopback only)
        data = json.loads(r.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("acceptance resolver returned a non-dict")
    return data
