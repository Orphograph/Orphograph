# PyPI upload — release runbook

`orphograph` is live on PyPI (0.1.0 uploaded 2026-08-26). Every release
after that follows the same three steps. **Always rebuild before uploading:**
`dist/` may hold artifacts from an older commit, and PyPI burns a version
number permanently — an upload of a stale wheel under a new version cannot
be undone.

## 1 · Bump the version in TWO places (a test holds them equal)

- `pyproject.toml` → `version = "X.Y.Z"`
- `orphograph/__init__.py` → `__version__ = "X.Y.Z"`

`tests/test_cli_offline.py::TestVersionAndCopyDrift` fails if they differ.

## 2 · Rebuild from the commit you intend to ship, then check

```sh
cd sdk-python
git log --oneline -1                       # confirm this is the merged commit
rm -rf dist build orphograph.egg-info
python3 -m build .
python3 -m twine check dist/*              # both artifacts must read PASSED
unzip -p dist/orphograph-X.Y.Z-py3-none-any.whl orphograph/_cli.py | head -5
```

## 3 · Upload

The token lives in the login keychain under the service name `PYPI_TOKEN`.
It is never pasted into a file or a chat.

```sh
cd sdk-python
TWINE_USERNAME=__token__ \
TWINE_PASSWORD="$(security find-generic-password -s PYPI_TOKEN -w)" \
python3 -m twine upload --non-interactive dist/*
```

Twine prints the upload progress and a final URL on success.

## 4 · Verify the PUBLISHED artifact, not the local one

```sh
python3 -m venv /tmp/orpho-check && /tmp/orpho-check/bin/pip install --no-cache-dir "orphograph==X.Y.Z"
/tmp/orpho-check/bin/python -m orphograph --help
curl -s https://pypi.org/pypi/orphograph/json | python3 -c "import sys,json; d=json.load(sys.stdin)['info']; print(d['version'], '|', d['summary'])"
```

The summary printed by the last line is the first sentence a relying party
reads on PyPI; it must match `pyproject.toml`.

## If the upload fails with `400 File already exists`

That version number is spent. Bump to the next patch version in both places
(step 1), rebuild (step 2), and upload again. Never reuse a number.
