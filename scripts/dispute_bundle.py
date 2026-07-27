#!/usr/bin/env python3
"""dispute_bundle.py — Build a portable, fully-verifiable proof bundle.

For use in disputes or for long-term archival. Produces a single .tar.gz
containing:

  - The original file (or a copy you provide)
  - The Orphograph receipt JSON
  - The 5 .ots Bitcoin proof files
  - The standalone open-source verifier (stdlib Python, no deps)
  - VERIFIER_SPEC.md — the normative algorithm, so a recipient can write a
    second implementation and compare instead of trusting ours
  - SUMMARY.txt / RESUMEN.txt — the plain-language sheet (EN / ES) a
    non-technical reader can act on, stating the claim ceiling
  - VERIFY.md — step-by-step verification instructions
  - sha256sum.txt — checksums for every file in the bundle

Anyone receiving the bundle can verify the proof offline against Bitcoin's
public ledger without trusting Orphograph or its domain.

Usage:
    python3 dispute_bundle.py <file> <receipt_dir> [-o <output.tar.gz>]

Example:
    # If you saved your receipt to ~/Downloads/r_abc123/, do:
    python3 dispute_bundle.py photo.jpg ~/Downloads/r_abc123/

Output: <basename>_dispute_bundle.tar.gz
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path


VERIFY_MD = """# Verifying this Orphograph dispute bundle

This bundle contains a Bitcoin-anchored timestamp proof. You can verify it
offline using only Python standard library — no Orphograph server required.

## What's in this bundle

- `<filename>` — the original file
- `receipt.json` — Orphograph receipt metadata (hashes, calendars, attestation)
- `*.ots` — 5 OpenTimestamps binary proof files
- `verify_cli.py` — standalone verifier, stdlib Python only
- `VERIFIER_SPEC.md` — the normative algorithm the verifier implements, so you
  can write your own checker and compare rather than trusting ours
- `SUMMARY.txt` / `RESUMEN.txt` — one plain-language page (English / Spanish)
  stating what this shows, what it does not, and how to check it
- `sha256sum.txt` — checksums for every file in the bundle
- `VERIFY.md` — this file

## Run verification (3 steps)

```bash
# 1. Verify the bundle hasn't been tampered with
sha256sum -c sha256sum.txt

# 2. Verify the file's hash matches the receipt
python3 -c "
import hashlib, json, sys
data = open('<filename>', 'rb').read()
sha256 = hashlib.sha256(data).hexdigest()
sha512 = hashlib.sha512(data).hexdigest()
rec = json.load(open('receipt.json'))
print('SHA-256 file:    ', sha256)
print('SHA-256 receipt: ', rec['hash_hex'])
print('Match:', sha256 == rec['hash_hex'])
if rec.get('sha512_hex'):
    print('SHA-512 match:', sha512 == rec['sha512_hex'])
"

# 3. Verify the .ots proofs against Bitcoin's chain
python3 verify_cli.py receipt.json
```

## What this proves

The file's SHA-256 hash was anchored to the Bitcoin blockchain on the date
recorded in `receipt.json` (field `created_at`). The Merkle path in each
`.ots` file ties the hash to a specific Bitcoin transaction in a specific
block.

## What this does NOT prove

- It does NOT prove who created the file.
- It does NOT prove ownership.
- It does NOT replace legal evidence (consult a digital evidence specialist).

It proves the file existed in the form anchored at the time anchored. The
rest of the evidence chain (RAW camera files, social media posts, email
records, witnesses) must come from elsewhere.

## Attestation

If the receipt has an `attestation` field, it is a free-form claim the
anchorer attached to the receipt at anchor time. The claim itself is part
of the anchored data — its existence at the receipt date is provable. Its
truth value is a separate question for the parties involved.
"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ── plain-language summary (Wedge 04) ───────────────────────────────────────
# The last mile is a document, not a digest. One page a non-technical reader —
# an adjuster, a paralegal, a contractor — gets through without help, stating
# the claim ceiling explicitly. Shipped in English and Spanish: Puerto Rico is
# the first market, and a sheet nobody can read is not a deliverable.

