"""Three guards added 2026-07-26.

1. ATTESTATION REPORTING — `status` answers "are ALL calendars Bitcoin-pinned?",
   which is permanently "partial" for every receipt ever issued: measured on all
   214 production receipts, a.pool and b.pool.opentimestamps.org return HTTP 404
   for their commitments and never upgrade, while alice/btc/finney upgrade every
   time. The upgrade worker correctly freezes after repeated no-progress runs.
   The API therefore said "partial" about receipts that ARE anchored, to the
   exact audience — SDKs, third-party verifiers — most likely to read that as
   incomplete. `bitcoin_attested` answers the question they actually have.

2. CLAIM CEILING ON CUSTOMER SURFACES — the ceiling is existence-by-a-time.
   Not authorship, not truth of contents, not admissibility.

3. CUSTODY (Wedge 02) — the promise is that a receipt outlives the service.
   The brief's own test for it had never been run.
"""

import subprocess
import tempfile
import sys
from pathlib import Path

import pytest

from conftest import write_fixture_receipt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WEB = ROOT / "web"


# ───────────────────── 1. attestation reporting ─────────────────────
def _local_receipts_dir():
    """Real local receipts when the checkout has them (backdata), otherwise a
    synthetic receipt built by conftest.write_fixture_receipt — never an empty
    parameter set, which pytest reports as a skip and CI reads as green."""
    d = ROOT / "data" / "receipts"
    if d.is_dir() and any((p / "receipt.json").is_file() for p in d.iterdir()):
        return d
    synth = Path(tempfile.mkdtemp(prefix="orpho-fixture-receipts-"))
    write_fixture_receipt(synth)
    return synth


RECEIPTS_DIR = _local_receipts_dir()


def _receipt_ids():
    return [p.name for p in RECEIPTS_DIR.iterdir() if (p / "receipt.json").is_file()][:5]


@pytest.mark.parametrize("rid", _receipt_ids())
def test_verify_receipt_reports_bitcoin_attestation(rid, monkeypatch):
    from server import engine
    # Pin RECEIPTS_DIR explicitly. These ids are resolved at collection time,
    # but other tests in the suite repoint the module global, so without this
    # the result depends on execution order — it passed alone and failed in
    # the full run.
    monkeypatch.setattr(engine, "RECEIPTS_DIR", RECEIPTS_DIR)
    out = engine.verify_receipt(rid)
    assert out["found"] is True
    assert isinstance(out.get("bitcoin_attested"), bool)
    for field in ("bitcoin_attested", "pinned_count", "pinned_total", "upgrade_frozen"):
        assert field in out, f"{field} missing from verify_receipt output"
    assert isinstance(out["bitcoin_attested"], bool)
    # The invariant that makes the field meaningful.
    assert out["bitcoin_attested"] == (out["pinned_count"] > 0)


def test_bitcoin_attested_is_true_when_any_calendar_pinned(tmp_path, monkeypatch):
    """A receipt pinned on 3 of 5 calendars is anchored, and must say so."""
    from server import engine
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path)
    d = tmp_path / "r_test"
    d.mkdir()
    (d / "receipt.json").write_text(
        '{"hash_hex": "%s", "created_at": "2026-01-01T00:00:00+00:00",'
        ' "status": "partial", "pinned_count": 3, "pinned_total": 5,'
        ' "upgrade_frozen": true}' % ("ab" * 32)
    )
    out = engine.verify_receipt("r_test")
    assert out["status"] == "partial"          # unchanged semantics
    assert out["bitcoin_attested"] is True     # the honest answer
    assert out["pinned_count"] == 3
    assert out["upgrade_frozen"] is True


def test_bitcoin_attested_false_when_nothing_pinned(tmp_path, monkeypatch):
    from server import engine
    monkeypatch.setattr(engine, "RECEIPTS_DIR", tmp_path)
    d = tmp_path / "r_none"
    d.mkdir()
    (d / "receipt.json").write_text(
        '{"hash_hex": "%s", "created_at": "2026-01-01T00:00:00+00:00",'
        ' "status": "pending", "pinned_count": 0, "pinned_total": 5}' % ("cd" * 32)
    )
    out = engine.verify_receipt("r_none")
    assert out["bitcoin_attested"] is False


# ───────────────────── 2. claim ceiling ─────────────────────
CUSTOMER_PAGES = [
    p for p in WEB.rglob("*.html")
    if "_mockups" not in p.parts and "index-legacy" not in p.name
]

