"""A test that polls a JSONL file a server process is still appending to can
read the last record half-written. Master's CI failed that way on 2026-11-17
(test_pack_recover_mails_each_code_to_the_address_on_its_own_row: the stub
mail log ended mid-string at char 801). A record is only complete once its
newline is on disk, so the shared reader keeps complete lines only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import _srv  # noqa: E402

import test_email_twin_closed as twin  # noqa: E402
import test_gift_recover_recipient_only as gift  # noqa: E402
import test_waitlist_double_opt_in as waitlist  # noqa: E402
import test_x402_stripe_record as x402rec  # noqa: E402


def _half_written(path: Path) -> None:
    done = json.dumps({"payload": {"to": ["a@example.com"], "text": "done"}})
    partial = json.dumps({"payload": {"to": ["b@example.com"], "text": "x" * 900}})[:801]
    path.write_text(done + "\n" + partial)


def test_shared_reader_keeps_complete_lines_only(tmp_path):
    p = tmp_path / "stub_mail_sent.jsonl"
    _half_written(p)
    assert _srv.jsonl_rows(p) == [{"payload": {"to": ["a@example.com"], "text": "done"}}]


def test_shared_reader_on_missing_and_empty(tmp_path):
    assert _srv.jsonl_rows(tmp_path / "absent.jsonl") == []
    (tmp_path / "empty.jsonl").write_text("")
    assert _srv.jsonl_rows(tmp_path / "empty.jsonl") == []


def test_every_mail_log_reader_survives_a_half_written_record(tmp_path):
    p = tmp_path / "stub_mail_sent.jsonl"
    _half_written(p)
    assert [m["to"] for m in twin._mails(tmp_path)] == [["a@example.com"]]
    assert len(gift._rows(p)) == 1
    assert waitlist._mails(tmp_path, "a@example.com")[0]["payload"]["text"] == "done"
    assert len(waitlist._all_mails(tmp_path)) == 1


def test_stripe_call_log_reader_survives_a_half_written_record(tmp_path):
    p = tmp_path / "stub_stripe_calls.jsonl"
    done = json.dumps({"path": x402rec.PI_PATH, "n": 1})
    p.write_text(done + "\n" + json.dumps({"path": x402rec.PI_PATH, "n": 2})[:20])
    assert x402rec._pi_calls(tmp_path) == [{"path": x402rec.PI_PATH, "n": 1}]
