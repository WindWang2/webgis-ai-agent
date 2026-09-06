"""Design System V4 契约测试 —— 统一 manifest / requirement 投影.

锁定：
- manifest 投影是四个 registry 的纯投影（计数一致、确定性双跑一致）；
- map_model_requirement / component_requirement 不编造（未注册 → None）；
- chart 状态机词表与迁移合法性；
- 跨域校验（validate_design_system）无违规。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.chart_kinds import (
    CHART_STATES,
    can_transition,
    resolve_chart_kind,
    validate_chart_kind_registry,
)
from app.lib.cartography.design_system import (
    DESIGN_SYSTEM_SCHEMA_VERSION,
    build_design_system_manifest,
    resolve_component_requirement,
    resolve_map_model_requirement,
    validate_design_system,
)

pytestmark = pytest.mark.cartography


def test_manifest_is_deterministic_projection():
    a = build_design_system_manifest()
    b = build_design_system_manifest()
    assert a == b
    assert a["schemaVersion"] == DESIGN_SYSTEM_SCHEMA_VERSION == 4


def test_manifest_counts_are_consistent():
    m = build_design_system_manifest()
    counts = m["counts"]
    assert counts["mapModels"] == (
        counts["mapModelsNative"] + counts["mapModelsPlanned"])
    assert counts["mapModels"] >= 50
    assert counts["componentVariants"] >= 60
    assert counts["compositionTemplates"] >= 28
    assert counts["themes"] >= 5
    assert counts["chartKinds"] >= 18
    # 投影集合与 registry 全集一致
    from app.lib.cartography.model_library import get_map_model_registry
    assert sorted(m["mapModels"]["native"] + m["mapModels"]["planned"]) == (
        get_map_model_registry().all_ids)
    assert sorted(m["compositionTemplates"]) == m["compositionTemplates"]
    assert set(m["chartStates"]) == set(CHART_STATES)


def test_map_model_requirement_known_and_unknown():
    req = resolve_map_model_requirement("administrative_choropleth")
    assert req is not None
    assert req.model_id == "administrative_choropleth"
    assert req.runtime_status == "native"
    assert "legend" in req.required_components
    assert req.legend_needs["kind"] == "graduated"
    assert req.data_requirements["geometryKinds"] == ["polygon"]
    # 别名可解析
    alias_req = resolve_map_model_requirement("choropleth")
    assert alias_req is not None and alias_req.model_id == "administrative_choropleth"
    # 未注册模型不编造
    assert resolve_map_model_requirement("no_such_model__") is None


def test_map_model_requirement_planned_discloses_fallback():
    from app.lib.cartography.model_library import get_map_model_registry
    planned = get_map_model_registry().planned_ids()
    for mid in planned:
        req = resolve_map_model_requirement(mid)
        assert req is not None
        assert req.runtime_status == "planned"
        assert req.pitfalls_zh, f"planned 模型 {mid} 必须披露原因"


def test_component_requirement_shape_and_variant_guard():
    req = resolve_component_requirement("chart_panel")
    assert req is not None
    assert req.component_type == "chart_panel"
    assert req.variant in req.variants
    assert req.collision_class in ("chrome", "legend", "panel", "canvas", "none")
    assert "hidden" in req.states and "visible" in req.states
    # 非法 variant 拒绝，不静默回落
    assert resolve_component_requirement("chart_panel", "no_such_variant") is None
    assert resolve_component_requirement("no_such_type") is None


def test_chart_state_machine_transitions():
    assert can_transition("hidden", "visible")
    assert can_transition("visible", "collapsed")
    assert can_transition("floating", "docked")
    assert can_transition("docked", "anchored")
    # hidden 只能经 visible 回场
    assert not can_transition("hidden", "floating")
    assert not can_transition("hidden", "collapsed")
    assert can_transition("visible", "visible")


def test_chart_kind_registry_honesty():
    issues = validate_chart_kind_registry()
    assert issues == []
    violin = resolve_chart_kind("violin")
    assert violin is not None
    assert violin.live_engine == "planned"
    assert violin.export_level == "unsupported"
    # 别名解析
    assert resolve_chart_kind("hbar").id == "horizontal_bar"
    assert resolve_chart_kind("time_series").id == "timeseries"
    assert resolve_chart_kind("nope") is None


def test_validate_design_system_clean():
    assert validate_design_system() == []