# Claims outside the ceiling. Each maps to the reason it is forbidden so a
# future reader knows why rather than just seeing a banned word.
FORBIDDEN = {
    "tamper-proof": "only 'tamper-evident' is defensible",
    "court-admissib": "admissibility is a court's decision, not ours",
    "legally binding": "we do not make anything legally binding",
    "proves you wrote": "authorship is outside the ceiling",
}

# The site MENTIONS every one of these — correctly, in disclaimers and referrals
# ("We make no claim of court-admissibility"; "for legally binding contexts:
# consult the relevant national QTSP list"). A bare keyword rule would flag that
# honest writing as a violation, which is how a guard trains people to delete
# their own disclaimers. So the rule is: a mention must be DISCLAIMED.
_DISCLAIMERS = (
    "no claim", "not ", "n't ", "never ", "consult", "does not", "do not",
    "outside", "no federal", "cannot", "separate", "beyond", "unlike",
    "instead", "rather than", "is not", "aren't", "won't",
)


def _is_disclaimed(text: str, idx: int, window: int = 160) -> bool:
    """True if the mention sits inside a denial or a referral elsewhere.

    Looks BOTH directions: "we make no claim of court-admissibility" puts the
    denial before the phrase, while "for legally binding contexts: consult the
    relevant national QTSP list" puts the referral after it. A backwards-only
    window flags the second one, which is honest writing.
    """
    around = text[max(0, idx - window):idx + window]
    return any(d in around for d in _DISCLAIMERS)


@pytest.mark.parametrize("page", CUSTOMER_PAGES, ids=lambda p: p.name)
def test_no_claims_beyond_the_ceiling(page):
    text = page.read_text(encoding="utf-8", errors="replace").lower()
    for phrase, why in FORBIDDEN.items():
        start = 0
        while (idx := text.find(phrase, start)) != -1:
            assert _is_disclaimed(text, idx), (
                f"{page.name}: {phrase!r} asserted without a disclaimer — {why}\n"
                f"  context: ...{text[max(0, idx-90):idx+60]}..."
            )
            start = idx + len(phrase)


def test_no_authorship_claim_on_landing_pages():
    """'proof of authorship' as a CLAIM. web/writers.html uses it correctly as a
    denial ('Evidence of process, NOT proof of authorship') — that must survive."""
    for page in (WEB / "lp").rglob("*.html"):
        text = page.read_text(encoding="utf-8", errors="replace").lower()
        if "proof of authorship" in text:
            idx = text.index("proof of authorship")
            preceding = text[max(0, idx - 40):idx]
            assert "not " in preceding, (
                f"{page.name} asserts proof of authorship, which is outside the ceiling"
            )


def test_writers_disclaimer_is_intact():
    """The model wording. If this disappears, the honest framing went with it."""
    text = (WEB / "writers.html").read_text(encoding="utf-8")
    assert "not proof of authorship" in text.lower()


def test_no_absolute_forgery_promise_on_homepage():
    """'the date can't be forged or revoked' was an absolute capability promise,
    and a shaky one: 5 calendars accept a commitment but only 3 ever carry a
    Bitcoin attestation."""
    text = (WEB / "index.html").read_text(encoding="utf-8").lower()
    assert "be forged or revoked" not in text


# ───────────────────── 3. custody (Wedge 02) ─────────────────────
NETWORK_MODULES = ("urllib", "socket", "http.client", "requests", "httpx", "ssl")


def test_standalone_verifier_has_no_network_capability():
    """The custody promise, made structural rather than promissory.

    A receipt is supposed to outlive the office. If the verifier could call
    home, that promise would depend on the office answering. It cannot: the
    checker imports only argparse, hashlib, json, sys and pathlib.
    """
    src = (ROOT / "server" / "verify_cli.py").read_text()
    import_lines = [ln for ln in src.splitlines()
                    if ln.startswith(("import ", "from "))]
    joined = " ".join(import_lines)
    for mod in NETWORK_MODULES:
        assert mod not in joined, (
            f"verify_cli.py imports {mod!r}; the verifier must not be able to "
            f"reach the network, or 'verifies without us' is unenforceable"
        )


def test_verifier_runs_with_no_account_and_no_env():
    """No API key, no account, no service configuration of any kind."""
    ids = _receipt_ids()
    assert ids, "fixture receipt missing — conftest.write_fixture_receipt failed"
    rdir = RECEIPTS_DIR / ids[0]
    proc = subprocess.run(
        [sys.executable, str(ROOT / "server" / "verify_cli.py"),
         str(rdir / "receipt.json")],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin"},          # nothing else: no keys, no config
        cwd=rdir,
    )
    assert proc.returncode == 0, proc.stderr
    assert "hash_match=True" in proc.stdout
