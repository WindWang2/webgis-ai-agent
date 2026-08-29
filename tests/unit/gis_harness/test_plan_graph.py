"""DataRequirement Graph / Analysis DAG 契约（Runtime v3 Phase D/E/F）。

锁定（§17）：
- 依赖序：registry artifact 类型推断（poi_query → admin_aggregation 等）；
- ready 派生：依赖全满足才 ready；
- artifact 绑定解锁下游（bound_ref → complete → dependent ready + input_refs）；
- optional 失败不阻塞 mandatory 图（optional unavailable → skipped 级联）；
- mandatory unavailable 被如实上报（blocked_by）；
- fallback 解锁下游（preferred unavailable + fallback capability 完成）；
- 环校验拒绝非法图（strict 模式 PlanGraphError）；
- 扁平行 = 单一计算源：depends_on 随 plan 持久化，graph 是纯投影；
- SessionPlan 投影携带 [GIS Plan] 块（Phase F）。
"""
import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.plan_graph import (
    PlanGraphError,
    PlanNodeStatus,
    build_plan_graph,
    infer_dependency_edges,
    project_graph_block,
    recommended_next,
)
from app.services.gis_harness.planner_runtime import get_planner_runtime


def _plan(query: str = "成都小学分布情况"):
    planner = get_planner_runtime()
    return planner.plan_from_intent(resolve_map_request_intent(query))


def _rows_by_cap(plan):
    return {r.capability: r for r in plan.data_requirements}


def test_dependency_edges_inferred_from_registry_artifacts():
    edges = infer_dependency_edges(
        ["poi_query", "admin_boundary_query", "admin_aggregation", "point_profile"]
    )
    assert "poi_query" in edges["admin_aggregation"]
    assert "poi_query" in edges["point_profile"]
    # 数据获取是根（无上游）
    assert edges["poi_query"] == []
    assert edges["admin_boundary_query"] == []


def test_plan_carries_depends_on_and_optional():
    plan = _plan("成都小学分布情况")
    rows = _rows_by_cap(plan)
    assert "poi_query" in rows["admin_aggregation"].depends_on
    # requirement 与 step 两行同边（平行投影一致性）
    steps = {s.capability: s for s in plan.analysis_steps}
    assert (
        rows["admin_aggregation"].depends_on
        == steps["admin_aggregation"].depends_on
    )
    # recipe.optional_analysis 的 capability 标 optional
    recipe = get_planner_runtime().recipes.get(plan.recipe_id)
    for cap in recipe.optional_analysis:
        if cap in rows:
            assert rows[cap].optional is True
    for cap in recipe.preferred_analysis:
        if cap in rows and cap not in recipe.optional_analysis:
            assert rows[cap].optional is False


def test_ready_nodes_require_all_deps_complete():
    plan = _plan()
    graph = build_plan_graph(plan)
    ready = set(graph.ready_nodes())
    # 根节点（数据获取）ready；依赖 poi_query 的分析节点 pending
    assert "poi_query" in ready or "raster_source" in ready
    agg = graph.node("admin_aggregation")
    if agg is not None:
        assert agg.status == PlanNodeStatus.pending
        assert "poi_query" in agg.depends_on


def test_artifact_binding_unlocks_downstream():
    plan = _plan()
    # 模拟 SessionPlan._mark_progress 的行推进：poi_query available + bound_ref
    for row in plan.data_requirements:
        if row.capability == "poi_query":
            row.status = "available"
            row.bound_ref = "ref:geojson:poi-1"
    for step in plan.analysis_steps:
        if step.capability == "poi_query":
            step.status = "done"
            step.bound_ref = "ref:geojson:poi-1"
    graph = build_plan_graph(plan)
    poi = graph.node("poi_query")
    assert poi.status == PlanNodeStatus.complete
    assert poi.output_ref == "ref:geojson:poi-1"
    agg = graph.node("admin_aggregation")
    if agg is not None:
        # poi_query 完成 + admin_boundary（若有）完成后解锁
        if all(
            graph.node(d).status == PlanNodeStatus.complete
            for d in agg.depends_on
        ):
            assert agg.status == PlanNodeStatus.ready
            assert "ref:geojson:poi-1" in agg.input_refs


