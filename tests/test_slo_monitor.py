"""test_slo_monitor - local/public SLO evidence monitor tests.

Network is mocked. Tests never call production, GitHub, Fly, Stripe, or any
other external service.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "slo_monitor.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("slo_monitor", str(SCRIPT_PATH))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["slo_monitor"] = mod
    spec.loader.exec_module(mod)
    return mod


slo = _load_module()


def _cfg(tmp_path: Path, **kw):
    base = {
        "base_url": "https://example.test",
        "receipts_dir": tmp_path / "receipts",
        "upgrade_log": tmp_path / "upgrade_log.jsonl",
        "log_path": tmp_path / "slo.jsonl",
        "report_dir": tmp_path / "reports",
    }
    base.update(kw)
    return slo.MonitorConfig(**base)


def _http(status: int, payload, latency: float = 100.0):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return status, body, {}, latency


def _write_receipt(base: Path, rid: str, created_at: datetime, status: str = "pending", **extra):
    rdir = base / rid
    rdir.mkdir(parents=True)
    data = {
        "receipt_id": rid,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "status": status,
    }
    data.update(extra)
    (rdir / "receipt.json").write_text(json.dumps(data), encoding="utf-8")


def test_run_checks_all_pass_with_mocked_public_endpoints(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.receipts_dir.mkdir()
    now = datetime(2026, 6, 8, 12, tzinfo=timezone.utc)
    _write_receipt(cfg.receipts_dir, "pinned-receipt", now - timedelta(hours=2), status="pinned")

    responses = {
        "https://example.test/": _http(200, b"<html>ok</html>", 120.0),
        "https://example.test/api/health": _http(200, {"ok": True, "uptime_sec": 42, "version": "0.1.0"}, 80.0),
        "https://example.test/api/config": _http(200, {"toggles": {"maintenance_mode": False, "anchoring_disabled": False}}, 75.0),
        "https://example.test/api/stats": _http(200, {"anchors": {"total": 3, "last_24h": 1, "last_7d": 2}}, 90.0),
    }
    monkeypatch.setattr(slo, "_http_get", lambda url, timeout=slo.HTTP_TIMEOUT_S: responses[url])

    findings = slo.run_checks(cfg, now=now)
    summary = slo.summarize(findings, when=now)

    assert summary["overall"] == slo.PASS
    assert summary["counts"][slo.FAIL] == 0
    assert {f.name for f in findings} == {
        "site_root", "api_health", "public_config", "public_stats",
        "local_receipts", "upgrade_log",
    }


def test_health_ok_false_is_failure(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        slo,
        "_http_get",
        lambda url, timeout=slo.HTTP_TIMEOUT_S: _http(200, {"ok": False}, 10.0),
    )

    f = slo.check_api_health(cfg)

    assert f.status == slo.FAIL
    assert "health.ok" in f.summary


def test_checkout_disabled_warns_but_maintenance_fails(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        slo,
        "_http_get",
        lambda url, timeout=slo.HTTP_TIMEOUT_S: _http(200, {"toggles": {"checkout_disabled": True}}, 10.0),
    )
    assert slo.check_public_config(cfg).status == slo.WARN

    monkeypatch.setattr(
        slo,
        "_http_get",
        lambda url, timeout=slo.HTTP_TIMEOUT_S: _http(200, {"toggles": {"maintenance_mode": True}}, 10.0),
    )
    f = slo.check_public_config(cfg)
    assert f.status == slo.FAIL
    assert "maintenance_mode" in f.summary


def test_stale_pending_receipt_fails_and_redacts_id(tmp_path):
    cfg = _cfg(tmp_path, pending_sla_hours=36)
    now = datetime(2026, 6, 8, 12, tzinfo=timezone.utc)
    cfg.receipts_dir.mkdir()
    _write_receipt(
        cfg.receipts_dir,
        "receipt-secret-full-id",
        now - timedelta(hours=60),
        status="pending",
        upgrade_stalls=5,
    )

    f, summary = slo.scan_local_receipts(cfg, now=now)

    assert f.status == slo.FAIL
    assert summary["pending"] == 1
    assert summary["stale_pending"][0]["receipt"] == "receip..."
    assert "receipt-secret-full-id" not in f.summary
    assert "receipt-secret-full-id" not in f.details


def test_no_network_mode_skips_public_checks_and_scans_local_state(tmp_path):
    cfg = _cfg(tmp_path, no_network=True)
    cfg.receipts_dir.mkdir()
    now = datetime(2026, 6, 8, 12, tzinfo=timezone.utc)

    findings = slo.run_checks(cfg, now=now)
    by_name = {f.name: f for f in findings}

    assert by_name["site_root"].status == slo.SKIPPED
    assert by_name["api_health"].status == slo.SKIPPED
    assert by_name["local_receipts"].status == slo.PASS


def test_upgrade_log_stale_warns_when_pending_exists(tmp_path):
    cfg = _cfg(tmp_path, upgrade_stale_hours=6)
    now = datetime(2026, 6, 8, 12, tzinfo=timezone.utc)
    cfg.upgrade_log.write_text(
        json.dumps({"ts": "2026-06-08T00:00:00Z", "scanned": 1}) + "\n",
        encoding="utf-8",
    )
    receipt_summary = {"pending": 1}

    f = slo.check_upgrade_log_freshness(cfg, receipt_summary, now=now)

    assert f.status == slo.WARN
    assert "stale" in f.summary


def test_main_writes_jsonl_and_markdown_report(tmp_path, monkeypatch):
    cfg_args = [
        "--no-network",
        "--receipts-dir", str(tmp_path / "receipts"),
        "--upgrade-log", str(tmp_path / "upgrade.jsonl"),
        "--log", str(tmp_path / "slo.jsonl"),
        "--report-dir", str(tmp_path / "reports"),
        "--write-report",
        "--json",
    ]
    (tmp_path / "receipts").mkdir()

    rc = slo.main(cfg_args)

    assert rc == 0
    lines = (tmp_path / "slo.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["counts"][slo.SKIPPED] == 4
    reports = list((tmp_path / "reports").glob("*.md"))
    assert len(reports) == 1
    assert "Orphograph SLO monitor" in reports[0].read_text(encoding="utf-8")
