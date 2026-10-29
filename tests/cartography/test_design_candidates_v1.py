"""MapDesignCandidate 引擎单测（/goal cartography-design M1）。

规则 R1–R6 的正/负例 + 契约纪律（确定性、≤4、无 query 字符串、有界载荷）。
fixtures 语义对齐 G05 成都学校族（合成数据，fixtures_data.py 同源惯例）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.design_candidates import (
    DESIGN_CANDIDATES_VERSION,
    MAX_DESIGN_CANDIDATES,
    CandidateSuitability,
    DenominatorCondition,
    DenominatorEvidence,
    DesignTaskContext,
    generate_design_candidates,
)
from app.lib.cartography.visualization_plan import DistributionStats

pytestmark = pytest.mark.cartography


def _chengdu_points_ctx(**overrides) -> DesignTaskContext:
    """成都小学式任务事实（点 + 行政边界 + 聚合表；合成数据语义）。"""
    base = dict(
        task_categories=("spatial_distribution", "density"),
        geometry_kind="point",
        variable_kind="continuous",
        statistic="count",
        artifact_types=("point_feature_set", "admin_boundary_set",
                        "admin_aggregate_table"),
        point_count=40,
        point_count_basis="dataset",
        denominator=DenominatorEvidence(),
        scale_hint="city",
    )
    base.update(overrides)
    return DesignTaskContext(**base)


def test_point_distribution_yields_multi_family_candidates() -> None:
    """成都点分布：点族 + 密度/聚族多候选，全部非 blocked（40 点足额）。"""
    cs = generate_design_candidates(_chengdu_points_ctx())
    assert 2 <= len(cs.candidates) <= MAX_DESIGN_CANDIDATES
    families = {c.expression_family for c in cs.candidates}
    assert "point" in families
    assert families & {"density_surface", "aggregation"}
    assert all(c.suitability != CandidateSuitability.BLOCKED
               for c in cs.candidates)


def test_heatmap_carries_kde_disclosure() -> None:
    cs = generate_design_candidates(_chengdu_points_ctx())
    heat = [c for c in cs.candidates if c.map_model_id == "visual_heatmap"]
    if heat:  # 组合层给出热力候选时必须带披露（不依赖其在场）
        assert "KDE_NOT_POPULATION_DENSITY" in heat[0].condition_codes
        assert any("核密度" in r for r in heat[0].scientific_risks_zh)


def test_sparse_heatmap_typed_blocked() -> None:
    """3 点 < HEATMAP_MIN_POINTS → 热力 BLOCKED + typed 码 + 降级路径。"""
    ctx = _chengdu_points_ctx(
        point_count=3,
        user_requested_models=("visual_heatmap",),
    )
    cs = generate_design_candidates(ctx)
    heat = [c for c in cs.candidates if c.map_model_id == "visual_heatmap"]
    assert heat, "用户显式要求的 blocked 候选必须可见（不静默丢弃）"
    assert heat[0].suitability == CandidateSuitability.BLOCKED
    assert "INSUFFICIENT_POINTS_FOR_DENSITY" in heat[0].condition_codes
    assert heat[0].degradation_path  # 降级路径非空（诚实指路）
    assert cs.user_request_handling["typed_blocked"] == ["visual_heatmap"]
    assert cs.user_request_handling["honored"] == []


def test_point_count_unknown_no_rule_fires() -> None:
    """点数证据缺席（basis=unknown）→ 不裁决热力资格（诚实缺席）。"""
    ctx = _chengdu_points_ctx(point_count=0, point_count_basis="unknown")
    cs = generate_design_candidates(ctx)
    for c in cs.candidates:
        assert "INSUFFICIENT_POINTS_FOR_DENSITY" not in c.condition_codes


def _admin_ctx(**overrides) -> DesignTaskContext:
    base = dict(
        task_categories=("administrative_aggregation",),
        geometry_kind="polygon",
        variable_kind="continuous",
        statistic="density",
        artifact_types=("admin_aggregate_table", "admin_boundary_set"),
        scale_hint="district",
    )
    base.update(overrides)
    return DesignTaskContext(**base)


def test_denominator_missing_blocks_normalized_and_discloses_raw() -> None:
    """缺分母：normalized BLOCKED；原始计数 choropleth CONDITIONAL 披露。"""
    cs = generate_design_candidates(_admin_ctx())
    normalized = [c for c in cs.candidates
                  if c.map_model_id == "normalized_choropleth"]
    assert normalized, "率语义亲和候选应在池内（亲和表锁定）"
    assert normalized[0].suitability == CandidateSuitability.BLOCKED
    assert normalized[0].denominator_condition == DenominatorCondition.REQUIRED_MISSING
    assert "DENOMINATOR_MISSING" in normalized[0].condition_codes
    raw = [c for c in cs.candidates
           if c.map_model_id == "administrative_choropleth"]
    assert raw, "无分母时原始计数表达必须仍在场（可用方案）"
    assert raw[0].denominator_condition == DenominatorCondition.NOT_APPLICABLE
    assert "DENOMINATOR_MISSING" in raw[0].condition_codes  # 披露分支
    assert raw[0].suitability == CandidateSuitability.CONDITIONAL
    assert any("不得标注" in r for r in raw[0].condition_reasons_zh)


def test_denominator_available_enables_normalized() -> None:
    ctx = _admin_ctx(
        denominator=DenominatorEvidence(
            field="population", available=True, unit="人"),
        variable_kind="rate",
    )
    cs = generate_design_candidates(ctx)
    normalized = [c for c in cs.candidates
                  if c.map_model_id == "normalized_choropleth"]
    assert normalized
    assert normalized[0].denominator_condition == DenominatorCondition.REQUIRED_SATISFIED
    assert normalized[0].suitability == CandidateSuitability.SUITABLE


def test_temporal_periods_introduce_change_family() -> None:
    """双期面域数据（双时期土地利用对比式）→ change 族候选在场。

    点数据双期无 native change 模型（small_multiple_map 为 planned）——
    诚实缺席，不伪造 change 候选；负向断言同锁。
    """
    ctx = _admin_ctx(temporal_periods=2)
    cs = generate_design_candidates(ctx)
    assert any(c.expression_family == "change" for c in cs.candidates)
    point_ctx = _chengdu_points_ctx(temporal_periods=2)
    cs2 = generate_design_candidates(point_ctx)
    assert all(c.expression_family != "change" for c in cs2.candidates)


def test_overplot_downgrades_point_family() -> None:
    ctx = _chengdu_points_ctx(point_count=300)
    cs = generate_design_candidates(ctx)
    point = [c for c in cs.candidates if c.expression_family == "point"]
    assert point
    assert point[0].suitability == CandidateSuitability.CONDITIONAL
    assert "POINT_OVERPLOT_RISK" in point[0].condition_codes
    agg = [c for c in cs.candidates
           if c.expression_family in ("aggregation", "density_surface")]
    assert agg and any(a.against_zh for a in agg)


def test_aggregate_centroid_placement_disclosed() -> None:
    """比例符号承载聚合表（district 口径）→ 质心定位谬误披露。

    point 族逐要素定位（每点即真实位置）不受此谬误影响——负向断言同锁。
    """
    ctx = _chengdu_points_ctx(
        scale_hint="district", user_requested_models=("proportional_symbol",))
    cs = generate_design_candidates(ctx)
    symbol = [c for c in cs.candidates
              if c.expression_family == "symbol_scalar"]
    assert symbol, "用户显式要求的候选必须可见（保留席位）"
    assert "AGGREGATE_CENTROP_PLACEMENT" in symbol[0].condition_codes \
        or "AGGREGATE_CENTROID_PLACEMENT" in symbol[0].condition_codes
    point = [c for c in cs.candidates if c.expression_family == "point"]
    for p in point:
        assert "AGGREGATE_CENTROID_PLACEMENT" not in p.condition_codes


def test_classification_wired_from_distribution() -> None:
    """重尾分布 + 空 recommended 的 graduated 聚合模型 → C3 裁决 head_tail。

    （administrative_aggregation 推荐集为空 → choose_classification 规则 2
    允许 head_tail 兜住重尾计数形态——R6 接 C3 唯一裁决，不另行发明。）
    """
    stats = DistributionStats(n=20, min=1.0, max=980.0, mean=120.0, median=8.0)
    ctx = _admin_ctx(
        statistic="count",
        denominator=DenominatorEvidence(field="population", available=True),
        distribution=stats,
    )
    cs = generate_design_candidates(ctx)
    agg = [c for c in cs.candidates
           if c.map_model_id == "administrative_aggregation"]
    assert agg, "空推荐集的 graduated 聚合模型应在池内（亲和表锁定）"
    assert agg[0].classification is not None
    assert agg[0].classification["method"] == "head_tail"
    assert agg[0].classification["source"] == "distribution"


def test_classification_deferred_without_stats() -> None:
    ctx = _chengdu_points_ctx()
    cs = generate_design_candidates(ctx)
    graduated = [c for c in cs.candidates
                 if c.map_model_id == "administrative_choropleth"]
    if graduated:
        assert graduated[0].classification is None
        assert "CLASSIFICATION_DEFERRED" in graduated[0].condition_codes


def test_determinism_and_bounds() -> None:
    ctx = _chengdu_points_ctx()
    a = generate_design_candidates(ctx).model_dump()
    b = generate_design_candidates(ctx).model_dump()
    assert a == b, "同输入两次必须完全一致（golden 可锁）"
    payload = generate_design_candidates(ctx).to_bounded_dict()
    assert payload["version"] == DESIGN_CANDIDATES_VERSION
    assert payload["count"] == len(payload["candidates"]) <= MAX_DESIGN_CANDIDATES


def test_families_unique_except_user_request() -> None:
    ctx = _chengdu_points_ctx(
        user_requested_models=("visual_heatmap", "kernel_density_surface"))
    cs = generate_design_candidates(ctx)
    auto = [c for c in cs.candidates if not c.user_requested]
    fams = [c.expression_family for c in auto]
    assert len(fams) == len(set(fams)), "自动候选表达族去重"


def test_context_has_no_query_field() -> None:
    fields = set(DesignTaskContext.model_fields)
    assert not any("query" in f or "text" in f or "message" in f
                   for f in fields), "结构化事实投影，禁止 query 字符串面"


def test_primary_model_alignment() -> None:
    ctx = _chengdu_points_ctx()
    cs = generate_design_candidates(ctx, primary_model_id="visual_heatmap")
    sel = [c for c in cs.candidates
           if c.candidate_id == cs.selected_candidate_id]
    assert sel and sel[0].map_model_id == "visual_heatmap"
    assert cs.selection_basis.startswith("planner_primary")


def test_requested_infeasible_model_typed_blocked_with_reason_type() -> None:
    """用户显式要求与数据形态不匹配 → typed block（分型：几何 vs 产物）。"""
    # aggregate_grid 需 grid_aggregate 产物（先跑格网聚合）——对原始点数据
    ctx = _chengdu_points_ctx(user_requested_models=("aggregate_grid",))
    cs = generate_design_candidates(ctx)
    grid = [c for c in cs.candidates if c.map_model_id == "aggregate_grid"]
    assert grid, "不可行的用户显式要求也必须可见（typed block）"
    assert grid[0].suitability == CandidateSuitability.BLOCKED
    assert "DATA_REQUIREMENT_UNMET" in grid[0].condition_codes
    assert any("先执行对应分析" in r for r in grid[0].condition_reasons_zh)
    assert cs.user_request_handling["typed_blocked"] == ["aggregate_grid"]

    # administrative_choropleth 需 polygon 几何——对点数据是几何不匹配
    ctx2 = _chengdu_points_ctx(
        user_requested_models=("administrative_choropleth",))
    cs2 = generate_design_candidates(ctx2)
    chor = [c for c in cs2.candidates
            if c.map_model_id == "administrative_choropleth"]
    assert chor
    assert chor[0].suitability == CandidateSuitability.BLOCKED
    assert "GEOMETRY_INCOMPATIBLE" in chor[0].condition_codes
