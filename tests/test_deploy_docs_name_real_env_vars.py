"""Every variable a deploy doc tells the operator to `fly secrets set` must be
one the code actually reads.

2026-10-31: deploy/FLY_PREFLIGHT.md recommended `HMAC_SECRET` (the server reads
`ORPHO_HMAC_SECRET`) and `ORPHO_RATE_LIMIT_PER_HOUR="10"` (the server reads
`RATE_LIMIT_PER_DAY`, canonical 3). Following the runbook set two secrets that
nothing read, and the operator believed a signing key was configured.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_DIRS = ("server", "scripts", "tools")
SECRETS_BLOCK = re.compile(r"(?:fly|flyctl) secrets set((?:[^\n]*\\\n)*[^\n]*)")
NAME = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=")


def _code_text() -> str:
    parts = [p.read_text(errors="ignore")
             for d in CODE_DIRS for p in (ROOT / d).rglob("*")
             if p.is_file() and p.suffix in (".py", ".sh")]
    parts.append((ROOT / "fly.toml").read_text())
    return "\n".join(parts)


def unread_names(doc_text: str, code_text: str) -> list[str]:
    out = []
    for block in SECRETS_BLOCK.findall(doc_text):
        for name in NAME.findall(block):
            # \b treats "_" as a word character, so HMAC_SECRET does not match
            # inside ORPHO_HMAC_SECRET or HMAC_SECRET_PATH.
            if not re.search(r"\b" + re.escape(name) + r"\b", code_text):
                out.append(name)
    return out


def test_the_check_fires_on_a_name_nothing_reads():
    doc = 'fly secrets set \\\n  REAL_ONE="x" \\\n  ORPHO_NOT_A_REAL_SETTING="1"\n'
    assert unread_names(doc, "os.environ.get('REAL_ONE')") == ["ORPHO_NOT_A_REAL_SETTING"]


def test_a_prefixed_name_does_not_count_as_the_bare_one():
    doc = 'fly secrets set HMAC_SECRET="x"\n'
    assert unread_names(doc, 'os.environ.get("ORPHO_HMAC_SECRET")') == ["HMAC_SECRET"]


def test_every_deploy_doc_secret_is_read_by_the_code():
    code = _code_text()
    docs = sorted((ROOT / "deploy").glob("*.md"))
    assert docs, "no deploy docs found; the check would pass vacuously"
    bad = [f"{d.relative_to(ROOT)}: {n}" for d in docs for n in unread_names(d.read_text(errors="ignore"), code)]
    assert bad == []
