"""orphograph._client — HTTP transport to the Orphograph REST API.

This client never reads file contents. It builds a Merkle manifest from
on-disk SHA-256 digests, then transmits only the manifest and root hash.
File bodies remain on the local machine at all times. The wire payload is
the manifest JSON described in ``orphograph-merkle-v1-rfc6962`` — relative
POSIX paths, per-file SHA-256 digests, leaf hashes, and a 32-byte root.

The module uses ``urllib.request`` and ``json`` from the standard library
only. No third-party HTTP dependency is introduced on the runtime path.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

DEFAULT_SERVER_URL = "https://orphograph.com"
DEFAULT_TIMEOUT = 60.0
USER_AGENT = "orphograph-python-sdk/0.1"


class OrphographError(RuntimeError):
    """Raised when the hosted service returns a non-2xx response."""

    def __init__(self, status: int, message: str, payload: Optional[dict] = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload or {}


def _normalise_base(server_url: str) -> str:
    if not server_url:
        raise ValueError("server_url is required")
    return server_url.rstrip("/")


def _build_headers(api_key: Optional[str], content_type: Optional[str]) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key.strip()
    return headers


def _request(
    method: str,
    url: str,
    *,
    body: Optional[bytes] = None,
    headers: Optional[dict] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    req = urllib.request.Request(url=url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        status = e.code
        payload: dict = {}
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"error": raw[:200].decode("utf-8", "replace")}
        raise OrphographError(status, payload.get("error") or e.reason or "request failed", payload)
    if not (200 <= status < 300):
        raise OrphographError(status, "unexpected status")
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise OrphographError(status, f"invalid JSON response: {e}")


def post_anchor_folder(
    manifest: dict,
    *,
    server_url: str = DEFAULT_SERVER_URL,
    api_key: Optional[str] = None,
    client_label: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """POST a manifest to /api/anchor_folder.

    Only the manifest (paths + per-file SHA-256 + leaf hashes + root) is
    transmitted. File contents are not part of the payload.
    """
    base = _normalise_base(server_url)
    payload: dict[str, Any] = {"manifest": manifest}
    if client_label is not None:
        payload["client_label"] = str(client_label)[:200]
    body = json.dumps(payload).encode("utf-8")
    headers = _build_headers(api_key, "application/json")
    return _request("POST", base + "/api/anchor_folder", body=body, headers=headers, timeout=timeout)


def get_verify_folder(
    receipt_id: str,
    *,
    server_url: str = DEFAULT_SERVER_URL,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """GET /api/verify_folder/<receipt_id>."""
    base = _normalise_base(server_url)
    rid = urllib.parse.quote(receipt_id, safe="")
    headers = _build_headers(api_key, None)
    return _request("GET", f"{base}/api/verify_folder/{rid}", headers=headers, timeout=timeout)


def get_inclusion_proof(
    receipt_id: str,
    rel_path: str,
    *,
    server_url: str = DEFAULT_SERVER_URL,
    api_key: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict:
    """GET /api/inclusion_proof?receipt_id=<rid>&path=<rel_path>."""
    base = _normalise_base(server_url)
    qs = urllib.parse.urlencode({"receipt_id": receipt_id, "path": rel_path})
    headers = _build_headers(api_key, None)
    return _request("GET", f"{base}/api/inclusion_proof?{qs}", headers=headers, timeout=timeout)
