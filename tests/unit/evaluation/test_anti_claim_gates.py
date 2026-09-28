"""anti_claim 门禁判定正反例（G08）。

``run_workflow_contract_case`` 驱动真实 ``compile_workflow``（离线确定、
零 LLM）：义务状态 / 阻断项 / 警告码 / verdict 逐层判定；tampered 案例
证明门禁非空洞（能红，不是恒真断言）。
"""
from __future__ import annotations

import dataclasses

from app.evaluation.anti_claim import (
    build_anti_claim_plan_cases,
    build_v2_recipe_coverage_sweep,
    build_workflow_contract_cases,
    run_workflow_contract_case,
)


def _contract_cases_by_id():
    return {c.case_id: c for c in build_workflow_contract_cases()}


def test_equity_with_denominator_satisfies_obligation():
    case = _contract_cases_by_id()["WC-equity-with-denominator"]
    result = run_workflow_contract_case(case)
    assert result.passed, result.failures


def test_kriging_method_blocker_reaches_blocked_verdict():
    case = _contract_cases_by_id()["WC-kriging-blocked-no-field"]
    result = run_workflow_contract_case(case)
    assert result.passed, result.failures


def test_gate_detects_tampered_expectation_non_vacuous():
    """把「缺分母 → warning」篡改为「缺分母 → satisfied」，门禁必须红。"""
    case = _contract_cases_by_id()["WC-equity-no-denominator"]
    tampered = dataclasses.replace(
        case,
        expect_obligation_status={"equity_denominator_required": "satisfied"},
    )
    result = run_workflow_contract_case(tampered)
    assert result.passed is False
    assert any("obligation" in f for f in result.failures), result.failures


def test_recipe_coverage_sweep_sorted_unique_deterministic():
    sweep1 = build_v2_recipe_coverage_sweep()
    sweep2 = build_v2_recipe_coverage_sweep()
    assert sweep1 == sweep2 and sweep1
    assert sweep1 == sorted(sweep1)
    assert len(set(sweep1)) == len(sweep1)


def test_plan_and_contract_layers_share_warning_vocabulary():
    """反声明词表跨层一致：plan 级警告码与 contract 级期望码有交集。"""
    plan_codes = {
        code
        for c in build_anti_claim_plan_cases()
        for code in c.expected_warning_codes
    }
    contract_codes = {
        code
        for c in build_workflow_contract_cases()
        for code in c.expect_warning_codes
    }
    assert plan_codes & contract_codes, "plan/contract 两层警告码词表脱钩"
