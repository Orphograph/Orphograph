"""Tests for adapter_spec — functional vertical adapters over attest_core."""
from __future__ import annotations

import adapter_spec


def test_legal_vertical_is_a_functional_adapter():
    specs = adapter_spec.load_specs()
    assert "legal" in specs, "legal vertical config must load"
    legal = specs["legal"]
    assert legal.functional and not legal.errors, f"legal should be functional: {legal.errors}"
    assert legal.claim == "existence"
    assert legal.canonicalization == "folder-merkle"
    assert "did:key" in legal.accepted_signers


def test_landing_only_verticals_load_without_errors():
    # The other shipped verticals have no attestation_profile yet — they must load
    # as landing-only (functional False) but carry NO errors (valid landing pages).
    specs = adapter_spec.load_specs()
    for slug in ("accounting", "construction", "healthcare", "inspection", "realestate"):
        assert slug in specs, f"{slug} must load"
        assert specs[slug].errors == [], f"{slug} landing-only must have no errors"


def test_functional_slugs_includes_legal():
    assert "legal" in adapter_spec.functional_slugs()


def test_invalid_profile_is_rejected(tmp_path):
    (tmp_path / "bogus.yml").write_text(
        "slug: bogus\n"
        "attestation_profile:\n"
        "  claim: predicts_the_market\n"      # forbidden claim
        "  subject_kind: spaceship\n"          # invalid kind
        "  canonicalization: vibes\n"          # invalid
        "  disclosure: full\n"
        "  accepted_signers: []\n"             # empty
    )
    specs = adapter_spec.load_specs(tmp_path)
    assert "bogus" in specs
    sp = specs["bogus"]
    assert not sp.functional
    assert any("claim" in e for e in sp.errors)
    assert any("subject_kind" in e for e in sp.errors)
    assert any("canonicalization" in e for e in sp.errors)
    assert any("accepted_signers" in e for e in sp.errors)


def test_valid_state_snapshot_profile_is_functional(tmp_path):
    # An observer-only state-snapshot adapter (a system-state issuer) is valid.
    (tmp_path / "obs.yml").write_text(
        "slug: obs\n"
        "attestation_profile:\n"
        "  claim: state-snapshot\n"
        "  subject_kind: json-state\n"
        "  canonicalization: json-snapshot\n"
        "  disclosure: full\n"
        "  accepted_signers: [did:key]\n"
    )
    sp = adapter_spec.load_specs(tmp_path)["obs"]
    assert sp.functional and not sp.errors
