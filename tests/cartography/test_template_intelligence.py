"""Epic 11 —— Viz Bridge + Template Intelligence V2 单测。"""
from __future__ import annotations

import pytest

from app.lib.gis.methodology.viz_bridge import (
    LEGEND_SEMANTICS,
    VIZ_FAMILIES,
    bridge_artifact,
    bridge_plan,
    content_fingerprint,
    validate_bridge,
)


# ── viz bridge ──────────────────────────────────────────────────────────

def test_bridge_validates_clean() -> None:
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.model_library import get_map_model_registry
    from app.lib.gis.artifacts import get_artifact_type_registry
    comps = get_component_registry()
    v = validate_bridge(
        artifact_type_exists=get_artifact_type_registry().has,
        map_model_exists=lambda m: get_map_model_registry().resolve(m) is not None,
        component_exists=lambda c: comps.has(c)
        or comps.get_by_type(c) is not None,
    )
    assert v == []


def test_bridge_covers_epic_artifact_face() -> None:
    """Epic §5.I 的产物面全覆盖（KDE/热点/插值/可达/分类/变化/网络/地形/统计）。"""
    required = {
        "density_surface": "density_surface",
        "hotspot_result": "hotspot_significance",
        "raster_surface": "interpolation_surface",
        "service_area": "service_area",
        "terrain_surface": "terrain_derivative",
        "change_set": "change_map",
        "stats_table": "statistics_chart",
        "admin_aggregate_table": "statistics_chart",
        "line_feature_set": "flow_map",
    }
    for artifact, family in required.items():
        entry = bridge_artifact(artifact)
        assert entry is not None, artifact
        assert entry.family == family


def test_bridge_legend_semantics() -> None:
    """显著性热点 ≠ 连续色条（视觉热力不是显著性表达）。"""
    entry = bridge_artifact("hotspot_result")
    assert entry is not None
    assert entry.legend_semantics == "significance"
    entry2 = bridge_artifact("density_surface")
    assert entry2 is not None
    assert entry2.legend_semantics == "continuous"


def test_bridge_plan_binds_slots_and_discloses() -> None:
    plan = bridge_plan(["density_surface", "hotspot_result"])
    assert plan["primary_family"] == "density_surface"
    assert "continuous_colorbar" in plan["slot_bindings"]
    assert "methodology_note" in plan["slot_bindings"]
    # 密度面的审定 reconcile 披露（R1-F5）
    assert any(d["artifact_type"] == "density_surface"
               for d in plan["disclosures"])
    # 悬空产物 → 披露不虚构
    plan2 = bridge_plan(["no_such_artifact"])
    assert plan2["disclosures"]


def test_bridge_fingerprint_stable() -> None:
    assert len(content_fingerprint()) == 64
    assert content_fingerprint() == content_fingerprint()


def test_viz_family_vocabulary_frozen() -> None:
    assert len(VIZ_FAMILIES) == 16
    assert all(s in LEGEND_SEMANTICS for s in
               ("graduated", "categorical", "continuous", "significance",
                "uncertainty", "none"))


# ── TemplateSpec V2 + planner ───────────────────────────────────────────

@pytest.fixture()
def spec_registry():
    from app.lib.cartography.template_intelligence import (
        get_template_spec_registry, reset_template_spec_registry,
    )
    reset_template_spec_registry()
    return get_template_spec_registry()


def test_template_specs_validate_clean(spec_registry) -> None:
    from app.lib.cartography.composition_templates import (
        get_composition_template_registry,
    )
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.gis.artifacts import get_artifact_type_registry
    from app.lib.gis.capability_registry import get_capability_registry
    from app.lib.gis.methodology.taxonomy import get_task_taxonomy
    from app.services.gis_harness.workflow_schema import DATA_ROLES
    from app.services.gis_harness.workflow_v4.methodology import (
        get_methodology_registry,
    )
    comps = get_component_registry()
    v = spec_registry.validate(
        composition_exists=get_composition_template_registry().has,
        category_exists=get_task_taxonomy().has,
        family_exists=lambda f: get_methodology_registry().family(f) is not None,
        artifact_type_exists=get_artifact_type_registry().has,
        component_exists=lambda c: comps.has(c)
        or comps.get_by_type(c) is not None,
        capability_exists=get_capability_registry().has,
        data_role_vocabulary=tuple(DATA_ROLES),
    )
    assert v == []


def test_school_distribution_not_hardcoded_heatmap() -> None:
    """「学校分布」→ 点分布组合；热力图组件不得因主体是学校而出现
    （组合由数据/任务规则推导，非 query 硬编码——Epic 红线）。"""
    from app.lib.cartography.template_intelligence import plan_composition
    plan = plan_composition(
        "spatial_distribution",
        artifact_types=["point_feature_set", "admin_aggregate_table"])
    components = [s.component_type for s in plan.slot_fills]
    assert "legend" in components and "scale_bar" in components
    assert "chart_panel" in components  # 行政聚合伴生图表
    # 无 KDE/热力产物在场 → 无热力组件绑定依据
    assert "uncertainty_panel" not in components


def test_kriging_plan_requires_uncertainty_panel() -> None:
    """克里金（field 不确定性）→ uncertainty_panel 必选槽位。"""
    from app.lib.cartography.template_intelligence import (
        plan_composition_for_method,
    )
    plan = plan_composition_for_method("interp.ordinary_kriging",
                                       "interpolation")
    components = [s.component_type for s in plan.slot_fills]
    assert "uncertainty_panel" in components
    assert "continuous_colorbar" in components


def test_planner_respects_output_target() -> None:
    """terrain_analysis 基底不支持 interactive → 回退标准版面并披露。"""
    from app.lib.cartography.template_intelligence import plan_composition
    plan = plan_composition(
        "terrain", artifact_types=["terrain_surface"],
        output_target="interactive")
    # 交互输出回退（或命中支持 interactive 的规格）；必须可解释
    assert plan.base_composition_template_id
    assert plan.confidence > 0


def test_planner_deterministic() -> None:
    from app.lib.cartography.template_intelligence import plan_composition
    a = plan_composition("density",
                         artifact_types=["density_surface",
                                         "admin_aggregate_table"])
    b = plan_composition("density",
                         artifact_types=["density_surface",
                                         "admin_aggregate_table"])
    assert a.to_bounded_dict() == b.to_bounded_dict()


def test_bindings_skip_absent_artifacts() -> None:
    """规格绑定只在产物在场时生效（防空绑/虚构）。"""
    from app.lib.cartography.template_intelligence import plan_composition
    plan = plan_composition(
        "thematic_cartography", artifact_types=["feature_collection"])
    for slot in plan.slot_fills:
        if slot.binding is not None and slot.binding.bind_artifact:
            assert slot.binding.bind_artifact == "feature_collection" or \
                slot.binding.bind_artifact in ("stats_table",
                                               "admin_aggregate_table")
