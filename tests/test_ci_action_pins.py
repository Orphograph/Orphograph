"""GitHub Action pins: runtime floor, one major per action, docs parity.

History. PR #224 moved checkout/setup-python/setup-node off the deprecated
Node 20 runtime; review found the public copy-paste template in
integrations/github-action/README.md still shipped `checkout@v4` and
`upload-artifact@v4`, and nothing read README code fences. PR #225 added a
regex checker; its review found the checker (1) had `upload-artifact` at
floor 5 when v5 still runs node20, (2) stayed silent on any `actions/*`
name missing from its table, (3) could not see quoted, SHA, or branch
pins, (4) counted commented-out and prose mentions as live pins, and
(5) only read `*.yml`. The SHA-pin pass (cycle 7) reads raw lines because
the parser drops the `# vN.N.N` comment that says what a SHA is. This version parses the workflow YAML (so comments
vanish), fails CLOSED on unknown `actions/*` names and on branch refs,
accepts SHA pins, reads every fenced block in every *.md and every <pre>
in web/*.html, and proves each of those behaviours with a negative control.
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
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}

# First major of each official action whose runtime is node24. Verified from
# each tag's action.yml `using:` line (upload-artifact v5 is still node20).
NODE24_FLOOR = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/setup-node": 5,
    "actions/upload-artifact": 6,
}

USES = re.compile(r"^(?P<action>[^@\s'\"]+)@(?P<ref>[^\s'\"]+)$")
TAG = re.compile(r"^v(\d+)(?:\.\d+)*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
USES_LINE = re.compile(r"""^\s*-?\s*uses:\s*["']?([^\s"'#]+)["']?\s*(?:#.*)?$""")


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


def uses_in_snippet(text):
    """`uses:` values from a code snippet that may not be a full workflow.
    Lines whose first non-blank character is `#` are comments, not pins."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = USES_LINE.match(line)
        if m:
            out.append(m.group(1))
    return out


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


SHA_LINE = re.compile(
    r"""^\s*-?\s*uses:\s*["']?(?P<action>actions/[a-z0-9-]+)@(?P<sha>[0-9a-f]{40})["']?\s*(?:#\s*v(?P<major>\d+)(?:\.\d+)*\s*)?$"""
)


def sha_pins_in_text(text):
    """Raw-text pass for SHA pins: the YAML parser drops comments, and the
    `# vN.N.N` comment is the only human-readable statement of what a SHA
    is. Returns [(action, sha, major_or_None)]."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        m = SHA_LINE.match(line)
        if m:
            out.append((m.group("action"), m.group("sha"),
                        int(m.group("major")) if m.group("major") else None))
    return out


def uncommented_sha_pins(text):
    return sorted(f"{a}@{s[:12]}" for a, s, major in sha_pins_in_text(text) if major is None)


def majors_by_action(uses_list, raw_text=""):
    """Tag pins contribute their major; SHA pins contribute the major named in
    their `# vN` comment, so docs-vs-workflow parity still has something to
    compare once the workflows are SHA-pinned."""
    out = {}
    for u in uses_list:
        action, kind, major = classify(u)
        if action.startswith("actions/") and kind == "tag":
            out.setdefault(action, set()).add(major)
    for action, _sha, major in sha_pins_in_text(raw_text):
        if major is not None:
            out.setdefault(action, set()).add(major)
    return out


def doc_pins():
    """{path: [uses...]} for every fenced/<pre> block in docs and web pages."""
    found = {}
    md = [p for p in ROOT.rglob("*.md") if not (set(p.parts) & SKIP_DIRS)]
    for p in md:
        us = [u for b in fenced_blocks(p.read_text(errors="replace")) for u in uses_in_snippet(b)]
        if us:
            found[p] = us
    for p in (ROOT / "web").glob("*.html"):
        us = [u for b in pre_blocks(p.read_text(errors="replace")) for u in uses_in_snippet(b)]
        if us:
            found[p] = us
    return found


class Workflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = "\n".join(p.read_text() for p in WORKFLOWS)
        cls.uses = [u for p in WORKFLOWS for u in uses_in_workflow_text(p.read_text())]

    def test_every_sha_pin_names_its_version(self):
        self.assertEqual(uncommented_sha_pins(self.raw), [])

    def test_sha_pins_and_floor_agree(self):
        # A SHA whose comment says v4 is a Node 20 pin wearing a disguise.
        commented = [f"{a}@v{m}" for a, _s, m in sha_pins_in_text(self.raw) if m is not None]
        self.assertEqual(violations(commented), [])

    def test_there_are_pins_to_check(self):
        # Floor: an empty extraction would pass every other test vacuously.
        self.assertGreaterEqual(len(WORKFLOWS), 3)
        actions = {classify(u)[0] for u in self.uses}
        self.assertIn("actions/checkout", actions)
        self.assertIn("actions/setup-python", actions)

    def test_pins_are_sha_or_known_tag_at_node24_floor(self):
        self.assertEqual(violations(self.uses), [])

    def test_one_major_per_action(self):
        split = {a: sorted(m) for a, m in majors_by_action(self.uses, self.raw).items() if len(m) > 1}
        self.assertEqual(split, {}, "same action pinned at different majors")


class DocsTemplates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = doc_pins()
        raw = "\n".join(p.read_text() for p in WORKFLOWS)
        cls.workflow_majors = majors_by_action(
            [u for p in WORKFLOWS for u in uses_in_workflow_text(p.read_text())], raw
        )

    def test_the_known_template_is_scanned(self):
        readme = ROOT / "integrations" / "github-action" / "README.md"
        self.assertIn(readme, self.docs, "README template not found by the scan")
        self.assertIn("actions/checkout", {classify(u)[0] for u in self.docs[readme]})

    def test_every_doc_snippet_meets_floor_and_matches_workflows(self):
        for path, us in self.docs.items():
            rel = path.relative_to(ROOT)
            self.assertEqual(violations(us), [], f"{rel}")
            for action, majors in majors_by_action(us).items():
                if action in self.workflow_majors:
                    self.assertEqual(
                        majors, self.workflow_majors[action],
                        f"{rel} pins {action} at {sorted(majors)}, "
                        f"workflows use {sorted(self.workflow_majors[action])}",
                    )


class NegativeControls(unittest.TestCase):
    """Each control is a way the previous checker stayed silent."""

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

    def test_sha_pin_is_accepted(self):
        self.assertEqual(violations(["actions/checkout@" + "a" * 40]), [])

    def test_quoted_pin_is_seen(self):
        for q in ('uses: "actions/checkout@v4"', "uses: 'actions/checkout@v4'"):
            self.assertEqual(len(violations(uses_in_snippet(q))), 1, q)

    def test_comments_and_prose_do_not_count(self):
        self.assertEqual(uses_in_snippet("# - uses: actions/checkout@v4\n"), [])
        self.assertEqual(uses_in_snippet("Previously `uses: actions/checkout@v4` was used.\n"), [])
        wf = "jobs:\n  a:\n    runs-on: x\n    steps:\n      # - uses: actions/checkout@v4\n      - uses: actions/checkout@v7\n"
        self.assertEqual(uses_in_workflow_text(wf), ["actions/checkout@v7"])

    def test_sha_pin_without_version_comment_is_caught(self):
        sha = "a" * 40
        self.assertEqual(uncommented_sha_pins(f"- uses: actions/checkout@{sha}\n"), [f"actions/checkout@{sha[:12]}"])
        self.assertEqual(uncommented_sha_pins(f"- uses: actions/checkout@{sha} # v7.0.1\n"), [])
        self.assertEqual(majors_by_action([], f"- uses: actions/checkout@{sha} # v7.0.1\n"), {"actions/checkout": {7}})

    def test_sha_pin_commented_as_old_major_fails_floor(self):
        sha = "b" * 40
        pins = sha_pins_in_text(f"- uses: actions/checkout@{sha} # v4.2.2\n")
        self.assertEqual(len(violations([f"{a}@v{m}" for a, _s, m in pins])), 1)

    def test_non_github_actions_are_not_floor_checked(self):
        self.assertEqual(violations(["superfly/flyctl-actions/setup-flyctl@master",
                                     "Orphograph/Orphograph/.github/actions/anchor@master"]), [])


if __name__ == "__main__":
    unittest.main()
