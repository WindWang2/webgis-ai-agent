"""G04 W2（CM-01）：判选输入单一投影器 selection_context 契约.

锁定：
- 同一份 (intent 事实, profile) 无论从 planner 侧还是 tool_map_product 侧
  投影，进入判选链的公共字段**逐字段一致**（此前的漂移面：design 侧
  variable_kind 恒缺省 continuous、statistic 两套口径、几何坍缩两处实现）；
- 几何坍缩与 harness recipes._geometry_category 在几何词表上等价（不造
  第二坍缩事实源）；
- 产物表 id 闭合于 ArtifactType 词表；
- 显式产物声明优先；overlay 面证据扩展 admin_boundary_set。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.selection_context import (
    GEOMETRY_KIND_ARTIFACTS,
    TASK_CATEGORY_BY_INTENT,
    artifact_types_of,
    collapse_geometry_types,
    geometry_kind_of,
    project_selection_facts,
    variable_kind_of,
)

pytestmark = pytest.mark.cartography

_GEO_VOCAB = (
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
)


# ── 几何坍缩：与 recipes._geometry_category 等价 ────────────────────────


def test_geometry_collapse_matches_harness_recipes():
    from itertools import combinations

    from app.services.gis_harness.recipes import _geometry_category

    for r in range(1, len(_GEO_VOCAB) + 1):
        for combo in combinations(_GEO_VOCAB, r):
            assert collapse_geometry_types(list(combo)) == _geometry_category(
                list(combo),
            ), combo


def test_geometry_kind_prefers_profile_then_expectation():
    assert geometry_kind_of(geometry_types=["MultiPoint"]) == "point"
    assert geometry_kind_of(
        geometry_types=[], geometry_expectation="line",
    ) == "line"
    assert geometry_kind_of() == "polygon"


# ── 产物表 ──────────────────────────────────────────────────────────────


def test_artifact_table_ids_close_over_artifact_types():
    from app.lib.gis.artifacts import get_artifact_type_registry

    known = set(get_artifact_type_registry().all_ids)
    assert known, "artifact registry must not be empty"
    for ids in GEOMETRY_KIND_ARTIFACTS.values():
        for aid in ids:
            assert aid in known, aid


def test_artifact_types_explicit_overrides_and_boundary_evidence():
    base = artifact_types_of("point")
    assert base == ("point_feature_set", "admin_aggregate_table")
    explicit = artifact_types_of("point", explicit=("raster_surface",))
    assert explicit == ("raster_surface",)
    with_boundary = artifact_types_of("point", admin_boundary_evidence=True)
    assert "admin_boundary_set" in with_boundary


def test_artifact_types_rejects_unknown_geometry_kind():
    with pytest.raises(ValueError):
        artifact_types_of("hexagon")


# ── 单一投影：双侧一致 ─────────────────────────────────────────────────


def _facts(**overrides):
    kwargs = dict(
        intent_task="aggregation_overview",
        profile={
            "geometryTypes": ["Polygon", "MultiPolygon"],
            "dataKind": "sequential",
            "featureCount": 120,
            "fields": {"population": {"type": "number"}},
        },
        scale_hint="district",
    )
    kwargs.update(overrides)
    return project_selection_facts(**kwargs)


def test_shared_fields_identical_for_both_caller_faces():
    from app.lib.cartography.composition_selection import TaskCartographyContext
    from app.lib.cartography.design_candidates import DesignTaskContext

    facts = _facts()
    task_ctx = TaskCartographyContext(**facts.task_ctx_kwargs())
    design_ctx = DesignTaskContext(**facts.design_ctx_kwargs())
    # 公共判选字段逐字段一致（CM-01 单一投影）
    assert task_ctx.task_categories == design_ctx.task_categories
    assert task_ctx.geometry_kind == design_ctx.geometry_kind
    assert task_ctx.variable_kind == design_ctx.variable_kind
    assert task_ctx.statistic == design_ctx.statistic
    assert task_ctx.artifact_types == design_ctx.artifact_types
    assert task_ctx.scale_hint == design_ctx.scale_hint
    assert task_ctx.output_target == design_ctx.output_target


def test_variable_kind_flows_from_profile_data_kind():
    assert variable_kind_of({"dataKind": "categorical"}) == "categorical"
    assert variable_kind_of({"dataKind": "diverging"}) == "continuous"
    # 字段类型粗判（无显式 dataKind）：文本/类别 → categorical
    assert variable_kind_of({
        "fields": {"name": {"type": "text"}},
    }) == "categorical"
    assert variable_kind_of({}) == "continuous"


def test_statistic_unified_density_rule():
    density_measure = _facts(intent_task="aggregation_overview",
                             measure="density")
    assert density_measure.statistic == "density"
    density_task = _facts(intent_task="analytical_density")
    assert density_task.statistic == "density"
    plain = _facts()
    assert plain.statistic == "count"


def test_point_count_carries_basis():
    facts = _facts(
        intent_task="distribution_overview",
        profile={"geometryTypes": ["Point"], "featureCount": 320},
    )
    assert facts.geometry_kind == "point"
    assert facts.point_count == 320
    assert facts.point_count_basis == "profile"
    unknown = _facts(
        intent_task="distribution_overview",
        profile={"geometryTypes": ["Point"]},
    )
    assert unknown.point_count == 0
    assert unknown.point_count_basis == "unknown"


def test_task_category_table_covers_intent_vocab_and_falls_back():
    assert TASK_CATEGORY_BY_INTENT["change_detection"] == (
        "change_detection", "spatiotemporal_pattern",
    )
    facts = _facts(intent_task="never_heard_of_it")
    assert facts.task_categories == ("thematic_cartography",)


def test_secondary_geometry_kinds_exclude_primary():
    facts = _facts(overlay_geometry_kinds=("polygon", "line", "polygon"))
    assert facts.geometry_kind == "polygon"
    assert facts.secondary_geometry_kinds == ("line",)
