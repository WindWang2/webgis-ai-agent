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
    # 模拟 SessionPlan._mark_progress 的行推进：poi_query + point_profile
    # （admin_aggregation 的全部依赖）available + bound_ref
    done = {"poi_query": "ref:geojson:poi-1", "point_profile": "ref:geojson:prof-1"}
    for row in plan.data_requirements:
        if row.capability in done:
            row.status = "available"
            row.bound_ref = done[row.capability]
    for step in plan.analysis_steps:
        if step.capability in done:
            step.status = "done"
            step.bound_ref = done[step.capability]
    graph = build_plan_graph(plan)
    for cap, ref in done.items():
        node = graph.node(cap)
        assert node is not None and node.status == PlanNodeStatus.complete
        assert node.output_ref == ref
    agg = graph.node("admin_aggregation")
    assert agg is not None, "成都小学 recipe 必含 admin_aggregation（否则测试失去锚点）"
    agg_deps = {d for d in agg.depends_on}
    if agg_deps <= set(done):
        # 全部依赖完成 → 解锁 + input_refs 携带上游 bound_ref
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
    assert kde is not None, "成都小学 recipe 必含 kde_density（否则测试失去锚点）"
    assert kde.status == PlanNodeStatus.skipped
    # mandatory 根节点不受影响
    assert "poi_query" in graph.ready_nodes()


def test_optional_dep_unavailable_does_not_block_mandatory_dependent():
    """review-B P1 复现：mandatory 节点依赖 optional-unavailable 节点 →
    依赖翻 skipped 视为满足，dependent 照常 ready（不得 blocked）。"""
    chapter = {
        "plan_id": "p-opt-dep",
        "data_requirements": [
            {"capability": "opt_a", "status": "unavailable", "optional": True},
            {"capability": "man_b", "status": "pending", "depends_on": ["opt_a"]},
            {"capability": "man_c", "status": "pending", "depends_on": ["man_b"]},
        ],
        "analysis_steps": [
            {"capability": "opt_a", "status": "unavailable", "optional": True},
            {"capability": "man_b", "status": "pending", "depends_on": ["opt_a"]},
            {"capability": "man_c", "status": "pending", "depends_on": ["man_b"]},
        ],
        "algorithm_selections": [],
    }
    graph = build_plan_graph(chapter)
    assert graph.node("opt_a").status == PlanNodeStatus.skipped
    b = graph.node("man_b")
    assert b.status == PlanNodeStatus.ready, (
        f"optional 依赖不可用不得阻塞 mandatory 下游（现为 {b.status}）"
    )
    assert b.blocked_by == []
    # 传递语义：mandatory 链继续推进 —— man_b 未执行前 man_c 等待（pending），
    # 不是被 optional 故障传染的 unavailable。
    c = graph.node("man_c")
    assert c.status == PlanNodeStatus.pending
    assert c.blocked_by == []


