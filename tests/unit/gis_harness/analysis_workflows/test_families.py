"""族模板回归锁（goal-02 M2）。

不变式：
- 五族模板对**真实工具注册表**编译期校验全绿（悬空工具引用 fatal——
  模板腐烂在编译期暴露，不落 run 期）；
- 模板科学前置引用既有注册表 id；
- 契约构建确定性 + 参数覆盖白名单（防拼错参数名 fail-open 跑默认值）；
- optional 步骤剔除后依赖闭包仍闭合；
- 科学口径钉扎：可达性族无 network 角色需求必须 block；
  分布族 denominator 降级不 block（ADR-0099 显式分母语义）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.analysis_workflows import families
from app.services.gis_harness.analysis_workflows.contracts import (
    DataRoleBinding,
    contract_fingerprint,
    validate_workflow_contract,
)


def _registry_has() -> "callable":
    """真实注册面存在性检查器（与生产同源：init_tools 全量注册面）。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    registry = ToolRegistry()
    init_tools(registry)
    return registry.has


def test_all_families_selfcheck_against_real_registry():
    """全族模板 × 真实注册表：悬空工具引用 = 模板腐烂，必须 fatal。"""
    has = _registry_has()
    warnings = families.validate_families(tool_exists=has)
    # 警告允许（如 style 维度提示），但不得抛 ContractValidationError
    assert isinstance(warnings, list)


def test_family_ids_stable_vocabulary():
    assert families.FAMILY_IDS == (
        "admin_facility_distribution",
        "service_accessibility",
        "hazard_exposure",
        "temporal_change",
        "statistical_zones",
    )


@pytest.mark.parametrize("family", families.FAMILY_IDS)
def test_every_family_builds_valid_contract(family):
    contract = families.build_contract(
        family, workflow_id=f"awf.test.{family}")
    validate_workflow_contract(
        contract, family_exists=families.family_exists)
    assert contract.steps
    assert contract.risk_notes
    # 角色（非 optional 步骤引用的）必须在族角色需求里声明
    declared = {r.role for r in families.role_requirements(family)}
    for step in contract.steps:
        for source in step.role_inputs.values():
            if source.startswith("role:"):
                assert source.removeprefix("role:") in declared


def test_accessibility_network_role_is_blocking():
    reqs = {r.role: r for r in families.role_requirements("service_accessibility")}
    assert reqs["network"].required
    assert reqs["network"].missing_policy == "block"


def test_distribution_denominator_is_degrade_not_block():
    reqs = {r.role: r for r in families.role_requirements("admin_facility_distribution")}
    d = reqs["denominator"]
    assert not d.required and d.missing_policy == "degrade"


def test_param_override_whitelist_rejects_unknown_keys():
    with pytest.raises(ValueError, match="越界"):
        families.build_contract(
            "service_accessibility",
            workflow_id="awf.test.override",
            params_overrides={"isochrone": {"travel_time_typo": 30}})


def test_param_override_applies():
    contract = families.build_contract(
        "service_accessibility",
        workflow_id="awf.test.override",
        params_overrides={"isochrone": {"travel_time": 30.0, "mode": "cycling"}})
    step = next(s for s in contract.steps if s.step_id == "isochrone")
    assert step.step_params["travel_time"] == 30.0
    assert step.step_params["mode"] == "cycling"


def test_optional_step_exclusion_keeps_dag_closed():
    contract = families.build_contract(
        "admin_facility_distribution",
        workflow_id="awf.test.noopt",
        include_optional_steps=False)
    ids = {s.step_id for s in contract.steps}
    assert "per_capita_rate" not in ids
    for step in contract.steps:
        assert set(step.depends_on) <= ids


def test_build_contract_deterministic():
    kw = dict(
        bindings={"subject": DataRoleBinding(role="subject", ref="ref:geojson-x")},
        recipe_refs=("edu_facility_distribution",),
    )
    c1 = families.build_contract("admin_facility_distribution",
                                 workflow_id="awf.test.d", **kw)
    c2 = families.build_contract("admin_facility_distribution",
                                 workflow_id="awf.test.d", **kw)
    assert contract_fingerprint(c1) == contract_fingerprint(c2)
    assert c1.recipe_refs == ("edu_facility_distribution",)


def test_temporal_change_has_alignment_disclosure():
    contract = families.build_contract("temporal_change",
                                       workflow_id="awf.test.t")
    assert any("对齐" in n for n in contract.risk_notes)


def test_statistical_zones_declares_grid_identity_precondition():
    contract = families.build_contract("statistical_zones",
                                       workflow_id="awf.test.s")
    preconds = {p for s in contract.steps for p in s.preconditions}
    assert "grid_identity_match" in preconds
    assert "point_support_required" in preconds