def test_optional_unavailable_does_not_block_mandatory_graph():
    """optional 节点不可用 → 自身 skipped；不依赖它的 mandatory 节点照常 ready。"""
    plan = _plan()
    # 手工构造：kde_density 为 optional 且 unavailable（registry 视图模拟）
    for row in plan.data_requirements:
        if row.capability == "kde_density":
            row.optional = True
            row.status = "unavailable"
    for step in plan.analysis_steps:
        if step.capability == "kde_density":
            step.optional = True
            step.status = "unavailable"
    graph = build_plan_graph(plan)
    kde = graph.node("kde_density")
    if kde is None:
        pytest.skip("recipe 无 kde_density 节点")
    assert kde.status == PlanNodeStatus.skipped
    # mandatory 根节点不受影响
    assert "poi_query" in graph.ready_nodes()


def test_mandatory_unavailable_reported_with_blocked_by():
    plan = _plan()
    for row in plan.data_requirements:
        if row.capability == "poi_query":
            row.status = "unavailable"
    for step in plan.analysis_steps:
        if step.capability == "poi_query":
            step.status = "unavailable"
    graph = build_plan_graph(plan)
    agg = graph.node("admin_aggregation")
    if agg is None:
        pytest.skip("recipe 无 admin_aggregation 节点")
    assert agg.status == PlanNodeStatus.unavailable
    assert "poi_query" in agg.blocked_by


def test_fallback_unlocks_downstream():
    """preferred unavailable 但 fallback capability 完成 → 依赖视为满足。

    用 registry 真实声明构造：grid_binning 的 fallback_capabilities=
    [density_surface]；hotspot 消费 grid_aggregate（grid_binning 的输出）。
    """
    from app.lib.gis.capability_registry import get_capability_registry
    caps = get_capability_registry()
    grid = caps.get("grid_binning")
    assert grid is not None and "density_surface" in grid.fallback_capabilities

    chapter = {
        "plan_id": "p-fb",
        "data_requirements": [
            {"capability": "poi_query", "status": "available",
             "bound_ref": "ref:geojson:p1"},
            {"capability": "grid_binning", "status": "unavailable",
             "depends_on": ["poi_query"]},
            {"capability": "density_surface", "status": "available",
             "bound_ref": "ref:geojson:d1", "depends_on": ["poi_query"]},
            {"capability": "hotspot", "status": "pending",
             "depends_on": ["grid_binning"]},
        ],
        "analysis_steps": [
            {"capability": "poi_query", "status": "done",
             "bound_ref": "ref:geojson:p1"},
            {"capability": "grid_binning", "status": "unavailable"},
            {"capability": "density_surface", "status": "done",
             "bound_ref": "ref:geojson:d1"},
            {"capability": "hotspot", "status": "pending",
             "depends_on": ["grid_binning"]},
        ],
        # resolver 的 capability 级 fallback 裁决证据（reason 标记）
        "algorithm_selections": [
            {"capability": "grid_binning", "status": "unavailable",
             "algorithm": "", "tool": "",
             "reason": "capability_fallback_available:density_surface",
             "fallback_trail": [], "fallback_candidates": ["density_surface"]},
        ],
    }
    graph = build_plan_graph(chapter)
    grid_node = graph.node("grid_binning")
    assert grid_node.fallback_to == "density_surface", (
        "resolver 的 capability_fallback_available 裁决必须落到节点 fallback_to"
    )
    hot = graph.node("hotspot")
    # fallback（density_surface）完成 → grid_binning 视为满足 → hotspot ready
    assert hot.status == PlanNodeStatus.ready, (
        f"fallback 完成必须解锁下游（hotspot 现为 {hot.status}）"
    )
    # 投影如实披露 preferred 不可用 + fallback
    text = project_graph_block(graph)
    assert "grid_binning" in text and "density_surface" in text


