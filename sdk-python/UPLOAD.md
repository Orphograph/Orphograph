# PyPI upload — one-command path

The wheel and sdist for `orphograph 0.1.0` are already built and pass
`twine check`. Once your PyPI account is verified and you have an API
token, the upload is one command.

## Steps the founder runs

### 1 · Generate a PyPI API token (after verification email lands)

1. Sign in at <https://pypi.org/manage/account/>.
2. Enable 2FA (PyPI requires it for any upload).
3. Under "API tokens" → "Add API token". For the first upload, choose
   "Entire account" scope (after the first upload you can re-scope to
   the `orphograph` project specifically).
4. Copy the token. It looks like `pypi-AgEIcHlwaS5vcmcCJ…`.

### 2 · Upload

```sh
cd /path/to/orphograph/sdk-python
python3 -m twine upload dist/*
```

When prompted:
- Username: `__token__` (literal string, that's the API-token
  convention)
- Password: paste the token from step 1

Twine prints the upload progress and a final URL on success.
The package is then live at `https://pypi.org/project/orphograph/`
and installable everywhere with `pip install orphograph`.

### 3 · Verify

```sh
pip install --no-cache-dir orphograph
python3 -m orphograph --help
```

## Artifacts

```
dist/
  orphograph-0.1.0-py3-none-any.whl     (15.7 KB)
  orphograph-0.1.0.tar.gz                (17.2 KB)
```

Both pass `twine check`. Stdlib-only at runtime, MIT-licensed.

## Subsequent releases

Bump `version` in `pyproject.toml` AND `[tool.orphograph]` `mcp_name`
(if used), then:

```sh
rm -rf dist build
python3 -m build .
python3 -m twine upload dist/*
```

The same token works for all future uploads under the project once
the first upload reserves the name.

## If the upload fails with `403 Forbidden`

The name `orphograph` was taken between when this office reserved it
and when the upload ran. Pick `orphograph-anchor` or `orphograph-mcp`
as a fallback name; the import path `orphograph` stays unchanged
inside the package.
