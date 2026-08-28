"""test_snark_wire_path.py

The snark-exec-v1 proof must survive the REAL request path, not just the
engine (wire-path pin added 2026-08-28, closing audit backlog item C:
"prove the existing path works before any promotion").

tests/test_snark_receipt.py covers the engine and the sanitizer's bindings.
This covers the seam that has broken before for the sibling schnorr proof
(`/api/anchor` silently dropped `zk_proof` on the wire while every
engine-level test stayed green — see tests/test_zk_wire_path.py):

    build_snark_anchor_payload(committed 8-round groth16 evidence)
        -> POST /api/anchor -> GET /api/receipt/<id>
        -> dist/orphograph-verify/verify_snark.py

Verdicts come from EXIT CODES, never stdout. verify_snark.py's contract:
0 = bindings AND groth16 pairing check verified · 1 = a check failed ·
5 = INCOMPLETE, bindings hold but the pairing check never ran (no --vk or
no snarkjs). 5 is never a pass, and this file asserts it exactly where it
is the correct verdict rather than treating it as one.

The pairing-check tests need snarkjs (node). Where it is unavailable (CI)
they SKIP — visibly, not silently: the binding tests and both wire-survival
tests still run there, and the tampered-stN control proves the verifier can
reject without snarkjs. Verified locally 2026-08-28 with snarkjs on PATH:
exit 0 on the real API receipt, exit 1 on tampered pi_a (only the pairing
check can catch pi_a — the hash bindings do not cover it).
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

import _srv

REPO_ROOT = Path(__file__).resolve().parent.parent
ZK_DIR = REPO_ROOT / "zk-provenance"
EVIDENCE = ZK_DIR / "snark" / "evidence_8round_2026_08_04"
VERIFY_SNARK = REPO_ROOT / "dist" / "orphograph-verify" / "verify_snark.py"
MODEL_ID = "gpt-class-v3"  # the id the committed evidence's st0 binds to

HAVE_SNARKJS = shutil.which("snarkjs") is not None

pytestmark = pytest.mark.skipif(
    not (EVIDENCE / "proof.json").exists(),
    reason="committed 8-round evidence not present")


@pytest.fixture(scope="module")
def zk():
    """Import the generator by path — its package name must not leak into
    sys.modules for the rest of the run (same isolation as test_zk_wire_path)."""
    spec = importlib.util.spec_from_file_location(
        "zk_provenance_snark_wire", ZK_DIR / "zk_provenance.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("snark_wire_data")
    yield from _srv.server_processes(data_dir, stub_calendars=True)


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def _run_verifier(receipt_path: Path, vk: bool) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(VERIFY_SNARK), str(receipt_path)]
    if vk:
        cmd += ["--vk", str(EVIDENCE / "verification_key.json")]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


@pytest.fixture(scope="module")
def anchored(zk, server):
    payload = zk.build_snark_anchor_payload(
        MODEL_ID,
        EVIDENCE / "proof.json",
        EVIDENCE / "public.json",
        EVIDENCE / "verification_key.json",
        label="snark wire-path pin",
    )
    resp = _post_json(server + "/api/anchor", payload)
    return payload, resp


@pytest.fixture()
def receipt(anchored, server, tmp_path):
    _payload, resp = anchored
    rec = _get_json(f"{server}/api/receipt/{resp['receipt_id']}")
    rp = tmp_path / "receipt.json"
    rp.write_text(json.dumps(rec))
    return rec, rp, tmp_path


def test_anchor_response_carries_the_proof(anchored):
    """It must not be swallowed by the response allowlist."""
    _payload, resp = anchored
    assert resp.get("receipt_id"), resp
    assert resp.get("zk_provenance"), "zk_provenance missing from /api/anchor response"


def test_proof_survives_to_the_receipt_endpoint(anchored, server):
    """THE PIN — the exact hop that silently dropped the schnorr field before."""
    payload, resp = anchored
    rec = _get_json(f"{server}/api/receipt/{resp['receipt_id']}")
    z = rec.get("zk_provenance")
    assert z, "zk_provenance did not survive to GET /api/receipt/<id>"
    for field in ("proof_type", "output_hash", "model_id", "program", "protocol",
                  "curve", "vk_sha256", "stN_hex", "commitment_hex", "st0_hex",
                  "proof"):
        assert field in z, f"{field} lost on the wire"
    expected = json.loads((EVIDENCE / "expected.json").read_text())
    assert z["stN_hex"] == expected["stN_hex"]
    assert z["commitment_hex"] == expected["commitment_hex"]
    assert z["st0_hex"] == expected["st0_hex"]
    assert z["output_hash"] == payload["hash_hex"]
    assert rec["hash_hex"] == payload["hash_hex"]


def test_bindings_hold_but_no_vk_is_incomplete_never_pass(receipt):
    """Without --vk the ONLY correct verdict is 5: bindings hold, proof
    unverified. Anything else — 0 especially — is the defect."""
    _rec, rp, _tmp = receipt
    r = _run_verifier(rp, vk=False)
    assert r.returncode == 5, (
        f"expected 5 INCOMPLETE without --vk, got {r.returncode}:\n{r.stdout}\n{r.stderr}")


def test_verifier_rejects_tampered_stn_without_snarkjs(receipt):
    """CAN-THIS-TEST-FAIL control that runs everywhere: a flipped stN_hex
    breaks the output_hash binding, which is pure hashing — no snarkjs
    needed to reject it."""
    rec, _rp, tmp = receipt
    z = rec["zk_provenance"]
    z["stN_hex"] = ("0" if z["stN_hex"][0] != "0" else "1") + z["stN_hex"][1:]
    bad = tmp / "bad_stn.json"
    bad.write_text(json.dumps(rec))
    r = _run_verifier(bad, vk=False)
    assert r.returncode == 1, (
        f"tampered stN_hex was ACCEPTED (exit {r.returncode}):\n{r.stdout}\n{r.stderr}")


@pytest.mark.skipif(not HAVE_SNARKJS, reason="snarkjs not on PATH")
def test_shipped_verifier_fully_accepts_the_api_receipt(receipt):
    """End to end including the groth16 pairing check: exit 0, nothing less."""
    _rec, rp, _tmp = receipt
    r = _run_verifier(rp, vk=True)
    assert r.returncode == 0, (
        f"shipped verifier rejected a real receipt (exit {r.returncode}):\n{r.stdout}\n{r.stderr}")


@pytest.mark.skipif(not HAVE_SNARKJS, reason="snarkjs not on PATH")
def test_shipped_verifier_rejects_tampered_pi_a(receipt):
    """Only the pairing check can catch a mutated pi_a — the hash bindings
    do not cover it. This is the control that proves the pairing check RAN."""
    rec, _rp, tmp = receipt
    rec["zk_provenance"]["proof"]["pi_a"][0] = str(
        int(rec["zk_provenance"]["proof"]["pi_a"][0]) + 1)
    bad = tmp / "bad_pi_a.json"
    bad.write_text(json.dumps(rec))
    r = _run_verifier(bad, vk=True)
    assert r.returncode == 1, (
        f"tampered pi_a was ACCEPTED (exit {r.returncode}):\n{r.stdout}\n{r.stderr}")
