"""test_docs_hub.py

Covers the docs hub and the registry-honesty guard added 2026-08-25.

Two defects are pinned here:

  1. ROUTES.  /docs, /docs/cli and /docs/sdk all 404'd in production while
     /docs/api, /docs/webhooks, /docs/install, /docs/quickstart and
     /docs/verify served 200. web/docs/ had no index.html, and the resolver
     in _serve_static() falls through to 404 for a directory with neither an
     index.html nor a <dir>.html sibling. /docs/mcp 404'd too; it now 301s to
     the canonical /mcp rather than becoming a second page about one product.

  2. REGISTRY HONESTY.  The live /docs/install page told developers to run
     `pip install orphograph` and `npm install orphograph`. Neither package
     exists on PyPI or npm — both return 404 — so every developer who
     followed the documented path got "No matching distribution found". A
     404 says "not here yet"; a failing install says "this product is
     broken", which is worse. The guard below fails the build if any page
     names a registry package we do not actually publish.

The static assertions read the shipped HTML directly (fast, no server); the
route assertions exercise a real server instance the same way the access-hub
suite does.
"""
from __future__ import annotations

import html
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB = REPO_ROOT / "web"


# ───────────────────────── registry-honesty guard ─────────────────────────

# Packages this project actually publishes to a public registry, verified
# 2026-08-25:
#   orphograph-mcp        PyPI 200, version 0.1.1
#   opentimestamps-client PyPI 200 (third-party, linked from the verify docs)
# NOT published, and therefore not installable by name:
#   orphograph            PyPI 404 AND npm 404 — the SDKs install from source
#
# When a package does go live, add it here in the same commit that changes
# the docs to name it. Adding it here without publishing re-opens the defect.
PUBLISHED_PACKAGES = frozenset({
    "orphograph-mcp",
    "opentimestamps-client",
})

# `pip install "git+https://..."` and `npm install /local/path` are source
# installs, not registry lookups — they are exempt because they do not depend
# on a registry entry existing.
# SAME-LINE whitespace only. `\s` would span the newline in
#     npm install
#     npm install /path/to/pkg
# and capture "npm" from the next line as the package name — which is exactly
# what happened on 2026-08-25 when the docs gained a bare `npm install`.
# A command with no package on its own line installs from package.json and
# names nothing, so it must match nothing.
_REGISTRY_INSTALL = re.compile(
    r"(?:pip|pipx|npm)[ \t]+install[ \t]+(?!['\"]?(?:git\+|https?://|file:|\.|/))"
    r"(?:-[\w-]+[ \t]+)*['\"]?([A-Za-z][\w.@/-]*)"
)


def _html_pages() -> list[Path]:
    return sorted(WEB.rglob("*.html"))


# Only <code>/<pre> blocks are scanned. A command a visitor is meant to RUN is
# always in one; prose legitimately contains the words ("no pip install step",
# "npm install straight from the git URL would fail") and flagging those would
# train everyone to ignore this guard.
_CODE_BLOCK = re.compile(r"<(code|pre)\b[^>]*>(.*?)</\1>", re.S | re.I)


def _install_claims() -> list[tuple[Path, str]]:
    """Every (page, package) pair the visitor surface tells someone to install."""
    found = []
    for page in _html_pages():
        text = page.read_text(encoding="utf-8", errors="replace")
        for _tag, block in _CODE_BLOCK.findall(text):
            # Strip nested markup so `npm install <b>orphograph</b>` still reads
            # as one command, and unescape so &quot; around a git+ URL is seen.
            flat = re.sub(r"<[^>]+>", "", block)
            flat = html.unescape(flat)
            for pkg in _REGISTRY_INSTALL.findall(flat):
                found.append((page, pkg))
    return found


def test_no_unpublished_package_is_advertised() -> None:
    """THE BUMP GATE. A page may only name a package we actually publish.

    This is the defect that shipped: /docs/install said `pip install
    orphograph` for eight weeks while PyPI 404'd on that name.
    """
    offenders = [
        (p.relative_to(REPO_ROOT).as_posix(), pkg)
        for p, pkg in _install_claims()
        if pkg not in PUBLISHED_PACKAGES
    ]
    assert not offenders, (
        "These pages tell a developer to install a package that is not on a "
        "public registry — they will get 'No matching distribution found':\n  "
        + "\n  ".join(f"{page}: {pkg}" for page, pkg in offenders)
    )


