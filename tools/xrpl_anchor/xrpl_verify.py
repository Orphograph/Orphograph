#!/usr/bin/env python3
"""xrpl_verify.py — check an Orphograph root anchored in an XRPL transaction memo.

Standard library only, like dist/orphograph-verify. It asks a public XRPL node
for the transaction (JSON-RPC `tx`, api_version 2) and checks four things:
  1. the transaction is in a VALIDATED ledger,
  2. its result is tesSUCCESS,
  3. it carries an Orphograph memo (MemoType orphograph.com/xrpl-anchor/v0),
  4. the memo's root_hex equals the expected root (from --root or a manifest).
With --account it also requires the sender to be that address; without it the
verdict says UNBOUND, because anyone can write any memo.

OK proves the root existed no later than the ledger close time. Nothing more.

Exit: 0 OK · 1 FAIL · 2 PENDING (not found / not validated) · 3 UNAVAILABLE
(the node could not be read — never reported as a pass or a fail).

  python3 xrpl_verify.py <tx_hash> --manifest manifest.json [--account r...]
"""
import argparse
import json
import re
import sys
import urllib.error
import urllib.request

TESTNET = "https://s.altnet.rippletest.net:51234/"
MEMO_TYPE = "orphograph.com/xrpl-anchor/v0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TX_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")


class Unavailable(Exception):
    pass


def fetch_tx(tx_hash, node=TESTNET, timeout=20):
    body = json.dumps({"method": "tx", "params": [
        {"transaction": tx_hash, "binary": False, "api_version": 2}]}).encode()
    req = urllib.request.Request(node, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise Unavailable(f"{node}: {e}") from e


def memo_payload(memo_data_hex):
    """MemoData hex -> payload dict for v0 anchors."""
    return json.loads(bytes.fromhex(memo_data_hex).decode("utf-8"))


def orphograph_memos(tx_json):
    out = []
    for wrap in tx_json.get("Memos") or []:
        m = wrap.get("Memo") or {}
        try:
            mtype = bytes.fromhex(m.get("MemoType", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if mtype == MEMO_TYPE:
            out.append(m)
    return out


def check(response, expected_root, account=None):
    """RPC response -> (verdict, exit_code, detail dict). Pure: no network."""
    res = (response or {}).get("result") or {}
    if res.get("error") == "txnNotFound":
        return "PENDING", 2, {"reason": "transaction not found on this node"}
    if res.get("status") != "success" or "error" in res:
        return "UNAVAILABLE", 3, {"reason": f"node error: {res.get('error') or res.get('status')}"}
    if res.get("validated") is not True:
        return "PENDING", 2, {"reason": "transaction not in a validated ledger yet"}
    tx = res.get("tx_json") or res          # api_version 1 nests nothing
    meta = res.get("meta") or {}
    detail = {"hash": res.get("hash") or tx.get("hash"),
              "ledger_index": res.get("ledger_index") or tx.get("ledger_index"),
              "close_time_iso": res.get("close_time_iso"),
              "account": tx.get("Account")}
    if meta.get("TransactionResult") != "tesSUCCESS":
        return "FAIL", 1, {**detail, "reason": f"result {meta.get('TransactionResult')}"}
    if account and tx.get("Account") != account:
        return "FAIL", 1, {**detail, "reason": f"sent by {tx.get('Account')}, expected {account}"}
    memos = orphograph_memos(tx)
    if len(memos) != 1:
        return "FAIL", 1, {**detail, "reason": f"{len(memos)} Orphograph memos (need exactly 1)"}
    try:
        payload = memo_payload(memos[0].get("MemoData", ""))
    except (ValueError, UnicodeDecodeError) as e:
        return "FAIL", 1, {**detail, "reason": f"memo data unreadable: {e}"}
    detail["memo"] = payload
    if payload.get("v") != 0 or payload.get("root_hex") != expected_root:
        return "FAIL", 1, {**detail, "reason": "memo root does not match the expected root"}
    return ("OK" if account else "OK-UNBOUND"), 0, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tx_hash")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--root", help="expected root, 64 lowercase hex")
    g.add_argument("--manifest", help="folder manifest.json; uses its root_hex")
    ap.add_argument("--account", help="require this sender address (r...)")
    ap.add_argument("--node", default=TESTNET)
    a = ap.parse_args(argv)
    if not TX_HASH.match(a.tx_hash):
        ap.error("tx_hash must be 64 hex characters")
    root = a.root if a.root else json.load(open(a.manifest))["root_hex"]
    if not HEX64.match(root or ""):
        ap.error("expected root must be 64 lowercase hex characters")
    try:
        resp = fetch_tx(a.tx_hash, a.node)
    except Unavailable as e:
        print(json.dumps({"verdict": "UNAVAILABLE", "reason": str(e)}, indent=2))
        return 3
    verdict, code, detail = check(resp, root, a.account)
    print(json.dumps({"verdict": verdict, "expected_root": root, "node": a.node, **detail}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
