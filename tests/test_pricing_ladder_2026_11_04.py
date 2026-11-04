"""test_pricing_ladder_2026_11_04.py — the pack ladder the founder set on
2026-11-04, held end to end.

The ladder:
  * Writer Pack: $5 for 25 anchors (was $19 for 10). Card plan `pack`,
    crypto plan `writer_pack`.
  * Pack of 200 / "Pack of Two Hundred": $20 for 200 anchors (was the $29
    "Pack of Fifty", 50 anchors). Card plan `pack50`, crypto plan `pack_50`.
  * Standing Order $9/month, free tier and x402 unchanged.

What this file holds, and why each part exists:
  (a) No customer-visible page under web/ still shows the retired ladder.
      A stale "$19" next to a 25-credit grant is a contract the buyer did not
      agree to. The scan has a planted-defect control so it cannot pass by
      matching nothing.
  (b) The REAL /api/stripe/checkout handler, with Stripe stubbed, sends an
      explicit credit_count on EVERY pack session: 25 for `pack`, 200 for
      `pack50`. Before this change the `pack` session sent none and leaned on
      the PACK_CREDIT_COUNT default.
  (c) The Stripe webhook grants 25 when a session carries no credit_count
      (a Payment Link) and 200 when it carries credit_count=200.
  (d) nowpayments_api.PLANS, nowpayments_webhook.PLAN_CREDITS,
      public_config pricing and web/pricing.html all state the same numbers.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import _srv

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT / "server"))

import credits  # noqa: E402
import nowpayments_api  # noqa: E402
import nowpayments_webhook  # noqa: E402
import public_config  # noqa: E402
import stripe_webhook  # noqa: E402

WRITER_USD, WRITER_CREDITS = 5, 25
BIG_USD, BIG_CREDITS = 20, 200

# ── (a) no retired ladder on a customer-visible page ─────────────────────────

# Pages under web/ that are never served to a customer: design mockups, and
# index-legacy, which server/app.py lists in _PRIVATE_PATH_EXACT.
NOT_SERVED = ("web/_mockups/", "web/index-legacy.html")
SCANNED_SUFFIXES = (".html", ".js", ".xml", ".json", ".txt", ".md")

RETIRED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<![\d.])\$(?:19|29)\b"), "retired pack price $19/$29"),
    (re.compile(r"Pack\s+of\s+(?:Fifty|50)\b", re.I), "retired name Pack of Fifty / Pack of 50"),
    (re.compile(r"\b(?:10|50|ten|fifty)[- ](?:anchor|credit)[- ]?(?:pack|credits)\b", re.I),
     "a 10- or 50-anchor pack"),
    (re.compile(r"\b(?:10|50)-(?:anchor|pack)\b", re.I), "a 10- or 50-anchor pack"),
    (re.compile(r"\b(?:fifty|ten)\s+(?:anchors|receipts|credits)\s+that\s+never\s+expire", re.I),
     "a 10- or 50-anchor pack"),
    (re.compile(r"Writer\s+Pack\s*(?:\(|of\s+)10\b", re.I), "a 10-anchor Writer Pack"),
    (re.compile(r"Writer\s+Pack[^.<\n]{0,30}\b10\s+anchors\b", re.I), "a 10-anchor Writer Pack"),
]


def _served_web_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "web"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    # Untracked new pages are customer-visible the moment they merge, and
    # `git ls-files` alone cannot see them.
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "web"],
                               cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    files = []
    for rel in sorted(set(out) | set(untracked)):
        if rel.startswith(NOT_SERVED) or not rel.endswith(SCANNED_SUFFIXES):
            continue
        p = ROOT / rel
        if p.is_file():
            files.append(p)
    return files


def _retired_hits(text: str) -> list[str]:
    hits = []
    for pattern, reason in RETIRED:
        for m in pattern.finditer(text):
            hits.append(f"{m.group(0)!r} ({reason})")
    return hits


def test_scan_covers_the_pages_that_carried_the_old_ladder():
    files = {p.relative_to(ROOT).as_posix() for p in _served_web_files()}
    for rel in ("web/pricing.html", "web/terms.html", "web/v2/index.html",
                "web/pay/crypto.html", "web/pay/crypto.js", "web/gift.html",
                "web/blog/atom.xml", "web/v2.js", "web/lp/start.html"):
        assert rel in files, f"{rel} fell out of the scan"


@pytest.mark.parametrize("planted", [
    "Writer Pack $19 one-time",
    "Pack of Fifty — 50 anchors",
    "$29 · Pack of 50 (50 anchors)",
    "<li>10 anchor credits</li>",
    "<li>50 anchor credits</li>",
    "$19 for a 10-anchor pack",
    "A Writer Pack (10 anchors, $5)",
    "covers fifty anchors that never expire",
])
def test_the_scan_catches_each_retired_phrase(planted):
    """Planted-defect control: every phrase the old ladder used is caught."""
    assert _retired_hits(planted), planted


@pytest.mark.parametrize("current", [
    "Writer Pack — 25 anchors · $5",
    "Pack of Two Hundred — 200 anchors · $20",
    "$20 · Pack of 200 (200 anchors)",
    "<li>25 anchor credits</li>",
    "Standing Order $9/month",
    "3 anchors every 24 hours",
    "$1.9 billion",
])
def test_the_scan_passes_current_copy(current):
    assert not _retired_hits(current), current


def test_no_served_page_shows_the_retired_ladder():
    hits = []
    for path in _served_web_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for h in _retired_hits(text):
            hits.append(f"{path.relative_to(ROOT)}: {h}")
    assert not hits, "retired pack ladder still on a served page:\n" + "\n".join(hits)


# ── (b) the real checkout sends an explicit count on every pack session ──────

STRIPE_CALLS = "stub_stripe_calls.jsonl"
EGRESS = "stub_egress_blocked.jsonl"


@pytest.fixture(scope="module")
def checkout_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("pricing_ladder")
    for base in _srv.server_processes(
            d, stub_calendars=True, stub_stripe=True, egress_guard=True,
            STRIPE_SECRET_KEY="sk_test_not_a_real_key",
            STRIPE_PRICE_PACK="price_test_pack_not_real",
            STRIPE_PRICE_PACK50="price_test_pack50_not_real",
            CHECKOUT_RATE_PER_HOUR="1000"):
        yield base, d
    tried = {row["host"] for row in _rows(d / EGRESS)}
    assert not tried, f"checkout tests tried to reach {tried}"


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _checkout(base: str, d: Path, plan: str) -> dict:
    before = len(_rows(d / STRIPE_CALLS))
    status, body = _srv.post_json(base, "/api/stripe/checkout",
                                  {"plan": plan, "email": "ladder@example.test"},
                                  {"Content-Type": "application/json"}, timeout=30)
    assert status == 200, (plan, status, body)
    sent = _rows(d / STRIPE_CALLS)[before:]
    assert [c["path"] for c in sent] == ["/checkout/sessions"], sent
    return sent[0]["form"]


@pytest.mark.parametrize("plan,price,credit_count,meta_plan", [
    ("pack", "price_test_pack_not_real", str(WRITER_CREDITS), "pack"),
    ("pack50", "price_test_pack50_not_real", str(BIG_CREDITS), "pack50"),
    ("pack_50", "price_test_pack50_not_real", str(BIG_CREDITS), "pack50"),
])
def test_checkout_sends_explicit_credit_count(checkout_server, plan, price,
                                              credit_count, meta_plan):
    base, d = checkout_server
    form = _checkout(base, d, plan)
    assert form["line_items[0][price]"] == price, form
    assert form.get("metadata[credit_count]") == credit_count, form
    assert form.get("metadata[plan]") == meta_plan, form


# ── (c) the webhook grants what the ladder says ──────────────────────────────

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(stripe_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "events.jsonl")
    monkeypatch.setattr(stripe_webhook, "PI_SESSION_MAP_PATH", tmp_path / "pi_map.jsonl")
    monkeypatch.setattr(nowpayments_webhook, "PROCESSED_EVENTS_PATH", tmp_path / "np_events.jsonl")
    path = tmp_path / "credit_ledger.jsonl"
    monkeypatch.setattr(credits, "LEDGER_PATH", path)
    return path


def _granted(path: Path) -> int:
    return sum(r["credits_delta"] for r in _rows(path))


def _session_completed(session_id: str, metadata: dict | None) -> bytes:
    obj = {"id": session_id, "customer_email": "ladder@example.test"}
    if metadata is not None:
        obj["metadata"] = metadata
    return json.dumps({"type": "checkout.session.completed",
                       "data": {"object": obj}}).encode()


def test_stripe_default_grant_is_the_writer_pack(ledger):
    assert stripe_webhook.PACK_CREDITS == WRITER_CREDITS
    result = stripe_webhook.handle_event(_session_completed("cs_ladder_nometa", None))
    assert result.get("claim_code_minted") is True, result
    assert _granted(ledger) == WRITER_CREDITS


def test_stripe_pack_of_200_grants_200(ledger):
    result = stripe_webhook.handle_event(
        _session_completed("cs_ladder_200", {"credit_count": "200", "plan": "pack50"}))
    assert result.get("claim_code_minted") is True, result
    assert _granted(ledger) == BIG_CREDITS


def test_stripe_explicit_writer_pack_metadata_grants_25(ledger):
    result = stripe_webhook.handle_event(
        _session_completed("cs_ladder_25", {"credit_count": "25", "plan": "pack"}))
    assert result.get("claim_code_minted") is True, result
    assert _granted(ledger) == WRITER_CREDITS


@pytest.mark.parametrize("tag,fields,plan,granted", [
    ("a", {"plan": "writer_pack"}, "writer_pack", WRITER_CREDITS),
    ("b", {"plan": "pack_50"}, "pack_50", BIG_CREDITS),
    # No plan key and no plan token in order_id: only the human-readable
    # description names the pack, and the new name must still resolve.
    ("c", {"order_description": "Orphograph Pack of 200"}, "pack_50", BIG_CREDITS),
])
def test_crypto_ipn_grants_the_ladder(ledger, tag, fields, plan, granted):
    body = {"order_id": f"np_opaque_{tag}", "payment_status": "finished",
            "invoice_id": f"inv_{tag}", "customer_email": "ladder@example.test", **fields}
    result = nowpayments_webhook.handle_event(json.dumps(body).encode())
    assert result.get("claim_code_minted") is True, result
    assert result["plan"] == plan
    assert _granted(ledger) == granted


# ── (d) every surface states the same numbers ────────────────────────────────

def test_plans_config_and_pricing_page_agree(monkeypatch):
    for var in ("PACK_PRICE_USD", "PACK_CREDIT_COUNT",
                "PACK50_PRICE_USD", "PACK50_CREDIT_COUNT"):
        monkeypatch.delenv(var, raising=False)
    pricing = public_config.snapshot()["pricing"]
    plans = nowpayments_api.PLANS

    assert (pricing["pack_usd"], pricing["pack_credits"]) == (WRITER_USD, WRITER_CREDITS)
    assert (pricing["pack50_usd"], pricing["pack50_credits"]) == (BIG_USD, BIG_CREDITS)
    assert plans["writer_pack"] == {"price_usd": WRITER_USD, "credit_count": WRITER_CREDITS,
                                    "label": "Writer Pack"}
    assert plans["pack_50"] == {"price_usd": BIG_USD, "credit_count": BIG_CREDITS,
                                "label": "Pack of 200"}
    assert nowpayments_webhook.PLAN_CREDITS == {
        k: v["credit_count"] for k, v in plans.items()}

    html = re.sub(r"<!--.*?-->", "", (WEB / "pricing.html").read_text(), flags=re.DOTALL)
    for name, usd, n in (("Writer Pack", pricing["pack_usd"], pricing["pack_credits"]),
                         ("Pack of Two Hundred", pricing["pack50_usd"], pricing["pack50_credits"])):
        card = re.search(
            r'<div class="t-name">%s</div>\s*<div class="t-price">\$(\d+)<small> one-time'
            r'.*?<li>(\d+) anchor credits</li>' % re.escape(name), html, re.DOTALL)
        assert card, f"{name} tier card not found on web/pricing.html"
        assert (int(card.group(1)), int(card.group(2))) == (usd, n), name
        assert re.search(r"<strong>%s</strong> — %d anchors · \$%d</span>"
                         % (re.escape(name), n, usd), html), f"{name} price strip"


def test_crypto_page_offers_only_the_pack_of_200():
    html = (WEB / "pay" / "crypto.html").read_text()
    assert re.findall(r'data-plan="([^"]+)"', html) == ["pack_50"]
    label = re.search(r'data-plan="pack_50"[^>]*>\s*([^<]+?)\s*</button>', html).group(1)
    assert label == f"${BIG_USD} · Pack of 200 ({BIG_CREDITS} anchors)"
