"""test_retention_promise.py — the retention copy must match the scheduler.

The Terms and Privacy pages said free-tier receipts "may be pruned" after 30
days while nothing ever ran the pruning job, so every free receipt was kept
and the published terms described a system that did not exist. Since
2026-09-11 the pages say free receipts are kept. This test ties that wording
to whether anything schedules server/expire_worker.py, in either direction:
unscheduled means the pages say "kept"; scheduled means they disclose pruning.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

JOB = re.compile(r"expire_worker|expire_cron")
PRUNE_DISCLOSED = re.compile(r"(?i)\bmay be pruned\b|\bpruned from our servers\b")
KEPT = re.compile(r"(?i)free-tier receipts\W+(?:\w+\W+){0,3}(kept|retained)\b")

POLICY_PAGES = [WEB / "terms.html", WEB / "privacy.html"]
OTHER_PAGES = [WEB / "faq.html", WEB / "pricing.html", WEB / "llms.txt"]


def _schedulers() -> list[Path]:
    paths = [ROOT / "fly.toml", ROOT / "Dockerfile"]
    paths += sorted((ROOT / ".github" / "workflows").glob("*.y*ml"))
    return [p for p in paths if p.is_file()]


def _scheduled() -> bool:
    return any(JOB.search(p.read_text(encoding="utf-8")) for p in _schedulers())


def _text(p: Path) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", p.read_text(encoding="utf-8")))


def test_retention_copy_matches_whether_pruning_is_scheduled() -> None:
    if _scheduled():
        for page in POLICY_PAGES:
            assert PRUNE_DISCLOSED.search(_text(page)), (
                f"{page.relative_to(ROOT)}: the pruning job is scheduled, so the "
                "page must disclose that free-tier receipts are pruned."
            )
        return
    for page in POLICY_PAGES:
        text = _text(page)
        assert not PRUNE_DISCLOSED.search(text), (
            f"{page.relative_to(ROOT)} says free-tier receipts may be pruned, "
            "but nothing schedules expire_worker; the page promises a deletion "
            "that never happens."
        )
        assert KEPT.search(text), (
            f"{page.relative_to(ROOT)} must say free-tier receipts are kept."
        )
    for page in OTHER_PAGES:
        assert not PRUNE_DISCLOSED.search(_text(page)), page.relative_to(ROOT)


def test_scheduler_detection_can_fire() -> None:
    # Floor and control: the scan reads real deploy files, and the pattern
    # matches the command expire_cron.sh would run.
    assert (ROOT / "fly.toml").is_file()
    assert JOB.search("exec python3 server/expire_worker.py")
    assert not JOB.search("exec python3 server/upgrade_worker.py")
    assert PRUNE_DISCLOSED.search("Free-tier receipts may be pruned after 30 days.")
    assert KEPT.search("Free-tier receipts are kept on our servers.")
