"""What the integration and folder pages say, held against what the code does.

The folder pages told readers the office sees only the combined fingerprint.
A folder anchor sends the manifest: every relative path, per-file digest and
size, with the root. The pages also described signer credentials and a
hash-labeled path scheme that no code implements, the construction page said
an inclusion proof fixes the exact day a photo belongs to, the dataset page
called the CLI's exit code a release gate when a failed anchor exits zero, and
the integrations page stated a GitHub Action default the action does not have.

Each claim is checked on the page as a server serves it, next to a test of
the behaviour the claim rests on, so the page and the code cannot drift apart
without one of them failing here.
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
DATASET_CLI = ROOT / "dataset-provenance" / "provenance.py"
FOLDER_ROUTES = ("/matters/", "/workpapers/", "/listings/", "/construction/")
# Plain ASCII after the JPEG marker, so the bytes are still found if they
# leak into a JSON field, where the marker itself would be escaped.
PHOTO_BYTES = b"\xff\xd8 rebar mat, slab 3, north bay"

# Sentences that say more than the code does. Matched on text with markup
# and runs of whitespace removed, so a line wrap cannot hide one.
UNSUPPORTED = (
    "only thing the office sees",
    "only ever sees the combined fingerprint",
    "capture-credential issued",
    "published trust list",
    "hash-labeled scheme",
    "recorded as an attribute on the signature",
    # A receipt bounds when the folder existed. It says nothing about the day
    # a photo in it was taken.
    "folder for that exact date",
)


def plain(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def unsupported_in(markup: str) -> list[str]:
    text = plain(markup).lower()
    return [phrase for phrase in UNSUPPORTED if phrase in text]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    yield from _srv.server_processes(
        tmp_path_factory.mktemp("pages"), stub_calendars=True, RATE_LIMIT_PER_DAY="50")


def served(base, path) -> str:
    status, body, _ = _srv.request(base, path)
    assert status == 200, (path, status)
    text = body.decode("utf-8")
    assert "</html>" in text, f"{path} did not render a page"
    return text


def test_the_scan_finds_the_sentences_it_was_written_for():
    # Without this, a scan that matches nothing reads as a clean site.
    old = ("<p>The fingerprints for the day are combined into one fingerprint.\n"
           "    That combined fingerprint is the only thing the\n office sees.</p>")
    assert unsupported_in(old) == ["only thing the office sees"]
    assert unsupported_in("<p>The office only ever sees the <em>combined</em> "
                          "fingerprint for the folder.</p>") == [
        "only ever sees the combined fingerprint"]
    assert unsupported_in("<p>Relative paths, digests and sizes are sent.</p>") == []


@pytest.mark.parametrize("route", FOLDER_ROUTES)
def test_folder_pages_say_what_is_sent(server, route):
    page = served(server, route)
    assert unsupported_in(page) == []
    assert "relative paths" in plain(page).lower(), (
        f"{route} does not tell the reader that file paths are sent")


def _bundle(tmp_path) -> Path:
    bundle = tmp_path / "bundle"
    (bundle / "data" / "images").mkdir(parents=True)
    (bundle / "data" / "images" / "site-visit-0413.jpg").write_bytes(PHOTO_BYTES)
    (bundle / "licenses").mkdir()
    (bundle / "licenses" / "terms.txt").write_text("terms")
    (bundle / "acquisition_log.json").write_text('{"sources": []}')
    return bundle


def run_dataset_cli(api, *args):
    # The CLI's own default is the live service.
    assert api.startswith("http://127.0.0.1:"), api
    return subprocess.run(
        [sys.executable, str(DATASET_CLI), *args, "--api", api],
        capture_output=True, text=True, timeout=120)


def test_a_folder_anchor_hands_the_service_paths_digests_and_sizes(tmp_path):
    bundle = _bundle(tmp_path)
    out = tmp_path / "out"
    data = tmp_path / "data"
    data.mkdir()
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        run = run_dataset_cli(base, "anchor", "--bundle", str(bundle),
                              "--name", "T", "--out", str(out))
        assert run.returncode == 0, (run.stdout, run.stderr)
        cert = json.loads((out / "certificate.json").read_text())
        assert cert["anchor"]["status"] == "anchored"
        rid = cert["anchor"]["receipt_id"]
        assert rid

    # The manifest the service keeps for this receipt carries the path, the
    # digest and the size the CLI computed for each file.
    local = next(l for l in json.loads((out / "manifest.json").read_text())["leaves"]
                 if l["path"] == "data/images/site-visit-0413.jpg")
    stored_paths = [p for p in data.rglob("manifest.json") if p.parent.name == rid]
    assert len(stored_paths) == 1, stored_paths
    stored = {l["path"]: l for l in json.loads(stored_paths[0].read_text())["leaves"]}
    leaf = stored["data/images/site-visit-0413.jpg"]
    assert leaf["file_sha256_hex"] == local["file_sha256_hex"]
    assert leaf["size_bytes"] == len(PHOTO_BYTES)

    held = b"".join(p.read_bytes() for p in data.rglob("*") if p.is_file())
    assert PHOTO_BYTES[3:] not in held, "file bytes must not reach the service"


def _assert_unanchored_and_exit_zero(run, out):
    assert run.returncode == 0, (run.stdout, run.stderr)
    cert = json.loads((out / "certificate.json").read_text())
    assert cert["anchor"]["status"] == "unanchored"
    assert {p.name for p in out.iterdir()} == {
        "certificate.json", "certificate.txt", "manifest.json"}


def test_a_failed_dataset_anchor_still_exits_zero_and_says_unanchored(tmp_path):
    # The dataset page tells release gates to read anchor.status, because the
    # CLI exits zero after a network or an HTTP failure. If the CLI starts
    # failing the step itself, the page changes with it.
    bundle = _bundle(tmp_path)

    # Network failure: nothing listens on port 9.
    out = tmp_path / "out-network"
    run = run_dataset_cli("http://127.0.0.1:9", "anchor", "--bundle", str(bundle),
                          "--name", "T", "--out", str(out))
    _assert_unanchored_and_exit_zero(run, out)

    # HTTP failure: a server with anchoring switched off answers 503.
    out = tmp_path / "out-http"
    data = tmp_path / "data"
    data.mkdir()
    for base in _srv.server_processes(data, stub_calendars=True,
                                      ORPHO_DISABLE_ANCHORING="1"):
        run = run_dataset_cli(base, "anchor", "--bundle", str(bundle),
                              "--name", "T", "--out", str(out))
    _assert_unanchored_and_exit_zero(run, out)
    cert = json.loads((out / "certificate.json").read_text())
    assert "HTTP 503" in json.dumps(cert["anchor"]), cert["anchor"]


def test_the_dataset_page_does_not_call_the_exit_code_a_gate(server):
    page = plain(served(server, "/dataset-provenance"))
    assert "Exit codes make it a CI gate" not in page
    assert "anchor.status" in page
    assert "all anyone needs" not in page, (
        "a manifest without its timestamp proof does not establish a time")


def test_the_integrations_page_states_the_action_default(server):
    action = (ROOT / "integrations" / "github-action" / "action.yml").read_text()
    default = re.search(
        r'^  fail_on_error:\n(?:    .*\n)*?    default: "(\w+)"', action, re.M).group(1)
    page = plain(served(server, "/integrations"))
    stated = re.findall(r"default (?:is )?fail_on_error: (\w+)", page)
    assert stated == [default], (
        f"the page states {stated}, action.yml defaults to {default!r}")
    # The release manifest was never shipped; the page must not describe it.
    assert "manifest generation" not in page
