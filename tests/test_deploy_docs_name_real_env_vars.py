"""Every variable a doc tells the operator to `fly secrets set` must be one the
running app actually reads.

2026-10-31: deploy/FLY_PREFLIGHT.md recommended `HMAC_SECRET` (the server reads
`ORPHO_HMAC_SECRET`) and `ORPHO_RATE_LIMIT_PER_HOUR` (the server reads
`RATE_LIMIT_PER_DAY`), and two runbooks set `STRIPE_API_KEY` where the server
reads `STRIPE_SECRET_KEY`. Each set a secret that nothing read.

"Read" means an environment read in server/ (os.environ.get / [] / setdefault,
os.getenv, or a server helper named *env*, such as _int_env("NAME", 19)), or a
key under fly.toml [env]. A mention in a comment or a local helper script does
not count: the first version of this test accepted those, and STRIPE_API_KEY
passed because scripts/stripe_bootstrap.sh reads it from a local .env.local.
"""
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS_BLOCK = re.compile(r"(?:fly|flyctl) secrets set((?:[^\n]*\\\n)*[^\n]*)")
NAME = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=")
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")
READ_CALL = re.compile(r"""\b\w*(?:environ\.get|environ\.setdefault|getenv|env\w*)\(\s*["']([A-Z][A-Z0-9_]+)["']""")
READ_INDEX = re.compile(r"""environ\[\s*["']([A-Z][A-Z0-9_]+)["']\s*\]""")


def names_read(server_text: str, fly_env: dict) -> set[str]:
    return set(READ_CALL.findall(server_text)) | set(READ_INDEX.findall(server_text)) | set(fly_env)


def unread_names(doc_text: str, read: set[str]) -> list[str]:
    out = []
    for block in SECRETS_BLOCK.findall(doc_text.replace("\r\n", "\n")):
        # Blank out quoted values first, so KEY_A in X="KEY_A=1" is not a name.
        for name in NAME.findall(QUOTED.sub('""', block)):
            if name not in read:
                out.append(name)
    return out


def _app_reads() -> set[str]:
    server = "\n".join(p.read_text(errors="ignore") for p in (ROOT / "server").rglob("*.py"))
    fly_env = tomllib.loads((ROOT / "fly.toml").read_text()).get("env", {})
    return names_read(server, fly_env)


def _tracked_markdown() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_the_check_fires_on_a_name_nothing_reads():
    doc = 'fly secrets set \\\n  REAL_ONE="x" \\\n  ZZ_NOT_A_REAL_SETTING="1"\n'
    assert unread_names(doc, names_read("os.environ.get('REAL_ONE')", {})) == ["ZZ_NOT_A_REAL_SETTING"]


def test_a_mention_that_is_not_a_read_does_not_count():
    # The STRIPE_API_KEY case: named in a comment, read by nothing in the app.
    server = '# STRIPE_API_KEY lives in .env.local\nkey = os.environ["STRIPE_SECRET_KEY"]\n'
    assert unread_names('fly secrets set STRIPE_API_KEY="sk"\n', names_read(server, {})) == ["STRIPE_API_KEY"]


def test_a_prefixed_name_does_not_count_as_the_bare_one():
    assert unread_names('fly secrets set HMAC_SECRET="x"\n',
                        names_read('os.environ.get("ORPHO_HMAC_SECRET")', {})) == ["HMAC_SECRET"]


def test_helper_reads_crlf_and_quoted_values_are_handled():
    read = names_read('price = _int_env("PACK_PRICE_USD", 19)', {"MIN_CALENDARS_OK": "3"})
    doc = 'fly secrets set \\\r\n  PACK_PRICE_USD="19" \\\r\n  MIN_CALENDARS_OK="X=1" -a app\r\n'
    assert unread_names(doc, read) == []


def test_every_secrets_set_in_tracked_markdown_is_read_by_the_app():
    read = _app_reads()
    docs = _tracked_markdown()
    assert any(d.startswith("deploy/") for d in docs), "no deploy docs found; the check would pass vacuously"
    bad = [f"{d}: {n}" for d in docs for n in unread_names((ROOT / d).read_text(errors="ignore"), read)]
    assert bad == []
