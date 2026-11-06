"""AnalysisWorkflowContract v1 契约层回归锁（goal-02 M2）。

不变式：
- fail-closed 校验：未知角色/维度/单位/环依赖/悬空工具/未注册前置显式失败；
- 词表单一事实源：角色与重算维度引用 workflow_schema（越界即拒）；
- 指纹确定性：同契约同指纹；参数/单位/科学上下文变化必变指纹；
- 缺失码与既有 DATA_ROLE_MISSING_<ROLE> 家族同构（AWF_ 前缀 + 同尾段）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows.contracts import (
    AWF_DATA_ROLE_MISSING,
    AWF_INVALID_CONTRACT,
    AWF_TOOL_UNREGISTERED,
    AWF_UNKNOWN_FAMILY,
    AWF_UNIT_CONTRACT_VIOLATION,
    ContractValidationError,
    DataRoleBinding,
    AnalysisStepSpec,
    AnalysisWorkflowContract,
    ScienceContext,
    contract_fingerprint,
    role_missing_code,
    validate_workflow_contract,
)


def _contract(**kw) -> AnalysisWorkflowContract:
    base = dict(
        workflow_id="awf.test.0001",
        family="admin_facility_distribution",
        steps=(
            AnalysisStepSpec(
                step_id="zones_count",
                tool_name="spatial_aggregate",
                role_inputs={"points": "role:subject",
                             "polygons": "role:boundary"},
            ),
        ),
        data_contracts={
            "subject": DataRoleBinding(role="subject", ref="ref:geojson-a"),
            "boundary": DataRoleBinding(role="boundary", ref="ref:geojson-b"),
        },
    )
    base.update(kw)
    return AnalysisWorkflowContract(**base)


def test_minimal_contract_validates_with_tool_check():
    warnings = validate_workflow_contract(
        _contract(), tool_exists=lambda n: n == "spatial_aggregate",
        family_exists=lambda f: True)
    assert warnings == []


def test_unknown_family_rejected():
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(
            _contract(family="nonexistent_family"),
            family_exists=lambda f: False)
    assert AWF_UNKNOWN_FAMILY in str(ei.value)


def test_unknown_data_role_rejected():
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(_contract(
            data_contracts={"poi_layer": DataRoleBinding(role="poi_layer")}))
    assert AWF_INVALID_CONTRACT in str(ei.value)


def test_unknown_recompute_dimension_rejected():
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(_contract(recompute_dimensions=("style", "vibe")))
    assert AWF_INVALID_CONTRACT in str(ei.value)


def test_style_dimension_warns_not_fails():
    warnings = validate_workflow_contract(
        _contract(recompute_dimensions=("data", "style")))
    assert any("style" in w for w in warnings)


def test_unknown_unit_rejected():
    bad = AnalysisStepSpec(
        step_id="s1", tool_name="buffer_analysis",
        role_inputs={"geojson": "role:subject"},
        unit_contract={"distance": "furlongs"})
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(
            _contract(steps=(bad,)), tool_exists=lambda n: True)
    assert AWF_UNIT_CONTRACT_VIOLATION in str(ei.value)


def test_cycle_dependency_rejected():
    a = AnalysisStepSpec(step_id="a", tool_name="t1", depends_on=("b",))
    b = AnalysisStepSpec(step_id="b", tool_name="t2", depends_on=("a",))
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(_contract(steps=(a, b)))
    assert "依赖环" in str(ei.value)


def test_dangling_step_reference_rejected():
    bad = AnalysisStepSpec(
        step_id="s1", tool_name="t1",
        role_inputs={"layer_a": "step:ghost"})
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(_contract(steps=(bad,)))
    assert "ghost" in str(ei.value)


def test_dangling_tool_rejected_when_checker_injected():
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(
            _contract(), tool_exists=lambda n: False)
    assert AWF_TOOL_UNREGISTERED in str(ei.value)
    assert "spatial_aggregate" in str(ei.value)


def test_unknown_precondition_rejected():
    bad = AnalysisStepSpec(
        step_id="s1", tool_name="t1",
        role_inputs={}, preconditions=("no_such_precondition",))
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(
            _contract(steps=(bad,)),
            precondition_exists=lambda p: False)
    assert "no_such_precondition" in str(ei.value)


def test_role_source_must_be_declared():
    bad = AnalysisStepSpec(
        step_id="s1", tool_name="t1",
        role_inputs={"geojson": "role:network"})  # 未在 data_contracts 声明
    with pytest.raises(ContractValidationError) as ei:
        validate_workflow_contract(_contract(steps=(bad,)))
    assert "role:network" in str(ei.value) or "network" in str(ei.value)


def test_empty_steps_rejected():
    with pytest.raises(ContractValidationError):
        validate_workflow_contract(_contract(steps=()))


# ── 指纹确定性 ────────────────────────────────────────────────────────────

def test_fingerprint_deterministic_and_param_sensitive():
    c1, c2 = _contract(), _contract()
    assert contract_fingerprint(c1) == contract_fingerprint(c2)
    changed = _contract(steps=(
        AnalysisStepSpec(
            step_id="zones_count", tool_name="spatial_aggregate",
            role_inputs={"points": "role:subject",
                         "polygons": "role:boundary"},
            step_params={"count_field": "renamed"}),
    ))
    assert contract_fingerprint(changed) != contract_fingerprint(c1)


def test_fingerprint_sensitive_to_units_and_science_context():
    base = _contract()
    sci = _contract(science_context=ScienceContext(
        units={"travel_time": "min"}, timezone="Asia/Shanghai"))
    assert contract_fingerprint(base) != contract_fingerprint(sci)


def test_fingerprint_ignores_binding_refs():
    """同内容换 ref 不变指纹——ref 是地址不是内容（重算语义的关键）。"""
    c1 = _contract(data_contracts={
        "subject": DataRoleBinding(role="subject", ref="ref:geojson-a"),
        "boundary": DataRoleBinding(role="boundary", ref="ref:geojson-b"),
    })
    c2 = _contract(data_contracts={
        "subject": DataRoleBinding(role="subject", ref="ref:geojson-a2"),
        "boundary": DataRoleBinding(role="boundary", ref="ref:geojson-b2"),
    })
    assert contract_fingerprint(c1) == contract_fingerprint(c2)


def test_role_missing_code_aligns_with_legacy_family():
    assert role_missing_code("boundary") == f"{AWF_DATA_ROLE_MISSING}:BOUNDARY"
    assert role_missing_code("target_time") == \
        f"{AWF_DATA_ROLE_MISSING}:TARGET_TIME"
