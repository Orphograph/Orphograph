"""_xrpl_fake.py — a scripted XRPL JSON-RPC node on loopback, for the XRPL rail tests.

Never the real ledger: every test of server/xrpl_pay.py points ORPHO_XRPL_RPC
(or the module's node list) at one or more of these. Each node answers
`account_tx` from `entries` and `tx` from the same entries by hash, in the API
v2 shape (hash, ledger_index, close_time_iso, validated, tx_json, meta).
`mode` makes the node fail the way real nodes do: an HTTP status, a redirect,
a rate-limit reply, garbage, or a hang past the client's timeout.
`overrides` lets one node lie about one transaction (a different answer for
a hash than its siblings give).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import xrpl_pay

RLUSD = xrpl_pay.default_currency()
MERCHANT = xrpl_pay.encode_classic_address(bytes([7]) * 20)
ISSUER = xrpl_pay.encode_classic_address(bytes([9]) * 20)
SENDER = xrpl_pay.encode_classic_address(bytes([11]) * 20)
IMPOSTOR = xrpl_pay.encode_classic_address(bytes([13]) * 20)


def tx_hash(n: int) -> str:
    return f"{n:064X}"


def payment(n: int, tag: int, value: str = "5", *, close_unix: float | None = None,
            destination: str = MERCHANT, currency: str = RLUSD, issuer: str = ISSUER,
            delivered=None, deliver_max=None, result: str = "tesSUCCESS",
            validated: bool = True, ledger_index: int | None = None) -> dict:
    """One account_tx entry (API v2). `delivered` overrides delivered_amount
    wholesale (an XRP drops string, a dict, None to omit it); `deliver_max`
    is the sender's offered amount, which the rail must ignore."""
    close = int(close_unix if close_unix is not None else time.time())
    amount = {"currency": currency, "issuer": issuer, "value": value}
    tx = {"TransactionType": "Payment", "Account": SENDER, "Destination": destination,
          "DestinationTag": tag, "DeliverMax": deliver_max or amount, "Fee": "12",
          "Flags": 0, "Sequence": n, "date": close - xrpl_pay.RIPPLE_EPOCH}
    meta = {"TransactionResult": result, "TransactionIndex": 0}
    if delivered is not None:
        meta["delivered_amount"] = delivered
    elif result == "tesSUCCESS":
        meta["delivered_amount"] = amount
    return {"hash": tx_hash(n), "ledger_index": ledger_index or 1000 + n,
            "ledger_hash": f"{n + 7:064X}",
            "close_time_iso": datetime.fromtimestamp(close, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "validated": validated, "tx_json": tx, "meta": meta}


class FakeNode:
    def __init__(self, host: str = "127.0.0.1"):
        self.entries: list[dict] = []
        self.overrides: dict[str, dict] = {}
        self.mode = None          # None | int status | "redirect" | "ratelimit" | "slowdown" | "garbage" | "hang"
        self.redirect_to = ""
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        node = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = {}
                with node._lock:
                    node.requests.append({"method": body.get("method"),
                                          "params": (body.get("params") or [{}])[0],
                                          "ua": self.headers.get("User-Agent")})
                status, payload, headers = node.answer(body)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}/"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def calls(self, method: str | None = None) -> int:
        with self._lock:
            return sum(1 for r in self.requests if method is None or r["method"] == method)

    def answer(self, body: dict):
        mode = self.mode
        if isinstance(mode, int):
            return mode, {"error": "scripted"}, {}
        if mode == "redirect":
            return 302, b"", {"Location": self.redirect_to}
        if mode == "ratelimit":
            return 200, {"result": {"status": "error", "error": "slowDown"}}, {}
        if mode == "garbage":
            return 200, b"<html>not json</html>", {}
        if mode == "hang":
            time.sleep(xrpl_pay.TIMEOUT + 2)
            return 200, {"result": {}}, {}
        method = body.get("method")
        params = (body.get("params") or [{}])[0]
        if method == "account_tx":
            return 200, {"result": {"status": "success", "account": params.get("account"),
                                    "transactions": list(reversed(self.entries))}}, {}
        if method == "tx":
            want = str(params.get("transaction", "")).upper()
            if want in self.overrides:
                return 200, {"result": {**self.overrides[want], "status": "success"}}, {}
            for e in self.entries:
                if e["hash"] == want:
                    return 200, {"result": {**e, "status": "success"}}, {}
            return 200, {"result": {"status": "error", "error": "txnNotFound"}}, {}
        return 200, {"result": {"status": "error", "error": "unknownCmd"}}, {}
