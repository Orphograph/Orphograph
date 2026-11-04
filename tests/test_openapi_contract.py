#!/usr/bin/env python3
"""test_openapi_contract.py — /openapi.json must describe the server that serves it.

Added 2026-11-04 with the file. An agent reads the contract and calls what it
says, so a documented path that 404s, a required response field the server
does not send, or a request field the server ignores is a wire defect, not a
docs nit. Everything here drives the real HTTP server (tests/_srv.py,
calendars stubbed); nothing asserts on a value this file built itself.

Each checker returns a list of problems, and every checker is run once
against a planted mutation of the spec to prove it can fire. A checker that
cannot report a problem is not evidence that there is none.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import _srv

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "web" / "openapi.json"

# Words a proof-of-existence contract must not put in an agent's mouth.
FORBIDDEN = ("authorship", "admissib", "tamper-proof", "legally binding",
             "guarantee", "xrp", "xrpl", "ripple")


def _schema(spec: dict, name: str) -> dict:
    return spec["components"]["schemas"][name]


def _anchor_request_fields_in_code() -> set[str]:
    """The field names _anchor_input_error() type-checks, read from the AST
    of server/app.py (semantic, not a regex), plus the mandatory hash_hex."""
    tree = ast.parse((ROOT / "server" / "app.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_anchor_input_error":
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Assign) and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Name)
                        and sub.targets[0].id == "types"
                        and isinstance(sub.value, ast.Dict)):
                    return {k.value for k in sub.value.keys} | {"hash_hex"}
    raise AssertionError("_anchor_input_error / its `types` dict not found in app.py")


def _x402_default_network() -> str:
    spec = importlib.util.spec_from_file_location("x402_under_test", ROOT / "server" / "x402.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.NETWORK_BASE_SEPOLIA


# ---------------------------------------------------------------- checkers

def check_claims(spec: dict) -> list[str]:
    text = json.dumps(spec).lower()
    return [f"forbidden claim word {w!r}" for w in FORBIDDEN if w in text]


def check_request_fields(spec: dict, code_fields: set[str]) -> list[str]:
    documented = set(_schema(spec, "AnchorRequest")["properties"])
    out = [f"documented but not read by the server: {f}" for f in sorted(documented - code_fields)]
    out += [f"read by the server but undocumented: {f}" for f in sorted(code_fields - documented)]
    return out


def check_required(spec: dict, schema_name: str, body: dict) -> list[str]:
    return [f"{schema_name}.{f} documented as required, absent on the wire"
            for f in _schema(spec, schema_name)["required"] if f not in body]


def check_paths(spec: dict, base: str, rid: str) -> list[str]:
    """Every documented GET path answers 200 on the real server."""
    out = []
    for path, ops in spec["paths"].items():
        if "get" not in ops:
            continue
        code, _, _ = _srv.request(base, path.replace("{receipt_id}", rid), timeout=15)
        if code != 200:
            out.append(f"GET {path} -> {code}")
    return out


class TestOpenapiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        bases, procs, logs = _srv.spin(Path(cls._tmp.name), stub_calendars=True)
        _srv.wait_ready(bases, procs, logs)
        cls._handle, cls.base = (procs, logs), bases[0]
        code, raw, headers = _srv.request(cls.base, "/openapi.json", timeout=15)
        cls.served = (code, raw, headers)
        cls.spec = json.loads(raw) if code == 200 else json.loads(SPEC_PATH.read_text())
        example = cls.spec["paths"]["/api/anchor"]["post"]["requestBody"]["content"][
            "application/json"]["example"]
        cls.anchor_code, cls.anchor = _srv.anchor(cls.base, example, timeout=15)
        cls.rid = cls.anchor.get("receipt_id", "missing")

    @classmethod
    def tearDownClass(cls):
        _srv._kill_all(*cls._handle)
        cls._tmp.cleanup()

    # -- served, and served the same way to HEAD
    def test_served_as_json_and_matches_the_file(self):
        code, raw, headers = self.served
        self.assertEqual(code, 200, "/openapi.json is not served")
        self.assertTrue(headers.get("Content-Type", "").startswith("application/json"))
        self.assertEqual(raw, SPEC_PATH.read_bytes())
        self.assertTrue(self.spec["openapi"].startswith("3.1"))

    def test_head_matches_get(self):
        code, _, headers = _srv.request(self.base, "/openapi.json", "HEAD", timeout=15)
        _, _, get_headers = self.served
        self.assertEqual(code, 200)
        for h in ("Content-Type", "Content-Length", "Strict-Transport-Security",
                  "Content-Security-Policy"):
            self.assertEqual(headers.get(h), get_headers.get(h), h)

    def test_llms_txt_links_the_contract(self):
        code, raw, _ = _srv.request(self.base, "/llms.txt", timeout=15)
        self.assertEqual(code, 200)
        self.assertIn(b"https://orphograph.com/openapi.json", raw)

    # -- the contract against the wire
    def test_documented_example_anchors(self):
        self.assertEqual(self.anchor_code, 200, self.anchor)
        self.assertEqual(check_required(self.spec, "AnchorResponse", self.anchor), [])

    def test_verify_and_receipt_carry_required_fields(self):
        for path in (f"/api/verify/{self.rid}", f"/api/receipt/{self.rid}"):
            body = _srv.ok_json(*_srv.get_json(self.base, path, timeout=15))
            self.assertEqual(check_required(self.spec, "VerifyResponse", body), [], path)

    def test_every_documented_get_path_answers(self):
        self.assertEqual(check_paths(self.spec, self.base, self.rid), [])

    def test_unknown_receipt_is_404_as_documented(self):
        code, _, _ = _srv.request(self.base, "/api/verify/no-such-receipt", timeout=15)
        self.assertEqual(code, 404)

    def test_request_fields_match_the_server_both_ways(self):
        self.assertEqual(check_request_fields(self.spec, _anchor_request_fields_in_code()), [])

    def test_every_documented_field_is_read_on_the_wire(self):
        """A wrong-typed value for each documented field must be refused by
        name — proof the server reads it, through the real entry point."""
        wrong = {"string": 12345, "boolean": "yes", "object": "not-an-object"}
        props = _schema(self.spec, "AnchorRequest")["properties"]
        for field, sch in props.items():
            body = {"hash_hex": "cd" * 32, field: wrong[sch["type"]]}
            code, rec = _srv.anchor(self.base, body, timeout=15)
            self.assertEqual(code, 400, (field, rec))
            self.assertIn(field, rec.get("error", ""), (field, rec))

    def test_x402_network_and_claims(self):
        desc = self.spec["paths"]["/api/anchor"]["post"]["description"]
        self.assertIn(_x402_default_network(), desc)
        self.assertIn("TEST network", desc)
        self.assertEqual(check_claims(self.spec), [])

    # -- negative controls: each checker must fire on a planted defect
    def test_controls_every_checker_can_fire(self):
        bad = copy.deepcopy(self.spec)
        bad["info"]["description"] += " Courts accept it as authorship evidence."
        self.assertTrue(check_claims(bad))

        bad = copy.deepcopy(self.spec)
        _schema(bad, "AnchorRequest")["properties"]["invented_field"] = {"type": "string"}
        self.assertTrue(check_request_fields(bad, _anchor_request_fields_in_code()))
        bad = copy.deepcopy(self.spec)
        del _schema(bad, "AnchorRequest")["properties"]["zk_proof"]
        self.assertTrue(check_request_fields(bad, _anchor_request_fields_in_code()))

        bad = copy.deepcopy(self.spec)
        _schema(bad, "AnchorResponse")["required"].append("not_sent_by_server")
        self.assertTrue(check_required(bad, "AnchorResponse", self.anchor))

        bad = copy.deepcopy(self.spec)
        bad["paths"]["/api/not-a-route"] = {"get": {}}
        self.assertTrue(check_paths(bad, self.base, self.rid))


if __name__ == "__main__":
    unittest.main()
