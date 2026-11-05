#!/usr/bin/env python3
"""Two read-only checks for switching x402 to Base mainnet.

    python3 tools/x402_mainnet_check.py payto 0xYourAddress
    python3 tools/x402_mainnet_check.py bazaar

``payto`` runs the server's own payTo rules (format, zero address, USDC token
contract, EIP-55 checksum) and then asks a public Base mainnet RPC whether the
address holds contract code. A wallet such as Phantom's or a Ledger's is an
externally owned account: no code. Code there means a contract, which may not
be able to move USDC out; refuse it unless you know what it is.

EIP-7702 is the exception: a wallet that delegated to a smart-account
contract carries 23 bytes of code, ``0xef0100`` plus the delegate's address.
It is still a wallet with its own key, so it passes, with a note.

``bazaar`` asks the Coinbase x402 discovery search whether a resource on
orphograph.com is listed. The listing appears only after the first settled
mainnet payment through the CDP facilitator.

Neither check signs, sends or stores anything. Exit codes: 0 pass,
1 fail (the reason is printed), 2 could not check (network or bad answer).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import x402  # noqa: E402

BASE_RPC = "https://mainnet.base.org"
DISCOVERY_SEARCH = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search"
OUR_HOSTS = ("orphograph.com", "www.orphograph.com", "orphograph.fly.dev")
UA = "orphograph-tools/1 (x402 mainnet check)"
TIMEOUT = 20
EIP7702_PREFIX = "0xef0100"
SEARCH_LIMIT = 20  # the search answers 400 above 20 (checked 2026-11-05)


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_RefuseRedirects)


class CheckError(RuntimeError):
    """The check could not run (exit 2), as opposed to a failed check (exit 1)."""


def _json(url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"User-Agent": UA, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise CheckError(f"{url}: {exc}") from exc


def contract_code(address: str) -> str:
    answer = _json(BASE_RPC, {"jsonrpc": "2.0", "id": 1, "method": "eth_getCode",
                              "params": [address, "latest"]})
    code = answer.get("result")
    if not isinstance(code, str) or not code.startswith("0x"):
        raise CheckError(f"unexpected eth_getCode answer: {str(answer)[:200]}")
    return code


def check_pay_to(address: str) -> tuple[bool, str]:
    address = address.strip()
    problem = x402.pay_to_problem(address)
    if problem:
        return False, problem.replace("ORPHO_X402_PAY_TO_ADDRESS", "this address")
    lines = []
    body = address[2:]
    if body == body.lower() or body == body.upper():
        lines.append("no checksum in what you pasted (all one case); use this exact form: "
                     + x402.eip55_checksum(address))
    code = contract_code(address).lower()
    if code.startswith(EIP7702_PREFIX) and len(code) == len(EIP7702_PREFIX) + 40:
        lines.append("note: this wallet has an EIP-7702 delegation to 0x"
                     + code[len(EIP7702_PREFIX):] + " (a smart-account upgrade); it still "
                     "receives USDC with its own key")
    elif code not in ("0x", "0x0"):
        return False, (f"this address holds contract code on Base mainnet ({(len(code) - 2) // 2} "
                       "bytes): a contract, not a wallet. Use a wallet address.")
    lines.insert(0, "PASS: valid Base address, checksum ok, a wallet (not a contract)")
    return True, "\n".join(lines)


def _resource_urls(item: dict) -> list[str]:
    urls = [item.get("resource")] + [a.get("resource") for a in item.get("accepts") or []
                                     if isinstance(a, dict)]
    return [u for u in urls if isinstance(u, str)]


def find_listing(answer: dict) -> list[str]:
    items = answer.get("resources")
    if items is None:
        items = answer.get("items")
    if not isinstance(items, list):
        raise CheckError(f"unexpected discovery answer: keys {sorted(answer)[:8]}")
    found = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for url in _resource_urls(item):
            host = urllib.request.urlparse(url).hostname or ""
            if host.lower() in OUR_HOSTS:
                found.append(url)
    return sorted(set(found))


def check_bazaar() -> tuple[bool, str]:
    answer = _json(DISCOVERY_SEARCH + f"?query=orphograph&limit={SEARCH_LIMIT}")
    found = find_listing(answer)
    if found:
        return True, "LISTED: " + ", ".join(found)
    return False, ("NOT LISTED: no orphograph.com resource in the x402 discovery search. "
                   "Expected until the first settled mainnet payment through CDP.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("payto", help="check a receiving address before staging it")
    p.add_argument("address")
    sub.add_parser("bazaar", help="is Orphograph listed in the x402 discovery search?")
    args = parser.parse_args(argv)
    try:
        ok, message = check_pay_to(args.address) if args.cmd == "payto" else check_bazaar()
    except CheckError as exc:
        print(f"COULD NOT CHECK: {exc}", file=sys.stderr)
        return 2
    print(message if ok else "FAIL: " + message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
