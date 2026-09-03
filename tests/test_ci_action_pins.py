"""GitHub Action pins: runtime floor, one major per action, docs parity,
SHA pins tied to the version they claim.

History. PR #224 moved checkout/setup-python/setup-node off the deprecated
Node 20 runtime; review found the public copy-paste template in
integrations/github-action/README.md still shipped `checkout@v4` and
`upload-artifact@v4`, and nothing read README code fences. PR #225 added a
regex checker; its review found the checker (1) had `upload-artifact` at
floor 5 when v5 still runs node20, (2) stayed silent on any `actions/*`
name missing from its table, (3) could not see quoted, SHA, or branch
pins, (4) counted commented-out and prose mentions as live pins, and
(5) only read `*.yml`. PR #226 parsed the workflow YAML (comments vanish),
failed CLOSED on unknown names and branch refs, and scanned every *.md
fence and web/*.html <pre>. PR #227 SHA-pinned the workflows; its review
found the first SHA pass (6) dropped any pin whose comment was not exactly
`# vN.N.N`, (7) trusted the comment as the SHA's version, and (8) left docs
parity vacuous for SHA-pinned snippets. This version matches the SHA first
and judges the comment separately, ties every SHA to a checked-in ledger
(KNOWN_SHAS) so a pasted SHA cannot wear the wrong version, and threads raw
snippet text through docs parity. Each behaviour has a negative control.
"""
import html
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / ".github" / "workflows"
WORKFLOWS = sorted(
    p for ext in ("*.yml", "*.yaml") for p in WORKFLOW_DIR.glob(ext)
)
WORKFLOW_TEXT = {p: p.read_text() for p in WORKFLOWS}
WORKFLOW_RAW = "\n".join(WORKFLOW_TEXT.values())
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

# First major of each official action whose runtime is node24. Verified from
# each tag's action.yml `using:` line (upload-artifact v5 is still node20).
NODE24_FLOOR = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/setup-node": 5,
    "actions/upload-artifact": 6,
}

# SHA → (action, version). The ledger that ties a 40-hex pin to the release
# it claims. Resolved with `gh api repos/<action>/git/ref/tags/<version>` and
# confirmed as commit objects. A dependabot bump that changes a SHA fails
# here until a human adds the new row — that is the review step, on purpose.
KNOWN_SHAS = {
    "3d3c42e5aac5ba805825da76410c181273ba90b1": ("actions/checkout", "v7.0.1"),
    "5fda3b95a4ea91299a34e894583c3862153e4b97": ("actions/setup-python", "v7.0.0"),
    "820762786026740c76f36085b0efc47a31fe5020": ("actions/setup-node", "v7.0.0"),
}

