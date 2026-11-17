"""test_xrpl_pay_server.py — the XRPL rail's HTTP routes on a real server process.

The server is tests/_srv.py's; its XRPL nodes are two tests/_xrpl_fake.FakeNode
instances in this process, on loopback (the rail allows plain http only to
loopback). Nothing reaches the real XRP Ledger.

Covered here: a disarmed rail is indistinguishable from an unknown route
(404) and reports armed=false; a misconfigured rail counts its problems and
stays off; the armed routes keep the app's content-type gate, security
headers, email rule, id templating in the access log and HEAD-is-read-only;
a paid invoice credits exactly once and never echoes the email or claim
code; a node failure answers UNAVAILABLE with nothing changed.
"""
from __future__ import annotations

import json

import pytest

import _srv
from _xrpl_fake import ISSUER, MERCHANT, FakeNode, payment

PROBE_ID = "xr_ProbeInvoiceId01"


def _health(base):
    code, body = _srv.get_json(base, "/api/health")
    assert code == 200
    return body["xrpl"]


def _ledger(data_dir):
    p = data_dir / "credit_ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# --------------------------------------------------------------- disarmed

@pytest.fixture(scope="module")
def off(tmp_path_factory):
    yield from _srv.server_processes(tmp_path_factory.mktemp("xrpl_off"))


@pytest.fixture(scope="module")
def misconfigured(tmp_path_factory):
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("xrpl_bad"),
        ORPHO_XRPL_ADDRESS=MERCHANT,
        ORPHO_XRPL_RLUSD_ISSUER="rNotARealIssuerAddress",
        ORPHO_XRPL_RPC="http://127.0.0.1:1/,http://localhost:1/")


def _routes_absent(base):
    code_get, _, _ = _srv.request(base, f"/api/xrpl/invoice/{PROBE_ID}")
    unknown_get, _, _ = _srv.request(base, "/api/xrpl/not-a-route")
    assert code_get == unknown_get == 404
    code_post, _ = _srv.post_json(base, "/api/xrpl/invoice",
                                  {"plan": "writer_pack", "email": "a@example.com"})
    unknown_post, _ = _srv.post_json(base, "/api/xrpl/not-a-route", {})
    assert code_post == unknown_post == 404


def test_rail_off_is_404_and_health_says_unarmed(off):
    _routes_absent(off)
    assert _health(off) == {"armed": False, "config_problems": 0, "min_agree": None,
                            "nodes": None, "nodes_blocked": 0}


def test_misconfigured_rail_counts_problems_and_stays_off(misconfigured):
    _routes_absent(misconfigured)
    h = _health(misconfigured)
    assert h["armed"] is False and h["config_problems"] >= 1
    assert MERCHANT not in json.dumps(h)


# ------------------------------------------------------------------ armed

@pytest.fixture(scope="module")
def nodes():
    a, b = FakeNode("127.0.0.1"), FakeNode("localhost")
    yield a, b
    a.close()
    b.close()


@pytest.fixture(scope="module")
def armed(tmp_path_factory, nodes):
    a, b = nodes
    data_dir = tmp_path_factory.mktemp("xrpl_on")
    gen = _srv.server_processes(
        data_dir,
        ORPHO_XRPL_ADDRESS=MERCHANT,
        ORPHO_XRPL_RLUSD_ISSUER=ISSUER,
        ORPHO_XRPL_RPC=f"{a.url},{b.url}",
        # Many invoices from one address in one module: the checkout bucket
        # is not what these tests are about.
        CHECKOUT_RATE_PER_HOUR="1000")
    base = next(gen)
    yield base, data_dir, a, b
    gen.close()


@pytest.fixture(autouse=True)
def _reset_nodes(request):
    if "armed" in request.fixturenames:
        _base, _dir, a, b = request.getfixturevalue("armed")
        for n in (a, b):
            n.mode = None
            n.entries.clear()
            n.overrides.clear()


def _create(base, plan="writer_pack", email="buyer@example.com"):
    code, body = _srv.post_json(base, "/api/xrpl/invoice", {"plan": plan, "email": email})
    assert code == 200, body
    return body


def _status(base, invoice_id):
    code, raw, headers = _srv.request(base, f"/api/xrpl/invoice/{invoice_id}")
    return code, json.loads(raw), headers, raw


def test_armed_health(armed):
    base, *_ = armed
    h = _health(base)
    assert h["armed"] is True and h["config_problems"] == 0
    assert (h["min_agree"], h["nodes"]) == (2, 2)


def test_create_returns_payment_instructions(armed):
    base, *_ = armed
    code, raw, headers = _srv.request(
        base, "/api/xrpl/invoice", "POST",
        json.dumps({"plan": "pack_50", "email": "buyer@example.com"}).encode(),
        {"Content-Type": "application/json"})
    assert code == 200
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("Content-Security-Policy")
    body = json.loads(raw)
    pay = body["pay"]
    assert pay["destination"] == MERCHANT and 1 <= pay["destination_tag"] < 2**32
    assert pay["amount"] == {"currency": "524C555344" + "0" * 30, "issuer": ISSUER, "value": "20"}
    assert body["credits"] == 200 and body["status_url"] == f"/api/xrpl/invoice/{body['invoice_id']}"
    assert "buyer@example.com" not in raw.decode()


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_create_keeps_the_content_type_gate(armed, ctype):
    base, *_ = armed
    headers = {"Content-Type": ctype} if ctype else {}
    code, _, _ = _srv.request(base, "/api/xrpl/invoice", "POST",
                              b'{"plan":"writer_pack","email":"a@example.com"}', headers)
    assert code == 415


