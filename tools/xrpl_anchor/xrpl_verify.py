#!/usr/bin/env python3
"""xrpl_verify.py — check an Orphograph root anchored in an XRPL transaction memo.

Standard library only, like dist/orphograph-verify. It asks public XRPL nodes
for the transaction (JSON-RPC `tx`, api_version 2) and checks that:
  1. the node returned the transaction that was asked for,
  2. it is in a VALIDATED ledger,
  3. its result is tesSUCCESS,
  4. it carries one Orphograph memo (MemoType orphograph.com/xrpl-anchor/v0),
  5. the memo's root_hex equals the expected root (from --root or a manifest).
With --account it also requires the sender to be that address; without it the
verdict says UNBOUND, because anyone can write any memo.

A node is untrusted: a lying node could fabricate the whole transaction. So OK
needs --min-agree (default 2) DISTINCT hosts that agree on hash, ledger,
sender and memo; the defaults are run by different operators. A node that
reports a different verdict or transaction makes it a FAIL. A node that cannot
be read, sends a malformed answer, or lacks the ledger in its history (most
keep hours, not years) counts as unavailable, not as dissent. --min-agree 1 is
allowed and is labelled SINGLE-NODE. The tool cannot tell whether two
hostnames share an operator; choose --node values with that in mind.

OK proves the root existed no later than the ledger close time. Nothing more.
Testnet is reset from time to time; a testnet anchor is a demo, not a record.

Exit: 0 OK · 1 FAIL · 2 PENDING (found, not yet validated) · 3 UNAVAILABLE
(too few nodes confirmed — never reported as a pass or a fail).

  python3 xrpl_verify.py <tx_hash> --manifest manifest.json [--account r...]
"""
import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

TESTNET = "https://s.altnet.rippletest.net:51234/"   # run by Ripple
# Default quorum: two different operators. clio.altnet is ALSO Ripple's, so it
# would not add independence. XRPL Labs keeps ~2 h of history (checked
# 2026-10-30); older anchors need full-history nodes passed with --node.
TESTNET_NODES = [TESTNET, "https://testnet.xrpl-labs.com/"]
MAX_BODY = 2 * 1024 * 1024
# Honest and named. A CDN in front of one node 403s urllib's default agent.
USER_AGENT = "orphograph-xrpl-verify/0 (+https://github.com/Orphograph/Orphograph)"
MEMO_TYPE = "orphograph.com/xrpl-anchor/v0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TX_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")
# What OK nodes must agree on. ledger_hash is compared only between nodes that
# report it (some servers omit it); the rest must match exactly.
AGREE_ON = ("hash", "ledger_index", "account", "memo")


class Unavailable(Exception):
    pass


