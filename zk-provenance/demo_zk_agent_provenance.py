#!/usr/bin/env python3
"""
demo_zk_agent_provenance.py

End-to-end prototype: an AI agent produces an output, proves (ZK) that it knows
the hidden prompt+seed that derived it, and anchors the output hash into
Orphograph's REAL engine.anchor_hash() with ZERO changes to the crypto pipeline.

Run:  python3 zk-provenance/demo_zk_agent_provenance.py
(No network, no dependencies beyond stdlib + the local repo's server/engine.py)
"""
from __future__ import annotations
import sys, os, json
from pathlib import Path

# Make the local repo importable so we call the REAL anchor engine.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# MUST be set BEFORE importing server.engine: DATA_DIR/RECEIPTS_DIR are
# resolved at import time (engine.py:25) — setting it later silently writes
# demo receipts into the repo's real data/ tree.
os.environ["ORPHO_DATA_DIR"] = "/tmp/orpho_zk_demo"
import zk_provenance as zk

# No monkeypatch needed anymore: the engine now has a dedicated zk_proof
# kwarg -> zk_provenance receipt field with its own strict sanitizer
# (engine._sanitize_zk_provenance). attestation stays human-claims-only.
import server.engine as engine


def agent_run(model_id: str, prompt: str, seed: str):
    """The 'agent'. Returns its output + a ZK provenance proof."""
    output, proof = zk.prove(model_id, prompt, seed)
    return output, proof


def main():
    print("=" * 70)
    print("ORPHOGRAPH ZK AGENT-PROVENANCE — PROTOTYPE")
    print("=" * 70)

    # 1) Agent produces an output and a ZK proof of derivation.
    model_id = "gpt-class-v3"
    prompt   = "Summarize the Q2 earnings call, focus on margins"
    seed     = "seed-4417"   # hidden
    output, proof = agent_run(model_id, prompt, seed)
    print(f"\n[1] AGENT ran model={model_id}")
    print(f"    output      = {output}")
    print(f"    anchored C  = {proof.output_hash}   (SHA-256 of output)")
    print(f"    hidden P,S  = '{prompt}' / '{seed}'  -> NEVER leaves agent")

    # 2) Verify the proof WITHOUT the prompt/seed (the whole point).
    check = zk.verify(proof)
    print(f"\n[2] ZK VERIFY (no prompt/seed available): {check['valid']}")
    assert check["valid"], "ZK proof failed"

    # 3) Anchor via the REAL engine — payload Orphograph already accepts.
    payload = zk.build_anchor_payload(proof, label="agent-output")
    # engine.anchor_hash reads ORPHO_DATA_DIR / data/receipts; force a temp dir.
    os.environ["ORPHO_DATA_DIR"] = str(Path("/tmp/orpho_zk_demo"))
    receipt = engine.anchor_hash(
        hash_hex=payload["hash_hex"],
        client_label=payload["client_label"],
        zk_proof=payload["zk_proof"],
    )
    rid = receipt["receipt_id"]
    print(f"\n[3] ANCHORED via real engine. receipt_id={rid}")
    print(f"    calendars_ok = {receipt['calendars_ok']}/{receipt['calendars_total']}")
    print(f"    receipt carries zk_provenance.proof_type = "
          f"{receipt.get('zk_provenance', {}).get('proof_type')}")
    print(f"    (output hash is now commit-ready for Bitcoin via OpenTimestamps)")

    # 4) Independent re-verify straight from the persisted receipt file.
    recheck = engine.verify_receipt(rid)
    print(f"\n[4] RE-VERIFY from persisted receipt (anyone, later): "
          f"{recheck['found']}")
    stored_proof = zk.ProvenanceProof(
        **{k: recheck["zk_provenance"][k]
           for k in ("model_id", "output_hash", "commitment", "A", "s1", "s2", "challenge")}
    ) if recheck.get("zk_provenance", {}).get("proof_type") else None
    if stored_proof:
        again = zk.verify(stored_proof)
        print(f"    ZK proof still valid from cold storage: {again['valid']}")
        assert again["valid"]

    # 5) Honesty: show what this prototype does NOT yet prove.
    print(f"\n[5] HONESTY GAP (must be stated in any novel claim):")
    print(f"    - Proof shows KNOWLEDGE of committed inputs (P,S,M), not that")
    print(f"      PROGRAM(M,P,S) actually produced O. Closing that = SNARK.")
    print(f"    - Today's 'program' is a toy hash; the SNARK circuit targets")
    print(f"      the same wire format (commitment, proof, output_hash, model_id).")

    print(f"\nRECEIPT PATH: /tmp/orpho_zk_demo/receipts/{rid}/receipt.json")
    print("=" * 70)


if __name__ == "__main__":
    main()
