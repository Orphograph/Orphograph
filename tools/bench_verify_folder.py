#!/usr/bin/env python3
"""bench_verify_folder.py — re-measure the number quoted in dist/orphograph-verify/README.md.

Builds a synthetic folder (N files across D dirs, random bytes of random size in
[min,max]), times the anchor-side tree build (server/merkle.py) and the shipped
verifier (`dist/orphograph-verify/verify.py folder`, real subprocess), prints both.

    python3 tools/bench_verify_folder.py --files 10000 --dirs 50

Stdlib only. Deterministic layout (seeded), non-deterministic wall time by nature.
The point is that the README figure is ONE COMMAND away, not one sentence away.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import merkle  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--files", type=int, default=10_000)
    ap.add_argument("--dirs", type=int, default=50)
    ap.add_argument("--min-bytes", type=int, default=200)
    ap.add_argument("--max-bytes", type=int, default=4_000)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    with tempfile.TemporaryDirectory(prefix="orpho-bench-") as td:
        folder = Path(td) / "folder"
        total = 0
        for i in range(a.files):
            d = folder / f"d{i % a.dirs}"
            d.mkdir(parents=True, exist_ok=True)
            n = rnd.randint(a.min_bytes, a.max_bytes)
            (d / f"f{i}.bin").write_bytes(os.urandom(n))
            total += n
        t0 = time.perf_counter()
        tree = merkle.MerkleTree.from_folder(folder)
        t_build = time.perf_counter() - t0
        mpath = Path(td) / "manifest.json"
        mpath.write_text(json.dumps(tree.manifest()))
        t0 = time.perf_counter()
        proc = subprocess.run([sys.executable, str(ROOT / "dist" / "orphograph-verify" / "verify.py"),
                               "folder", "--dir", str(folder), "--manifest", str(mpath)],
                              capture_output=True, text=True)
        t_verify = time.perf_counter() - t0
        print(f"files={a.files} dirs={a.dirs} bytes={total:,} ({total/1e6:.1f} MB)")
        print(f"anchor-side tree build: {t_build:.2f} s")
        print(f"verify.py folder:       {t_verify:.2f} s (rc={proc.returncode})")
        print(f"python {sys.version.split()[0]} on {sys.platform}")
        return 0 if proc.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