def test_the_guard_actually_finds_install_commands() -> None:
    """NEGATIVE CONTROL. If the regex matched nothing, the test above would
    pass vacuously no matter what the site claimed — which is exactly how the
    original defect survived. The site does document real installs, so a zero
    here means the scanner broke, not that the site got clean."""
    claims = _install_claims()
    assert claims, "scanner found no install commands at all — regex is broken"
    assert any(pkg == "opentimestamps-client" for _, pkg in claims), (
        "scanner no longer sees the known-good opentimestamps-client command"
    )


def test_guard_would_catch_the_original_defect() -> None:
    """The regex must match the exact strings that shipped, or the guard is
    theatre. These are the literal lines removed from /docs/install."""
    for bad in ("pip install orphograph", "npm install orphograph"):
        assert _REGISTRY_INSTALL.findall(bad) == ["orphograph"], bad
    # …and must NOT flag the source-install forms that replaced them.
    for ok in (
        'pip install "git+https://github.com/Orphograph/Orphograph#subdirectory=sdk-python"',
        "npm install /path/to/Orphograph/sdk-node",
    ):
        assert _REGISTRY_INSTALL.findall(ok) == [], ok
    # A bare `npm install` names no package. It must not swallow the newline
    # and capture the next line's command — the false positive this guard
    # produced on 2026-08-25 against its own docs.
    multiline = "cd Orphograph/sdk-node\nnpm install\nnpm install /path/to/Orphograph/sdk-node\n"
    assert _REGISTRY_INSTALL.findall(multiline) == [], multiline


# ─────────────────────────────── static checks ────────────────────────────

NEW_PAGES = ("docs/index.html", "docs/cli.html", "docs/sdk.html")


@pytest.mark.parametrize("rel", NEW_PAGES)
def test_new_docs_pages_are_csp_clean(rel: str) -> None:
    """Strict CSP: no inline <script>/<style> and no on*= handlers."""
    html = (WEB / rel).read_text(encoding="utf-8")
    assert "<script>" not in html and "<style>" not in html, rel
    assert not re.search(r"\son[a-z]+\s*=", html), rel
    assert 'style="' not in html, rel


def test_docs_index_links_every_docs_page() -> None:
    """The hub is only a hub if it actually reaches the pages."""
    html = (WEB / "docs/index.html").read_text(encoding="utf-8")
    for target in ("/docs/quickstart", "/docs/install", "/docs/verify",
                   "/docs/api", "/docs/webhooks", "/docs/cli", "/docs/sdk",
                   "/mcp"):
        assert f'href="{target}"' in html, f"docs index does not link {target}"


# ─────────────────────────────── route checks ─────────────────────────────

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("docs_hub_data")
    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "ORPHO_DATA_DIR": str(data_dir),
        "ORPHO_COOKIE_SECURE": "0",
    }
    env.pop("RESEND_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "server" / "app.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1).read()
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _get(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


@pytest.mark.parametrize("path", ["/docs", "/docs/cli", "/docs/sdk"])
def test_docs_routes_serve_200(server, path: str) -> None:
    """All three 404'd in production before 2026-08-25."""
    status, body = _get(server + path)
    assert status == 200, f"{path} returned {status}"
    assert "Orphograph" in body


def test_docs_pages_that_already_worked_still_work(server) -> None:
    """Regression guard: the hub must not shadow the existing docs pages."""
    for path in ("/docs/api", "/docs/webhooks", "/docs/install",
                 "/docs/quickstart", "/docs/verify", "/mcp"):
        status, _ = _get(server + path)
        assert status == 200, f"{path} regressed to {status}"


def test_docs_mcp_redirects_to_canonical_mcp(server) -> None:
    """One product, one page. /docs/mcp is a 301, not a second MCP page."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(server + "/docs/mcp", timeout=5)
        status, location = r.status, r.headers.get("Location")
    except urllib.error.HTTPError as e:
        status, location = e.code, e.headers.get("Location")
    assert status == 301, f"expected 301, got {status}"
    assert location == "/mcp", location


def test_new_docs_pages_are_in_the_sitemap(server) -> None:
    status, body = _get(server + "/sitemap.xml")
    assert status == 200
    for loc in ("/docs</loc>", "/docs/cli</loc>", "/docs/sdk</loc>"):
        assert loc in body, f"sitemap missing {loc}"
    # The redirect must NOT be advertised — crawlers should index /mcp.
    assert "/docs/mcp</loc>" not in body
