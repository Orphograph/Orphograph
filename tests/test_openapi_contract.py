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


# The folder handler's one non-option key: a body whose top-level
# "algorithm" is the Merkle algorithm IS the raw manifest (the handler's other
# accepted form). It is documented on FolderManifest, not as an option.
_RAW_MANIFEST_MARKER = "algorithm"


def _folder_request_fields_in_code() -> set[str]:
    """Every key _anchor_folder_one() reads from the request body, as
    payload.get("k") or payload["k"], read from the AST of server/app.py."""
    tree = ast.parse((ROOT / "server" / "app.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_anchor_folder_one":
            keys = set()
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "get" and isinstance(sub.func.value, ast.Name)
                        and sub.func.value.id == "payload" and sub.args
                        and isinstance(sub.args[0], ast.Constant)):
                    keys.add(sub.args[0].value)
                if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name)
                        and sub.value.id == "payload" and isinstance(sub.slice, ast.Constant)):
                    keys.add(sub.slice.value)
            if not keys:
                raise AssertionError("_anchor_folder_one reads no payload keys?")
            return keys - {_RAW_MANIFEST_MARKER}
    raise AssertionError("_anchor_folder_one not found in app.py")


def _x402_default_network() -> str:
    spec = importlib.util.spec_from_file_location("x402_under_test", ROOT / "server" / "x402.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.NETWORK_BASE_SEPOLIA


# ---------------------------------------------------------------- checkers

def check_claims(spec: dict) -> list[str]:
    text = json.dumps(spec).lower()
    return [f"forbidden claim word {w!r}" for w in FORBIDDEN if w in text]


def check_request_fields(spec: dict, code_fields: set[str],
                         schema_name: str = "AnchorRequest") -> list[str]:
    documented = set(_schema(spec, schema_name)["properties"])
    out = [f"documented but not read by the server: {f}" for f in sorted(documented - code_fields)]
    out += [f"read by the server but undocumented: {f}" for f in sorted(code_fields - documented)]
    return out


def _resolve(spec: dict, schema: dict) -> dict:
    ref = schema.get("$ref", "")
    return _schema(spec, ref.rsplit("/", 1)[-1]) if ref else schema


def _check_required_in(spec: dict, schema: dict, body: dict, where: str) -> list[str]:
    """Required fields of `schema` present in `body`, and the same, one level
    at a time, for every nested object the body carries."""
    schema = _resolve(spec, schema)
    out = [f"{where}.{f} documented as required, absent on the wire"
           for f in schema.get("required", []) if f not in body]
    for name, sub in schema.get("properties", {}).items():
        if isinstance(body.get(name), dict):
            out += _check_required_in(spec, sub, body[name], f"{where}.{name}")
    return out


def check_required(spec: dict, schema_name: str, body: dict) -> list[str]:
    return _check_required_in(spec, _schema(spec, schema_name), body, schema_name)


def _required_query(op: dict) -> str:
    """The query string an operation documents as required, built from each
    required query parameter's own documented example. A required parameter
    with no example cannot be called from the contract: reported as missing
    (the request goes out without it and must then fail)."""
    pairs = [f"{p['name']}={p['example']}" for p in op.get("parameters", [])
             if p.get("in") == "query" and p.get("required") and "example" in p]
    return ("?" + "&".join(pairs)) if pairs else ""


def check_paths(spec: dict, base: str, rid: str, rids: dict | None = None) -> list[str]:
    """Every documented GET path answers 200 on the real server, called the
    way the contract documents it (required query parameters included).
    `rids` names the receipt a path needs when the single-anchor `rid` is
    the wrong kind for it (a folder receipt for /api/verify_folder)."""
    out = []
    for path, ops in spec["paths"].items():
        if "get" not in ops:
            continue
        target = (path.replace("{receipt_id}", (rids or {}).get(path, rid))
                  + _required_query(ops["get"]))
        code, _, _ = _srv.request(base, target, timeout=15)
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
        folder_example = cls.spec["paths"]["/api/anchor_folder"]["post"]["requestBody"][
            "content"]["application/json"]["example"]
        cls.folder_code, cls.folder = _srv.post_json(
            cls.base, "/api/anchor_folder", folder_example, timeout=15)
        cls.folder_rid = cls.folder.get("receipt_id", "missing")
        cls.rids = {"/api/verify_folder/{receipt_id}": cls.folder_rid}

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

    def test_verify_output_carries_required_fields(self):
        op = self.spec["paths"]["/api/verify_output/{receipt_id}"]["get"]
        anchored = self.anchor.get("hash_hex", "")
        for sha, match in ((anchored, True), ("0" * 64, False)):
            body = _srv.ok_json(*_srv.get_json(
                self.base, f"/api/verify_output/{self.rid}?sha256={sha}", timeout=15))
            self.assertEqual(check_required(self.spec, "VerifyOutputResponse", body), [], sha)
            self.assertIs(body["match"], match)
        # The documented example is a real call, and the 400 is documented.
        self.assertIn("400", op["responses"])
        code, _, _ = _srv.request(self.base, f"/api/verify_output/{self.rid}", timeout=15)
        self.assertEqual(code, 400, "sha256 is documented as required")

    def test_every_documented_get_path_answers(self):
        self.assertEqual(check_paths(self.spec, self.base, self.rid, self.rids), [])

    # -- folders: POST /api/anchor_folder, GET /api/verify_folder/{id}
    def test_documented_folder_example_anchors(self):
        self.assertEqual(self.folder_code, 200, self.folder)
        self.assertEqual(check_required(self.spec, "FolderAnchorResponse", self.folder), [])
        self.assertEqual(self.folder["kind"], "folder")

    def test_verify_folder_carries_required_fields(self):
        body = _srv.ok_json(*_srv.get_json(
            self.base, f"/api/verify_folder/{self.folder_rid}", timeout=15))
        self.assertEqual(check_required(self.spec, "VerifyFolderResponse", body), [])
        # The example asked for public paths, so they are on the wire.
        self.assertEqual([l.get("path") for l in body["manifest"]["leaves"]],
                         ["step-000001", "step-000002"])
        op = self.spec["paths"]["/api/verify_folder/{receipt_id}"]["get"]
        code, _, _ = _srv.request(self.base, f"/api/verify_folder/{self.rid}", timeout=15)
        self.assertEqual(code, 400, "a single anchor on the folder route")
        self.assertIn("400", op["responses"])
        code, _, _ = _srv.request(self.base, "/api/verify_folder/no-such-receipt", timeout=15)
        self.assertEqual(code, 404)
        self.assertIn("404", op["responses"])

    def test_folder_request_fields_match_the_server_both_ways(self):
        self.assertEqual(check_request_fields(self.spec, _folder_request_fields_in_code(),
                                              "FolderAnchorRequest"), [])
        # The raw-manifest form's marker is documented where it lives.
        self.assertIn(_RAW_MANIFEST_MARKER, _schema(self.spec, "FolderManifest")["properties"])

    def test_every_documented_folder_field_is_read_on_the_wire(self):
        """A wrong-typed value for each documented option is refused by name.
        `manifest` is sent as a string: the body then carries no manifest."""
        wrong = {"string": 12345, "boolean": "yes"}
        example = self.spec["paths"]["/api/anchor_folder"]["post"]["requestBody"][
            "content"]["application/json"]["example"]
        props = _schema(self.spec, "FolderAnchorRequest")["properties"]
        for field, sch in props.items():
            bad_value = "not-a-manifest" if field == "manifest" else wrong[sch["type"]]
            body = {**example, field: bad_value}
            code, rec = _srv.post_json(self.base, "/api/anchor_folder", body, timeout=15)
            self.assertEqual(code, 400, (field, rec))
            self.assertIn(field, rec.get("error", ""), (field, rec))

    def test_stats_carries_the_documented_counters(self):
        body = _srv.ok_json(*_srv.get_json(self.base, "/api/stats", timeout=15))
        self.assertEqual(check_required(self.spec, "StatsResponse", body), [])
        # The folder example is an agent run (step-NNNNNN paths) and its
        # label starts with "agent-", as does the single anchor's example.
        self.assertGreaterEqual(body["anchors"]["run_anchors"], 1)
        self.assertGreaterEqual(body["anchors"]["agent_label_anchors"], 2)

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
        # Production runs mainnet (set by Fly secrets, 2026-11-05), so the
        # contract names it, and never the test network an agent would
        # discount as play money.
        self.assertIn("eip155:8453", desc)
        self.assertNotIn("TEST network", desc)
        self.assertNotIn("84532", desc)
        self.assertIn("1 cent", desc)
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
        self.assertTrue(check_paths(bad, self.base, self.rid, self.rids))
        # The folder route called with a single anchor's id is not a 200.
        self.assertTrue(check_paths(self.spec, self.base, self.rid))

        bad = copy.deepcopy(self.spec)
        _schema(bad, "FolderAnchorRequest")["properties"]["invented_field"] = {"type": "string"}
        self.assertTrue(check_request_fields(bad, _folder_request_fields_in_code(),
                                             "FolderAnchorRequest"))
        bad = copy.deepcopy(self.spec)
        del _schema(bad, "FolderAnchorRequest")["properties"]["paths_public"]
        self.assertTrue(check_request_fields(bad, _folder_request_fields_in_code(),
                                             "FolderAnchorRequest"))
        bad = copy.deepcopy(self.spec)
        _schema(bad, "FolderAnchorResponse")["required"].append("not_sent_by_server")
        self.assertTrue(check_required(bad, "FolderAnchorResponse", self.folder))
        # Nested: a required field inside an object the body carries.
        bad = copy.deepcopy(self.spec)
        _schema(bad, "StatsResponse")["properties"]["anchors"]["required"].append("nope")
        stats = _srv.ok_json(*_srv.get_json(self.base, "/api/stats", timeout=15))
        self.assertTrue(check_required(bad, "StatsResponse", stats))
        # A required query parameter the contract cannot fill is called
        # without it, and the route must then answer non-200.
        bad = copy.deepcopy(self.spec)
        for prm in bad["paths"]["/api/verify_output/{receipt_id}"]["get"]["parameters"]:
            prm.pop("example", None)
        self.assertTrue(check_paths(bad, self.base, self.rid))

        bad = copy.deepcopy(self.spec)
        _schema(bad, "VerifyOutputResponse")["required"].append("not_sent_by_server")
        body = _srv.ok_json(*_srv.get_json(
            self.base, f"/api/verify_output/{self.rid}?sha256={'0' * 64}", timeout=15))
        self.assertTrue(check_required(bad, "VerifyOutputResponse", body))


if __name__ == "__main__":
    unittest.main()