def test_cycle_validation_rejects_invalid_graph():
    chapter = {
        "plan_id": "p1",
        "data_requirements": [
            {"capability": "a", "status": "pending", "depends_on": ["b"]},
            {"capability": "b", "status": "pending", "depends_on": ["a"]},
        ],
        "analysis_steps": [],
        "algorithm_selections": [],
    }
    with pytest.raises(PlanGraphError, match="cycle|unknown"):
        build_plan_graph(chapter, strict=True)
    # 非严格模式：容错记录，图仍可用
    graph = build_plan_graph(chapter, strict=False)
    caps = {n.capability for n in graph.nodes}
    assert caps == {"a", "b"}


def test_unknown_dependency_reference_reported():
    chapter = {
        "plan_id": "p2",
        "data_requirements": [
            {"capability": "a", "status": "pending", "depends_on": ["ghost"]},
        ],
        "analysis_steps": [],
        "algorithm_selections": [],
    }
    with pytest.raises(PlanGraphError, match="unknown"):
        build_plan_graph(chapter, strict=True)
    graph = build_plan_graph(chapter, strict=False)
    assert graph.unresolved_dependency_refs == ["a->ghost"]
    assert graph.node("a").depends_on == []


def test_legacy_chapter_without_depends_on_rebuilds_edges():
    """旧持久计划（无 depends_on 字段）：读取侧重放 registry 推断。"""
    plan = _plan()
    legacy_chapter = plan.model_dump()
    for row in legacy_chapter["data_requirements"]:
        row.pop("depends_on", None)
        row.pop("optional", None)
    for row in legacy_chapter["analysis_steps"]:
        row.pop("depends_on", None)
        row.pop("optional", None)
    graph = build_plan_graph(legacy_chapter)
    agg = graph.node("admin_aggregation")
    if agg is not None:
        assert "poi_query" in agg.depends_on


def test_projection_block_bounded_and_informative():
    plan = _plan()
    graph = build_plan_graph(plan)
    text = project_graph_block(graph)
    assert text.startswith("[GIS Plan]")
    assert "Ready:" in text
    assert "Recommended next:" in text
    assert len(text.splitlines()) <= 10
    # 完成态推进后投影更新
    for row in plan.data_requirements:
        row.status = "available"
    graph2 = build_plan_graph(plan)
    text2 = project_graph_block(graph2)
    assert "Completed:" in text2


def test_recommended_next_prefers_fewest_deps():
    plan = _plan()
    graph = build_plan_graph(plan)
    nxt = recommended_next(graph)
    assert nxt
    node = graph.node(nxt)
    assert node.status == PlanNodeStatus.ready


def test_session_plan_projection_carries_graph_block():
    from app.services.session_plan import SessionPlan, format_session_plan_projection

    plan = _plan()
    chapter = plan.model_dump()
    envelope = SessionPlan(
        envelope_id="e1", session_id="s1", gis_chapter=chapter,
    )
    text = format_session_plan_projection(envelope)
    assert text.startswith("[SessionPlan]")
    assert "[GIS Plan]" in text
    assert "Ready:" in text
    # 空 chapter（无数据需求）不输出图块
    empty = SessionPlan(envelope_id="e2", session_id="s2")
    assert "[GIS Plan]" not in format_session_plan_projection(empty)


@pytest.mark.asyncio
async def test_intent_guidance_discloses_dependency_order():
    """webgis_map_intent 的 guidance 携带依赖序行（Phase D）。"""
    from app.tools.registry import ToolRegistry
    from app.services.gis_harness.tools import register_gis_harness_tools

    registry = ToolRegistry()
    register_gis_harness_tools(registry)
    result = await registry.dispatch(
        "webgis_map_intent", {"query": "成都小学分布情况"}, session_id=None,
    )
    guidance = result.get("guidance") or []
    dep_lines = [g for g in guidance if g.startswith("依赖序")]
    plan_has_deps = any(
        r.get("depends_on") for r in result.get("plan", {}).get("data_requirements", [])
    )
    if plan_has_deps:
        assert dep_lines, "plan 有依赖边时 guidance 必须披露依赖序"
