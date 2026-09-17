"""Service wrapper — thin projection over the standards library (ADR-0200)."""
from __future__ import annotations

import copy

from app.services.cartography.standards_qa import (
    run_postcompile_qa,
    run_precompile_gate,
)
from tests.cartography.test_standards_evaluate_v1 import base_mapspec


def test_gate_service_blocks_only_with_explicit_profile():
    spec = base_mapspec()
    del spec["layout"]
    inferred = run_precompile_gate(copy.deepcopy(spec))
    assert inferred["verdict"] == "allow"
    explicit = run_precompile_gate(
        copy.deepcopy(spec), purpose="publication", medium="print")
    assert explicit["verdict"] == "block"


def test_postcompile_service_shape_and_determinism():
    spec = base_mapspec()
    first = run_postcompile_qa(copy.deepcopy(spec), purpose="analysis", medium="screen")
    second = run_postcompile_qa(copy.deepcopy(spec), purpose="analysis", medium="screen")
    assert first == second
    assert first["stage"] == "standards_qa"
    assert first["profile"]["source"] == "explicit"


def test_service_honors_disable_switch():
    payload = run_postcompile_qa(base_mapspec(), enabled=False)
    assert payload["status"] == "disabled"
    assert payload["violations"] == []