_SUMMARY = {
    "en": {
        "file": "SUMMARY.txt",
        "title": "WHAT THIS FOLDER IS",
        "body": """\
Someone gave you this folder to show that a file existed by a certain date.
You do not need an account, a password, or their permission to check it, and
you do not need to take their word for anything. Everything needed is here.

THE FILE
    Name         {filename}
    Fingerprint  {hash_hex}

    A fingerprint is a short code calculated from the file's contents. Change
    one character anywhere in the file and the code comes out completely
    different. That is what makes it useful as evidence.

WHAT THIS SHOWS
    This exact file existed no later than {created}.

    That date is recorded in the public Bitcoin ledger. No single person or
    company controls that ledger, and older entries are not rewritten, which
    is why a date recorded there is hard to argue with. This folder holds
    {ots_count} independent records of it.

WHAT THIS DOES NOT SHOW
    Who wrote or created the file.
    Whether anything written in the file is true, accurate, or complete.
    Who owns the file or holds any rights to it.
    Whether a court will accept it. That is for a court and a lawyer to
        decide, and nothing in this folder decides it for them.

    If someone tells you this folder proves any of the four things above,
    they are overstating it.

HOW TO CHECK IT YOURSELF (about five minutes)
    You need a computer with Python installed. No internet connection is
    required for steps 1 and 2.

    1. Open a terminal in this folder.
    2. Run:  python3 verify_cli.py receipt.json
       It reads the records here and reports whether they match the
       fingerprint above.
    3. Optional, and the strongest check: confirm the date directly against
       Bitcoin itself, using the free OpenTimestamps tool. VERIFY.md has the
       exact commands.

    VERIFIER_SPEC.md describes exactly how the checking works, so a
    programmer you trust can write their own checker and compare answers
    rather than relying on ours.

IF YOU READ ONE LINE
    This says WHEN a file existed. It says nothing about who made it or
    whether it is true.
""",
    },
    "es": {
        "file": "RESUMEN.txt",
        "title": "QUE ES ESTA CARPETA",
        "body": """\
Alguien le entrego esta carpeta para demostrar que un archivo ya existia en
cierta fecha. Usted no necesita una cuenta, una contrasena, ni el permiso de
esa persona para comprobarlo, y no tiene que confiar en su palabra. Todo lo
necesario esta aqui.

EL ARCHIVO
    Nombre        {filename}
    Huella        {hash_hex}

    La huella es un codigo corto que se calcula a partir del contenido del
    archivo. Si se cambia un solo caracter, el codigo cambia por completo.
    Eso es lo que la hace util como evidencia.

LO QUE ESTO DEMUESTRA
    Que este archivo exacto ya existia a mas tardar el {created}.

    Esa fecha esta registrada en el libro publico de Bitcoin. Ninguna persona
    ni empresa controla ese registro, y las entradas antiguas no se reescriben,
    por eso una fecha registrada alli es dificil de disputar. Esta carpeta
    contiene {ots_count} registros independientes.

LO QUE ESTO NO DEMUESTRA
    Quien escribio o creo el archivo.
    Si lo que dice el archivo es cierto, exacto o completo.
    Quien es el dueno del archivo ni que derechos tiene sobre el.
    Si un tribunal lo va a aceptar. Eso lo decide un tribunal y un abogado,
        y nada en esta carpeta lo decide por ellos.

    Si alguien le dice que esta carpeta demuestra alguna de esas cuatro
    cosas, esta exagerando.

COMO COMPROBARLO USTED MISMO (unos cinco minutos)
    Necesita una computadora con Python instalado. No hace falta conexion a
    internet para los pasos 1 y 2.

    1. Abra una terminal en esta carpeta.
    2. Ejecute:  python3 verify_cli.py receipt.json
       Lee los registros que estan aqui e indica si coinciden con la huella
       que aparece arriba.
    3. Opcional, y la comprobacion mas fuerte: confirme la fecha directamente
       contra Bitcoin con la herramienta gratuita OpenTimestamps. VERIFY.md
       tiene los comandos exactos.

    VERIFIER_SPEC.md explica exactamente como funciona la comprobacion, para
    que un programador de su confianza escriba su propio verificador y compare
    los resultados en vez de depender del nuestro.

SI SOLO LEE UNA LINEA
    Esto dice CUANDO existia un archivo. No dice nada sobre quien lo hizo ni
    sobre si es cierto.
""",
    },
}


