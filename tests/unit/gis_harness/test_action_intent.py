"""GISActionIntent 单元测试（ADR-0088 P1）—— 执行债/产品债统一动作层。

覆盖：
- 执行债优先于产品债（plan graph failed/ready 先于 facet 欠账）；
- 动作词表 / execution_mode / action_class 契约；
- artifact_inputs 随 ready 节点携带（P4 最小重计算输入）；
- 观察债（render:stale → reobserve）只在无真欠账时兜底；
- 确定性（同输入必同输出）；
- 投影行格式兼容（capability 直出；非 capability 通道带 mode 前缀）；
- no-second-loop 守卫：输出无任何可执行通道字段。
"""
from app.services.gis_harness.action_intent import (
    ACTION_PRODUCE_CHART,
    ACTION_REOBSERVE,
    ACTION_RETRY_CAPABILITY,
    ACTION_RUN_CAPABILITY,
    CLASS_EXECUTION,
    CLASS_OBSERVATION,
    CLASS_PRODUCTION,
    MODE_CAPABILITY,
    MODE_OBSERVATION,
    GISActionIntent,
    action_intent_projection,
    resolve_next_gis_action,
)
from app.services.gis_harness.plan_graph import build_plan_graph
from app.services.gis_harness.product_graph import (
    FS_NEEDS_REPAIR,
    FS_PENDING,
    KIND_ANALYSIS,
    KIND_CHART,
    KIND_MAP_LAYER,
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


def _chapter(*, req_status="pending", step_status="pending"):
    return {
        "plan_id": "plan-test",
        "query": "成都小学分布",
        "recipe_id": "poi_density",
        "data_requirements": [
            {"capability": "poi_query", "purpose": "POI", "status": req_status,
             "bound_ref": "ref:geojson-a" if req_status in ("available", "done") else ""},
        ],
        "analysis_steps": [
            {"capability": "density_surface", "purpose": "density", "status": step_status,
             "bound_ref": "ref:geojson-b" if step_status in ("available", "done") else ""},
        ],
        "map_layers": [{"role": "primary", "layer_id": "poi-main", "enabled": True}],
        "template_selection": {},
    }


def test_execution_debt_ready_beats_product_debt():
    """DAG ready 节点优先于 facet 产品欠账（执行债先清）。"""
    chapter = _chapter(req_status="available", step_status="pending")
    facets = [
        _facet(KIND_CHART, "chart-required", FS_PENDING),
        _facet(KIND_MAP_LAYER, "poi-main", FS_NEEDS_REPAIR),
    ]
    intent = resolve_next_gis_action(chapter, facets)
    assert intent is not None
    assert intent.action == ACTION_RUN_CAPABILITY
    assert intent.capability == "density_surface"
    assert intent.execution_mode == MODE_CAPABILITY
    assert intent.action_class == CLASS_EXECUTION
    # ready 节点携带上游已绑定 ref（P4：带什么输入执行）
    assert "ref:geojson-a" in intent.artifact_inputs


def test_failed_node_retry_beats_ready():
    """failed 节点（终态执行债）优先于 ready 节点。"""
    chapter = _chapter(req_status="failed", step_status="pending")
    intent = resolve_next_gis_action(chapter, [])
    assert intent is not None
    assert intent.action == ACTION_RETRY_CAPABILITY
    assert intent.capability == "poi_query"


def test_product_debt_chart_owed_without_plan_debt():
    """DAG 无欠账（全终态）时产品债接管：chart 欠账 → produce_chart。"""
    chapter = _chapter(req_status="available", step_status="done")
    facets = [_facet(KIND_CHART, "chart-required", FS_PENDING)]
    intent = resolve_next_gis_action(chapter, facets)
    assert intent is not None
    assert intent.action == ACTION_PRODUCE_CHART
    assert intent.capability == ""  # 产出通道是工具族而非 capability（不虚构）
    assert intent.action_class == CLASS_PRODUCTION


def test_render_stale_reobserve_only_when_no_real_debt():
    """render:stale 是观察债 —— 只在执行/产品债全无时兜底。"""
    chapter = _chapter(req_status="available", step_status="done")
    chapter["map_product"] = {"status": "complete", "render_status": "stale"}
    facets = [_facet(KIND_MAP_LAYER, "poi-main", FS_NEEDS_REPAIR)]
    # 有真欠账（render 缺席）→ 修复债优先
    intent = resolve_next_gis_action(chapter, facets)
    assert intent is not None
    assert intent.action == "repair_runtime_layer"
    # 无真欠账（facet 全 complete）→ reobserve
    intent2 = resolve_next_gis_action(chapter, [_facet(KIND_MAP_LAYER, "poi-main", "complete")])
    assert intent2 is not None
    assert intent2.action == ACTION_REOBSERVE
    assert intent2.execution_mode == MODE_OBSERVATION
    assert intent2.action_class == CLASS_OBSERVATION


def test_no_debt_returns_none():
    chapter = _chapter(req_status="available", step_status="done")
    facets = [_facet(KIND_MAP_LAYER, "poi-main", "complete")]
    assert resolve_next_gis_action(chapter, facets) is None
    assert action_intent_projection(chapter, facets) == ""


def test_deterministic_same_input_same_output():
    chapter = _chapter()
    facets = [
        _facet(KIND_MAP_LAYER, "poi-main", FS_NEEDS_REPAIR),
        _facet(KIND_CHART, "chart-required", FS_PENDING),
    ]
    a = resolve_next_gis_action(chapter, facets)
    b = resolve_next_gis_action(chapter, facets)
    assert a is not None and b is not None
    assert a.to_dict() == b.to_dict()


def test_projection_line_formats():
    # capability 直出（旧格式零漂移）
    intent = GISActionIntent(
        facet_id="analysis:x", kind=KIND_ANALYSIS, action=ACTION_RUN_CAPABILITY,
        reason="r", capability="density_surface",
    )
    assert intent.projection_line() == "[Next GIS Action] density_surface"
    # 非 capability 通道带 mode 前缀
    repair = GISActionIntent(
        facet_id="map_layer:y", kind=KIND_MAP_LAYER, action="repair_runtime_layer",
        reason="r", execution_mode="runtime_repair", action_class=CLASS_EXECUTION,
    )
    assert repair.projection_line() == (
        "[Next GIS Action] runtime_repair:map_layer:repair_runtime_layer"
    )


def test_intent_is_bounded_and_serializable():
    intent = GISActionIntent(
        facet_id="f", kind=KIND_ANALYSIS, action=ACTION_RUN_CAPABILITY, reason="r" * 300,
        capability="c" * 100,
        artifact_inputs=[f"ref:geojson-{i}" for i in range(20)],
    )
    d = intent.to_dict()
    assert len(d["capability"]) <= 64
    assert len(d["reason"]) <= 120
    assert len(d["artifact_inputs"]) <= 8


def test_no_execution_channel_in_output():
    """no-second-loop 守卫：intent 只是建议，不携带任何可执行通道。"""
    chapter = _chapter()
    intent = resolve_next_gis_action(chapter, [])
    assert intent is not None
    d = intent.to_dict()
    for forbidden in ("tool", "command", "mutation", "callback", "endpoint"):
        assert forbidden not in d


def test_graph_input_is_reused_not_rebuilt():
    """传入已评估 graph 时不再重建（单一计算源；同图同输出）。"""
    chapter = _chapter()
    graph = build_plan_graph(chapter)
    a = resolve_next_gis_action(chapter, [], graph=graph)
    b = resolve_next_gis_action(chapter, [], graph=graph)
    assert a is not None and b is not None
    assert a.to_dict() == b.to_dict()


def test_expired_source_escalates_render_debt_to_execution_debt():
    """Scenario B（P4 升级）：render 缺席 + source artifact 确认过期 →
    执行债（retry_capability），绝不建议 runtime repair / remount 死 ref。"""
    from app.services.gis_harness.product_lineage import build_facet_lineage

    chapter = _chapter(req_status="available", step_status="done")
    chapter["map_layers"][0]["source_capability"] = "poi_query"
    mapspec = {
        "layers": [{"id": "poi-main", "source": "s-poi"}],
        "sources": {"s-poi": {"type": "geojson", "ref_id": "ref:geojson-poi"}},
        "layout": {"components": []},
    }
    lineage = build_facet_lineage(
        chapter, mapspec, descriptors={"ref:geojson-poi": None}
    )
    facets = [_facet(KIND_MAP_LAYER, "poi-main", FS_NEEDS_REPAIR)]
    intent = resolve_next_gis_action(chapter, facets, lineage=lineage)
    assert intent is not None
    assert intent.action == ACTION_RETRY_CAPABILITY
    assert intent.capability == "poi_query"
    assert intent.execution_mode == MODE_CAPABILITY
    assert intent.action_class == CLASS_EXECUTION


def test_alive_source_keeps_runtime_repair_classification():
    """血缘确认 ref 存活时 render 缺席仍是 runtime repair 债（不升级）。"""
    from app.services.gis_harness.product_lineage import build_facet_lineage

    chapter = _chapter(req_status="available", step_status="done")
    chapter["map_layers"][0]["source_capability"] = "poi_query"
    mapspec = {
        "layers": [{"id": "poi-main", "source": "s-poi"}],
        "sources": {"s-poi": {"type": "geojson", "ref_id": "ref:geojson-poi"}},
        "layout": {"components": []},
    }
    lineage = build_facet_lineage(
        chapter, mapspec, descriptors={"ref:geojson-poi": {"feature_count": 2}}
    )
    facets = [_facet(KIND_MAP_LAYER, "poi-main", FS_NEEDS_REPAIR)]
    intent = resolve_next_gis_action(chapter, facets, lineage=lineage)
    assert intent is not None
    assert intent.action == "repair_runtime_layer"
    assert intent.execution_mode == "runtime_repair"


def test_production_intent_projection_discloses_reusable_input():
    """P4：产物债携带存活上游 ref → 投影行披露 reuse（Pi 零额外查询）。"""
    from app.services.gis_harness.action_intent import (
        CLASS_PRODUCTION,
        ACTION_PRODUCE_CHART,
        MODE_CAPABILITY,
    )
    intent = GISActionIntent(
        facet_id="chart:required", kind=KIND_CHART, action=ACTION_PRODUCE_CHART,
        reason="chart facet owed", capability="",
        artifact_inputs=["ref:stats-1"],
        execution_mode=MODE_CAPABILITY, action_class=CLASS_PRODUCTION,
    )
    line = intent.projection_line()
    assert line == "[Next GIS Action] chart:produce_chart (reuse: ref:stats-1)"
    # 执行债不披露 reuse（input_refs 是执行输入，语义不同）
    exec_intent = GISActionIntent(
        facet_id="analysis:x", kind=KIND_ANALYSIS, action=ACTION_RUN_CAPABILITY,
        reason="r", capability="density_surface",
        artifact_inputs=["ref:geojson-a"],
    )
    assert "(reuse:" not in exec_intent.projection_line()
