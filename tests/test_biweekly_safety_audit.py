"""test_biweekly_safety_audit.py — unit tests for the biweekly safety audit script.

All network/subprocess/Fly/Stripe interactions are mocked. Tests never touch the
real ~/orphograph data dir, never hit prod, and never depend on a running server.

Run with:  pytest -p no:anchorpy tests/test_biweekly_safety_audit.py
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "biweekly_safety_audit.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "biweekly_safety_audit", str(SCRIPT_PATH),
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["biweekly_safety_audit"] = mod
    spec.loader.exec_module(mod)
    return mod


audit = _load_module()


def _mock_http(status: int, payload):
    """Build a fake _http_get return tuple."""
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return (status, body, {})


# -------------------------------------------------- Finding dataclass


def test_finding_failed_true_when_fail():
    f = audit.Finding("s", audit.FAIL, "bad")
    assert f.failed is True
    assert f.section == "s"
    assert f.summary == "bad"


def test_finding_failed_false_when_pass_or_skipped():
    assert audit.Finding("s", audit.PASS, "ok").failed is False
    assert audit.Finding("s", audit.SKIPPED, "n/a").failed is False


# -------------------------------------------------- render_report


def test_render_report_contains_totals_and_date():
    when = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    findings = [
        audit.Finding("1. A", audit.PASS, "ok"),
        audit.Finding("2. B", audit.FAIL, "bad"),
        audit.Finding("3. C", audit.SKIPPED, "n/a"),
    ]
    text = audit.render_report(findings, when)
    assert "2026-05-18" in text
    assert "1 PASS" in text and "1 FAIL" in text and "1 SKIPPED" in text
    assert "2. B" in text


def test_render_report_no_fails_says_none():
    when = datetime(2026, 5, 18, tzinfo=timezone.utc)
    text = audit.render_report([audit.Finding("x", audit.PASS, "ok")], when)
    assert "## Findings (FAIL)" in text
    assert "None." in text


def test_render_report_includes_details_block():
    when = datetime(2026, 5, 18, tzinfo=timezone.utc)
    f = audit.Finding("s", audit.FAIL, "summary", details="DETAILS_LINE")
    text = audit.render_report([f], when)
    assert "DETAILS_LINE" in text
    assert "```" in text


# -------------------------------------------------- write_report


def test_write_report_creates_file_in_tmp(tmp_path):
    when = datetime(2026, 5, 18, tzinfo=timezone.utc)
    out = audit.write_report("hello body", when, reports_dir=tmp_path)
    assert out.exists()
    assert out.name == "2026-05-18.md"
    assert out.read_text(encoding="utf-8") == "hello body"


def test_write_report_creates_missing_dir(tmp_path):
    when = datetime(2026, 5, 18, tzinfo=timezone.utc)
    sub = tmp_path / "nested" / "AUDIT_REPORTS"
    out = audit.write_report("x", when, reports_dir=sub)
    assert out.exists() and sub.exists()


# -------------------------------------------------- inline-secrets scanner


def test_inline_secrets_clean_tree(tmp_path, monkeypatch):
    web = tmp_path / "web"
    server = tmp_path / "server"
    web.mkdir(); server.mkdir()
    (web / "index.html").write_text("<html>no secrets</html>", encoding="utf-8")
    (server / "app.py").write_text("API = os.environ['STRIPE_SECRET_KEY']\n", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    f = audit.check_inline_secrets()
    assert f.status == audit.PASS


def test_inline_secrets_detects_stripe_key(tmp_path, monkeypatch):
    web = tmp_path / "web"; web.mkdir()
    (web / "leak.js").write_text('STRIPE_SECRET_KEY="sk_live_abcd1234efgh5678"\n', encoding="utf-8")
    (tmp_path / "server").mkdir()
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    f = audit.check_inline_secrets()
    assert f.status == audit.FAIL
    assert "STRIPE_SECRET_KEY" in f.details
    # never echo the captured secret value itself
    assert "sk_live_abcd1234efgh5678" not in f.details


def test_inline_secrets_detects_resend_and_btc(tmp_path, monkeypatch):
    server = tmp_path / "server"; server.mkdir()
    (tmp_path / "web").mkdir()
    (server / "cfg.py").write_text(
        'RESEND_API_KEY="re_abcdefgh12345678"\n'
        'BTC_RECEIVE_ADDRESS="bc1qabcdefghijklmnopqrstuv"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    f = audit.check_inline_secrets()
    assert f.status == audit.FAIL
    assert "RESEND_API_KEY" in f.details
    assert "BTC_RECEIVE_ADDRESS" in f.details


def test_inline_secrets_skips_unknown_extensions(tmp_path, monkeypatch):
    web = tmp_path / "web"; web.mkdir()
    (tmp_path / "server").mkdir()
    (web / "leak.md").write_text('STRIPE_SECRET_KEY="sk_live_abcd1234efgh5678"', encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    f = audit.check_inline_secrets()
    assert f.status == audit.PASS


# -------------------------------------------------- _extract_machine_memory


def test_extract_machine_memory_finds_guest_block():
    data = {"Machines": [{"guest": {"memory_mb": 512}}, {"guest": {"memory_mb": 1024}}]}
    assert audit._extract_machine_memory(data) == 512


def test_extract_machine_memory_returns_none_when_absent():
    assert audit._extract_machine_memory({"Machines": [{"id": "x"}]}) is None


# -------------------------------------------------- check_fly_memory


def test_check_fly_memory_skipped_when_no_cli(monkeypatch):
    monkeypatch.setattr(audit.shutil, "which", lambda _name: None)
    f = audit.check_fly_memory()
    assert f.status == audit.SKIPPED


def test_check_fly_memory_fail_on_256mb(monkeypatch):
    monkeypatch.setattr(audit.shutil, "which", lambda _name: "/usr/local/bin/fly")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout=json.dumps({"Machines": [{"guest": {"memory_mb": 256}}]}),
        stderr="",
    )
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: fake)
    f = audit.check_fly_memory()
    assert f.status == audit.FAIL


def test_check_fly_memory_pass_on_512mb(monkeypatch):
    monkeypatch.setattr(audit.shutil, "which", lambda _name: "/usr/local/bin/fly")
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout=json.dumps({"Machines": [{"guest": {"memory_mb": 512}}]}),
        stderr="",
    )
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: fake)
    f = audit.check_fly_memory()
    assert f.status == audit.PASS


# -------------------------------------------------- check_api_health


def test_check_api_health_pass(monkeypatch):
    monkeypatch.setattr(audit, "_http_get",
                        lambda *a, **kw: _mock_http(200, {"ok": True, "uptime_sec": 42, "calendars": []}))
    f = audit.check_api_health()
    assert f.status == audit.PASS


def test_check_api_health_fail_when_ok_false(monkeypatch):
    monkeypatch.setattr(audit, "_http_get",
                        lambda *a, **kw: _mock_http(200, {"ok": False, "uptime_sec": 1, "calendars": []}))
    assert audit.check_api_health().status == audit.FAIL


def test_check_api_health_fail_on_unreachable_calendar(monkeypatch):
    payload = {"ok": True, "uptime_sec": 5,
               "calendars": [{"url": "https://x.example", "reachable": False}]}
    monkeypatch.setattr(audit, "_http_get", lambda *a, **kw: _mock_http(200, payload))
    f = audit.check_api_health()
    assert f.status == audit.FAIL
    assert "x.example" in f.summary


# -------------------------------------------------- check_genesis_receipt


def test_check_genesis_receipt_pass_pinned(monkeypatch):
    monkeypatch.setattr(audit, "_http_get", lambda *a, **kw: _mock_http(200, {"status": "pinned"}))
    assert audit.check_genesis_receipt().status == audit.PASS


def test_check_genesis_receipt_fail_pending(monkeypatch):
    monkeypatch.setattr(audit, "_http_get", lambda *a, **kw: _mock_http(200, {"status": "pending"}))
    assert audit.check_genesis_receipt().status == audit.FAIL


# -------------------------------------------------- check_kill_switch_state


def test_check_kill_switch_default_pass(monkeypatch):
    monkeypatch.setattr(audit, "_http_get",
                        lambda *a, **kw: _mock_http(200, {"toggles": {"maintenance_mode": False}}))
    assert audit.check_kill_switch_state().status == audit.PASS


def test_check_kill_switch_flipped_fails(monkeypatch):
    monkeypatch.setattr(audit, "_http_get",
                        lambda *a, **kw: _mock_http(200, {"toggles": {"checkout_disabled": True}}))
    f = audit.check_kill_switch_state()
    assert f.status == audit.FAIL
    assert "checkout_disabled" in f.summary


# -------------------------------------------------- check_calendars_reachable


def test_check_calendars_all_reachable(monkeypatch):
    monkeypatch.setattr(audit, "_head", lambda url, timeout=8: 200)
    assert audit.check_calendars_reachable().status == audit.PASS


def test_check_calendars_one_down_passes(monkeypatch):
    seq = {audit.CALENDARS[0]: 0}
    monkeypatch.setattr(audit, "_head", lambda url, timeout=8: seq.get(url, 200))
    f = audit.check_calendars_reachable()
    assert f.status == audit.PASS
    assert "within tolerance" in f.summary


def test_check_calendars_two_down_fails(monkeypatch):
    bad = set(audit.CALENDARS[:2])
    monkeypatch.setattr(audit, "_head", lambda url, timeout=8: 0 if url in bad else 200)
    assert audit.check_calendars_reachable().status == audit.FAIL


# -------------------------------------------------- check_receipts_pending


def test_check_receipts_pending_skipped_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "DATA_RECEIPTS", tmp_path / "nope")
    assert audit.check_receipts_pending().status == audit.SKIPPED


def test_check_receipts_pending_clean(tmp_path, monkeypatch):
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    sub = tmp_path / "abc"; sub.mkdir()
    (sub / "receipt.json").write_text(
        json.dumps({"status": "pending", "created_at": recent, "receipt_id": "abc123xyz"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "DATA_RECEIPTS", tmp_path)
    assert audit.check_receipts_pending().status == audit.PASS


def test_check_receipts_pending_stuck_fails(tmp_path, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    sub = tmp_path / "old1"; sub.mkdir()
    (sub / "receipt.json").write_text(
        json.dumps({"status": "pending", "created_at": old, "receipt_id": "stuckaaa"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "DATA_RECEIPTS", tmp_path)
    f = audit.check_receipts_pending()
    assert f.status == audit.FAIL
    # receipt_id is truncated to first 6 chars; full id should not appear
    assert "stuckaaa" not in f.summary


# -------------------------------------------------- check_hmac_secret_history


def test_check_hmac_secret_history_skipped_no_git(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    assert audit.check_hmac_secret_history().status == audit.SKIPPED


def test_check_hmac_secret_history_clean(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: fake)
    assert audit.check_hmac_secret_history().status == audit.PASS


def test_check_hmac_secret_history_leak(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout="abc123 leaked it\n", stderr="")
    monkeypatch.setattr(audit.subprocess, "run", lambda *a, **kw: fake)
    f = audit.check_hmac_secret_history()
    assert f.status == audit.FAIL
    # commit subject must not be echoed (it could contain secrets)
    assert "leaked it" not in f.summary


# -------------------------------------------------- check_stripe_reconcile


def test_check_stripe_reconcile_skipped_no_env(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert audit.check_stripe_reconcile().status == audit.SKIPPED


# -------------------------------------------------- check_css_cache_key


def test_check_css_cache_key_pass(tmp_path, monkeypatch):
    web = tmp_path / "web"; web.mkdir()
    (web / "index.html").write_text('<link href="/index.css?v=42">', encoding="utf-8")
    monkeypatch.setattr(audit, "WEB_INDEX", web / "index.html")
    monkeypatch.setattr(audit, "_http_get",
                        lambda *a, **kw: _mock_http(200, b'<link href="/index.css?v=42">'))
    assert audit.check_css_cache_key().status == audit.PASS


def test_check_css_cache_key_fail_when_prod_lags(tmp_path, monkeypatch):
    web = tmp_path / "web"; web.mkdir()
    (web / "index.html").write_text('<link href="/index.css?v=99">', encoding="utf-8")
    monkeypatch.setattr(audit, "WEB_INDEX", web / "index.html")
    monkeypatch.setattr(audit, "_http_get",
                        lambda *a, **kw: _mock_http(200, b'<link href="/index.css?v=42">'))
    assert audit.check_css_cache_key().status == audit.FAIL


# -------------------------------------------------- run_all + main


def test_run_all_catches_crashing_check(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(audit, "CHECKS", [boom])
    findings = audit.run_all()
    assert len(findings) == 1
    assert findings[0].status == audit.FAIL
    assert "kaboom" in findings[0].summary


def test_main_exit_zero_on_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audit, "CHECKS", [lambda: audit.Finding("ok", audit.PASS, "all good")])
    monkeypatch.setattr(audit, "REPORTS_DIR", tmp_path)
    rc = audit.main([])
    assert rc == 0
    assert "0 FAIL" in capsys.readouterr().out


def test_main_exit_one_on_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(audit, "CHECKS", [lambda: audit.Finding("bad", audit.FAIL, "nope")])
    monkeypatch.setattr(audit, "REPORTS_DIR", tmp_path)
    rc = audit.main([])
    assert rc == 1
    assert "1 FAIL" in capsys.readouterr().out


# -------------------------------------------------- _head helper


def test_head_returns_zero_on_socket_timeout(monkeypatch):
    def raiser(*a, **kw):
        raise socket.timeout("slow")
    monkeypatch.setattr(audit.urllib.request, "urlopen", raiser)
    assert audit._head("https://example.invalid") == 0


def test_head_returns_http_error_code(monkeypatch):
    def raiser(*a, **kw):
        raise urllib.error.HTTPError("u", 503, "x", {}, None)
    monkeypatch.setattr(audit.urllib.request, "urlopen", raiser)
    assert audit._head("https://example.invalid") == 503