def summary_text(lang: str, filename: str, receipt: dict, ots_count: int) -> str:
    """Render the plain-language sheet for one language.

    Deliberately ASCII-only in the Spanish text: the sheet is meant to be
    printed and emailed through arbitrary systems, and a mangled accent in a
    legal-adjacent document reads as carelessness. Plain wording survives
    transcoding; accented characters do not always.
    """
    spec = _SUMMARY[lang]
    created = str(receipt.get("created_at", "") or "an unrecorded date")[:19].replace("T", " ")
    body = spec["body"].format(
        filename=filename,
        hash_hex=receipt.get("hash_hex", "(not recorded)"),
        created=created,
        ots_count=ots_count,
    )
    bar = "=" * len(spec["title"])
    return f"ORPHOGRAPH\n{spec['title']}\n{bar}\n\n{body}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", help="path to the original file")
    ap.add_argument("receipt_dir", help="directory containing receipt.json + .ots files")
    ap.add_argument("-o", "--output", help="output .tar.gz path")
    args = ap.parse_args()

    src_file = Path(args.file).resolve()
    src_dir = Path(args.receipt_dir).resolve()
    if not src_file.is_file():
        print(f"error: file not found: {src_file}", file=sys.stderr)
        return 1
    if not src_dir.is_dir():
        print(f"error: receipt dir not found: {src_dir}", file=sys.stderr)
        return 1

    receipt_json = src_dir / "receipt.json"
    if not receipt_json.exists():
        print(f"error: receipt.json not found in {src_dir}", file=sys.stderr)
        return 1
    rec = json.loads(receipt_json.read_text())

    # Sanity: file hash must match receipt
    actual = sha256_of(src_file)
    expected = rec.get("hash_hex", "")
    if actual != expected:
        print(f"WARNING: file SHA-256 ({actual}) does NOT match receipt hash ({expected}).")
        print("This bundle would fail verification. Continuing anyway.")

    # Determine output path
    out = args.output
    if not out:
        bundle_name = f"{src_file.stem}_dispute_bundle.tar.gz"
        out = str(Path.cwd() / bundle_name)
    out_path = Path(out).resolve()

    # Locate standalone verifier (try several common locations)
    here = Path(__file__).resolve().parent.parent
    verifier_candidates = [
        here / "server" / "verify_cli.py",
        here / "dist" / "orphograph-verify" / "verify_cli.py",
    ]
    verifier = next((p for p in verifier_candidates if p.is_file()), None)
    if not verifier:
        print("error: could not locate verify_cli.py; download from https://orphograph.com/verify/",
              file=sys.stderr)
        return 1

    # Build bundle in a temp dir, then tar.gz it
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        # Copy original file
        shutil.copy2(src_file, td_path / src_file.name)
        # Copy receipt.json + .ots files
        shutil.copy2(receipt_json, td_path / "receipt.json")
        for ots in sorted(src_dir.glob("*.ots")):
            shutil.copy2(ots, td_path / ots.name)
        # Copy verifier
        shutil.copy2(verifier, td_path / "verify_cli.py")
        # Write VERIFY.md (substituting filename)
        verify_md = VERIFY_MD.replace("<filename>", src_file.name)
        (td_path / "VERIFY.md").write_text(verify_md)

        # Ship the verifier's SPEC alongside the verifier itself. Without it a
        # recipient can run our checker but cannot independently write a second
        # one to compare against — which is the whole point of shipping a
        # checker for inspection. Non-fatal if absent: an older checkout should
        # still produce a usable bundle.
        spec_src = next((p for p in (here / "docs" / "VERIFIER_SPEC.md",
                                     here / "VERIFIER_SPEC.md") if p.is_file()), None)
        if spec_src:
            shutil.copy2(spec_src, td_path / "VERIFIER_SPEC.md")
        else:
            print("warning: VERIFIER_SPEC.md not found; bundle will ship without it",
                  file=sys.stderr)

        # Plain-language sheets — the deliverable a non-technical reader acts on.
        ots_count = len(list(src_dir.glob("*.ots")))
        for lang, spec in _SUMMARY.items():
            (td_path / spec["file"]).write_text(
                summary_text(lang, src_file.name, rec, ots_count),
                encoding="utf-8",
            )

        # Build sha256sum.txt — iterates the directory, so every file added
        # above is covered automatically. Keep it LAST.
        sums = []
        for f in sorted(td_path.iterdir()):
            if f.is_file():
                sums.append(f"{sha256_of(f)}  {f.name}")
        (td_path / "sha256sum.txt").write_text("\n".join(sums) + "\n")
        # Tar.gz
        with tarfile.open(out_path, "w:gz") as tf:
            for f in sorted(td_path.iterdir()):
                if f.is_file():
                    tf.add(f, arcname=f"{src_file.stem}_dispute_bundle/{f.name}")

    print(f"✓ Bundle created: {out_path}")
    print(f"  Size: {out_path.stat().st_size:,} bytes")
    print(f"  SHA-256: {sha256_of(out_path)}")
    print()
    print(f"Share this bundle. Anyone can verify it with:")
    print(f"  tar xzf {out_path.name} && cd {src_file.stem}_dispute_bundle")
    print(f"  python3 verify_cli.py receipt.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
