#!/usr/bin/env python3
"""xrpl_anchor.py — anchor an Orphograph root in an XRPL TESTNET memo.

Sends one AccountSet (no flags: a no-op that still lands in a validated ledger)
with a single memo, the wire format in DESIGN.md, waits for validation and
appends the result to anchors/testnet.jsonl. Testnet only by construction: the
node is fixed and there is no flag to change it; mainnet is a founder decision.

Needs xrpl-py 5.2.0 (pinned; see requirements.txt). The wallet seed lives in
~/.config/orphograph-xrpl/testnet-wallet.json (0600) and is never printed.

  python xrpl_anchor.py --manifest ../../web/sample-folder/manifest.json
  python xrpl_anchor.py --new-testnet-wallet     # faucet-funded, once
"""
import argparse
import json
import os
import sys
from pathlib import Path

from xrpl.clients import JsonRpcClient
from xrpl.models.transactions import AccountSet, Memo
from xrpl.transaction import submit_and_wait
from xrpl.wallet import Wallet, generate_faucet_wallet

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xrpl_verify import HEX64, MEMO_TYPE, TESTNET, TESTNET_NODES, verify  # noqa: E402

WALLET = Path.home() / ".config" / "orphograph-xrpl" / "testnet-wallet.json"
LOG = Path(__file__).resolve().parent / "anchors" / "testnet.jsonl"


def memo_for(root_hex, receipt_id, alg):
    data = json.dumps({"alg": alg, "receipt_id": receipt_id, "root_hex": root_hex, "v": 0},
                      sort_keys=True, separators=(",", ":"))
    return Memo(memo_type=MEMO_TYPE.encode().hex(),
                memo_format=b"application/json".hex(),
                memo_data=data.encode().hex())


def save_wallet(wallet):
    WALLET.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(WALLET.parent, 0o700)
    fd = os.open(WALLET, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"network": "xrpl-testnet", "address": wallet.address, "seed": wallet.seed}, f)


def load_wallet():
    w = json.loads(WALLET.read_text())
    if w.get("network") != "xrpl-testnet":
        raise SystemExit(f"{WALLET} is not a testnet wallet; refusing")
    return Wallet.from_seed(w["seed"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--new-testnet-wallet", action="store_true")
    ap.add_argument("--manifest", help="folder manifest.json (root_hex, receipt_id, algorithm)")
    a = ap.parse_args(argv)
    client = JsonRpcClient(TESTNET)

    if a.new_testnet_wallet:
        if WALLET.exists():
            raise SystemExit(f"{WALLET} exists; not overwriting")
        w = generate_faucet_wallet(client)
        save_wallet(w)
        print(json.dumps({"created": str(WALLET), "address": w.address}))
        return 0
    if not a.manifest:
        ap.error("--manifest is required")

    m = json.load(open(a.manifest))
    root = m.get("root_hex", "")
    if not HEX64.match(root):
        raise SystemExit("manifest root_hex is not 64 lowercase hex")
    wallet = load_wallet()
    tx = AccountSet(account=wallet.address,
                    memos=[memo_for(root, m.get("receipt_id"), m.get("algorithm"))])
    sent = submit_and_wait(tx, client, wallet).result
    tx_hash = sent.get("hash") or (sent.get("tx_json") or {}).get("hash")

    # Submitted through the one node above (TESTNET), never fanned out. Then ONE
    # read-back through the verifier's quorum, with no retry loop: if the second
    # node lags, the anchor is logged UNAVAILABLE and can be re-verified later.
    verdict, code, detail = verify(tx_hash, root, wallet.address, TESTNET_NODES)
    rec = {"network": "xrpl-testnet", "tx_hash": tx_hash, "verdict": verdict,
           "root_hex": root, "receipt_id": m.get("receipt_id"),
           "account": detail.get("account"), "ledger_index": detail.get("ledger_index"),
           "close_time_iso": detail.get("close_time_iso"),
           "fee_drops": (sent.get("tx_json") or sent).get("Fee")}
    # Logged whatever the verdict: the fee is spent and the tx is on the ledger,
    # so an anchor missing from the log would be an anchor nobody can find.
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    print(json.dumps(rec, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
