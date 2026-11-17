"""test_xrpl_pay.py — the receive-only XRPL (RLUSD) rail, in process.

Every node here is tests/_xrpl_fake.FakeNode on loopback; nothing reaches
the real XRP Ledger. The HTTP routes are driven through a real server in
tests/test_xrpl_pay_server.py. Each failure path of server/xrpl_pay.py has a
test: config refusals, every way a payment is not a payment (partial,
wrong issuer, wrong currency, XRP, wrong tag, unvalidated, tec*), the
quorum (a node down, a node lying, a node refusing), replay of a hash, an
expired invoice, and the crash-heal of the exactly-once mint.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import time
from decimal import Decimal
from pathlib import Path

import pytest

import credits
import mailer
import request_mode
import xrpl_pay
from _xrpl_fake import (IMPOSTOR, ISSUER, MERCHANT, RLUSD, FakeNode, payment,
                        tx_hash)

ROOT = Path(__file__).resolve().parent.parent

GENESIS = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"      # XRPL genesis account
ACCOUNT_ZERO = "rrrrrrrrrrrrrrrrrrrrrhoLvTp"           # account id 0
ACCOUNT_ONE = "rrrrrrrrrrrrrrrrrrrrBZbvji"             # account id 1


@pytest.fixture
def nodes():
    a, b = FakeNode("127.0.0.1"), FakeNode("localhost")
    yield a, b
    a.close()
    b.close()


@pytest.fixture
def rail(tmp_path, monkeypatch, nodes):
    """An armed rail on two fake nodes (min_agree 2), its own data dir and
    ledger, and a mailer that records instead of sending."""
    a, b = nodes
    monkeypatch.setenv("ORPHO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ORPHO_XRPL_ADDRESS", MERCHANT)
    monkeypatch.setenv("ORPHO_XRPL_RLUSD_ISSUER", ISSUER)
    monkeypatch.setenv("ORPHO_XRPL_RPC", f"{a.url},{b.url}")
    monkeypatch.delenv("ORPHO_XRPL_RLUSD_CURRENCY", raising=False)
    monkeypatch.delenv("ORPHO_XRPL_MIN_AGREE", raising=False)
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "credit_ledger.jsonl")
    monkeypatch.setattr(xrpl_pay, "MIN_CHECK_INTERVAL", 0.0)
    xrpl_pay.BLOCKED.clear()
    xrpl_pay._last_check.clear()
    sent = []
    monkeypatch.setattr(mailer, "send_pack_claim_email",
                        lambda to, code, n: sent.append((to, code, n)) or True)
    assert xrpl_pay.armed(), xrpl_pay.config_problems()
    yield {"a": a, "b": b, "sent": sent, "dir": tmp_path}
    xrpl_pay.BLOCKED.clear()


def both(rail, entry):
    rail["a"].entries.append(entry)
    rail["b"].entries.append(entry)


def ledger_rows(rail):
    p = rail["dir"] / "credit_ledger.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def snapshot(rail):
    return {p.name: p.read_bytes() for p in rail["dir"].iterdir()
            if p.is_file() and not p.name.endswith(".lock")}


# ------------------------------------------------------------ address codec

class TestAddress:
    def test_known_addresses_decode(self):
        assert xrpl_pay.decode_classic_address(GENESIS) is not None
        assert xrpl_pay.decode_classic_address(ACCOUNT_ZERO) == bytes(20)
        assert xrpl_pay.decode_classic_address(ACCOUNT_ONE) == bytes(19) + b"\x01"

    def test_round_trip(self):
        for i in (1, 7, 200):
            acct = bytes([i]) * 20
            assert xrpl_pay.decode_classic_address(xrpl_pay.encode_classic_address(acct)) == acct
        with pytest.raises(ValueError):
            xrpl_pay.encode_classic_address(b"short")

    @pytest.mark.parametrize("bad", [
        "", "x", GENESIS[:-1] + ("j" if GENESIS[-1] != "j" else "k"),   # checksum
        "rHb9CJAWyB4rj91VRWn96DkukG4bwdty0h",                          # 0 not in alphabet
        "sHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh",                          # not r-prefixed
        "r" + "p" * 40,                                                 # too long
        "rrrr",                                                         # too short
        "0x" + "ab" * 20,                                               # an EVM address
        None, 12,
    ])
    def test_bad_addresses_refused(self, bad):
        assert xrpl_pay.decode_classic_address(bad) is None

    def test_black_holes_are_problems(self):
        assert "black-hole" in xrpl_pay.address_problem("X", ACCOUNT_ZERO)
        assert "black-hole" in xrpl_pay.address_problem("X", ACCOUNT_ONE)
        assert xrpl_pay.address_problem("X", GENESIS) is None
        assert "not set" in xrpl_pay.address_problem("X", "")


# ------------------------------------------------------------------ config

class TestConfig:
    @pytest.fixture(autouse=True)
    def clean(self, monkeypatch):
        for k in ("ORPHO_XRPL_ADDRESS", "ORPHO_XRPL_RLUSD_ISSUER", "ORPHO_XRPL_RLUSD_CURRENCY",
                  "ORPHO_XRPL_RPC", "ORPHO_XRPL_MIN_AGREE"):
            monkeypatch.delenv(k, raising=False)

    def arm(self, monkeypatch, **env):
        base = {"ORPHO_XRPL_ADDRESS": MERCHANT, "ORPHO_XRPL_RLUSD_ISSUER": ISSUER,
                "ORPHO_XRPL_RPC": "https://a.invalid/,https://b.invalid/"}
        for k, v in {**base, **env}.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, v)

    def test_off_by_default(self):
        assert not xrpl_pay.is_set() and not xrpl_pay.armed()
        assert xrpl_pay.config_problems() == []
        assert xrpl_pay.health_state() == {"armed": False, "config_problems": 0, "min_agree": None,
                                           "nodes": None, "nodes_blocked": 0}

    def test_good_config_arms(self, monkeypatch):
        self.arm(monkeypatch)
        assert xrpl_pay.config_problems() == []
        assert xrpl_pay.armed()
        assert xrpl_pay.currency() == "524C555344" + "0" * 30
        assert xrpl_pay.health_state()["armed"] is True

    @pytest.mark.parametrize("env, needle", [
        ({"ORPHO_XRPL_ADDRESS": "rNotAnAddress"}, "ORPHO_XRPL_ADDRESS is not a valid"),
        ({"ORPHO_XRPL_ADDRESS": ACCOUNT_ZERO}, "black-hole"),
        ({"ORPHO_XRPL_RLUSD_ISSUER": None}, "ORPHO_XRPL_RLUSD_ISSUER is not set"),
        ({"ORPHO_XRPL_RLUSD_ISSUER": GENESIS[:-1] + "j"}, "ORPHO_XRPL_RLUSD_ISSUER is not a valid"),
        ({"ORPHO_XRPL_RLUSD_ISSUER": MERCHANT}, "the merchant address itself"),
        ({"ORPHO_XRPL_RLUSD_CURRENCY": "RLUSD"}, "40 hex"),
        ({"ORPHO_XRPL_RLUSD_CURRENCY": "00" * 20}, "non-00"),
        ({"ORPHO_XRPL_RPC": "http://a.invalid/,https://b.invalid/"}, "must be https"),
        ({"ORPHO_XRPL_RPC": "https://u:p@a.invalid/,https://b.invalid/"}, "credentials"),
        ({"ORPHO_XRPL_RPC": "https://a.invalid:99999/,https://b.invalid/"}, "unparseable"),
        ({"ORPHO_XRPL_RPC": "https:///x,https://b.invalid/"}, "no host"),
        # The default is ONE node and the default quorum is two operators.
        ({"ORPHO_XRPL_RPC": None}, "ORPHO_XRPL_MIN_AGREE needs"),
        ({"ORPHO_XRPL_RPC": "https://a.invalid/,https://A.INVALID/x"}, "ORPHO_XRPL_MIN_AGREE needs"),
        ({"ORPHO_XRPL_MIN_AGREE": "two"}, "whole number"),
        ({"ORPHO_XRPL_MIN_AGREE": "0"}, "ORPHO_XRPL_MIN_AGREE needs"),
        ({"ORPHO_XRPL_MIN_AGREE": "3"}, "ORPHO_XRPL_MIN_AGREE needs"),
    ])
    def test_each_problem_disarms(self, monkeypatch, env, needle):
        self.arm(monkeypatch, **env)
        problems = xrpl_pay.config_problems()
        assert any(needle in p for p in problems), problems
        assert not xrpl_pay.armed()
        h = xrpl_pay.health_state()
        assert h["armed"] is False and h["config_problems"] == len(problems) >= 1
        # Counts only: no address or reason reaches the public health dict.
        assert MERCHANT not in json.dumps(h) and ISSUER not in json.dumps(h)

    def test_single_node_is_allowed_when_asked_for(self, monkeypatch):
        self.arm(monkeypatch, ORPHO_XRPL_RPC=None, ORPHO_XRPL_MIN_AGREE="1")
        assert xrpl_pay.armed()
        assert xrpl_pay.nodes() == [xrpl_pay.DEFAULT_RPC]

    def test_loopback_http_is_the_only_plain_http(self, monkeypatch):
        for ok in ("http://127.0.0.1:5005/", "http://localhost:1/", "http://[::1]:2/"):
            assert xrpl_pay.node_url_problem(ok) is None
        assert xrpl_pay.node_url_problem("http://127.0.0.2/") is not None
        assert xrpl_pay.node_url_problem("ftp://a.invalid/") is not None


# ------------------------------------------------------- node-use policy

class TestNodePolicy:
    """tools/xrpl_anchor/NODES.md, kept by the payment rail too. The tool is
    a standalone script outside the production image, so the rail restates
    its rules; these tests pin the two together."""

    @pytest.fixture
    def xv(self):
        spec = importlib.util.spec_from_file_location(
            "xrpl_verify", ROOT / "tools" / "xrpl_anchor" / "xrpl_verify.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_same_refusal_rules_as_the_anchor_tool(self, xv):
        assert xrpl_pay.REFUSED_STATUS == xv.REFUSED_STATUS
        assert xrpl_pay.TIMEOUT == xv.TIMEOUT
        assert xrpl_pay.MAX_BODY == xv.MAX_BODY
        sample = ["https://a.example/", "https://A.example:51234/x", "https://b.example/",
                  "http://127.0.0.1:1/", "http://127.0.0.1:2/"]
        assert xrpl_pay.distinct_nodes(sample) == xv.distinct_nodes(sample)

    def test_user_agent_is_one_honest_constant(self):
        for token in ("Mozilla", "Chrome", "Safari", "AppleWebKit"):
            assert token not in xrpl_pay.USER_AGENT
        assert xrpl_pay.USER_AGENT.startswith("orphograph-")
        tree = ast.parse((ROOT / "server" / "xrpl_pay.py").read_text())
        assigns = [n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
                   and any(getattr(t, "id", None) == "USER_AGENT"
                           for t in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
        assert len(assigns) == 1 and isinstance(assigns[0].value, ast.Constant)

    def test_every_request_carries_it(self, rail):
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        both(rail, payment(1, inv["destination_tag"]))     # so both nodes are asked
        xrpl_pay.check_invoice(inv["invoice_id"])
        for node in (rail["a"], rail["b"]):
            assert node.requests
            assert {r["ua"] for r in node.requests} == {xrpl_pay.USER_AGENT}

    @pytest.mark.parametrize("mode", [401, 403, 429, "ratelimit"])
    def test_a_refusal_blocks_the_node_for_good(self, rail, mode):
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        rail["a"].mode = mode
        rail["b"].mode = mode
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["verification"] == "unavailable"
        assert rail["a"].url in xrpl_pay.BLOCKED and rail["b"].url in xrpl_pay.BLOCKED
        before = rail["a"].calls() + rail["b"].calls()
        rail["a"].mode = rail["b"].mode = None          # even once they would answer
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["verification"] == "unavailable"
        assert rail["a"].calls() + rail["b"].calls() == before   # never asked again
        assert xrpl_pay.health_state()["nodes_blocked"] == 2

    def test_redirect_is_never_followed(self, rail):
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        other = FakeNode()
        try:
            rail["a"].mode = rail["b"].mode = "redirect"
            rail["a"].redirect_to = rail["b"].redirect_to = other.url
            out = xrpl_pay.check_invoice(inv["invoice_id"])
            assert out["verification"] == "unavailable"
            assert other.calls() == 0
        finally:
            other.close()

    def test_one_scan_is_bounded(self, rail, monkeypatch):
        """A node that always offers another page is read MAX_PAGES times."""
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        real = rail["a"].answer

        def endless(body):
            status, payload, headers = real(body)
            if body.get("method") == "account_tx":
                payload["result"]["marker"] = {"ledger": 1, "seq": 1}
            return status, payload, headers
        rail["a"].answer = endless
        xrpl_pay.check_invoice(inv["invoice_id"])
        assert rail["a"].calls("account_tx") == xrpl_pay.MAX_PAGES
        assert rail["b"].calls("account_tx") == 0       # discovery stops at the first node

    def test_candidates_per_check_are_capped(self, rail):
        """Many small payments to one tag: at most MAX_CANDIDATES are
        confirmed per check, the rest on later polls, each exactly once."""
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        n = xrpl_pay.MAX_CANDIDATES + 2
        for i in range(1, n + 1):
            both(rail, payment(i, inv["destination_tag"], "0.01"))
        xrpl_pay.check_invoice(inv["invoice_id"])
        assert rail["a"].calls("tx") == xrpl_pay.MAX_CANDIDATES
        assert len(xrpl_pay.get_invoice(inv["invoice_id"])["payments"]) == xrpl_pay.MAX_CANDIDATES
        xrpl_pay.check_invoice(inv["invoice_id"])
        assert len(xrpl_pay.get_invoice(inv["invoice_id"])["payments"]) == n
        assert rail["a"].calls("tx") == n

    def test_a_check_is_throttled_per_invoice(self, rail, monkeypatch):
        monkeypatch.setattr(xrpl_pay, "MIN_CHECK_INTERVAL", 60.0)
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "checked"
        n = rail["a"].calls()
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert (out["verification"], out["reason"]) == ("skipped", "throttled")
        assert rail["a"].calls() == n

    def test_head_asks_no_node(self, rail):
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        with request_mode.safe_method():
            out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert (out["verification"], out["reason"]) == ("skipped", "head")
        assert rail["a"].calls() == rail["b"].calls() == 0


# ----------------------------------------------------------------- invoices

class TestInvoice:
    def test_quote_is_the_pack_price_exactly(self, rail):
        w = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        p = xrpl_pay.create_invoice("pack_50", "buyer@example.com")
        assert (w["amount"], w["credits"]) == ("5", 25)
        assert (p["amount"], p["credits"]) == ("20", 200)
        for r in (w, p):
            assert r["currency"] == RLUSD and r["issuer"] == ISSUER
            assert r["destination"] == MERCHANT
            assert r["expires_at"] - r["created_at"] == 3600
            assert 1 <= r["destination_tag"] < 2**32
            assert xrpl_pay.INVOICE_ID_RE.match(r["invoice_id"])
        assert xrpl_pay.get_invoice(w["invoice_id"])["destination_tag"] == w["destination_tag"]

    def test_unknown_plan_and_disarmed(self, rail, monkeypatch):
        with pytest.raises(ValueError):
            xrpl_pay.create_invoice("pack_9999", "buyer@example.com")
        monkeypatch.delenv("ORPHO_XRPL_ADDRESS")
        with pytest.raises(RuntimeError):
            xrpl_pay.create_invoice("writer_pack", "buyer@example.com")

    def test_tag_never_reuses_a_stored_one(self, rail, monkeypatch):
        first = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        taken = first["destination_tag"]
        draws = iter([taken - 1, taken - 1, 41])       # randbelow(n) + 1 = tag
        monkeypatch.setattr(xrpl_pay.secrets, "randbelow", lambda n: next(draws))
        second = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        assert second["destination_tag"] == 42 != taken

    def test_tag_space_exhaustion_fails_loudly(self, rail, monkeypatch):
        first = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        monkeypatch.setattr(xrpl_pay.secrets, "randbelow",
                            lambda n: first["destination_tag"] - 1)
        with pytest.raises(RuntimeError):
            xrpl_pay.create_invoice("writer_pack", "buyer@example.com")

    def test_public_view_never_carries_email_or_claim_code(self, rail):
        inv = xrpl_pay.create_invoice("writer_pack", "secret-buyer@example.com")
        both(rail, payment(1, inv["destination_tag"]))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        view = json.dumps(xrpl_pay.public_view(out["record"], out["verification"]))
        assert "secret-buyer" not in view and "pk_" not in view
        assert json.loads(view)["status"] == "paid"

    def test_unknown_and_malformed_ids(self, rail):
        assert xrpl_pay.get_invoice("xr_doesnotexist1") is None
        assert xrpl_pay.get_invoice("../etc/passwd") is None
        with pytest.raises(KeyError):
            xrpl_pay.check_invoice("xr_doesnotexist1")

    def test_torn_store_line_is_skipped(self, rail):
        inv = xrpl_pay.create_invoice("writer_pack", "buyer@example.com")
        with xrpl_pay.invoices_path().open("a") as f:
            f.write('{"invoice_id": "xr_torn')
        assert xrpl_pay.get_invoice(inv["invoice_id"]) is not None


# ---------------------------------------------------------------- payments

def _paid_once(rail, inv):
    rows = ledger_rows(rail)
    assert len(rows) == 1, rows
    assert rows[0]["credits_delta"] == inv["credits"]
    assert rows[0]["source"] == f"xrpl:{tx_hash(1)}:{inv['invoice_id']}"
    assert rows[0]["email"] == "buyer@example.com"
    assert len(rail["sent"]) == 1 and rail["sent"][0][1] == rows[0]["claim_code"]


class TestPayments:
    def new(self):
        return xrpl_pay.create_invoice("writer_pack", "buyer@example.com")

    def test_valid_payment_credits_once(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["minted"] is True and out["verification"] == "checked"
        assert out["record"]["state"] == "paid" and out["record"]["credited"]
        _paid_once(rail, inv)
        # Polled again (forced past the "credited" short-cut): nothing more.
        again = xrpl_pay.check_invoice(inv["invoice_id"], force=True)
        assert again["minted"] is False
        _paid_once(rail, inv)

    def test_replayed_hash_never_credits_twice(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        xrpl_pay.check_invoice(inv["invoice_id"])
        # Even with the per-invoice short-cut defeated, the tx marker holds.
        rec = xrpl_pay.get_invoice(inv["invoice_id"])
        xrpl_pay._write_invoice({**rec, "credited": False, "state": "awaiting_payment"})
        xrpl_pay.check_invoice(inv["invoice_id"])
        _paid_once(rail, inv)

    def test_one_hash_cannot_pay_two_invoices(self, rail):
        """Nodes that re-report a credited hash under another invoice's tag
        (lying in unison) still cannot spend it twice."""
        a_inv, b_inv = self.new(), self.new()
        both(rail, payment(1, a_inv["destination_tag"]))
        xrpl_pay.check_invoice(a_inv["invoice_id"])
        forged = payment(1, b_inv["destination_tag"])
        rail["a"].entries[:] = [forged]
        rail["b"].entries[:] = [forged]
        out = xrpl_pay.check_invoice(b_inv["invoice_id"])
        assert out["minted"] is False and not out["record"]["credited"]
        _paid_once(rail, a_inv)

    def test_duplicate_entries_in_one_scan_credit_once(self, rail):
        inv = self.new()
        e = payment(1, inv["destination_tag"])
        rail["a"].entries[:] = [e, e]
        rail["b"].entries[:] = [e]
        xrpl_pay.check_invoice(inv["invoice_id"])
        _paid_once(rail, inv)

    def test_partial_payment_uses_delivered_amount(self, rail):
        """Amount/DeliverMax says 1000 RLUSD; the ledger delivered 0.5."""
        inv = self.new()
        big = {"currency": RLUSD, "issuer": ISSUER, "value": "1000"}
        both(rail, payment(1, inv["destination_tag"], "0.5", deliver_max=big))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["record"]["state"] == "underpaid" and not out["record"]["credited"]
        assert out["record"]["manual_review"] is True
        assert ledger_rows(rail) == [] and rail["sent"] == []

    def test_missing_delivered_amount_is_never_read_from_amount(self, rail):
        inv = self.new()
        e = payment(1, inv["destination_tag"])
        del e["meta"]["delivered_amount"]
        both(rail, e)
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert not out["record"]["credited"] and out["record"]["manual_review"]
        assert out["record"]["payments"][0]["verdict"] == "WRONG_ASSET"

    @pytest.mark.parametrize("kw", [
        {"issuer": IMPOSTOR},                                   # wrong issuer
        {"currency": "USD"},                                    # standard USD code
        {"currency": "534F4C4F00000000000000000000000000000000"},  # another IOU
        {"delivered": "5000000"},                               # XRP drops
        {"delivered": "unavailable"},                           # pre-2014 ledger
        {"delivered": {"currency": RLUSD, "issuer": ISSUER, "value": "NaN"}},
        {"delivered": {"currency": RLUSD, "issuer": ISSUER, "value": "Infinity"}},
        {"delivered": {"currency": RLUSD, "issuer": ISSUER, "value": "-5"}},
        {"delivered": {"currency": RLUSD, "issuer": ISSUER, "value": 5}},
    ])
    def test_wrong_asset_is_not_credited(self, rail, kw):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"], **kw))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        rec = out["record"]
        assert not rec["credited"] and rec["state"] == "awaiting_payment"
        assert rec["manual_review"] is True
        assert rec["payments"][0]["verdict"] == "WRONG_ASSET"
        assert ledger_rows(rail) == []

    def test_wrong_tag_or_destination_is_not_ours(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"] + 1))
        both(rail, payment(2, inv["destination_tag"], destination=IMPOSTOR))
        e = payment(3, inv["destination_tag"])
        e["tx_json"]["TransactionType"] = "OfferCreate"
        both(rail, e)
        e = payment(4, inv["destination_tag"])
        e["tx_json"]["DestinationTag"] = True                   # bool is not a tag
        both(rail, e)
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["record"]["payments"] == [] and not out["record"]["credited"]
        assert rail["a"].calls("tx") == 0                       # nothing to confirm
        assert ledger_rows(rail) == []

    def test_unvalidated_is_pending_then_credits_once_validated(self, rail):
        inv = self.new()
        e = payment(1, inv["destination_tag"], validated=False)
        both(rail, e)
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["record"]["state"] == "awaiting_payment" and out["record"]["payments"] == []
        assert ledger_rows(rail) == []
        e["validated"] = True
        xrpl_pay.check_invoice(inv["invoice_id"])
        _paid_once(rail, inv)

    @pytest.mark.parametrize("result", ["tecPATH_DRY", "tecUNFUNDED_PAYMENT", "tecNO_DST_INSUF_XRP"])
    def test_failed_transaction_is_not_credited(self, rail, result):
        inv = self.new()
        # A tec result carries no delivered_amount; give it one anyway, so the
        # result alone has to stop it.
        both(rail, payment(1, inv["destination_tag"], result=result,
                           delivered={"currency": RLUSD, "issuer": ISSUER, "value": "5"}))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["record"]["state"] == "awaiting_payment" and not out["record"]["credited"]
        assert ledger_rows(rail) == []
        # Marked, so it is not re-read on every poll.
        n = rail["a"].calls("tx")
        xrpl_pay.check_invoice(inv["invoice_id"])
        assert rail["a"].calls("tx") == n

    def test_underpaid_then_full_payment_credits(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"], "4.99"))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["record"]["state"] == "underpaid" and ledger_rows(rail) == []
        both(rail, payment(2, inv["destination_tag"], "5"))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["record"]["state"] == "paid"
        rows = ledger_rows(rail)
        assert len(rows) == 1 and rows[0]["source"] == f"xrpl:{tx_hash(2)}:{inv['invoice_id']}"

    def test_overpayment_credits_and_a_second_payment_is_flagged(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"], "5.000001"))
        both(rail, payment(2, inv["destination_tag"], "5"))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        rec = out["record"]
        assert rec["credited"] and rec["manual_review"]
        assert [p["verdict"] for p in rec["payments"]] == ["PAID", "EXTRA_PAYMENT"]
        assert len(ledger_rows(rail)) == 1

    def test_exponent_notation_value(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"], "5e0"))
        assert xrpl_pay.check_invoice(inv["invoice_id"])["record"]["credited"]

    def test_expired_invoice_is_not_auto_credited(self, rail, monkeypatch):
        t0 = time.time() - 3 * 3600
        monkeypatch.setattr(xrpl_pay, "_now", lambda: t0)
        inv = self.new()
        monkeypatch.setattr(xrpl_pay, "_now", time.time)
        both(rail, payment(1, inv["destination_tag"], close_unix=time.time()))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        rec = out["record"]
        assert rec["state"] == "late_payment" and rec["manual_review"] and not rec["credited"]
        assert ledger_rows(rail) == [] and rail["sent"] == []
        assert xrpl_pay.public_view(rec, "checked")["status"] == "late_payment"

    def test_expired_with_no_payment_reads_expired(self, rail, monkeypatch):
        t0 = time.time() - 2 * 3600
        monkeypatch.setattr(xrpl_pay, "_now", lambda: t0)
        inv = self.new()
        monkeypatch.setattr(xrpl_pay, "_now", time.time)
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert xrpl_pay.public_view(out["record"], out["verification"])["status"] == "expired"

    def test_past_the_watch_window_asks_no_node(self, rail, monkeypatch):
        t0 = time.time() - xrpl_pay.LATE_WATCH_SEC - 2 * 3600
        monkeypatch.setattr(xrpl_pay, "_now", lambda: t0)
        inv = self.new()
        monkeypatch.setattr(xrpl_pay, "_now", time.time)
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert (out["verification"], out["reason"]) == ("skipped", "past watch window")
        assert rail["a"].calls() == 0

    def test_payment_from_before_the_invoice_is_not_for_it(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"], close_unix=inv["created_at"] - 600))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert not out["record"]["credited"] and out["record"]["payments"] == []
        assert ledger_rows(rail) == []

    def test_no_close_time_goes_to_manual_review(self, rail):
        inv = self.new()
        e = payment(1, inv["destination_tag"])
        del e["tx_json"]["date"]
        del e["close_time_iso"]
        both(rail, e)
        rec = xrpl_pay.check_invoice(inv["invoice_id"])["record"]
        assert not rec["credited"] and rec["manual_review"] and rec["state"] == "late_payment"

    def test_crash_after_mint_heals_without_a_second_mint(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        # The ledger row landed; the markers and the snapshot did not.
        credits.add_credits(claim_code="pk_crashrow", email="buyer@example.com",
                            amount=25, source=f"xrpl:{tx_hash(1)}:{inv['invoice_id']}")
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["minted"] is False and out["record"]["credited"]
        assert len(ledger_rows(rail)) == 1 and rail["sent"] == []


# ------------------------------------------------------------------ quorum

class TestQuorum:
    def new(self):
        return xrpl_pay.create_invoice("writer_pack", "buyer@example.com")

    @pytest.mark.parametrize("mode", [500, 502, "garbage"])
    def test_rpc_failure_is_unavailable_and_changes_nothing(self, rail, mode):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        rail["a"].mode = rail["b"].mode = mode
        before = snapshot(rail)
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["verification"] == "unavailable"
        assert "nothing was changed" in out["reason"]
        assert snapshot(rail) == before
        assert xrpl_pay.public_view(out["record"], out["verification"])["status"] == "awaiting_payment"

    def test_timeout_is_unavailable(self, rail, monkeypatch):
        monkeypatch.setattr(xrpl_pay, "TIMEOUT", 0.3)
        inv = self.new()
        rail["a"].mode = rail["b"].mode = "hang"
        before = snapshot(rail)
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert snapshot(rail) == before

    def test_connection_refused_is_unavailable(self, rail, monkeypatch):
        monkeypatch.setenv("ORPHO_XRPL_RPC", "http://127.0.0.1:1/,http://localhost:1/")
        inv = self.new()
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"

    def test_discovery_falls_back_to_the_next_node(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        rail["a"].mode = 503
        # Discovery works through b; but a cannot confirm, so 1 of 2: no credit.
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["verification"] == "unavailable" and ledger_rows(rail) == []
        rail["a"].mode = None
        assert xrpl_pay.check_invoice(inv["invoice_id"])["minted"] is True

    def test_one_node_missing_the_tx_is_not_enough(self, rail):
        inv = self.new()
        rail["a"].entries.append(payment(1, inv["destination_tag"]))   # b never saw it
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["verification"] == "unavailable" and ledger_rows(rail) == []

    def test_a_node_reporting_a_different_amount_blocks_the_credit(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"], "5"))
        rail["b"].overrides[tx_hash(1)] = payment(1, inv["destination_tag"], "4")
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert out["verification"] == "unavailable" and ledger_rows(rail) == []
        assert out["record"]["payments"] == []

    def test_a_node_reporting_another_ledger_blocks_the_credit(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        other = payment(1, inv["destination_tag"])
        other["ledger_hash"] = "F" * 64
        rail["b"].overrides[tx_hash(1)] = other
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert ledger_rows(rail) == []

    def test_a_node_answering_with_another_transaction_blocks_the_credit(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        rail["b"].overrides[tx_hash(1)] = payment(2, inv["destination_tag"])
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert ledger_rows(rail) == []

    def test_a_lying_discovery_node_cannot_invent_a_payment(self, rail):
        """Node a lists a payment its own `tx` and node b do not confirm."""
        inv = self.new()
        rail["a"].entries.append(payment(1, inv["destination_tag"]))
        rail["a"].overrides[tx_hash(1)] = payment(1, inv["destination_tag"] + 5)
        rail["b"].entries.append(payment(1, inv["destination_tag"] + 5))
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        assert not out["record"]["credited"] and ledger_rows(rail) == []

    def test_malformed_node_answers_are_unavailable_not_tracebacks(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        for bad in ({"hash": "zz"}, {"hash": tx_hash(1), "tx_json": [], "meta": {}},
                    {"hash": tx_hash(1), "tx_json": {}, "meta": "x"}):
            rail["b"].overrides[tx_hash(1)] = bad
            assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert ledger_rows(rail) == []

    def test_account_tx_without_a_list_is_unavailable(self, rail):
        inv = self.new()
        for node in (rail["a"], rail["b"]):
            node.answer = lambda body: (200, {"result": {"status": "success", "transactions": "no"}}, {})
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"

    def test_account_tx_error_is_unavailable(self, rail):
        inv = self.new()
        for node in (rail["a"], rail["b"]):
            node.answer = lambda body: (200, {"result": {"status": "error", "error": "actNotFound"}}, {})
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"

    def test_oversized_answer_is_unavailable(self, rail, monkeypatch):
        monkeypatch.setattr(xrpl_pay, "MAX_BODY", 64)
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"

    def test_single_node_quorum_when_configured(self, rail, monkeypatch):
        monkeypatch.setenv("ORPHO_XRPL_RPC", rail["a"].url)
        monkeypatch.setenv("ORPHO_XRPL_MIN_AGREE", "1")
        inv = self.new()
        rail["a"].entries.append(payment(1, inv["destination_tag"]))
        assert xrpl_pay.check_invoice(inv["invoice_id"])["minted"] is True

    def test_confirm_combines_like_the_anchor_verifier(self):
        inv = {"destination": MERCHANT, "destination_tag": 5, "currency": RLUSD,
               "issuer": ISSUER, "amount": "5", "created_at": 0, "expires_at": 2**40}
        ok = {"verdict": "PAID", "hash": "H", "ledger_index": 1, "close_unix": 9,
              "value": "5", "ledger_hash": "L"}

        def votes(*vs):
            it = iter(vs)
            return lambda node, h, i: {"node": node, **next(it)}
        three = ["https://a.x/", "https://b.x/", "https://c.x/"]
        assert xrpl_pay.confirm("H", inv, three, 2, votes(ok, ok, {"verdict": "UNAVAILABLE"}))["agreeing_nodes"] == 2
        with pytest.raises(xrpl_pay.Unavailable):
            xrpl_pay.confirm("H", inv, three, 3, votes(ok, ok, {"verdict": "UNAVAILABLE"}))
        with pytest.raises(xrpl_pay.Unavailable):
            xrpl_pay.confirm("H", inv, three, 2, votes(ok, ok, {"verdict": "DISSENT"}))
        with pytest.raises(xrpl_pay.Unavailable):
            xrpl_pay.confirm("H", inv, three, 2, votes(ok, ok, {**ok, "verdict": "FAILED"}))
        # A repeated host is one vote.
        assert xrpl_pay.confirm("H", inv, ["https://a.x/", "https://A.x/y"], 1,
                                votes(ok))["agreeing_nodes"] == 1


# ------------------------------------------------------ credits matcher

def test_xrpl_mint_matcher_is_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(credits, "LEDGER_PATH", tmp_path / "l.jsonl")
    credits.add_credits("pk_a", "a@example.com", 25, f"xrpl:{'A' * 64}:xr_abcdefghij")
    credits.add_credits("pk_b", "b@example.com", 25, "nowpayments:1:xr_zzzzzzzzzz")
    assert credits.find_xrpl_mint("xr_abcdefghij")["claim_code"] == "pk_a"
    for miss in ("xr_abcdefgh", "abcdefghij", "xr_zzzzzzzzzz", "", "A" * 64):
        assert credits.find_xrpl_mint(miss) is None
    m = credits.xrpl_mint_matcher("x")
    assert not m("xrpl:x") and not m("xrpl:a:b:x") and m("xrpl:h:x")


def test_decimal_quote_matches_plan_ladder():
    import nowpayments_api
    for plan, meta in nowpayments_api.PLANS.items():
        assert Decimal(str(meta["price_usd"])) > 0
        assert xrpl_pay.plans()[plan] is meta


# ------------------------------------------------------------ shapes

class TestShapes:
    INV = {"destination": MERCHANT, "destination_tag": 5, "currency": RLUSD,
           "issuer": ISSUER, "amount": "5", "created_at": 10**9, "expires_at": 2 * 10**9}

    def test_api_v1_shape_reads_the_same(self):
        v2 = payment(1, 5, close_unix=10**9 + 100)
        v1 = {"tx": {**v2["tx_json"], "hash": v2["hash"], "ledger_index": v2["ledger_index"]},
              "meta": v2["meta"], "validated": True}
        n1, n2 = xrpl_pay.normalize(v1), xrpl_pay.normalize(v2)
        assert (n1["hash"], n1["ledger_index"], n1["close_unix"]) == \
               (n2["hash"], n2["ledger_index"], n2["close_unix"])
        assert xrpl_pay.classify(n1, self.INV)[0] == xrpl_pay.classify(n2, self.INV)[0] == "PAID"

    def test_close_time_iso_alone(self):
        e = payment(1, 5, close_unix=10**9 + 100)
        del e["tx_json"]["date"]
        assert xrpl_pay.normalize(e)["close_unix"] == 10**9 + 100
        e["close_time_iso"] = "yesterday"
        assert xrpl_pay.normalize(e)["close_unix"] is None

    @pytest.mark.parametrize("bad", [None, [], "x", {"hash": tx_hash(1)},
                                     {"hash": 5, "tx_json": {}, "meta": {}},
                                     {"hash": "G" * 64, "tx_json": {}, "meta": {}}])
    def test_not_a_transaction(self, bad):
        assert xrpl_pay.normalize(bad) is None
        assert xrpl_pay.classify(xrpl_pay.normalize(bad), self.INV) == ("NOT_OURS", {})

    def test_too_early_and_unreadable_value(self):
        early = xrpl_pay.normalize(payment(1, 5, close_unix=10**9 - 3600))
        assert xrpl_pay.classify(early, self.INV)[0] == "TOO_EARLY"
        junk = xrpl_pay.normalize(payment(2, 5, "abc", close_unix=10**9 + 1))
        assert xrpl_pay.classify(junk, self.INV)[0] == "WRONG_ASSET"
        long = xrpl_pay.normalize(payment(3, 5, "1" * 65, close_unix=10**9 + 1))
        assert xrpl_pay.classify(long, self.INV)[0] == "WRONG_ASSET"

    def test_min_agree_garbage_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("ORPHO_XRPL_MIN_AGREE", "x")
        assert xrpl_pay.min_agree() == xrpl_pay.DEFAULT_MIN_AGREE


class TestRpcEdges:
    def new(self):
        return xrpl_pay.create_invoice("writer_pack", "buyer@example.com")

    def test_rate_limit_words_in_a_body_are_a_block(self, rail):
        inv = self.new()
        for node in (rail["a"], rail["b"]):
            node.answer = lambda body: (200, b'{"error":"You hit the Rate Limit"}', {})
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert len(xrpl_pay.BLOCKED) == 2

    def test_no_result_object_is_unavailable(self, rail):
        inv = self.new()
        for node in (rail["a"], rail["b"]):
            node.answer = lambda body: (200, {"result": [1]}, {})
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert not xrpl_pay.BLOCKED

    def test_a_node_refusing_only_the_tx_read(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        real = rail["b"].answer
        rail["b"].answer = lambda body: ((403, {"error": "no"}, {})
                                         if body.get("method") == "tx" else real(body))
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert rail["b"].url in xrpl_pay.BLOCKED and ledger_rows(rail) == []

    def test_a_node_error_on_the_tx_read(self, rail):
        inv = self.new()
        both(rail, payment(1, inv["destination_tag"]))
        real = rail["b"].answer
        rail["b"].answer = lambda body: ((200, {"result": {"status": "error", "error": "internal"}}, {})
                                         if body.get("method") == "tx" else real(body))
        assert xrpl_pay.check_invoice(inv["invoice_id"])["verification"] == "unavailable"
        assert ledger_rows(rail) == []

    def test_unavailable_reason_reaches_the_public_view(self, rail):
        inv = self.new()
        rail["a"].mode = rail["b"].mode = 500
        out = xrpl_pay.check_invoice(inv["invoice_id"])
        view = xrpl_pay.public_view(out["record"], out["verification"], out["reason"])
        assert view["verification"] == "unavailable" and "verification_reason" in view
