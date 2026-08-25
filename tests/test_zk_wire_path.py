"""test_zk_wire_path.py

The zk proof must survive the REAL request path, not just the engine
(wire-path pin added 2026-08-25 during the audit of backlog item A).

This is pinned because it has broken before: `/api/anchor` silently dropped
`zk_proof` on the wire for as long as the field had existed, while every
engine-level test stayed green. Engine-level green says nothing about whether
a field survives the request, the size cap, the sanitizer, the persistence
layer, and the response allowlist.

zk-provenance/test_zk_provenance.py covers the crypto. This covers the seam:

    zk_provenance.prove()  →  POST /api/anchor  →  GET /api/receipt/<id>
                           →  dist/orphograph-verify/verify_zk.py

Verified manually 2026-08-25 end to end, exit 0, including four negative
controls (tampered s1, tampered challenge, swapped model_id, wrong output
hash) that each correctly exit 1.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ZK_DIR = REPO_ROOT / "zk-provenance"
VERIFY_ZK = REPO_ROOT / "dist" / "orphograph-verify" / "verify_zk.py"


@pytest.fixture(scope="module")
def zk():
    """Import the generator by path — it is out of tree and its package name
    must not leak into sys.modules for the rest of the run."""
    spec = importlib.util.spec_from_file_location(
        "zk_provenance_wire", ZK_DIR / "zk_provenance.py")
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves __module__ through sys.modules, so the module must be
    # registered BEFORE exec. The name is deliberately unique: CI runs
    # zk-provenance/ last because that suite mutates sys.modules, and this pin
    # must not join that fight.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("zk_wire_data")
    port = _free_port()
    env = {**os.environ, "PORT": str(port), "HOST": "127.0.0.1",
           "ORPHO_DATA_DIR": str(data_dir), "ORPHO_COOKIE_SECURE": "0"}
    env.pop("RESEND_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


@pytest.fixture(scope="module")
def anchored(zk, server):
    _out, proof = zk.prove(model_id="claude-opus-5",
                           prompt="wire-path pin", seed="deadbeef")
    payload = zk.build_anchor_payload(proof, label="wire-path pin")
    resp = _post_json(server + "/api/anchor", payload)
    return proof, payload, resp


def test_anchor_response_carries_the_proof(anchored):
    """It must not be swallowed by the response allowlist."""
    _proof, _payload, resp = anchored
    assert resp.get("receipt_id"), resp
    assert resp.get("zk_provenance"), "zk_provenance missing from /api/anchor response"


def test_proof_survives_to_the_receipt_endpoint(anchored, server):
    """THE PIN. This is the exact hop that silently dropped the field before."""
    proof, _payload, resp = anchored
    rec = _get_json(f"{server}/api/receipt/{resp['receipt_id']}")
    z = rec.get("zk_provenance")
    assert z, "zk_provenance did not survive to GET /api/receipt/<id>"
    for field in ("A", "s1", "s2", "challenge", "commitment", "model_id",
                  "output_hash", "proof_type"):
        assert field in z, f"{field} lost on the wire"
    assert z["output_hash"] == proof.output_hash
    assert rec["hash_hex"] == proof.output_hash


def test_the_shipped_verifier_accepts_the_api_receipt(anchored, server, tmp_path):
    """End-to-end across TWO independent implementations: the generator's
    crypto and the standalone verifier that customers actually run."""
    _proof, _payload, resp = anchored
    rec = _get_json(f"{server}/api/receipt/{resp['receipt_id']}")
    rp = tmp_path / "receipt.json"
    rp.write_text(json.dumps(rec))
    r = subprocess.run(
        [sys.executable, str(VERIFY_ZK), "--output-hash", rec["hash_hex"],
         "--receipt", str(rp)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"shipped verifier rejected a real receipt:\n{r.stdout}\n{r.stderr}"
    assert "ZK proof VALID" in r.stdout


@pytest.mark.parametrize("field,mutate", [
    ("s1", lambda v: str(int(v) + 1)),
    ("challenge", lambda v: str(int(v) + 1)),
    ("model_id", lambda v: "not-our-model"),
])
def test_shipped_verifier_rejects_tampered_proofs(anchored, server, tmp_path, field, mutate):
    """CAN-THIS-TEST-FAIL control. Without these, the test above would pass for
    a verifier that returns 0 unconditionally — the exact defect class this
    repo has shipped twice (a chain check that read stdout and threw away the
    exit code)."""
    _proof, _payload, resp = anchored
    rec = _get_json(f"{server}/api/receipt/{resp['receipt_id']}")
    rec["zk_provenance"][field] = mutate(rec["zk_provenance"][field])
    rp = tmp_path / f"bad_{field}.json"
    rp.write_text(json.dumps(rec))
    r = subprocess.run(
        [sys.executable, str(VERIFY_ZK), "--output-hash", rec["hash_hex"],
         "--receipt", str(rp)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, f"tampered {field} was ACCEPTED (exit {r.returncode})"
