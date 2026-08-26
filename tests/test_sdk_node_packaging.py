"""test_sdk_node_packaging.py

Pins the invariant that makes sdk-node installable at all (2026-08-25).

sdk-node ships its compiled output in dist/, which is NOT committed, while
package.json's "main" and "bin" both point into it. So a consumer installing
the package by path or as a git dependency gets a package whose entry point
does not exist — unless something builds it at install time. That something
is the `prepare` script.

The subtlety worth keeping: `prepare` also fires when npm installs this
package BY PATH, and in that case npm runs it in the SOURCE directory without
installing that directory's devDependencies. If `npm install` has never been
run inside sdk-node, the consumer's install fails with `tsc: command not
found`. That is why the docs tell people to run `npm install` inside sdk-node
first, and why they no longer mention a separate `npm run build` — the
install IS the build.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_NODE = REPO_ROOT / "sdk-node"
WEB = REPO_ROOT / "web"


def _pkg() -> dict:
    return json.loads((SDK_NODE / "package.json").read_text(encoding="utf-8"))


def _tracked(rel: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", rel], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def test_entry_points_target_dist() -> None:
    """The premise of everything below. If this changes, re-derive the rest."""
    pkg = _pkg()
    assert pkg["main"].startswith("./dist/"), pkg["main"]
    for name, path in pkg.get("bin", {}).items():
        assert path.startswith("./dist/"), (name, path)


def test_dist_is_not_committed() -> None:
    """The other half of the premise — stated so a future commit of dist/
    trips this and prompts a re-read rather than silently making the guard
    below pointless."""
    assert _tracked("sdk-node/dist") == [], (
        "sdk-node/dist is committed now; the install-time build may no longer "
        "be required — re-read this module before deleting anything."
    )


def test_uncommitted_dist_requires_a_prepare_script() -> None:
    """THE GUARD. dist/ is not committed and the entry points need it, so an
    install-time build is mandatory. `prepare` is the only lifecycle hook npm
    runs for BOTH a path install and a git dependency — `prepublishOnly` and
    `build` are not run by a consumer."""
    scripts = _pkg().get("scripts", {})
    if _tracked("sdk-node/dist"):
        return  # dist committed: install-time build no longer load-bearing
    assert "prepare" in scripts, (
        "sdk-node/dist is not committed and package.json 'main' points into "
        "it, so without a 'prepare' script every path/git install produces a "
        "package that cannot be imported."
    )
    assert "tsc" in scripts["prepare"], scripts["prepare"]


def test_docs_do_not_ask_for_a_redundant_build_step() -> None:
    """`npm install` inside sdk-node now runs tsc via prepare. A page that
    still says `npm run build` is telling the reader to do something the
    install already did — the same class of defect as documenting an install
    command that does not work."""
    offenders = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in sorted(WEB.rglob("*.html"))
        if "npm run build" in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert not offenders, (
        "These pages still document a separate build step:\n  "
        + "\n  ".join(offenders)
    )


def test_docs_still_tell_people_to_install_inside_sdk_node() -> None:
    """NEGATIVE CONTROL for the test above. Dropping `npm run build` is only
    correct because `npm install` inside sdk-node replaces it. If the docs
    stopped mentioning that step too, a path install would fail with
    `tsc: command not found` and the test above would pass vacuously."""
    pages = [WEB / "docs/sdk.html", WEB / "docs/install.html"]
    for page in pages:
        text = page.read_text(encoding="utf-8", errors="replace")
        assert re.search(r"cd\s+Orphograph/sdk-node", text), page.name
        assert "npm install" in text, page.name
