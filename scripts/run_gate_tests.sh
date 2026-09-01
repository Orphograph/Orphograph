#!/bin/sh
# THE deploy-gate test command — the one local entry point.
#
# Do not re-derive this invocation by hand: a hand-typed variant ran
# `pytest tests/` with an invented --timeout flag on 2026-08-31, died
# instantly, and a piped tail laundered the exit code to 0 while CI failed
# on three real defects. tests/test_gate_command_pinned.py asserts the
# pytest lines below stay byte-identical to .github/workflows/test.yml and
# deploy.yml, so none of the three can drift alone.
set -e
cd "$(dirname "$0")/.."
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ capture/ tools/test_gate_read.py zk-provenance/test_zk_provenance.py -q --ignore=tests/test_biweekly_safety_audit.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest sdk-python/tests/ -q