@pytest.mark.parametrize("payload, status", [
    ({"plan": "pack_9999", "email": "a@example.com"}, 400),
    ({"plan": "writer_pack"}, 400),
    ({"plan": "writer_pack", "email": "not-an-email"}, 400),
    ({"plan": "writer_pack", "email": "\u00c9lise@example.com"}, 400),   # needs lowercase
])
def test_create_refuses_bad_input(armed, payload, status):
    base, *_ = armed
    code, _ = _srv.post_json(base, "/api/xrpl/invoice", payload)
    assert code == status


def test_create_refuses_non_object_and_bad_json(armed):
    base, *_ = armed
    for raw in (b"[1,2]", b"{nope"):
        code, _, _ = _srv.request(base, "/api/xrpl/invoice", "POST", raw,
                                  {"Content-Type": "application/json"})
        assert code == 400


def test_status_before_payment(armed):
    base, *_ = armed
    inv = _create(base)
    code, body, headers, _ = _status(base, inv["invoice_id"])
    assert code == 200 and headers.get("X-Content-Type-Options") == "nosniff"
    assert (body["status"], body["credited"], body["verification"]) == ("awaiting_payment", False, "checked")


def test_paid_invoice_credits_once_and_leaks_nothing(armed):
    base, data_dir, a, b = armed
    inv = _create(base, email="payer@example.com")
    entry = payment(1, inv["pay"]["destination_tag"])
    a.entries.append(entry)
    b.entries.append(entry)
    code, body, _, raw = _status(base, inv["invoice_id"])
    assert code == 200, body
    assert (body["status"], body["credited"], body["credits"]) == ("paid", True, 25)
    text = raw.decode()
    assert "payer@example.com" not in text and "pk_" not in text
    rows = [r for r in _ledger(data_dir) if r["source"].endswith(inv["invoice_id"])]
    assert len(rows) == 1 and rows[0]["credits_delta"] == 25
    # Polled again: still credited once, and no node is asked.
    calls = a.calls() + b.calls()
    _code, again, _, _ = _status(base, inv["invoice_id"])
    assert again["credited"] and again["verification"] == "skipped"
    assert a.calls() + b.calls() == calls
    assert len([r for r in _ledger(data_dir) if r["source"].endswith(inv["invoice_id"])]) == 1


def test_partial_payment_is_underpaid(armed):
    base, data_dir, a, b = armed
    inv = _create(base)
    big = {"currency": "524C555344" + "0" * 30, "issuer": ISSUER, "value": "1000"}
    entry = payment(2, inv["pay"]["destination_tag"], "0.01", deliver_max=big)
    a.entries.append(entry)
    b.entries.append(entry)
    _code, body, _, _ = _status(base, inv["invoice_id"])
    assert (body["status"], body["credited"], body["manual_review"]) == ("underpaid", False, True)
    assert not [r for r in _ledger(data_dir) if r["source"].endswith(inv["invoice_id"])]


def test_node_failure_is_unavailable_and_changes_nothing(armed):
    base, data_dir, a, b = armed
    inv = _create(base)
    entry = payment(3, inv["pay"]["destination_tag"])
    a.entries.append(entry)
    b.entries.append(entry)
    a.mode = b.mode = 500
    store = data_dir / "xrpl_invoices.jsonl"
    before = store.read_bytes()
    code, body, _, _ = _status(base, inv["invoice_id"])
    assert code == 200
    assert (body["status"], body["credited"], body["verification"]) == ("awaiting_payment", False, "unavailable")
    assert store.read_bytes() == before
    assert not [r for r in _ledger(data_dir) if r["source"].endswith(inv["invoice_id"])]


def test_head_asks_no_node(armed):
    base, _dir, a, b = armed
    inv = _create(base)
    calls = a.calls() + b.calls()
    code, _, _ = _srv.request(base, f"/api/xrpl/invoice/{inv['invoice_id']}", "HEAD")
    assert code == 200
    assert a.calls() + b.calls() == calls


def test_bad_and_unknown_ids(armed):
    base, *_ = armed
    assert _srv.request(base, "/api/xrpl/invoice/not-an-id!")[0] == 400
    assert _srv.request(base, "/api/xrpl/invoice/xr_NoSuchInvoice9")[0] == 404


def test_access_log_names_the_template_not_the_id(armed):
    base, data_dir, *_ = armed
    inv = _create(base)
    _status(base, inv["invoice_id"])
    text = "".join(p.read_text(errors="replace") for p in data_dir.glob("server-*.log"))
    assert "/api/xrpl/invoice/{id}" in text
    assert inv["invoice_id"] not in text
