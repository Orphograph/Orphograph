"""The tree must never fall behind, or silently diverge from, what PyPI ships.

Regression for the drift found 2026-11-06: PyPI orphograph 0.1.2 differed
from sdk-python@master in 3 of 5 modules, master was newer but still said
0.1.1, and nothing compared the two. sdk-python/RELEASED.json pins the
published sdist (version, sdist sha256, sha256 of every package file);
scripts/sdk_python_release_record.py builds it and holds the rule.

This file runs in the sdk-python interpreter (CI step "Run the sdk-python
test suite"), so it imports the record tool by path, not as a package.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

SDK = Path(__file__).resolve().parent.parent
REPO = SDK.parent

_spec = importlib.util.spec_from_file_location(
    "sdk_python_release_record", REPO / "scripts" / "sdk_python_release_record.py")
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)


def _sdist(dirpath: Path, version: str, files: dict[str, bytes]) -> Path:
    """A minimal sdist-shaped tar.gz, the layout PyPI's orphograph sdists use."""
    path = dirpath / f"orphograph-{version}.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, data in files.items():
            info = tarfile.TarInfo(f"orphograph-{version}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    return path


class TestTreeIsCurrentWithPyPI(unittest.TestCase):
    def setUp(self):
        self.record = rr.load_record()

    def test_record_is_well_formed(self):
        rr.version_tuple(self.record["version"])
        self.assertEqual(self.record["sdist"], f"orphograph-{self.record['version']}.tar.gz")
        self.assertRegex(self.record["sdist_sha256"], r"^[0-9a-f]{64}$")
        self.assertIn("orphograph/__init__.py", self.record["files"])
        for rel, digest in self.record["files"].items():
            self.assertTrue(rel.startswith("orphograph/"), rel)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_tree_version_is_not_below_the_released_one(self):
        self.assertGreaterEqual(
            rr.version_tuple(rr.tree_version()), rr.version_tuple(self.record["version"]),
            "sdk-python's version is below the one on PyPI: the next upload "
            "would publish an older number")

    def test_changed_package_files_carry_a_new_version(self):
        found = rr.problems(self.record, rr.tree_version(), rr.tree_files())
        self.assertEqual(found, [], "\n".join(found))


class TestTheRuleItself(unittest.TestCase):
    """The guard above is only as good as problems(); plant each defect."""

    REC = {"version": "0.1.2", "files": {"orphograph/a.py": "1" * 64, "orphograph/b.py": "2" * 64}}
    SAME = {"orphograph/a.py": "1" * 64, "orphograph/b.py": "2" * 64}

    def test_same_bytes_same_version_is_current(self):
        self.assertEqual(rr.problems(self.REC, "0.1.2", self.SAME), [])

    def test_lower_version_fails_even_with_identical_bytes(self):
        self.assertEqual(len(rr.problems(self.REC, "0.1.1", self.SAME)), 1)

    def test_edited_file_without_bump_fails(self):
        edited = dict(self.SAME, **{"orphograph/b.py": "3" * 64})
        found = rr.problems(self.REC, "0.1.2", edited)
        self.assertEqual(len(found), 1)
        self.assertIn("orphograph/b.py", found[0])

    def test_added_or_removed_file_without_bump_fails(self):
        added = dict(self.SAME, **{"orphograph/c.py": "4" * 64})
        self.assertIn("orphograph/c.py", rr.problems(self.REC, "0.1.2", added)[0])
        removed = {"orphograph/a.py": "1" * 64}
        self.assertIn("orphograph/b.py", rr.problems(self.REC, "0.1.2", removed)[0])

    def test_edited_file_with_bump_is_current(self):
        edited = dict(self.SAME, **{"orphograph/b.py": "3" * 64})
        self.assertEqual(rr.problems(self.REC, "0.1.3", edited), [])
        self.assertEqual(rr.problems(self.REC, "0.2.0", edited), [])

    def test_the_2026_11_06_drift_fails_both_ways(self):
        """master's state that day: newer bytes, version 0.1.1 < PyPI 0.1.2."""
        edited = dict(self.SAME, **{"orphograph/a.py": "9" * 64})
        self.assertEqual(len(rr.problems(self.REC, "0.1.1", edited)), 2)

    def test_non_xyz_versions_are_refused(self):
        for bad in ("0.1", "0.1.3rc1", "", "v0.1.3"):
            with self.assertRaises(ValueError):
                rr.version_tuple(bad)


class TestRecordFromSdist(unittest.TestCase):
    def test_records_only_package_files_in_memory(self):
        with tempfile.TemporaryDirectory() as d:
            files = {
                "orphograph/__init__.py": b"__version__ = '9.9.9'\n",
                "orphograph/py.typed": b"",
                "orphograph/__pycache__/x.cpython-311.pyc": b"junk",
                "tests/test_x.py": b"not package\n",
                "pyproject.toml": b"[project]\n",
            }
            path = _sdist(Path(d), "9.9.9", files)
            rec = rr.record_from_sdist(path)
            self.assertEqual(list(Path(d).iterdir()), [path], "nothing may be extracted")
        self.assertEqual(rec["version"], "9.9.9")
        self.assertEqual(rec["files"], {
            "orphograph/__init__.py": hashlib.sha256(files["orphograph/__init__.py"]).hexdigest(),
            "orphograph/py.typed": hashlib.sha256(b"").hexdigest(),
        })

    def test_sdist_hash_is_of_the_file_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            path = _sdist(Path(d), "1.2.3", {"orphograph/__init__.py": b"x"})
            rec = rr.record_from_sdist(path)
            self.assertEqual(rec["sdist_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_wrong_name_or_empty_package_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            other = _sdist(Path(d), "1.0.0", {"orphograph/__init__.py": b"x"})
            renamed = other.with_name("somethingelse-1.0.0.tar.gz")
            other.rename(renamed)
            with self.assertRaises(ValueError):
                rr.record_from_sdist(renamed)
            empty = _sdist(Path(d), "1.0.1", {"README.md": b"x"})
            with self.assertRaises(ValueError):
                rr.record_from_sdist(empty)

    def test_committed_record_is_what_the_tool_writes(self):
        """RELEASED.json is the tool's output format, byte for byte, so a
        hand edit (say, a version bumped without a real upload) shows up."""
        rec = rr.load_record()
        self.assertEqual(rr.RECORD.read_text(encoding="utf-8"), json.dumps(rec, indent=2) + "\n")
        self.assertEqual(list(rec), ["version", "sdist", "sdist_sha256", "files"])
        self.assertEqual(list(rec["files"]), sorted(rec["files"]))


if __name__ == "__main__":
    unittest.main()
