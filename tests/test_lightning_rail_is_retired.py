"""test_lightning_rail_is_retired.py - the Lightning (L402) rail is retired.

Founder decision, 2026-09-28, on "arm one crypto rail, or retire both": the
Lightning pay-per-anchor rail is retired until someone asks for it. It was
never armed in production.

Unlike the direct Bitcoin rail (tests/test_direct_btc_rail_is_gone.py), the
code is FENCED, not deleted: server/lightning.py carries one constant,
LIGHTNING_RETIRED, and re-arming is that constant plus the steps at the top of
docs/LIGHTNING_L402.md. What this file pins, through real server processes:

  1. lightning.configured() is False whatever the environment holds, so a
     stray secret cannot half-arm the rail.
  2. POST /api/ln/quote answers 410 with a JSON body that names card checkout,
     with or without Lightning secrets set.
  3. An anchor request carrying `Authorization: L402 ...` answers 410 before
     any anchor work: no pack credit spent, no x402 verify or settle, no read
     of the spent set or the macaroon secret, no receipt. A retired credential
     is never silently ignored in favour of another payment on the request.
  4. A free anchor past its allowance gets the plain 429 a server with no
     Lightning secrets gives, never a 402 L402 challenge.
  5. No file under web/ offers Lightning payment.

NOT retired, and answering exactly as before (the controls below): the free
tier, Packs, subscribers, API keys, and the x402 rail (server/x402.py) that
shares the anchor handler. tests/test_x402_payment_flow.py is the full x402
control and is unchanged by this retirement.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "server"))
# The constant and configured() only. Every server below is its own process
# and reads its own copy of the module.
import api_keys  # noqa: E402
import lightning  # noqa: E402
import x402 as x402_const  # noqa: E402

LN_MOCK = {"ORPHO_LN_BACKEND": "mock", "ORPHO_LN_ALLOW_MOCK": "1"}

# Shaped like a real credential: a dot in the macaroon makes the armed code
# go on to the HMAC check, which reads (and on first use writes) the macaroon
# secret. A retired rail must stop before that.
JUNK_L402 = "L402 eyJ2IjoxfQ.c2lnbmF0dXJl:" + "00" * 32

SUB_EMAIL = "lnretire-sub@example.test"
SESSION_ID = "lnretire-session-0001"
API_KEY = "orpho_lnRetireKey12345678901"
PACK_CODE = "pk_lnretirePack0001"

# What master answers a successful single anchor with, captured from the
# server before this change, for each of the paths that are not retired.
MASTER_ANCHOR_KEYS = frozenset({
    "badge_url", "calendars_distinct_ok", "calendars_distinct_total",
    "calendars_ok", "calendars_total", "client_label", "created_at",
    "credit_refunded", "failures", "hash_hex", "low_redundancy",
    "pack_consumed", "pack_remaining", "receipt_id", "receipt_url",
    "sha512_hex", "subscription_active", "successes", "verify_url",
})


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _seed_subscriber(data: Path) -> None:
    """One signed-in subscriber who also holds an API key, written exactly as
    sign-in, the Stripe webhook and key issue record them."""
    _write_jsonl(data / "auth_sessions.jsonl", [dict(
        event="created", session_hash=hashlib.sha256(SESSION_ID.encode()).hexdigest(),
        email=SUB_EMAIL, expires_unix=time.time() + 3600)])
    _write_jsonl(data / "subscriptions.jsonl", [dict(
        email=SUB_EMAIL, status="active", stripe_sub="sub_lnretire")])
    _write_jsonl(data / "api_keys.jsonl", [dict(
        event="issued", email=SUB_EMAIL,
        key_hash=api_keys._hash(API_KEY), key_prefix=API_KEY[:14])])


def _no_egress(closed_port: int) -> dict:
    """Every urllib call from the server goes to a port nothing listens on;
    loopback stays direct so an LNbits URL on 127.0.0.1 is reached as given."""
    proxy = f"http://127.0.0.1:{closed_port}"
    return {"HTTPS_PROXY": proxy, "https_proxy": proxy,
            "HTTP_PROXY": proxy, "http_proxy": proxy,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}


def _post(base: str, path: str, body: dict, headers: dict | None = None,
          ctype: str = "application/json"):
    h = {"Content-Type": ctype, **(headers or {})}
    status, raw, resp_headers = _srv.request(base, path, "POST",
                                             json.dumps(body).encode(), h, timeout=15)
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {"_raw": raw[:300].decode("utf-8", "replace")}
    return status, parsed, resp_headers


def _receipts_for(data: Path, hash_hex: str) -> list[str]:
    """Receipt ids on disk that anchor hash_hex. The disk, not a response
    flag: a refused request must have written nothing servable at /r/<id>."""
    found = []
    for receipt in (data / "receipts").glob("*/receipt.json"):
        try:
            if json.loads(receipt.read_text()).get("hash_hex") == hash_hex:
                found.append(receipt.parent.name)
        except (OSError, ValueError):
            continue
    return found


def _assert_retired_body(body: dict) -> None:
    assert "retired" in body.get("error", "").lower(), body
    assert "lightning" in body.get("error", "").lower(), body
    text = json.dumps(body).lower()
    assert "card" in text and "/pricing" in text, (
        f"the 410 must name card checkout as the way to pay: {body}")
    assert "—" not in json.dumps(body, ensure_ascii=False), "no em dash in customer copy"


def _assert_no_lightning_state(data: Path) -> None:
    """The spent set and the macaroon secret are the rail's own files. A
    retired rail neither reads nor creates them."""
    assert not (data / lightning._SPENT_FILE).exists(), "the spent set was touched"
    assert not (data / lightning._SECRET_FILE).exists(), "the macaroon secret was created"


# ── 1 · the fence ──────────────────────────────────────────────────────────

def test_the_rail_is_fenced_by_one_constant() -> None:
    assert lightning.LIGHTNING_RETIRED is True


ARMING_ENVS = {
    "mock": LN_MOCK,
    "lnbits": {"ORPHO_LN_BACKEND": "lnbits", "ORPHO_LN_LNBITS_URL": "http://127.0.0.1:9",
               "ORPHO_LN_LNBITS_KEY": "stub-key-not-real"},
    "opennode": {"ORPHO_LN_BACKEND": "opennode", "ORPHO_LN_OPENNODE_KEY": "stub-key-not-real"},
}


@pytest.mark.parametrize("name", sorted(ARMING_ENVS))
def test_configured_is_false_whatever_the_environment_holds(name, monkeypatch) -> None:
    for key, value in ARMING_ENVS[name].items():
        monkeypatch.setenv(key, value)
    assert lightning.configured() is False, f"{name} secrets half-armed a retired rail"
    # CONTROL: the same environment does arm the rail once the fence is
    # lifted, so the False above is the fence's doing, not a bad fixture.
    monkeypatch.setattr(lightning, "LIGHTNING_RETIRED", False)
    assert lightning.configured() is True


# ── 2 · the quote endpoint ─────────────────────────────────────────────────

@pytest.mark.parametrize("backend", ["none", "mock", "lnbits"])
def test_quote_answers_410_even_with_lightning_secrets_set(backend, tmp_path) -> None:
    closed = _srv.reserve_ports(1)[0]
    env = {"none": {}, "mock": LN_MOCK,
           "lnbits": {"ORPHO_LN_BACKEND": "lnbits",
                      "ORPHO_LN_LNBITS_URL": f"http://127.0.0.1:{closed}",
                      "ORPHO_LN_LNBITS_KEY": "stub-key-not-real"}}[backend]
    for base in _srv.server_processes(tmp_path, stub_calendars=True,
                                      **_no_egress(closed), **env):
        status, body, headers = _post(base, "/api/ln/quote", {})
        assert status == 410, (status, body)
        _assert_retired_body(body)
        assert "invoice" not in body and "macaroon" not in body, body
        assert headers.get("Content-Type", "").startswith("application/json")
        # Before the JSON gate, as the retired BTC endpoints are: a 415 would
        # tell the caller "send JSON and I will serve you".
        status, body, _h = _post(base, "/api/ln/quote", {}, ctype="text/plain")
        assert status == 410, (status, body)
        _assert_no_lightning_state(tmp_path)


# ── 3 · L402 credentials on the anchor endpoints ───────────────────────────

@pytest.fixture(scope="module")
def retired(tmp_path_factory):
    """No Lightning secrets (production as it stands), x402 unarmed, a
    subscriber with an API key, and the generous default free tier."""
    data = tmp_path_factory.mktemp("ln_retired")
    _seed_subscriber(data)
    closed = _srv.reserve_ports(1)[0]
    for base in _srv.server_processes(data, stub_calendars=True, **_no_egress(closed)):
        yield base, data


def _add_pack(data: Path, monkeypatch, credits_n: int = 3) -> None:
    import credits
    monkeypatch.setattr(credits, "LEDGER_PATH", data / "credit_ledger.jsonl")
    if credits.balance(PACK_CODE) == 0:
        credits.add_credits(PACK_CODE, "lnretire-buyer@example.test", credits_n, "test")


def _pack_balance(data: Path, monkeypatch) -> int:
    import credits
    monkeypatch.setattr(credits, "LEDGER_PATH", data / "credit_ledger.jsonl")
    return credits.balance(PACK_CODE)


@pytest.mark.parametrize("scheme", [JUNK_L402, "l402 junk:00", "L402"],
                         ids=["L402", "lowercase", "bare-scheme"])
def test_an_anchor_with_an_l402_credential_answers_410_and_makes_nothing(retired, scheme) -> None:
    base, data = retired
    digest = hashlib.sha256(scheme.encode()).hexdigest()
    status, body, _h = _post(base, "/api/anchor", {"hash_hex": digest},
                             {"Authorization": scheme})
    assert status == 410, (status, body)
    _assert_retired_body(body)
    assert body.get("charged") is False, body
    assert "without the authorization: l402 header" in json.dumps(body).lower(), body
    assert _receipts_for(data, digest) == []
    _assert_no_lightning_state(data)
    # The quote and the anchor refusal carry the same message.
    _q, quote, _qh = _post(base, "/api/ln/quote", {})
    assert body["error"] == quote["error"]


@pytest.mark.parametrize("path,payload", [
    ("/api/anchor/batch", {"hashes": [{"hash_hex": "b1" * 32}]}),
    ("/api/anchor_folder", {}),
])
def test_the_other_anchor_endpoints_refuse_an_l402_credential_too(retired, path, payload) -> None:
    base, data = retired
    status, body, _h = _post(base, path, payload, {"Authorization": JUNK_L402})
    assert status == 410, (status, body)
    _assert_retired_body(body)
    assert _receipts_for(data, "b1" * 32) == []
    _assert_no_lightning_state(data)


def test_an_l402_credential_beside_a_pack_token_spends_no_credit(retired, monkeypatch) -> None:
    """Master consumed the pack credit and ignored the L402 header. Now the
    retired credential is refused first, so the pack is not charged for a
    request that was refused."""
    base, data = retired
    _add_pack(data, monkeypatch)
    before = _pack_balance(data, monkeypatch)
    assert before > 0
    status, body, _h = _post(base, "/api/anchor", {"hash_hex": "c1" * 32},
                             {"Authorization": JUNK_L402, "X-Pack-Token": PACK_CODE})
    assert status == 410, (status, body)
    assert body.get("charged") is False, body
    assert _pack_balance(data, monkeypatch) == before, "a refused request spent a pack credit"
    assert _receipts_for(data, "c1" * 32) == []


@pytest.mark.parametrize("kind", ["free", "pack", "subscriber", "api_key", "bearer"])
def test_every_other_anchor_path_answers_as_master_did(retired, kind, monkeypatch) -> None:
    """CONTROLS: these passed before the retirement and must pass after.
    Status, body shape and the auth flags are master's, captured from the
    server before this change."""
    base, data = retired
    digest = hashlib.sha256(f"control-{kind}".encode()).hexdigest()
    headers = {
        "free": {},
        "pack": {"X-Pack-Token": PACK_CODE},
        "subscriber": {"Cookie": "orpho_sid=" + SESSION_ID},
        "api_key": {"X-Orpho-Api-Key": API_KEY},
        # An Authorization header in another scheme is not a Lightning
        # credential and falls through to the free tier, as it always did.
        "bearer": {"Authorization": "Bearer not-a-real-token"},
    }[kind]
    if kind == "pack":
        _add_pack(data, monkeypatch)
        before = _pack_balance(data, monkeypatch)
    status, body, _h = _post(base, "/api/anchor", {"hash_hex": digest}, headers)
    assert status == 200, (status, body)
    assert set(body) == MASTER_ANCHOR_KEYS, sorted(set(body) ^ MASTER_ANCHOR_KEYS)
    assert body["pack_consumed"] is (kind == "pack")
    assert body["subscription_active"] is (kind in ("subscriber", "api_key"))
    assert _receipts_for(data, digest) == [body["receipt_id"]]
    if kind == "pack":
        assert _pack_balance(data, monkeypatch) == before - 1


# ── 4 · past the free allowance ────────────────────────────────────────────

def test_a_free_anchor_past_its_allowance_gets_the_unconfigured_answer(tmp_path) -> None:
    """With Lightning secrets set, master answered the second anchor with a
    402 L402 challenge and a fresh invoice. Retired, it must be the same 429
    a server with no Lightning secrets gives, and a request carrying an L402
    credential must not create the macaroon secret on the way."""
    answers = {}
    for name, env in (("secrets", LN_MOCK), ("none", {})):
        data = tmp_path / name
        data.mkdir()
        closed = _srv.reserve_ports(1)[0]
        for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="1",
                                          **_no_egress(closed), **env):
            first, body, _h = _post(base, "/api/anchor", {"hash_hex": "d1" * 32})
            assert first == 200, body
            answers[name] = _post(base, "/api/anchor", {"hash_hex": "d2" * 32})
            status, body, _h = _post(base, "/api/anchor", {"hash_hex": "d3" * 32},
                                     {"Authorization": JUNK_L402})
            assert status == 410, (name, status, body)
            _assert_no_lightning_state(data)
            # Health reports the rail retired, stray secret or not.
            _s, raw, _hh = _srv.request(base, "/api/health")
            assert json.loads(raw)["lightning"] == {"rail": "retired", "configured": False}
    (s_status, s_body, s_headers), (n_status, n_body, n_headers) = answers["secrets"], answers["none"]
    assert n_status == 429, n_body
    assert s_status == n_status, (s_status, s_body)
    assert set(s_body) == set(n_body), (sorted(s_body), sorted(n_body))
    assert s_body["error"] == n_body["error"] == "rate limit exceeded"
    assert s_body["limit_per_day"] == n_body["limit_per_day"] == 1
    assert s_headers.get("WWW-Authenticate") is None, "an L402 challenge was issued"
    assert int(s_headers.get("Retry-After", "0")) > 0


# ── 3b · L402 beside an x402 payment ───────────────────────────────────────

PAY_TO = "0x00000000000000000000000000000000000BEEF"
X402_ENV = {
    "ORPHO_X402_BACKEND": "mock",
    "ORPHO_X402_ALLOW_MOCK": "1",
    "ORPHO_X402_PAY_TO_ADDRESS": PAY_TO,
    "ORPHO_X402_PRICE_CENTS": "5",
    "RATE_LIMIT_PER_DAY": "1",
}


def _x402_headers(nonce: str) -> dict:
    """A signed-payload shape the mock facilitator accepts; same fields as
    tests/test_x402_payment_flow.py builds."""
    payload = {
        "x402Version": 2,
        "accepted": {"scheme": "exact", "network": "eip155:84532",
                     "asset": x402_const.USDC_BASE_SEPOLIA, "amount": "50000",
                     "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": {}},
        "payload": {
            "signature": "0x" + "ab" * 65,
            "authorization": {"from": "0xagentPayer0000000000000000000000000001",
                              "to": PAY_TO, "value": "50000", "validAfter": "0",
                              "validBefore": "9999999999", "nonce": nonce},
            "mock_outcome": "ok",
        },
    }
    raw = base64.b64encode(json.dumps(payload).encode()).decode()
    return {x402_const.PAYMENT_SIGNATURE_HEADER: raw}


def _x402_rows(data: Path) -> list[dict]:
    path = data / "x402_ledger.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_l402_beside_an_x402_payment_is_refused_before_any_charge(tmp_path) -> None:
    """The retired credential is not silently dropped in favour of charging
    the x402 payment on the same request: 410, no charge, no receipt. Then
    the SAME x402 payment, sent alone, pays for an anchor. The mock refuses a
    settled nonce a second time, so that 200 proves settle never ran on the
    refused request."""
    for base in _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV):
        first, body, _h = _post(base, "/api/anchor", {"hash_hex": "e0" * 32})
        assert first == 200, body   # the one free anchor
        both = {"Authorization": JUNK_L402, **_x402_headers("lnretire-n1")}
        status, body, _h = _post(base, "/api/anchor", {"hash_hex": "e1" * 32}, both)
        assert status == 410, (status, body)
        _assert_retired_body(body)
        assert body.get("charged") is False, body
        assert "x402" in body.get("detail", ""), "the body must say the x402 payment was not used"
        assert _x402_rows(tmp_path) == [], "an x402 payment was touched for a refused request"
        assert _receipts_for(tmp_path, "e1" * 32) == []
        _assert_no_lightning_state(tmp_path)

        status, body, headers = _post(base, "/api/anchor", {"hash_hex": "e1" * 32},
                                      _x402_headers("lnretire-n1"))
        assert status == 200, (status, body)
        assert _receipts_for(tmp_path, "e1" * 32) == [body["receipt_id"]]
        assert headers.get(x402_const.PAYMENT_RESPONSE_HEADER)

        # CONTROL: past the free tier with no payment, x402 still challenges,
        # and no L402 challenge rides along with it.
        status, body, headers = _post(base, "/api/anchor", {"hash_hex": "e2" * 32})
        assert status == 402, (status, body)
        assert headers.get(x402_const.PAYMENT_REQUIRED_HEADER)
        assert headers.get("WWW-Authenticate") is None



def test_an_l402_credential_on_a_second_authorization_line_is_refused_too(tmp_path) -> None:
    """Bundle review round 2 (merge seam): the refusal read only the FIRST
    Authorization line, so `Authorization: Bearer x` followed by
    `Authorization: L402 ...` beside an x402 payment settled and anchored,
    which docs/LIGHTNING_L402.md and docs/X402_AGENT_PAYMENTS.md both say
    cannot happen. Every Authorization line is checked now. The same x402
    payment, sent alone afterwards, still pays: the mock refuses a settled
    nonce twice, so that 200 proves the refused request never settled."""
    for base in _srv.server_processes(tmp_path, stub_calendars=True, **X402_ENV):
        first, body, _h = _post(base, "/api/anchor", {"hash_hex": "f0" * 32})
        assert first == 200, body   # the one free anchor
        pay = _x402_headers("lnretire-dup1")
        lines = ("Authorization: Bearer not-a-credential\r\n"
                 f"Authorization: {JUNK_L402}\r\n"
                 + "".join(f"{k}: {v}\r\n" for k, v in pay.items())
                 + "Content-Type: application/json\r\n")
        raw = _srv.raw_request(base, "/api/anchor", "POST", headers=lines,
                               body=json.dumps({"hash_hex": "f1" * 32}).encode())
        head, _sep, payload = raw.partition(b"\r\n\r\n")
        assert int(head.split()[1]) == 410, raw[:400]
        body = json.loads(payload)
        _assert_retired_body(body)
        assert body.get("charged") is False, body
        assert _x402_rows(tmp_path) == [], "an x402 payment was settled for a refused request"
        assert _receipts_for(tmp_path, "f1" * 32) == []
        _assert_no_lightning_state(tmp_path)

        status, body, headers = _post(base, "/api/anchor", {"hash_hex": "f1" * 32}, pay)
        assert status == 200, (status, body)
        assert headers.get(x402_const.PAYMENT_RESPONSE_HEADER)

# ── 4b · the published copy ────────────────────────────────────────────────

# Bare "sats" is not a token: the founder panel formats the retired direct
# Bitcoin rail's payout history in sats (web/account.js), which offers nothing.
LN_TOKENS = re.compile(r"(?i)\blightning\b|\bl402\b|\blsat\b|/api/ln/|\bbolt11\b|macaroon")
# Phrases that offer the rail whatever else the line says. "retired" on the
# same line excuses a bare mention (the retired notices below), never these.
OFFER_PHRASES = (
    "pay-per-anchor over lightning",
    "over lightning for agents",
    "pay over lightning",
    "pay with lightning",
    "pay in sats",
    "sats for",
    "sats per",
    "price_sats",
    "pay the invoice",
    "lightning l402 pay-per-anchor",
    "macaroon",
    "bolt11",
)
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2",
                   ".ots", ".zip", ".gz", ".pdf"}


def _offers_lightning(line: str) -> bool:
    low = line.lower()
    if any(p in low for p in OFFER_PHRASES):
        return True
    return bool(LN_TOKENS.search(line)) and "retired" not in low


def _scan(root: Path) -> tuple[list[str], list[Path], list[Path]]:
    """(offending lines, files scanned, files skipped as binary) for every
    file under root. Every file, not a suffix list: a page added in a new
    format is read like the rest."""
    hits, scanned, skipped = [], [], []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(p)
            continue
        scanned.append(p)
        for n, line in enumerate(text.splitlines(), 1):
            if _offers_lightning(line):
                hits.append(f"{p.relative_to(root).as_posix()}:{n}: {line.strip()[:160]}")
    return hits, scanned, skipped


def test_no_file_under_web_offers_lightning_payment() -> None:
    hits, scanned, skipped = _scan(WEB)
    assert hits == [], "Lightning payment is offered again:\n" + "\n".join(hits)
    # CONTROLS: the walk reached the pages that carried the offer, and only
    # real binaries were skipped.
    names = {p.relative_to(WEB).as_posix() for p in scanned}
    assert {"llms.txt", "index.html", "pricing.html", "v2/index.html",
            "docs/api.html", "docs/agents.html", "lp/agent-receipts.html"} <= names
    assert len(scanned) > 200, len(scanned)
    assert {p.suffix for p in skipped} <= BINARY_SUFFIXES, sorted({p.suffix for p in skipped})


def test_the_scan_fires_on_a_planted_offer(tmp_path) -> None:
    """The scan is known to fire: the copy this retirement removed, planted
    in a file, is reported; the retired notices and the product copy are not."""
    must_fire = (
        "<p><strong>Coming: pay-per-anchor over Lightning for agents &mdash; no account "
        "needed.</strong> Not yet open; the <a href=\"/pricing\">schedule of fees</a> will "
        "record it the day it is.</p>",
        "- POST https://orphograph.com/api/ln/quote — Lightning L402 pay-per-anchor for",
        "  retry /api/anchor with Authorization: L402 <macaroon>:<preimage_hex>.",
        "Lightning is retired. Pay over Lightning instead at /api/ln/quote.",
        "Agents can pay in sats for one anchor.",
    )
    must_not_fire = (
        "- POST https://orphograph.com/api/ln/quote: retired, answers 410 Gone. Lightning",
        "Anchor a file's SHA-256 fingerprint to the Bitcoin blockchain",
        "Pay with crypto",
        "<p><strong>Preimage resistance.</strong> Given the 32-byte output",
    )
    planted = tmp_path / "web"
    (planted / "docs").mkdir(parents=True)
    (planted / "docs" / "planted.html").write_text("\n".join(must_fire), encoding="utf-8")
    (planted / "clean.txt").write_text("\n".join(must_not_fire), encoding="utf-8")
    hits, scanned, _skipped = _scan(planted)
    assert len(scanned) == 2
    assert len(hits) == len(must_fire), "\n".join(hits)
    assert all(h.startswith("docs/planted.html:") for h in hits), hits


def test_the_api_docs_mark_the_quote_endpoint_retired() -> None:
    """Where the endpoint was listed, it is now marked retired with its 410."""
    for rel in ("llms.txt", "docs/api.html"):
        lines = [line for line in (WEB / rel).read_text(encoding="utf-8").splitlines()
                 if "/api/ln/quote" in line]
        assert lines, f"{rel} no longer names /api/ln/quote at all"
        for line in lines:
            assert "retired" in line.lower() and "410" in line, f"{rel}: {line.strip()}"


def test_the_published_health_example_reports_the_rail_retired() -> None:
    docs = (WEB / "docs" / "api.html").read_text(encoding="utf-8")
    assert re.search(r'^\s*"lightning":\s*\{"rail": "retired", "configured": false\},?\s*$',
                     docs, re.MULTILINE), "docs/api.html health example lacks the lightning block"


@pytest.mark.parametrize("path, n_leaves", [("/api/anchor_folder", 55_000), ("/api/anchor/batch", 600)])
def test_the_retired_answer_reaches_a_client_that_sent_a_large_body(tmp_path, path, n_leaves) -> None:
    """Bundle review round 1 (LOW, reproduced). The L402 refusal drained only
    MAX_BODY_BYTES (4096) of the body, while the folder route takes up to 8
    MiB and the batch route 64 KiB: the unread rest made the close an RST, and
    the client saw a reset instead of the documented 410. The refusal drains
    with the route's own cap."""
    if path == "/api/anchor_folder":
        body = json.dumps({"merkle_root": "ab" * 32, "leaves": [
            {"path": f"dir/file-{i}.txt", "sha256": f"{i:064x}"} for i in range(1, n_leaves)]}).encode()
    else:
        body = json.dumps({"hashes": [{"hash_hex": f"{i:064x}"} for i in range(1, n_leaves)]}).encode()
    assert len(body) > 4096
    for base in _srv.server_processes(tmp_path, stub_calendars=True):
        for _ in range(5):
            status, raw, _h = _srv.request(base, path, "POST", body=body, headers={
                "Content-Type": "application/json", "Authorization": "L402 abc:def"}, timeout=30)
            assert status == 410, (status, raw[:120])
            _assert_retired_body(json.loads(raw))
