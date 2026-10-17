"""The unreleased browser extension sent the URL's last segment (query string
included) as the receipt's client_label on every anchor, without asking.
Founder ruling 2026-10-16 (@browser-extension-label): the extension asks
before labelling a receipt with the file name, and the default is no label.

Drives the real dist/browser-extension/background.js in node with a fake
`chrome` and `fetch`."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "dist" / "browser-extension"
HARNESS = ROOT / "tests" / "js" / "browser_extension_harness.mjs"


def run(scenario):
    if not shutil.which("node"):
        pytest.skip("node not on PATH: this check runs the real background.js")
    out = subprocess.run(["node", str(HARNESS), str(EXT / "background.js"), scenario],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_plain_entry_sends_no_label():
    assert run("plain")["anchors"] == [None]


def test_the_label_entry_asks_before_anything_is_sent():
    got = run("asked-only")
    assert got["anchors"] == [], "nothing is anchored before the answer"
    assert len(got["windows"]) == 1 and got["windows"][0].startswith("ask.html#")
    # The proposed name drops the query string and fragment (tokens live there).
    assert got["pending"][0]["label"] == "holiday photo.jpg"


@pytest.mark.parametrize("scenario,expected", [
    ("answer-nolabel", [None]),
    ("answer-label", ["holiday photo.jpg"]),
    ("answer-cancel", []),
    ("answer-label-foreign", []),
])
def test_the_answer_decides_the_label(scenario, expected):
    got = run(scenario)
    assert got["anchors"] == expected
    if scenario != "answer-label-foreign":
        assert got["pending"] == [], "an answered question is not kept"


def test_the_ask_page_defaults_to_no_label():
    html = (EXT / "ask.html").read_text()
    first_button = html[html.index("<button"):].split(">", 1)[0]
    assert 'id="nolabel"' in first_button and "autofocus" in first_button
    assert "<script src=\"ask.js\"></script>" in html and "<script>" not in html