USES = re.compile(r"^(?P<action>[^@\s'\"]+)@(?P<ref>[^\s'\"]+)$")
TAG = re.compile(r"^v(\d+)(?:\.\d+)*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
# A raw `uses:` line: the value, then whatever trails it (a comment or junk).
USES_LINE = re.compile(r"""^\s*-?\s*uses:\s*["']?(?P<uses>[^\s"'#]+)["']?\s*(?P<trail>.*)$""")
VERSION_COMMENT = re.compile(r"^#\s*(?P<version>v\d+(?:\.\d+)*)\s*$")


def classify(uses):
    """'owner/name@ref' -> (action, kind, major). kind: tag | sha | other."""
    m = USES.match(uses.strip())
    if not m:
        return uses, "other", None
    action, ref = m.group("action"), m.group("ref")
    t = TAG.match(ref)
    if t:
        return action, "tag", int(t.group(1))
    if SHA.match(ref):
        return action, "sha", None
    return action, "other", None


def uses_in_workflow_text(text):
    """Every `uses:` value in a workflow document, via the YAML parser so
    comments and prose never count."""
    doc = yaml.safe_load(text) or {}
    out = []
    for job in (doc.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        if job.get("uses"):
            out.append(str(job["uses"]))
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("uses"):
                out.append(str(step["uses"]))
    return out


def raw_uses_lines(text):
    """[(uses, trail)] from raw lines; whole-line comments are skipped."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = USES_LINE.match(line)
        if m:
            out.append((m.group("uses"), m.group("trail").strip()))
    return out


def uses_in_snippet(text):
    """`uses:` values from a code snippet that may not be a full workflow."""
    return [u for u, _trail in raw_uses_lines(text)]


def sha_pins_in_text(text):
    """[(action, sha, version_or_None)] for every SHA-pinned actions/* line.
    The SHA is matched FIRST; the trailing comment is judged on its own, so a
    pin with an odd comment is reported as unversioned rather than vanishing."""
    out = []
    for uses, trail in raw_uses_lines(text):
        action, kind, _ = classify(uses)
        if kind != "sha" or not action.startswith("actions/"):
            continue
        cm = VERSION_COMMENT.match(trail)
        out.append((action, uses.split("@", 1)[1], cm.group("version") if cm else None))
    return out


def unversioned_sha_pins(text):
    return sorted(f"{a}@{s[:12]}" for a, s, v in sha_pins_in_text(text) if v is None)


def ledger_violations(text, ledger=KNOWN_SHAS):
    """A SHA must be in the ledger, for the same action, with the comment
    naming the ledger's version. Anything else is a violation."""
    out = []
    for action, sha, version in sha_pins_in_text(text):
        row = ledger.get(sha)
        if row is None:
            out.append(f"{action}@{sha[:12]}: SHA not in KNOWN_SHAS")
        elif row[0] != action:
            out.append(f"{action}@{sha[:12]}: SHA belongs to {row[0]}")
        elif version != row[1]:
            out.append(f"{action}@{sha[:12]}: comment says {version}, ledger says {row[1]}")
    return sorted(out)


def fenced_blocks(markdown):
    return re.findall(r"```[^\n]*\n(.*?)```", markdown, re.S)


def pre_blocks(html_text):
    return [html.unescape(b) for b in re.findall(r"<pre[^>]*>(.*?)</pre>", html_text, re.S)]


def violations(uses_list, floor=NODE24_FLOOR):
    """Fail closed: every actions/* pin must be a SHA or a tag at/above a
    KNOWN floor. Unknown actions/* names and branch refs are violations."""
    out = []
    for u in uses_list:
        action, kind, major = classify(u)
        if not action.startswith("actions/"):
            continue
        if kind == "sha":
            continue
        if kind == "other":
            out.append(f"{u}: branch/unknown ref (pin a tag or SHA)")
        elif action not in floor:
            out.append(f"{u}: no NODE24_FLOOR entry (add one; unknown = fail)")
        elif major < floor[action]:
            out.append(f"{u}: below node24 floor v{floor[action]}")
    return sorted(out)


def majors_by_action(uses_list, raw_text):
    """Tag pins contribute their major; SHA pins contribute the major from
    their version comment. raw_text is required so a caller cannot silently
    drop the SHA half."""
    out = {}
    for u in uses_list:
        action, kind, major = classify(u)
        if action.startswith("actions/") and kind == "tag":
            out.setdefault(action, set()).add(major)
    for action, _sha, version in sha_pins_in_text(raw_text):
        if version is not None:
            out.setdefault(action, set()).add(int(TAG.match(version).group(1)))
    return out


def doc_snippets():
    """{path: snippet_text} — every fenced/<pre> block that carries a `uses:`."""
    found = {}
    md = [p for p in ROOT.rglob("*.md") if not (set(p.parts) & SKIP_DIRS)]
    for p in md:
        blocks = [b for b in fenced_blocks(p.read_text(errors="replace")) if uses_in_snippet(b)]
        if blocks:
            found[p] = "\n".join(blocks)
    for p in (ROOT / "web").glob("*.html"):
        blocks = [b for b in pre_blocks(p.read_text(errors="replace")) if uses_in_snippet(b)]
        if blocks:
            found[p] = "\n".join(blocks)
    return found


WORKFLOW_USES = [u for t in WORKFLOW_TEXT.values() for u in uses_in_workflow_text(t)]
WORKFLOW_MAJORS = majors_by_action(WORKFLOW_USES, WORKFLOW_RAW)


class Workflows(unittest.TestCase):
    def test_there_are_pins_to_check(self):
        # Floor: an empty extraction would pass every other test vacuously.
        self.assertGreaterEqual(len(WORKFLOWS), 3)
        actions = {classify(u)[0] for u in WORKFLOW_USES}
        self.assertIn("actions/checkout", actions)
        self.assertIn("actions/setup-python", actions)
        self.assertTrue(sha_pins_in_text(WORKFLOW_RAW), "no SHA pins found to check")

    def test_pins_are_sha_or_known_tag_at_node24_floor(self):
        self.assertEqual(violations(WORKFLOW_USES), [])

    def test_every_sha_pin_names_its_version(self):
        self.assertEqual(unversioned_sha_pins(WORKFLOW_RAW), [])

    def test_every_sha_pin_matches_the_ledger(self):
        self.assertEqual(ledger_violations(WORKFLOW_RAW), [])

    def test_ledger_versions_meet_floor(self):
        # A SHA whose ledger row says v4 is a Node 20 pin wearing a disguise.
        self.assertEqual(violations([f"{a}@{v}" for a, v in KNOWN_SHAS.values()]), [])

    def test_one_major_per_action(self):
        split = {a: sorted(m) for a, m in WORKFLOW_MAJORS.items() if len(m) > 1}
        self.assertEqual(split, {}, "same action pinned at different majors")


class DocsTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = doc_snippets()

    def test_the_known_template_is_scanned(self):
        readme = ROOT / "integrations" / "github-action" / "README.md"
        self.assertIn(readme, self.docs, "README template not found by the scan")
        self.assertIn("actions/checkout", {classify(u)[0] for u in uses_in_snippet(self.docs[readme])})

    def test_every_doc_snippet_meets_floor_and_matches_workflows(self):
        for path, text in self.docs.items():
            rel = path.relative_to(ROOT)
            us = uses_in_snippet(text)
            self.assertEqual(violations(us), [], f"{rel}")
            self.assertEqual(unversioned_sha_pins(text), [], f"{rel}")
            self.assertEqual(ledger_violations(text), [], f"{rel}")
            for action, majors in majors_by_action(us, text).items():
                if action in WORKFLOW_MAJORS:
                    self.assertEqual(
                        majors, WORKFLOW_MAJORS[action],
                        f"{rel} pins {action} at {sorted(majors)}, "
                        f"workflows use {sorted(WORKFLOW_MAJORS[action])}",
                    )


class NegativeControls(unittest.TestCase):
    """Each control is a way a previous checker stayed silent."""

    SHA_OK = "3d3c42e5aac5ba805825da76410c181273ba90b1"  # checkout v7.0.1

    def test_pre_fix_readme_is_caught(self):
        stale = "- uses: actions/checkout@v4\n  uses: actions/upload-artifact@v4\n"
        self.assertEqual(len(violations(uses_in_snippet(stale))), 2)

    def test_upload_artifact_v5_is_still_node20(self):
        self.assertEqual(len(violations(["actions/upload-artifact@v5"])), 1)
        self.assertEqual(violations(["actions/upload-artifact@v6"]), [])

    def test_unknown_actions_name_fails_closed(self):
        self.assertEqual(len(violations(["actions/cache@v3", "actions/download-artifact@v9"])), 2)

    def test_branch_ref_fails(self):
        self.assertEqual(len(violations(["actions/checkout@main"])), 1)

    def test_quoted_pin_is_seen(self):
        for q in ('uses: "actions/checkout@v4"', "uses: 'actions/checkout@v4'"):
            self.assertEqual(len(violations(uses_in_snippet(q))), 1, q)

    def test_comments_and_prose_do_not_count(self):
        self.assertEqual(uses_in_snippet("# - uses: actions/checkout@v4\n"), [])
        self.assertEqual(uses_in_snippet("Previously `uses: actions/checkout@v4` was used.\n"), [])
        wf = "jobs:\n  a:\n    runs-on: x\n    steps:\n      # - uses: actions/checkout@v4\n      - uses: actions/checkout@v7\n"
        self.assertEqual(uses_in_workflow_text(wf), ["actions/checkout@v7"])

    def test_sha_pin_with_odd_or_missing_comment_is_unversioned_not_invisible(self):
        for trail in ("", " # 7.0.1", " # v7.0.1 pinned", " # V7.0.1", " # v7.0.1 # extra"):
            line = f"- uses: actions/checkout@{self.SHA_OK}{trail}\n"
            self.assertEqual(len(sha_pins_in_text(line)), 1, repr(trail))
            self.assertEqual(unversioned_sha_pins(line), [f"actions/checkout@{self.SHA_OK[:12]}"], repr(trail))
        good = f"- uses: actions/checkout@{self.SHA_OK} # v7.0.1\n"
        self.assertEqual(unversioned_sha_pins(good), [])
        self.assertEqual(majors_by_action([], good), {"actions/checkout": {7}})

    def test_sha_not_in_ledger_is_caught(self):
        self.assertEqual(len(ledger_violations(f"- uses: actions/checkout@{'b' * 40} # v7.0.1\n")), 1)

    def test_ledger_sha_with_wrong_comment_or_wrong_action_is_caught(self):
        self.assertEqual(len(ledger_violations(f"- uses: actions/checkout@{self.SHA_OK} # v4.2.2\n")), 1)
        self.assertEqual(len(ledger_violations(f"- uses: actions/setup-node@{self.SHA_OK} # v7.0.1\n")), 1)
        self.assertEqual(ledger_violations(f"- uses: actions/checkout@{self.SHA_OK} # v7.0.1\n"), [])

    def test_sha_pinned_doc_snippet_is_not_vacuous(self):
        snippet = f"- uses: actions/checkout@{self.SHA_OK} # v4.2.2\n"
        self.assertEqual(majors_by_action(uses_in_snippet(snippet), snippet), {"actions/checkout": {4}})
        self.assertEqual(len(ledger_violations(snippet)), 1)

    def test_sha_pin_is_accepted_by_floor(self):
        self.assertEqual(violations([f"actions/checkout@{self.SHA_OK}"]), [])

    def test_non_github_actions_are_not_floor_checked(self):
        self.assertEqual(violations(["superfly/flyctl-actions/setup-flyctl@master",
                                     "Orphograph/Orphograph/.github/actions/anchor@master"]), [])


if __name__ == "__main__":
    unittest.main()