def fetch_tx(tx_hash, node=TESTNET, timeout=20):
    body = json.dumps({"method": "tx", "params": [
        {"transaction": tx_hash, "binary": False, "api_version": 2}]}).encode()
    req = urllib.request.Request(node, data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            raise Unavailable(f"{node}: response over {MAX_BODY} bytes")
        return json.loads(raw.decode())
    except (OSError, http.client.HTTPException, ValueError, RecursionError) as e:
        # OSError covers URLError, timeouts and resets mid-read.
        raise Unavailable(f"{node}: {type(e).__name__}: {e}") from e


def memo_payload(memo_data_hex):
    """MemoData hex -> payload dict for v0 anchors."""
    return json.loads(bytes.fromhex(memo_data_hex).decode("utf-8"))


def orphograph_memos(tx_json):
    out = []
    memos = tx_json.get("Memos")
    for wrap in memos if isinstance(memos, list) else []:
        m = wrap.get("Memo") if isinstance(wrap, dict) else None
        if not isinstance(m, dict):
            continue
        try:
            mtype = bytes.fromhex(m.get("MemoType", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError, TypeError):
            continue
        if mtype == MEMO_TYPE:
            out.append(m)
    return out


def _ledger_index(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def check(response, expected_root, account=None, tx_hash=None):
    """One node's RPC response -> (verdict, exit_code, detail). Pure: no network."""
    res = response.get("result") if isinstance(response, dict) else None
    if not isinstance(res, dict):
        return "UNAVAILABLE", 3, {"reason": "node returned no result object"}
    if res.get("error") == "txnNotFound":
        return "UNAVAILABLE", 3, {"reason": "not in this node's ledger history"}
    if res.get("status") != "success" or "error" in res:
        return "UNAVAILABLE", 3, {"reason": f"node error: {res.get('error') or res.get('status')}"}
    tx = res.get("tx_json") or res          # api_version 1 nests nothing
    meta = res.get("meta")
    if not isinstance(tx, dict) or not isinstance(meta, dict):
        return "UNAVAILABLE", 3, {"reason": "malformed transaction or metadata"}
    got = res.get("hash") or tx.get("hash")
    if tx_hash and str(got).upper() != tx_hash.upper():
        return "FAIL", 1, {"hash": got, "reason": f"node returned {got}, not the requested transaction"}
    if res.get("validated") is not True:
        return "PENDING", 2, {"reason": "transaction not in a validated ledger yet"}
    detail = {"hash": str(got).upper() if got else None,
              "ledger_index": _ledger_index(res.get("ledger_index") or tx.get("ledger_index")),
              "ledger_hash": res.get("ledger_hash"),
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
    except (ValueError, UnicodeDecodeError, TypeError, RecursionError) as e:
        return "FAIL", 1, {**detail, "reason": f"memo data unreadable: {type(e).__name__}"}
    if not isinstance(payload, dict):
        return "FAIL", 1, {**detail, "reason": "memo data is not a JSON object"}
    detail["memo"] = payload
    if payload.get("v") != 0 or payload.get("root_hex") != expected_root:
        return "FAIL", 1, {**detail, "reason": "memo root does not match the expected root"}
    return ("OK" if account else "OK-UNBOUND"), 0, detail


def distinct_nodes(nodes):
    """Drop repeats of the same host, so one node cannot cast two votes."""
    seen, out = set(), []
    for n in nodes:
        host = (urllib.parse.urlsplit(n).hostname or n).lower()
        if host not in seen:
            seen.add(host)
            out.append(n)
    return out


def _agree(oks):
    keys = {tuple(json.dumps(p.get(k), sort_keys=True) for k in AGREE_ON) for p in oks}
    lh = {p["ledger_hash"] for p in oks if p.get("ledger_hash")}
    return len(keys) == 1 and len(lh) <= 1


def verify(tx_hash, expected_root, account, nodes, min_agree=2, fetch=None):
    """Ask every distinct node, then combine. Any FAIL is a FAIL; OK needs
    min_agree nodes that agree; too few OKs is UNAVAILABLE, never OK."""
    fetch = fetch or fetch_tx               # looked up per call, so tests can patch it
    nodes = distinct_nodes(nodes)

    def one(node):
        try:
            v, code, d = check(fetch(tx_hash, node), expected_root, account, tx_hash)
        except Unavailable as e:
            v, code, d = "UNAVAILABLE", 3, {"reason": str(e)}
        return {"node": node, "verdict": v, **d}

    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        per = list(pool.map(one, nodes))
    oks = [p for p in per if p["verdict"].startswith("OK")]
    fails = [p for p in per if p["verdict"] == "FAIL"]
    summary = {"nodes": per}
    if fails:
        why = "nodes disagree" if oks else fails[0].get("reason")
        return "FAIL", 1, {**summary, "reason": why}
    if oks and not _agree(oks):
        return "FAIL", 1, {**summary, "reason": "nodes report different transactions or ledgers"}
    if oks and len(oks) >= min_agree:
        verdict = oks[0]["verdict"]
        if len(oks) == 1:
            verdict = verdict.replace("OK", "OK-SINGLE-NODE", 1)
        keep = {k: oks[0].get(k) for k in ("hash", "ledger_index", "ledger_hash",
                                           "close_time_iso", "account", "memo")}
        return verdict, 0, {**keep, "agreeing_nodes": len(oks), **summary}
    if oks:
        return "UNAVAILABLE", 3, {**summary, "reason":
                                  f"only {len(oks)} of the {min_agree} required nodes confirmed"}
    if any(p["verdict"] == "PENDING" for p in per):
        return "PENDING", 2, summary
    return "UNAVAILABLE", 3, summary


def confirm(tx_hash, expected_root, account, nodes, min_agree=2, tries=6, wait=5,
            verify_fn=None, sleep=time.sleep):
    """verify(), retried while the answer could still change (a node a ledger
    behind the one we submitted to). Returns the last verdict; never upgrades it."""
    verify_fn = verify_fn or verify
    result = None
    for i in range(max(1, tries)):
        result = verify_fn(tx_hash, expected_root, account, nodes, min_agree)
        if result[1] in (0, 1):
            return result
        if i + 1 < tries:
            sleep(wait)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("tx_hash")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--root", help="expected root, 64 lowercase hex")
    g.add_argument("--manifest", help="folder manifest.json; uses its root_hex")
    ap.add_argument("--account", help="require this sender address (r...)")
    ap.add_argument("--node", action="append",
                    help="node URL; repeat for each (replaces the defaults: "
                         "one Ripple + one XRPL Labs testnet node)")
    ap.add_argument("--min-agree", type=int, default=2,
                    help="distinct nodes that must agree (default 2; 1 is labelled SINGLE-NODE)")
    a = ap.parse_args(argv)
    if not TX_HASH.match(a.tx_hash):
        ap.error("tx_hash must be 64 hex characters")
    nodes = distinct_nodes(a.node or TESTNET_NODES)
    if not 1 <= a.min_agree <= len(nodes):
        ap.error(f"--min-agree must be between 1 and the number of distinct nodes ({len(nodes)})")
    if a.root:
        root = a.root
    else:
        with open(a.manifest) as f:
            root = json.load(f).get("root_hex")
    if not HEX64.match(root or ""):
        ap.error("expected root must be 64 lowercase hex characters")
    verdict, code, detail = verify(a.tx_hash, root, a.account, nodes, a.min_agree)
    print(json.dumps({"verdict": verdict, "expected_root": root, **detail}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
