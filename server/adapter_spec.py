"""adapter_spec.py — promote config/verticals/*.yml from landing-page data into
FUNCTIONAL adapter specs, validated against attest_core's canonical record.

The horizontal-primitive test ($1B-path panel, 2026-06-22): adding a vertical must
be a config + adapter, NEVER a new engine. A vertical is "functional" when its
YAML carries a valid ``attestation_profile`` block (claim / subject_kind /
canonicalization / disclosure / accepted_signers); otherwise it is "landing-only"
(a marketing page with no engine binding yet). This module loads + validates those
profiles and proves each produces a schema-valid AttestationRecord — so a new
vertical onboards as data, not code.

stdlib + PyYAML (already installed), mirroring verticals.py's loader.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import attest_core

try:
    import yaml as _yaml  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    from . import _minimal_yaml as _yaml  # type: ignore[no-redef]

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config" / "verticals"

# Allowed enum values for an attestation_profile, and the map from the profile's
# human "claim" to attest_core's schema-closed claim constant.
_CLAIM_MAP = {"existence": "existed_at_or_before_anchor", "state-snapshot": "state_snapshot_at_anchor"}
_SUBJECT_KINDS = ("file", "folder", "json-state")
_CANONICALIZATIONS = ("file", "folder-merkle", "json-snapshot")
_DISCLOSURES = ("full", "selective", "hash-labeled")


@dataclass
class AdapterSpec:
    slug: str
    functional: bool = False
    claim: str | None = None
    subject_kind: str | None = None
    canonicalization: str | None = None
    disclosure: str | None = None
    accepted_signers: list[str] = field(default_factory=list)
    compliance_framework: str | None = None
    compliance_status: str | None = None
    errors: list[str] = field(default_factory=list)


def _validate_profile(slug: str, prof: dict[str, Any]) -> AdapterSpec:
    spec = AdapterSpec(slug=slug)
    errs = spec.errors
    claim = prof.get("claim")
    spec.claim = claim
    if claim not in _CLAIM_MAP:
        errs.append(f"claim must be one of {tuple(_CLAIM_MAP)}, got {claim!r}")
    spec.subject_kind = prof.get("subject_kind")
    if spec.subject_kind not in _SUBJECT_KINDS:
        errs.append(f"subject_kind must be one of {_SUBJECT_KINDS}, got {spec.subject_kind!r}")
    spec.canonicalization = prof.get("canonicalization")
    if spec.canonicalization not in _CANONICALIZATIONS:
        errs.append(f"canonicalization must be one of {_CANONICALIZATIONS}, got {spec.canonicalization!r}")
    spec.disclosure = prof.get("disclosure")
    if spec.disclosure not in _DISCLOSURES:
        errs.append(f"disclosure must be one of {_DISCLOSURES}, got {spec.disclosure!r}")
    signers = prof.get("accepted_signers") or []
    spec.accepted_signers = list(signers)
    if not spec.accepted_signers:
        errs.append("accepted_signers must be a non-empty list")
    cm = prof.get("compliance_mapping") or {}
    spec.compliance_framework = cm.get("framework")
    spec.compliance_status = cm.get("status")
    # Prove the profile yields a schema-valid attest_core record (the real test:
    # the adapter actually maps onto the horizontal primitive).
    if claim in _CLAIM_MAP and spec.subject_kind in _SUBJECT_KINDS:
        sample = attest_core.AttestationRecord(
            receipt_id=f"sample-{slug}",
            subject=attest_core.Subject(digest_sha256="0" * 64, kind=spec.subject_kind),
            claimed_state=_CLAIM_MAP[claim],
            time_anchor=attest_core.TimeAnchor(),
        )
        errs.extend(f"derived record invalid: {e}" for e in attest_core.validate(sample))
    spec.functional = not errs
    return spec


def load_specs(config_dir: Path | None = None) -> dict[str, AdapterSpec]:
    """Load every vertical YAML; return slug -> AdapterSpec. Verticals with no
    ``attestation_profile`` are returned as landing-only (functional=False, no
    errors); verticals with an invalid profile carry errors."""
    cdir = config_dir or _CONFIG_DIR
    out: dict[str, AdapterSpec] = {}
    if not cdir.is_dir():
        return out
    for yml in sorted(cdir.glob("*.yml")):
        with yml.open("r", encoding="utf-8") as fh:
            cfg = _yaml.safe_load(fh) or {}
        slug = cfg.get("slug", yml.stem)
        prof = cfg.get("attestation_profile")
        if not prof:
            out[slug] = AdapterSpec(slug=slug, functional=False)  # landing-only
        else:
            out[slug] = _validate_profile(slug, prof)
    return out


def functional_slugs(config_dir: Path | None = None) -> list[str]:
    return sorted(s for s, sp in load_specs(config_dir).items() if sp.functional)


def main(argv: list[str] | None = None) -> int:
    specs = load_specs()
    bad = 0
    for slug, sp in sorted(specs.items()):
        if sp.errors:
            bad += 1
            print(f"  ✗ {slug}: INVALID profile — {'; '.join(sp.errors)}")
        elif sp.functional:
            print(f"  ✓ {slug}: functional adapter ({sp.claim}/{sp.canonicalization}/{sp.disclosure})")
        else:
            print(f"  · {slug}: landing-only (no attestation_profile yet)")
    print(f"\n{sum(1 for s in specs.values() if s.functional)} functional / {len(specs)} verticals; {bad} invalid")
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
