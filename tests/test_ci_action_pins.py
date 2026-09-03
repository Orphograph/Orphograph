"""GitHub Action pin parity and runtime floor.

PR #224 moved checkout/setup-python/setup-node off the deprecated Node 20
runtime, and review found the public copy-paste template in
integrations/github-action/README.md still shipped `checkout@v4` and
`upload-artifact@v4`: the repo's own CI was green while the documented
integration would print the exact warning the PR removed. Nothing read the
README's code fences. This test does, and it also refuses any `actions/*`
major below the Node 24 line so the next runtime deprecation is caught here
rather than by reading a run warning.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
TEMPLATE_DOCS = [ROOT / "integrations" / "github-action" / "README.md"]

# First major of each official action that runs on the Node 24 runtime.
NODE24_FLOOR = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/setup-node": 5,
    "actions/upload-artifact": 5,
}

PIN = re.compile(r"uses:\s*(actions/[a-z0-9-]+)@v(\d+)\b")


def pins_in(text):
    """{action: set(majors)} for every `uses: actions/<name>@vN` in text."""
    found = {}
    for action, major in PIN.findall(text):
        found.setdefault(action, set()).add(int(major))
    return found


def below_floor(pins, floor=NODE24_FLOOR):
    return sorted(
        f"{action}@v{major}"
        for action, majors in pins.items()
        for major in majors
        if action in floor and major < floor[action]
    )


class CiActionPins(unittest.TestCase):
    def test_workflows_have_pins_to_check(self):
        # Floor: a regex that matches nothing would pass every test below.
        pins = pins_in("\n".join(p.read_text() for p in WORKFLOWS))
        self.assertIn("actions/checkout", pins)
        self.assertIn("actions/setup-python", pins)

    def test_workflow_pins_meet_node24_floor(self):
        pins = pins_in("\n".join(p.read_text() for p in WORKFLOWS))
        self.assertEqual(below_floor(pins), [])

    def test_workflows_agree_on_one_major_per_action(self):
        pins = pins_in("\n".join(p.read_text() for p in WORKFLOWS))
        split = {a: sorted(m) for a, m in pins.items() if len(m) > 1}
        self.assertEqual(split, {}, "same action pinned at different majors")

    def test_public_template_matches_workflow_majors(self):
        workflow = pins_in("\n".join(p.read_text() for p in WORKFLOWS))
        for doc in TEMPLATE_DOCS:
            doc_pins = pins_in(doc.read_text())
            self.assertTrue(doc_pins, f"{doc} has no action pins to check")
            self.assertEqual(below_floor(doc_pins), [], f"{doc}")
            for action, majors in doc_pins.items():
                if action in workflow:
                    self.assertEqual(
                        majors, workflow[action],
                        f"{doc.relative_to(ROOT)} pins {action} at {sorted(majors)}, "
                        f"workflows use {sorted(workflow[action])}",
                    )

    def test_negative_control_stale_pin_is_caught(self):
        # Prove the checker can fail: the exact README state before this fix.
        stale = "- uses: actions/checkout@v4\n  uses: actions/upload-artifact@v4\n"
        self.assertEqual(
            below_floor(pins_in(stale)),
            ["actions/checkout@v4", "actions/upload-artifact@v4"],
        )
        self.assertEqual(below_floor(pins_in("uses: actions/checkout@v7")), [])


if __name__ == "__main__":
    unittest.main()