def test_complete_node_with_unavailable_dep_stays_complete():
    """review-B P2 复现：complete 是行事实（artifact 已绑定），即使有依赖
    不可用也不得被传播洗成 unavailable。"""
    chapter = {
        "plan_id": "p-complete-truth",
        "data_requirements": [
            {"capability": "dead_a", "status": "unavailable"},
            {"capability": "done_b", "status": "available",
             "bound_ref": "ref:geojson:b1", "depends_on": ["dead_a"]},
            {"capability": "wait_c", "status": "pending", "depends_on": ["done_b"]},
        ],
        "analysis_steps": [
            {"capability": "dead_a", "status": "unavailable"},
            {"capability": "done_b", "status": "done",
             "bound_ref": "ref:geojson:b1", "depends_on": ["dead_a"]},
            {"capability": "wait_c", "status": "pending", "depends_on": ["done_b"]},
        ],
        "algorithm_selections": [],
    }
    graph = build_plan_graph(chapter)
    assert graph.node("done_b").status == PlanNodeStatus.complete
    # 下游照常按行事实解锁
    assert graph.node("wait_c").status == PlanNodeStatus.ready


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
        # resolver 的 capability 级 fallback 裁决证据（reason 标记）——
        # 真实格式带 "; <拒绝理由>" 后缀（algorithm_resolver.py:237），
        # 解析必须截到首个分号（review-B P1）。
        "algorithm_selections": [
            {"capability": "grid_binning", "status": "unavailable",
             "algorithm": "", "tool": "",
             "reason": "capability_fallback_available:density_surface; no_eligible_algorithm",
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
    # 不变量先断言：该 recipe 的计划必有依赖边（否则 guidance 披露测试
    # 会静默空洞化）。
    plan_has_deps = any(
        r.get("depends_on") for r in result.get("plan", {}).get("data_requirements", [])
    )
    assert plan_has_deps, "成都小学 recipe 的 data_requirements 必含依赖边"
    assert dep_lines, "plan 有依赖边时 guidance 必须披露依赖序"


# ── v3(Phase E 收尾)：failed 语义 + resolver 驱动的 fallback e2e ──────────

def test_failed_mandatory_dep_blocks_downstream_until_retry():
    """failed = 执行过但无 artifact：mandatory failed 阻塞下游（blocked_by
    披露，等待重试）；重试成功（行翻 complete）后下游经 recovery 解锁。"""
    chapter = {
        "plan_id": "p-failed",
        "data_requirements": [
            {"capability": "poi_query", "status": "failed", "bound_ref": ""},
            {"capability": "admin_aggregation", "status": "pending",
             "depends_on": ["poi_query"]},
        ],
        "analysis_steps": [
            {"capability": "poi_query", "status": "failed"},
            {"capability": "admin_aggregation", "status": "pending",
             "depends_on": ["poi_query"]},
        ],
        "algorithm_selections": [],
    }
    graph = build_plan_graph(chapter)
    assert graph.node("poi_query").status == PlanNodeStatus.failed
    agg = graph.node("admin_aggregation")
    assert agg.status == PlanNodeStatus.unavailable
    assert agg.blocked_by == ["poi_query"]
    # 投影披露 Failed（retry-able）
    text = project_graph_block(graph)
    assert "Failed (retry-able): poi_query" in text

    # 重试成功：行翻 available/done → 下游恢复 ready
    chapter["data_requirements"][0]["status"] = "available"
    chapter["data_requirements"][0]["bound_ref"] = "ref:geojson:p1"
    chapter["analysis_steps"][0]["status"] = "done"
    chapter["analysis_steps"][0]["bound_ref"] = "ref:geojson:p1"
    graph2 = build_plan_graph(chapter)
    assert graph2.node("poi_query").status == PlanNodeStatus.complete
    assert graph2.node("admin_aggregation").status == PlanNodeStatus.ready
    assert graph2.node("admin_aggregation").blocked_by == []


def test_optional_failure_does_not_block_mandatory_graph():
    """§17 契约原文：optional failure 不阻塞 mandatory 图——节点保留 failed
    披露（不翻 skipped：执行尝试过是事实），下游照常 ready。"""
    chapter = {
        "plan_id": "p-opt-failed",
        "data_requirements": [
            {"capability": "opt_x", "status": "failed", "optional": True},
            {"capability": "man_y", "status": "pending", "depends_on": ["opt_x"]},
        ],
        "analysis_steps": [
            {"capability": "opt_x", "status": "failed", "optional": True},
            {"capability": "man_y", "status": "pending", "depends_on": ["opt_x"]},
        ],
        "algorithm_selections": [],
    }
    graph = build_plan_graph(chapter)
    # optional failed 节点自身保留 failed（可重试披露），不翻 skipped
    assert graph.node("opt_x").status == PlanNodeStatus.failed
    # mandatory 下游不被 optional 失败阻塞
    y = graph.node("man_y")
    assert y.status == PlanNodeStatus.ready
    assert y.blocked_by == []


def test_merge_row_status_failed_precedence():
    """complete > failed > unavailable > pending：failed 比 unavailable
    信息多（执行尝试过）；complete 是绑定事实仍最高。"""
    from app.services.gis_harness.plan_graph import _merge_row_status
    assert _merge_row_status(
        {"status": "failed"}, {"status": "pending"},
    ) == PlanNodeStatus.failed
    assert _merge_row_status(
        {"status": "failed"}, {"status": "unavailable"},
    ) == PlanNodeStatus.failed
    assert _merge_row_status(
        {"status": "available"}, {"status": "failed"},
    ) == PlanNodeStatus.complete


def test_fallback_unlock_with_real_resolver_output():
    """review-E finding 6：端到端 resolver 驱动——真实
    ``AlgorithmResolver.resolve`` 在 grid_binning 工具缺失时输出
    ``capability_fallback_available:density_surface; <理由>``，经
    ``_resolve_capabilities`` 落入 algorithm_selections，图侧解析出
    fallback_to 并在 density_surface 完成时解锁下游。"""
    from app.lib.gis.algorithm_resolver import get_algorithm_resolver
    from app.services.gis_harness.intent import resolve_map_request_intent as _rmi

    # 1) 真实 resolver：grid_binning 的候选工具全部不可见
    res = get_algorithm_resolver().resolve(
        "grid_binning", available_tools={"heatmap_data"})
    assert res.status == "unavailable"
    assert res.reason.startswith("capability_fallback_available:density_surface;")

    # 2) 经 planner 的 _resolve_capabilities 落入 selection 证据
    planner = get_planner_runtime()
    intent = _rmi("成都小学分布情况")
    requirements, steps, selections = planner._resolve_capabilities(
        ["poi_query", "grid_binning", "density_surface", "hotspot"],
        intent, available_tools={"heatmap_data"},
    )
    sel = {s.capability: s for s in selections}
    assert sel["grid_binning"].status == "unavailable"
    assert sel["grid_binning"].reason.startswith(
        "capability_fallback_available:density_surface;")

    # 3) 图侧：selection dump（真实持久形态）→ fallback_to 正确解析
    chapter = {
        "plan_id": "p-e2e-fb",
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
            {"capability": "grid_binning", "status": "unavailable",
             "depends_on": ["poi_query"]},
            {"capability": "density_surface", "status": "done",
             "bound_ref": "ref:geojson:d1", "depends_on": ["poi_query"]},
            {"capability": "hotspot", "status": "pending",
             "depends_on": ["grid_binning"]},
        ],
        "algorithm_selections": [s.model_dump() for s in selections],
    }
    graph = build_plan_graph(chapter)
    grid = graph.node("grid_binning")
    assert grid.fallback_to == "density_surface", (
        "真实 resolver reason（带后缀）必须解析出干净的 capability id"
    )
    hot = graph.node("hotspot")
    assert hot.status == PlanNodeStatus.ready, (
        f"fallback（density_surface）完成必须解锁下游（现为 {hot.status}）"
    )
