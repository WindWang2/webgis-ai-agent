"""P9 ProductActionAdvisor 单元测试 —— 确定性 / 只读 / 零 LLM。

覆盖：
- 确定性（同输入必同输出；优先级顺序锁定）；
- capability 映射仅在 registry 能力存在时填写（不 shortcut 到 tool id）；
- 无欠账 → None（零噪声）；
- [Next GIS Action] 单行有界投影；
- no-second-loop 守卫：advisor 输出结构里没有任何可执行通道字段。
"""
from app.services.gis_harness.product_action import (
    ACTION_PRODUCE_CHART,
    ACTION_PRODUCE_LAYER,
    ACTION_RETRY_ANALYSIS,
    advise_next_product_action,
    next_action_projection,
)
from app.services.gis_harness.product_graph import (
    FS_FAILED,
    FS_NEEDS_REPAIR,
    FS_PENDING,
    KIND_ANALYSIS,
    KIND_CHART,
    KIND_MAP_LAYER,
    KIND_NARRATIVE,
    ProductFacetCompletion,
)


def _facet(kind, key, status, *, capability="", facet_id=None):
    return ProductFacetCompletion(
        facet_id=facet_id or f"{kind}:{key}",
        kind=kind,
        key=key,
        label=key,
        status=status,
        capability_ids=[capability] if capability else [],
    )


def test_failed_analysis_beats_all_other_owed_facets():
    facets = [
        _facet(KIND_MAP_LAYER, "poi-heatmap", FS_PENDING),
        _facet(KIND_CHART, "chart-required", FS_PENDING),
        _facet(KIND_ANALYSIS, "hotspot", FS_FAILED, capability="hotspot"),
    ]
    rec = advise_next_product_action({}, facets)
    assert rec is not None
    assert rec.action == ACTION_RETRY_ANALYSIS
    assert rec.capability == "hotspot"
    assert rec.facet_id == "analysis:hotspot"


def test_pending_layer_uses_source_capability():
    facets = [
        _facet(KIND_MAP_LAYER, "district-fill", FS_PENDING, capability="admin_aggregation"),
        _facet(KIND_CHART, "chart-required", FS_PENDING),
    ]
    rec = advise_next_product_action({}, facets)
    assert rec.action == ACTION_PRODUCE_LAYER
    assert rec.capability == "admin_aggregation"


def test_chart_owed_maps_to_channel_not_tool_shortcut():
    facets = [
        _facet(KIND_CHART, "chart-required", FS_PENDING),
        _facet(KIND_NARRATIVE, "goal", FS_PENDING),
    ]
    rec = advise_next_product_action({}, facets)
    assert rec.action == ACTION_PRODUCE_CHART
    # chart 产出是 harness 工具族而非 registry capability —— capability
    # 如实留空，不把 tool id 冒充 capability（P9 §19）。
    assert rec.capability == ""


def test_deterministic_same_input_same_output():
    facets = [
        _facet(KIND_MAP_LAYER, "poi-heatmap", FS_NEEDS_REPAIR),
        _facet(KIND_CHART, "chart-required", FS_PENDING),
    ]
    a = advise_next_product_action({}, facets)
    b = advise_next_product_action({}, facets)
    assert a is not None and b is not None
    assert a.to_dict() == b.to_dict()
    assert a.projection_line() == b.projection_line()


def test_no_owed_facets_returns_none():
    from app.services.gis_harness.product_graph import FS_COMPLETE

    facets = [
        _facet(KIND_MAP_LAYER, "poi-heatmap", FS_COMPLETE),
        _facet(KIND_NARRATIVE, "goal", FS_COMPLETE),
    ]
    assert advise_next_product_action({}, facets) is None
    assert next_action_projection({}, facets) == ""


def test_next_action_projection_single_line_bounded():
    facets = [
        _facet(KIND_ANALYSIS, "density_surface", FS_PENDING, capability="density_surface"),
    ]
    line = next_action_projection({}, facets)
    assert line.startswith("[Next GIS Action]")
    assert "density_surface" in line
    assert "\n" not in line
    assert len(line) <= 80


def test_recommendation_has_no_executable_channel():
    """no-second-loop 守卫：建议结构不含可执行字段（tool/args/loop）。"""
    rec = advise_next_product_action(
        {}, [_facet(KIND_CHART, "chart-required", FS_PENDING)]
    )
    d = rec.to_dict()
    forbidden = {"tool", "tool_name", "args", "arguments", "loop", "execute"}
    assert not (forbidden & set(d.keys()))
