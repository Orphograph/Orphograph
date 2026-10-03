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
    "as of the filing date",
    # Review of this branch: the same class on pages the first pass did not
    # read, and rewordings of it. A folder or dataset anchor sends paths,
    # digests and sizes; no tool offers a hash-labeled path scheme; the
    # manifest has no ordering field; sizes are recorded but not committed.
    "office sees only",
    "sees only the combined",
    "only fingerprints travel",
    "only fingerprints leave",
    "only fingerprints are anchored",
    "paths, and contents never leave",
    "hash-labeled paths",
    "hash-only labeling",
    "ordering rule",
    "computed on your own machine, not uploaded",
    "commits to the submitted",
    # Review round 2: the universal wordings left on pages round 1 edited,
    # and "only" lists of what is sent that left out sizes or the public
    # label. Meta descriptions are scanned too (see unsupported_in).
    "only the fingerprint crosses the wire",
    "the fingerprint is the only artefact that leaves",
    "fingerprints are transmitted, and only those",
    "only cryptographic fingerprints (sha-256, optional sha-512 sibling) cross the wire",
    "only the fingerprints are submitted",
    "only the manifest — relative paths, digests, and the root",
    "only the manifest of relative paths, digests, and the root is submitted",
    "only the bundle's manifest (paths, digests and sizes) and its root are sent",
    "why filenames are excluded from the receipt",
    # Review round 3: what round 2 left in the same pages, and single-file
    # sentences that dropped the optional label (the MCP tools and the API
    # send it; a public receipt shows it).
    "the 32-byte sha-256 digest and — when the anchoring page computes it",
    "only the 64-hex digest crosses the wire",
    "digest-only",
    "only those fingerprints are committed",
    "only those fingerprints are transmitted to the office",
    "the bundle's --name are sent",
    # Review round 4.
    "the digest is what travels",
    "only the 64-hex digest",
    "we use one localstorage entry",
    "air-gapped mode builds the receipt",
)

# True of a single file's bytes and said on single-file pages, so it is not in
# the list above; on a folder page it reads as "nothing leaves", and paths do.
FOLDER_ONLY_UNSUPPORTED = ("never leave your device",)

# Every other served page that describes folder or dataset anchoring.
CLASS_ROUTES = (
    "/mcp", "/lp/eu-ai-act-training-data", "/certificate/DatasetProvenanceSample",
    "/method/", "/method/folder-merkle", "/method/why-filenames-are-not-stored",
    "/dataset-provenance", "/integrations", "/", "/privacy", "/docs/agents",
)


def plain(markup: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def unsupported_in(markup: str) -> list[str]:
    # The rendered text, and the raw markup too: a meta description or a
    # JSON-LD answer is read by search engines and lives inside tags.
    text = plain(markup).lower() + " \n " + re.sub(r"\s+", " ", markup).lower()
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
    text = plain(page).lower()
    assert not [p for p in FOLDER_ONLY_UNSUPPORTED if p in text], route
    assert "relative paths" in text, (
        f"{route} does not tell the reader that file paths are sent")


@pytest.mark.parametrize("route", CLASS_ROUTES)
def test_no_page_about_folders_says_only_the_fingerprint_leaves(server, route):
    assert unsupported_in(served(server, route)) == []


def test_the_filename_page_says_a_folder_sends_its_paths(server):
    # Its single-file argument ("the filename is never transmitted") is true;
    # without the folder caveat it read as true of every anchor. Round 2: the
    # caveat alone left the lede, the cost paragraph and the rejected
    # alternative stating it universally, so each keeps its qualifier.
    text = plain(served(server, "/method/why-filenames-are-not-stored")).lower()
    assert "never transmitted" in text
    assert "relative path" in text and "folder" in text
    for qualifier in ("for a single file anchored in the browser the labels are not received",
                      "who anchors many single files without labels",
                      "for single-file receipts, the alternative"):
        assert qualifier in text, qualifier


def test_the_privacy_policy_lists_what_a_folder_anchor_sends(server):
    text = plain(served(server, "/privacy")).lower()
    assert "folder manifests" in text and "relative path" in text and "byte size" in text


def test_the_dataset_name_is_public_and_the_page_says_so(tmp_path, server):
    # The behaviour: the CLI sends --name as the receipt label, and an
    # anonymous read of a public receipt returns it.
    bundle = _bundle(tmp_path)
    out = tmp_path / "out"
    data = tmp_path / "data"
    data.mkdir()
    for base in _srv.server_processes(data, stub_calendars=True, RATE_LIMIT_PER_DAY="50"):
        run = run_dataset_cli(base, "anchor", "--bundle", str(bundle),
                              "--name", "Name-Shown-Publicly", "--out", str(out))
        assert run.returncode == 0, (run.stdout, run.stderr)
        rid = json.loads((out / "certificate.json").read_text())["anchor"]["receipt_id"]
        status, body, _ = _srv.request(base, f"/api/verify/{rid}")
        assert status == 200 and json.loads(body).get("client_label") == "Name-Shown-Publicly"
    # The claim that rests on it.
    page = plain(served(server, "/dataset-provenance")).lower()
    assert "a public receipt shows it" in page and "not confidential" in page


def test_the_wider_scan_catches_the_sentences_master_carried():
    # Positive control for the review's additions, on master's own wording.
    for old, phrase in (
        ("the file stays on the agent's machine, only fingerprints travel.", "only fingerprints travel"),
        ("# Anchor a dataset bundle (only fingerprints leave the machine)", "only fingerprints leave"),
        ("only the fingerprint travels; names, paths, and contents never leave your device.",
         "paths, and contents never leave"),
        ("may anchor under hash-labeled paths, as noted above", "hash-labeled paths"),
        ("anchored exhibit folder as of the filing date six months earlier", "as of the filing date"),
        ("The office sees only the combined fingerprint.", "office sees only"),
        ('<meta name="description" content="... only the fingerprint crosses the wire.">',
         "only the fingerprint crosses the wire"),
        ("Hashed locally. Only the fingerprints are submitted.", "only the fingerprints are submitted"),
    ):
        assert phrase in unsupported_in(f"<p>{old}</p>"), old


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



def test_a_dataset_root_no_calendar_accepted_is_unanchored(tmp_path):
    """Review of the rescue branch: the CLI marked any receipt "anchored",
    including a 200 with calendars_ok 0, which has no Bitcoin commitment and
    never gets one; a gate written as the dataset page says passed it."""
    bundle = _bundle(tmp_path)
    out = tmp_path / "out-no-calendar"
    data = tmp_path / "data"
    data.mkdir()
    for base in _srv.server_processes(data, stub_calendars=True,
                                      fail_calendars="a,b,alice,finney,btc"):
        run = run_dataset_cli(base, "anchor", "--bundle", str(bundle),
                              "--name", "T", "--out", str(out))
    _assert_unanchored_and_exit_zero(run, out)
    cert = json.loads((out / "certificate.json").read_text())
    assert "no calendar" in json.dumps(cert["anchor"]).lower(), cert["anchor"]



def test_the_mcp_folder_tool_is_not_said_to_carry_lineage(server):
    # Review round 4: the tool's inputs are path and label; the manifest it
    # builds has no parent block, so it cannot commit to a parent receipt.
    assert "may commit to a parent receipt" not in plain(served(server, "/mcp")).lower()
