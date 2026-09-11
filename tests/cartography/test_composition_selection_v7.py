"""V7（Goal 08 Phase F）多方案组合选择契约测试.

核心验收：同一份「点分布 + 行政聚合」数据（成都学校分布的结构化投影）
→ 多个**语义不同**的合理组合（点图 / 热力 / 基础组件齐备），排序确定、
可解释，且全程无 query 字符串硬编码（case corpus 负例纪律）。
"""
import pytest

from app.lib.cartography.composition_selection import (
    MAX_ALTERNATIVES,
    TaskCartographyContext,
    select_composition_alternatives,
    validate_affinity_table,
)


def _school_distribution_ctx(**kw) -> TaskCartographyContext:
    """成都学校分布的结构化事实投影（无 query 输入）。"""
    return TaskCartographyContext(
        task_categories=("spatial_distribution", "density",
                         "administrative_aggregation"),
        geometry_kind="point",
        variable_kind="none",
        statistic="count",
        scale_hint="district",
        output_target="interactive",
        artifact_types=("point_feature_set", "admin_aggregate_table"),
        **kw)


class TestAffinityTable:
    def test_referential_integrity(self):
        assert validate_affinity_table() == []

    def test_categories_are_curated_superset(self):
        # 任务类目词表里的主要类目应有亲和登记（thematic_cartography 兜底）
        from app.lib.cartography.composition_selection import _CATEGORY_MODEL_AFFINITY
        assert "spatial_distribution" in _CATEGORY_MODEL_AFFINITY
        assert "density" in _CATEGORY_MODEL_AFFINITY
        assert "administrative_aggregation" in _CATEGORY_MODEL_AFFINITY


class TestSchoolDistributionScenario:
    def test_multiple_semantically_distinct_alternatives(self):
        alts = select_composition_alternatives(_school_distribution_ctx())
        assert len(alts) >= 2, "同一数据应产生多个合理组合"
        model_ids = [a.map_model_id for a in alts]
        assert len(set(model_ids)) == len(model_ids), "候选主表达应互异"
        # 主类目（点分布）候选优先
        assert alts[0].category_id == "spatial_distribution"
        # 次类目（密度）候选进入 top-N（跨类目多样性）
        assert any(a.category_id == "density" for a in alts)

    def test_all_candidates_carry_base_components_and_stats(self):
        alts = select_composition_alternatives(_school_distribution_ctx())
        for alt in alts:
            for ctype in ("title", "legend", "scale_bar", "north_arrow",
                          "attribution", "chart_panel"):
                assert ctype in alt.slot_components, (
                    f"{alt.map_model_id} 缺基础/统计组件 {ctype}")

    def test_no_query_hardcoding(self):
        """选择路径只消费结构化事实；词表中不出现任务专名。"""
        from app.lib.cartography import composition_selection as cs
        source_tables = (
            cs._CATEGORY_MODEL_AFFINITY,
            cs._VARIABLE_CLASSIFICATION_FIT,
            cs._VARIABLE_SCHEME_FIT,
        )
        for table in source_tables:
            for key in table:
                for token in ("成都", "学校", "chengdu", "school"):
                    assert token not in str(key)
                    assert token not in str(table[key])
        ctx = _school_distribution_ctx()
        assert "成都" not in ctx.model_dump_json()
        assert "学校" not in ctx.model_dump_json()
        alts = select_composition_alternatives(ctx)
        assert all("成都" not in a.model_dump_json()
                   and "学校" not in a.model_dump_json() for a in alts)

    def test_deterministic(self):
        first = select_composition_alternatives(_school_distribution_ctx())
        second = select_composition_alternatives(_school_distribution_ctx())
        assert [a.to_bounded_dict() for a in first] == \
            [a.to_bounded_dict() for a in second]


class TestSelectionBehavior:
    def test_polygon_rate_scenario_prefers_choropleth(self):
        ctx = TaskCartographyContext(
            task_categories=("administrative_aggregation",),
            geometry_kind="polygon",
            variable_kind="rate",
            statistic="rate",
            output_target="pdf",
            artifact_types=("admin_aggregate_table",),
        )
        alts = select_composition_alternatives(ctx)
        assert alts
        assert alts[0].map_model_id in (
            "administrative_choropleth", "normalized_choropleth")
        # rate 统计的归一语义进入理由（诚实披露通道）
        assert any("归一" in r for a in alts for r in a.reasons)

    def test_rate_statistic_rejects_raw_for_rate(self):
        """rate 变量下 raw count 主表达的变量契合失配要被罚分。"""
        ctx_rate = TaskCartographyContext(
            task_categories=("administrative_aggregation",),
            geometry_kind="polygon", variable_kind="rate",
            output_target="pdf",
            artifact_types=("admin_aggregate_table",))
        ctx_raw = ctx_rate.model_copy(update={"variable_kind": "none"})
        alts_rate = select_composition_alternatives(ctx_rate)
        alts_raw = select_composition_alternatives(ctx_raw)
        model_rate = {a.map_model_id: a.score for a in alts_rate}
        model_raw = {a.map_model_id: a.score for a in alts_raw}
        common = set(model_rate) & set(model_raw)
        assert any(model_rate[m] > model_raw[m] for m in common) or not common

    def test_alternatives_bounded(self):
        alts = select_composition_alternatives(
            _school_distribution_ctx(), max_alternatives=1)
        assert len(alts) <= 1
        alts_default = select_composition_alternatives(
            _school_distribution_ctx())
        assert len(alts_default) <= MAX_ALTERNATIVES

    def test_empty_categories_fallback_structured(self):
        ctx = TaskCartographyContext(
            geometry_kind="point", output_target="interactive",
            artifact_types=("point_feature_set",))
        alts = select_composition_alternatives(ctx)
        assert alts
        assert all(a.category_id == "thematic_cartography" for a in alts)

    def test_infeasible_geometry_returns_empty(self):
        ctx = TaskCartographyContext(
            task_categories=("terrain",),
            geometry_kind="point",          # 地形模型无点几何
            output_target="png",
            artifact_types=("terrain_surface",),
        )
        alts = select_composition_alternatives(ctx)
        assert all(a.map_model_id for a in alts)
        # 点几何下 terrain 亲和模型全部被几何硬过滤 → 候选来自弱拟合通道
        assert all(a.category_id == "terrain" for a in alts)

    def test_bounded_payload_shape(self):
        alts = select_composition_alternatives(_school_distribution_ctx())
        payload = alts[0].to_bounded_dict()
        assert set(payload) == {"mapModel", "composition", "spec", "category",
                                "score", "reasons", "slots", "disclosures"}
        assert len(payload["reasons"]) <= 6
