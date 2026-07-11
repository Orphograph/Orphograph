#!/usr/bin/env python3
"""anchor_prompt.py — notarized lineage for prompt files.

Each anchor produces a "prompt card": a small canonical JSON document that
contains the prompt's hash, the parent card's receipt id, and an optional
self-reported score. The CARD's hash is what gets anchored, so the lineage
claim itself is inside the notarized bytes — it cannot be edited later
without breaking the hash. Scores are the author's claim, attested at a
point in time; the receipt proves WHEN the claim was made, not that it is
true.

Usage:
    python3 anchor_prompt.py anchor <prompt_file> [--parent <receipt_id>]
                                    [--score 0.83] [--label "v12"]
    python3 anchor_prompt.py show   <prompt_file>
    python3 anchor_prompt.py verify <prompt_file>

Lineage appends to <prompt_file>.lineage.jsonl (card + server response).
Auth: --api-key / ORPHO_API_KEY, or --pack-token / ORPHO_PACK_TOKEN.
Stdlib only; contents never leave the machine, only hashes.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_BASE = "https://orphograph.com"
USER_AGENT = "OrphographPromptLineage/0.1 (+https://orphograph.com/integrations)"
HTTP_TIMEOUT_SEC = 15


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_card(prompt_sha256: str, created_utc: str, parent_receipt=None,
               parent_prompt_sha256=None, score=None, label=None) -> str:
    """Canonical prompt-card JSON. Deterministic: sorted keys, no whitespace.

    Only present fields are included so a card's hash never depends on
    which optional flags a future version adds.
    """
    card = {"version": 1, "kind": "prompt-card",
            "prompt_sha256": prompt_sha256, "created_utc": created_utc}
    if parent_receipt:
        card["parent_receipt"] = parent_receipt
    if parent_prompt_sha256:
        card["parent_prompt_sha256"] = parent_prompt_sha256
    if score is not None:
        card["score"] = float(score)
    if label:
        card["label"] = str(label)[:120]
    return json.dumps(card, sort_keys=True, separators=(",", ":"))


def post_anchor(base, sha256_hex, label=None, api_key=None, pack_token=None) -> dict:
    payload = {"hash_hex": sha256_hex}
    if label:
        payload["client_label"] = str(label)[:200]
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    if api_key:
        headers["X-Orpho-Api-Key"] = api_key
    elif pack_token:
        headers["X-Pack-Token"] = pack_token
    req = urllib.request.Request(base.rstrip("/") + "/api/anchor",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as e:
        return {"error": "http_error", "status": e.code,
                "body": e.read().decode("utf-8", errors="replace")[:400]}
    except urllib.error.URLError as e:
        return {"error": "network_error", "reason": str(getattr(e, "reason", e))}


def lineage_path(prompt_file: str) -> str:
    return prompt_file + ".lineage.jsonl"


def read_lineage(prompt_file: str) -> list:
    path = lineage_path(prompt_file)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base", default=os.environ.get("ORPHO_BASE_URL", DEFAULT_BASE))
    p.add_argument("--api-key", default=os.environ.get("ORPHO_API_KEY") or None)
    p.add_argument("--pack-token", default=os.environ.get("ORPHO_PACK_TOKEN") or None)
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("anchor")
    sp.add_argument("prompt_file")
    sp.add_argument("--parent", default=None, help="receipt id of the parent card")
    sp.add_argument("--score", type=float, default=None,
                    help="self-reported eval score (a claim, not a fact)")
    sp.add_argument("--label", default=None)
    sp.add_argument("--dry-run", action="store_true")
    sub.add_parser("show").add_argument("prompt_file")
    sub.add_parser("verify").add_argument("prompt_file")
    args = p.parse_args()
    if args.api_key and args.pack_token:
        p.error("use --api-key or --pack-token, not both")

    if args.cmd == "show":
        for i, rec in enumerate(read_lineage(args.prompt_file)):
            card = json.loads(rec["card"])
            print(f"[{i}] {card.get('created_utc')} label={card.get('label')} "
                  f"score={card.get('score')} parent={card.get('parent_receipt')} "
                  f"receipt={rec.get('response', {}).get('receipt_id')}")
        return 0

    if args.cmd == "verify":
        lineage = read_lineage(args.prompt_file)
        if not lineage:
            print(json.dumps({"error": "no_lineage"}))
            return 1
        last = lineage[-1]
        card = json.loads(last["card"])
        current = sha256_file(args.prompt_file)
        card_hash = hashlib.sha256(last["card"].encode("utf-8")).hexdigest()
        out = {"prompt_matches_last_card": current == card["prompt_sha256"],
               "current_prompt_sha256": current,
               "last_card_sha256": card_hash,
               "last_card_hash_matches_record": card_hash == last["card_sha256"],
               "last_receipt": last.get("response", {}).get("receipt_id")}
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if all(v for k, v in out.items() if isinstance(v, bool)) else 1

    # anchor
    prompt_sha = sha256_file(args.prompt_file)
    lineage = read_lineage(args.prompt_file)
    parent_prompt_sha = None
    parent_receipt = args.parent
    if lineage and not parent_receipt:
        prev = lineage[-1]
        parent_receipt = prev.get("response", {}).get("receipt_id")
        parent_prompt_sha = json.loads(prev["card"]).get("prompt_sha256")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    card = build_card(prompt_sha, created, parent_receipt=parent_receipt,
                      parent_prompt_sha256=parent_prompt_sha,
                      score=args.score, label=args.label)
    card_sha = hashlib.sha256(card.encode("utf-8")).hexdigest()
    if args.dry_run:
        print(json.dumps({"dry_run": True, "card": card, "card_sha256": card_sha}))
        return 0
    label = f"prompt-card:{args.label}" if args.label else "prompt-card"
    result = post_anchor(args.base, card_sha, label=label,
                         api_key=args.api_key, pack_token=args.pack_token)
    record = {"card": card, "card_sha256": card_sha, "response": result}
    with open(lineage_path(args.prompt_file), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
