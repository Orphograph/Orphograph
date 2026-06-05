"""orphograph — Python SDK for Bitcoin-anchored file timestamping.

Anchor a file, bytes, or a string to the Bitcoin chain in two lines. The content
is hashed locally (SHA-256 + SHA-512); **only the hashes are transmitted** — the
bytes never leave your machine. Receipts verify against the public Bitcoin chain
via OpenTimestamps, independently of orphograph.com.

    import orphograph
    r = orphograph.anchor_file("contract.pdf")
    print(r.receipt_url)          # https://orphograph.com/r/<id>

    # CI / agent output, no file needed:
    r = orphograph.anchor_text(model_output, label="run-42")

Privacy + portability: stdlib only, no dependencies. The receipt and its `.ots`
proofs can be verified offline with the standalone verifier even if the service
disappears. Proof-of-existence only — not authorship or legal authenticity.

MIT. https://github.com/Orphograph/Orphograph
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field

__version__ = "0.1.0"
__all__ = [
    "Client", "Receipt", "OrphographError",
    "anchor_file", "anchor_bytes", "anchor_text", "verify", "get_receipt",
    "DEFAULT_ENDPOINT",
]

DEFAULT_ENDPOINT = "https://orphograph.com"
_TIMEOUT = 30
_UA = f"orphograph-sdk/{__version__} (python; stdlib)"
_CHUNK = 4 * 1024 * 1024


class OrphographError(Exception):
    """Raised on a failed anchor/verify (network error or non-2xx API response)."""


@dataclass
class Receipt:
    receipt_id: str
    receipt_url: str
    sha256: str
    sha512: str
    calendars_ok: int | None = None
    calendars_total: int | None = None
    created_at: str | None = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def _from_response(cls, resp: dict, endpoint: str, sha256: str, sha512: str) -> "Receipt":
        rid = resp.get("receipt_id", "")
        return cls(
            receipt_id=rid,
            receipt_url=resp.get("receipt_url") or (f"{endpoint.rstrip('/')}/r/{rid}" if rid else ""),
            sha256=sha256, sha512=sha512,
            calendars_ok=resp.get("calendars_ok"),
            calendars_total=resp.get("calendars_total"),
            created_at=resp.get("created_at"),
            raw=resp,
        )


def _sha(data: bytes) -> tuple[str, str]:
    return hashlib.sha256(data).hexdigest(), hashlib.sha512(data).hexdigest()


def _sha_file(path: str) -> tuple[str, str]:
    s256, s512 = hashlib.sha256(), hashlib.sha512()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            s256.update(chunk)
            s512.update(chunk)
    return s256.hexdigest(), s512.hexdigest()


def _urllib_transport(method: str, url: str, body: dict | None, headers: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return e.code, {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        raise OrphographError(f"network error: {type(e).__name__}: {e}") from e


class Client:
    """Reusable client. `api_key` (from orphograph.com/account.html) raises your
    rate limits and ties anchors to your subscription; omit it for the free tier.

    `_transport(method, url, body, headers) -> (status, dict)` is injectable for
    tests; defaults to a stdlib urllib implementation.
    """

    def __init__(self, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT, *, _transport=None):
        self.api_key = api_key or os.environ.get("ORPHOGRAPH_API_KEY", "")
        self.endpoint = endpoint.rstrip("/")
        self._transport = _transport or _urllib_transport

    # ── internal ──
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": _UA}
        if self.api_key:
            h["X-Orpho-Api-Key"] = self.api_key
        return h

    def _anchor(self, sha256: str, sha512: str, label: str | None) -> Receipt:
        body = {"hash_hex": sha256, "sha512_hex": sha512}
        if label:
            body["client_label"] = str(label)[:200]
        status, resp = self._transport("POST", f"{self.endpoint}/api/anchor", body, self._headers())
        if status < 200 or status >= 300 or resp.get("error"):
            raise OrphographError(f"anchor failed (HTTP {status}): {resp.get('error', resp)}")
        return Receipt._from_response(resp, self.endpoint, sha256, sha512)

    # ── public ──
    def anchor_file(self, path: str, *, label: str | None = None) -> Receipt:
        return self._anchor(*_sha_file(path), label)

    def anchor_bytes(self, data: bytes, *, label: str | None = None) -> Receipt:
        return self._anchor(*_sha(data), label)

    def anchor_text(self, text: str, *, label: str | None = None) -> Receipt:
        return self._anchor(*_sha(text.encode("utf-8")), label)

    def get_receipt(self, receipt_id: str) -> dict:
        rid = "".join(c for c in receipt_id if c.isalnum() or c in "_-")[:64]
        status, resp = self._transport("GET", f"{self.endpoint}/api/receipt/{rid}", None, self._headers())
        if status < 200 or status >= 300:
            raise OrphographError(f"get_receipt failed (HTTP {status})")
        return resp

    def verify(self, receipt_id: str) -> dict:
        rid = "".join(c for c in receipt_id if c.isalnum() or c in "_-")[:64]
        status, resp = self._transport("GET", f"{self.endpoint}/api/verify/{rid}", None, self._headers())
        if status < 200 or status >= 300:
            raise OrphographError(f"verify failed (HTTP {status})")
        return resp


# ── module-level convenience (a default Client per call) ──
def anchor_file(path: str, *, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT,
                label: str | None = None) -> Receipt:
    return Client(api_key, endpoint).anchor_file(path, label=label)


def anchor_bytes(data: bytes, *, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT,
                 label: str | None = None) -> Receipt:
    return Client(api_key, endpoint).anchor_bytes(data, label=label)


def anchor_text(text: str, *, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT,
                label: str | None = None) -> Receipt:
    return Client(api_key, endpoint).anchor_text(text, label=label)


def get_receipt(receipt_id: str, *, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT) -> dict:
    return Client(api_key, endpoint).get_receipt(receipt_id)


def verify(receipt_id: str, *, api_key: str = "", endpoint: str = DEFAULT_ENDPOINT) -> dict:
    return Client(api_key, endpoint).verify(receipt_id)
