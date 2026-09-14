#!/usr/bin/env python3
"""enumerate_surface.py — produce the sweep's domain, and count it TWICE.

A totality claim ("all N surfaces were classified") is only as good as N. If N
comes from the same walk that produced the list, a walk that silently misses a
directory yields a smaller N and a perfectly balanced, perfectly wrong manifest.
So every domain here is counted by two independent means and the run FAILS if
they disagree. That is the mechanical form of the "counted twice" rule; it is
not a promise in prose.

Domains:

  assets   web/asset_versions.json — every served asset with a pinned sha256.
           Count A: entries in the manifest.  Count B: files found on disk that
           the manifest claims.  A disagreement means the manifest and the tree
           have drifted, which is itself the finding.

  diff     files changed against a base ref. Count A: `git diff --name-only`.
           Count B: `git diff --numstat` rows. These come from different
           plumbing paths, so a mismatch means one of them is being filtered.

  routes   the literal dispatch in server/app.py (built 2026-09-12). There is
           ONE source of truth — the `if path == ...` chains — so this domain
           is NOT counted twice, and says so. It is counted once, from the AST
           of every `do_<METHOD>` handler, and that count is held against two
           oracles that do not share its blind spots:
             probes  every entry in scripts/all_endpoints_probe.py PROBES must
                     be an enumerated route or a file in the served web/ tree.
                     This oracle is what found `POST /api/anchor`, dispatched by
                     `if self.path != "/api/anchor": ... return` — an `==`-only
                     walk misses the product's main endpoint and still balances.
             shape   every handler line whose text compares `path` must be a
                     line the AST turned into a route or a guard. A dispatch
                     shape the walk cannot read fails loudly instead of
                     shrinking N.
           Element = (method, eq|prefix, literal[, suffix]). `path != "x"` is a
           route (the fallthrough is the only way to reach the code after it);
           `not path.startswith(...)` is a guard, reported but not counted.
           Not in this domain: the final `_serve_static(self, path)`
           fallthrough, which serves web/ files — that is `assets`. HEAD runs
           the GET routing, so GET routes are not repeated under HEAD.
           Known blind spot of BOTH the AST walk and the shape oracle: reversed
           containment (`"/x" in path`) and `endswith`-only dispatch. Neither
           routes a request in app.py as of 2026-09-12 (`"?" in self.path` and
           `path.endswith(".json")` pick content, not routes); the probes oracle
           is the only guard if that changes.

Exit codes (house contract):
    0  domain enumerated, both counts agree (routes: both oracles hold)
    1  the two counts DISAGREE, or a routes oracle failed — the enumeration is
       untrustworthy, so no sweep over it can claim totality
    3  usage error / not a git repo / unreadable input
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout


def enumerate_assets(repo: Path, ref: str = "origin/master") -> tuple[list[str], int, int]:
    """Count A = manifest entries. Count B = those paths present in the tree."""
    raw = _git(repo, "show", f"{ref}:web/asset_versions.json")
    manifest = json.loads(raw)
    paths = sorted(manifest)
    count_a = len(paths)

    tracked = set(_git(repo, "ls-tree", "-r", "--name-only", ref).split("\n"))
    count_b = sum(1 for p in paths if f"web{p}" in tracked)
    return paths, count_a, count_b


def enumerate_diff(repo: Path, base: str = "origin/master") -> tuple[list[str], int, int]:
    """Count A = --name-only rows. Count B = --numstat rows. Different plumbing."""
    names = [l for l in _git(repo, "diff", "--name-only", f"{base}...HEAD").split("\n") if l]
    numstat = [l for l in _git(repo, "diff", "--numstat", f"{base}...HEAD").split("\n") if l]
    return sorted(names), len(names), len(numstat)


# ----------------------------------------------------------------------------- routes
HANDLER = re.compile(r"do_[A-Z]+$")
# Text form of a path comparison. `\bpath` does not match `rpath` / `rel_path`.
TEXT_COMPARE = re.compile(r"\b(?:self\.)?path\s*(?:==|!=|in\s*\(|\.startswith\()")


def _is_subject(node: ast.AST) -> bool:
    """`path` or `self.path` — the two names the handlers dispatch on."""
    if isinstance(node, ast.Name):
        return node.id == "path"
    return (isinstance(node, ast.Attribute) and node.attr == "path"
            and isinstance(node.value, ast.Name) and node.value.id == "self")


def _literals(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        vals = [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        return vals if vals and len(vals) == len(node.elts) else None
    return None


def _path_call(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == name and _is_subject(node.func.value))


def _walk_handler(fn: ast.FunctionDef, method: str):
    routes: set[tuple] = set()
    guards: set[tuple] = set()
    lines: set[int] = set()
    negated = {id(sub) for node in ast.walk(fn)
               if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)
               for sub in ast.walk(node.operand)}
    suffix_of: dict[int, str] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
            starts = [v for v in node.values if _path_call(v, "startswith")]
            ends = [v for v in node.values if _path_call(v, "endswith")]
            if len(starts) == 1 and len(ends) == 1 and ends[0].args:
                lits = _literals(ends[0].args[0])
                if lits and len(lits) == 1:
                    suffix_of[id(starts[0])] = lits[0]
    for node in ast.walk(fn):
        found: list[tuple] = []
        if (isinstance(node, ast.Compare) and len(node.ops) == 1 and _is_subject(node.left)
                and isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.In))):
            lits = _literals(node.comparators[0])
            if lits is not None:
                found = [(method, "eq", lit, None) for lit in lits]
        elif _path_call(node, "startswith") and node.args:
            lits = _literals(node.args[0])
            if lits is not None:
                found = [(method, "prefix", lit, suffix_of.get(id(node))) for lit in lits]
        if found:
            (guards if id(node) in negated else routes).update(found)
            lines.add(node.lineno)
    return routes, guards, lines


def _probe_entries(probes_src: str) -> list[tuple[str, str]]:
    out = []
    for node in ast.walk(ast.parse(probes_src)):
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Probe"
                and len(node.args) >= 3):
            m, p = node.args[1], node.args[2]
            if isinstance(m, ast.Constant) and isinstance(p, ast.Constant):
                out.append((str(m.value).upper(), str(p.value)))
            elif isinstance(m, ast.Constant) and isinstance(p, ast.JoinedStr):
                # Canonical sample probes interpolate an id; retain their route
                # family in the oracle without evaluating repository code.
                path = "".join(str(v.value) if isinstance(v, ast.Constant) else "sweep-probe"
                               for v in p.values)
                out.append((str(m.value).upper(), path))
    return out


def routes_report(app_src: str, probes_src: str, tracked: set[str]) -> dict:
    """Pure: one AST enumeration plus the probes and shape oracles."""
    tree = ast.parse(app_src)
    source_lines = app_src.splitlines()
    routes: set[tuple] = set()
    guards: set[tuple] = set()
    handlers: list[str] = []
    unaccounted: list[tuple[int, str]] = []
    shape_total = 0
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef) and HANDLER.match(fn.name)):
                continue
            method = fn.name[3:]
            handlers.append(f"{cls.name}.{fn.name}")
            r, g, lines = _walk_handler(fn, method)
            routes |= r
            guards |= g
            for no in range(fn.lineno, (fn.end_lineno or fn.lineno) + 1):
                text = source_lines[no - 1].strip()
                if text.startswith("#") or not TEXT_COMPARE.search(text):
                    continue
                shape_total += 1
                if no not in lines:
                    unaccounted.append((no, text[:100]))

    get_routes = [r for r in routes if r[0] in ("GET", "HEAD")]
    probe_missing, probe_static = [], 0
    probes = _probe_entries(probes_src)
    for method, raw in probes:
        path = raw.split("?", 1)[0]
        pool = get_routes if method in ("GET", "HEAD") else [r for r in routes if r[0] == method]
        if any((k == "eq" and lit == path) or (k == "prefix" and path.startswith(lit))
               for _, k, lit, _ in pool):
            continue
        if method == "GET" and (f"web{path}" in tracked
                                or (path.endswith("/") and f"web{path}index.html" in tracked)):
            probe_static += 1
            continue
        probe_missing.append(f"{method} {raw}")

    as_dicts = lambda s: [dict(method=m, kind=k, literal=lit, suffix=suf)
                          for m, k, lit, suf in sorted(s, key=lambda t: (t[0], t[1], t[2], t[3] or ""))]
    return dict(elements=as_dicts(routes), guards=as_dicts(guards), handlers=handlers,
                probe_total=len(probes), probe_static=probe_static, probe_missing=probe_missing,
                shape_total=shape_total, shape_unaccounted=unaccounted,
                ok=bool(handlers) and not probe_missing and not unaccounted)


def enumerate_routes(repo: Path, ref: str = "origin/master") -> dict:
    app = _git(repo, "show", f"{ref}:server/app.py")
    probes = _git(repo, "show", f"{ref}:scripts/all_endpoints_probe.py")
    tracked = set(_git(repo, "ls-tree", "-r", "--name-only", ref).split("\n"))
    return routes_report(app, probes, tracked)


_FIXTURE_APP = '''
class H:
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return
        if MAINT and not path.startswith(("/founder/", "/status.html")):
            return
        if path.startswith("/api/badge/") and path.endswith(".svg"):
            return
        if path in ("/blog", "/blog/"):
            return
        _serve_static(self, path)

    def do_POST(self):
        if self.path == "/api/x":
            return
        if self.path != "/api/anchor":
            return

    def do_HEAD(self):
        self.do_GET()
'''
_FIXTURE_PROBES = ('PROBES = [Probe("a", "GET", "/"), Probe("b", "POST", "/api/anchor"),\n'
                   '          Probe("c", "GET", "/privacy.html"), Probe("d", "GET", "/api/badge/x.svg?y=1")]\n')
_FIXTURE_TRACKED = {"web/privacy.html"}


def selftest() -> int:
    """Each planted defect must change the verdict it targets."""
    fails, ran = [], []

    def check(name, cond):
        ran.append(name)
        if not cond:
            fails.append(name)

    base = routes_report(_FIXTURE_APP, _FIXTURE_PROBES, _FIXTURE_TRACKED)
    keys = {(e["method"], e["kind"], e["literal"], e["suffix"]) for e in base["elements"]}
    check("baseline ok", base["ok"])
    check("baseline count 6", len(base["elements"]) == 6)
    check("!= fallthrough is a route", ("POST", "eq", "/api/anchor", None) in keys)
    check("prefix carries its suffix", ("GET", "prefix", "/api/badge/", ".svg") in keys)
    check("negated startswith is a guard, not a route",
          not any(e["literal"] == "/founder/" for e in base["elements"])
          and any(g["literal"] == "/founder/" for g in base["guards"]))
    check("static probe resolved from the tree", base["probe_static"] == 1)

    planted = _FIXTURE_APP.replace('        _serve_static(self, path)',
                                   '        if path == "/sweep-control-xyz":\n            return\n'
                                   '        _serve_static(self, path)')
    r = routes_report(planted, _FIXTURE_PROBES, _FIXTURE_TRACKED)
    check("planted route raises the count by one",
          r["ok"] and len(r["elements"]) == 7
          and any(e["literal"] == "/sweep-control-xyz" for e in r["elements"]))

    unseen = _FIXTURE_APP.replace('        _serve_static(self, path)',
                                  '        if path.startswith(PREFIXES):\n            return\n'
                                  '        _serve_static(self, path)')
    r = routes_report(unseen, _FIXTURE_PROBES, _FIXTURE_TRACKED)
    check("unreadable dispatch shape fails the shape oracle",
          not r["ok"] and len(r["shape_unaccounted"]) == 1)

    r = routes_report(_FIXTURE_APP, _FIXTURE_PROBES + 'PROBES += [Probe("e", "GET", "/nowhere")]\n',
                      _FIXTURE_TRACKED)
    check("probe outside routes and tree fails the probes oracle",
          not r["ok"] and r["probe_missing"] == ["GET /nowhere"])

    eq_only = _FIXTURE_APP.replace('self.path != "/api/anchor"', 'self.path is not None')
    r = routes_report(eq_only, _FIXTURE_PROBES, _FIXTURE_TRACKED)
    check("losing the != route is caught by the probes oracle",
          not r["ok"] and r["probe_missing"] == ["POST /api/anchor"])

    for f in fails:
        print(f"SELFTEST FAIL: {f}", file=sys.stderr)
    # Counted, not typed: a hand-written denominator printed "9/9" over ten checks.
    print(f"selftest: {len(ran) - len(fails)}/{len(ran)} cases pass")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    args = [a for a in argv[1:] if not a.startswith("-")]
    domain = args[0] if args else ""
    repo = Path(args[1]).expanduser() if len(args) > 1 else Path.cwd()
    ref = args[2] if len(args) > 2 else "origin/master"

    if domain not in ("assets", "diff", "routes"):
        print("usage: enumerate_surface.py <assets|diff|routes> [repo] [ref] | --selftest",
              file=sys.stderr)
        return 3

    try:
        if domain == "routes":
            rep = enumerate_routes(repo, ref)
        elif domain == "assets":
            items, a, b = enumerate_assets(repo, ref)
        else:
            items, a, b = enumerate_diff(repo, ref)
    except (RuntimeError, json.JSONDecodeError, OSError, SyntaxError) as e:
        print(f"cannot enumerate {domain}: {e}", file=sys.stderr)
        return 3

    if domain == "routes":
        print(f"domain=routes count={len(rep['elements'])} guards={len(rep['guards'])} "
              f"handlers={len(rep['handlers'])} "
              f"probes={rep['probe_total'] - len(rep['probe_missing'])}/{rep['probe_total']} "
              f"(static {rep['probe_static']}) "
              f"shape_lines={rep['shape_total'] - len(rep['shape_unaccounted'])}/{rep['shape_total']}")
        for miss in rep["probe_missing"]:
            print(f"PROBES ORACLE: {miss} is neither an enumerated route nor a served file",
                  file=sys.stderr)
        for no, text in rep["shape_unaccounted"]:
            print(f"SHAPE ORACLE: app.py:{no} compares path in a shape the AST did not read: {text}",
                  file=sys.stderr)
        if not rep["ok"]:
            print("routes enumeration is untrustworthy — no sweep over it can claim totality.",
                  file=sys.stderr)
            return 1
        for e in rep["elements"]:
            print(f"{e['method']}\t{e['kind']}\t{e['literal']}" + (f"\t{e['suffix']}" if e["suffix"] else ""))
        return 0

    print(f"domain={domain} count_a={a} count_b={b}")
    if a != b:
        print(f"COUNT MISMATCH: {a} != {b} — the enumeration is untrustworthy, so a\n"
              f"  sweep over it cannot claim totality. Reconcile before sweeping.",
              file=sys.stderr)
        return 1
    for it in items:
        print(it)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
