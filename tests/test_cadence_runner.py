from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = ROOT / "scripts" / "cadence_runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("cadence_runner_under_test", RUNNER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _isolate_runner(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "DATA_DIR", tmp_path)
    monkeypatch.setattr(mod, "PROSPECTS_CSV", tmp_path / "prospects.csv")
    monkeypatch.setattr(mod, "STATE_LOG", tmp_path / "cadence_state.jsonl")
    monkeypatch.setattr(mod, "SUPPRESSIONS", tmp_path / "suppressions.jsonl")
    monkeypatch.setattr(mod, "AUDIT_LOG", tmp_path / "cadence_audit.jsonl")


def _write_prospects(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["email", "first_name", "vertical", "public_detail", "added_iso"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _audit_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_execute_requires_explicit_automation_env(tmp_path, monkeypatch, capsys):
    mod = _load_runner()
    _isolate_runner(mod, tmp_path, monkeypatch)
    monkeypatch.delenv("ORPHO_CADENCE_AUTOMATION_ENABLED", raising=False)
    monkeypatch.delenv("ORPHO_CADENCE_DISABLED", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cadence_runner.py", "--execute", "--force-day-of-week"],
    )

    assert mod.main() == 2
    err = capsys.readouterr().err
    assert "ORPHO_CADENCE_AUTOMATION_ENABLED=1 is required" in err
    rows = _audit_rows(mod.AUDIT_LOG)
    assert rows[-1]["event"] == "execute_blocked"


def test_kill_switch_blocks_execute_even_when_enabled(tmp_path, monkeypatch, capsys):
    mod = _load_runner()
    _isolate_runner(mod, tmp_path, monkeypatch)
    monkeypatch.setenv("ORPHO_CADENCE_AUTOMATION_ENABLED", "1")
    monkeypatch.setenv("ORPHO_CADENCE_DISABLED", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["cadence_runner.py", "--execute", "--force-day-of-week"],
    )

    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "ORPHO_CADENCE_DISABLED=1" in out
    rows = _audit_rows(mod.AUDIT_LOG)
    assert rows[-1]["event"] == "disabled"


def test_dry_run_writes_audit_and_respects_lower_cap(tmp_path, monkeypatch):
    mod = _load_runner()
    _isolate_runner(mod, tmp_path, monkeypatch)
    today = mod.datetime.date.today().isoformat()
    _write_prospects(
        mod.PROSPECTS_CSV,
        [
            {
                "email": "one@example.com",
                "first_name": "One",
                "vertical": "accounting",
                "public_detail": "their public services page",
                "added_iso": today,
            },
            {
                "email": "two@example.com",
                "first_name": "Two",
                "vertical": "construction",
                "public_detail": "their public portfolio",
                "added_iso": today,
            },
        ],
    )
    monkeypatch.setenv("ORPHO_CADENCE_DAILY_CAP", "1")
    monkeypatch.setattr(sys, "argv", ["cadence_runner.py", "--force-day-of-week"])

    assert mod.main() == 0
    rows = _audit_rows(mod.AUDIT_LOG)
    assert rows[-1]["event"] == "run_complete"
    assert rows[-1]["cap"] == 1
    assert rows[-1]["planned"] == 1
    assert rows[-1]["dry"] == 1


def test_suppressed_contact_is_not_planned(tmp_path, monkeypatch):
    mod = _load_runner()
    _isolate_runner(mod, tmp_path, monkeypatch)
    today = mod.datetime.date.today().isoformat()
    _write_prospects(
        mod.PROSPECTS_CSV,
        [
            {
                "email": "stop@example.com",
                "first_name": "Stop",
                "vertical": "legal_solos",
                "public_detail": "their public practice page",
                "added_iso": today,
            }
        ],
    )
    mod.SUPPRESSIONS.write_text(
        json.dumps({"email": "stop@example.com", "reason": "STOP_REPLY"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["cadence_runner.py", "--force-day-of-week"])

    assert mod.main() == 0
    rows = _audit_rows(mod.AUDIT_LOG)
    assert rows[-1]["event"] == "run_complete"
    assert rows[-1]["suppressed"] == 1
    assert rows[-1]["planned"] == 0


def test_sent_today_count_uses_utc_day_to_match_sent_at_stamp():
    """sent_at is stamped in UTC (_now_iso). The daily-cap counter must key on
    the UTC day too, or it undercounts near midnight in a non-UTC zone and lets
    the 20/day cap be exceeded."""
    mod = _load_runner()
    utc_today = mod.datetime.datetime.now(mod.datetime.timezone.utc).date().isoformat()
    # A send stamped earlier today in UTC must be counted.
    state = [{"sent_at": f"{utc_today}T00:00:01+00:00", "ok": True}]
    assert mod._sent_today_count(state) == 1
    # The counter's notion of "today" is the UTC day, matching _now_iso().
    assert mod._now_iso().startswith(utc_today)
