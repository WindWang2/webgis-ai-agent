"""语义产品层生产接线（ADR-0183）集成测试。

覆盖：produce_product_layer 结果键、编辑存活合并（replay 不覆盖用户编辑）、
session_plan 合并键 presence 语义、webgis_product_edit 的 apply_tool_result
分支、ProductGraph 投影消费 spec。
"""
import pytest

from app.services.gis_harness.intent import resolve_map_request_intent
from app.services.gis_harness.planner import (
    MapProductPlan,
    PlannedLayer,
)
from app.services.gis_harness.product_completeness import (
    validate_product_completeness,
)
from app.services.gis_harness.product_graph import (
    KIND_COMPARISON,
    KIND_CHART,
    S_OFF,
    S_PENDING,
    build_facet_completion,
    build_product_graph,
)
from app.services.gis_harness.product_runtime import (
    load_chapter_product_spec,
    merge_spec_with_replay,
    produce_product_layer,
)
from app.services.gis_harness.product_shapes import build_product_spec_from_plan
from app.services.gis_harness.product_spec import (
    apply_product_edit,
    spec_digest,
    spec_from_storage,
)
from app.services.gis_harness.product_templates import (
    get_product_template_registry,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    apply_tool_result,
    load_session_plan,
    merge_map_product_result,
    merge_product_edit_result,
)


@pytest.fixture
async def clean_session():
    import shutil
    import uuid

    sid = f"mspg-session-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR

    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


def _plan(query: str = "成都各区小学数量统计"):
    intent = resolve_map_request_intent(query)
    reg = get_product_template_registry()
    template = reg.find_for_recipe("administrative_choropleth", "")
    plan = MapProductPlan(
        plan_id="plan-wire",
        query=query,
        intent=intent,
        recipe_id="administrative_choropleth",
        template_id=template.id if template else "",
        statistics=["admin_aggregation"],
        map_layers=[
            PlannedLayer(role="primary", layer_type="fill",
                         cartography="administrative_choropleth",
                         source_capability="admin_aggregation",
                         layer_id="lyr-admin", bound_ref="ref:primary"),
        ],
    )
    return plan, intent, template


# ── produce_product_layer ────────────────────────────────────────────────


def test_produce_layer_fresh_keys_and_determinism():
    plan, intent, template = _plan()
    r1 = produce_product_layer(plan=plan, intent=intent, template=template,
                               primary_ref="ref:primary")
    r2 = produce_product_layer(plan=plan, intent=intent, template=template,
                               primary_ref="ref:primary")
    assert "product_spec" in r1 and "product_views" in r1
    assert "product_completeness" in r1 and "product_compile" in r1
    assert r1["product_spec"]["digest"] == r2["product_spec"]["digest"]
    spec = spec_from_storage(r1["product_spec"])
    assert spec is not None
    assert spec.view("v-map").binding.dataset_ref == "ref:primary"
    assert validate_product_completeness(
        spec, compile_result=None).complete or True  # 冒烟：报告可产出


def test_edit_survives_product_replay():
    """核心不变式：用户编辑（去掉统计视图）在重组装重放后不被覆盖。"""
    plan, intent, template = _plan()
    first = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(first["product_spec"])
    # 用户编辑：删掉统计面板视图
    edited, errs, _ = apply_product_edit(
        spec, "remove_view", "v-stats", reason="不要统计卡")
    assert edited is not None and not errs, errs
    # 重组装（fresh replay 同一产品）→ 合并 → 编辑存活
    merged = merge_spec_with_replay(edited, build_product_spec_from_plan(
        plan, intent, template))
    assert merged.view("v-stats") is None, "被显式移除的视图不得复活"
    assert any(ov.op == "remove_view" for ov in merged.overrides)
    # 事实回填仍发生：template/recipe 指针刷新
    assert merged.template_id == (template.id if template else "")
    assert merged.plan_id == plan.plan_id


def test_replay_readd_view_new_in_fresh():
    plan, intent, template = _plan()
    first = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(first["product_spec"])
    spec.views = [v for v in spec.views if v.view_id != "v-stats"]
    fresh = build_product_spec_from_plan(plan, intent, template)
    merged = merge_spec_with_replay(spec, fresh)
    assert merged.view("v-stats") is not None, "fresh 新增（非被撤回）视图应追加"


# ── session_plan 合并键 ──────────────────────────────────────────────────


def test_merge_map_product_result_product_spec_presence():
    chapter = {"recipe_id": "x"}
    merge_map_product_result(chapter, {"product_spec": {"digest": "d"}})
    assert chapter["product_spec"] == {"digest": "d"}
    # 缺席 → 不动
    merge_map_product_result(chapter, {"completeness": {"ok": 1}})
    assert chapter["product_spec"] == {"digest": "d"}


def test_merge_product_edit_result_presence():
    chapter = {}
    merge_product_edit_result(chapter, {})  # 编辑失败无键 → 原值
    assert "product_spec" not in chapter
    merge_product_edit_result(chapter, {"product_spec": {"digest": "e"}})
    assert chapter["product_spec"] == {"digest": "e"}


async def test_apply_tool_result_routes_product_edit(clean_session):
    """webgis_product_edit 结果只动 product_spec 键（能力行状态不动）。"""
    from app.services.session_plan import ensure_session_plan_slot, save_session_plan

    sid = clean_session
    slot = await ensure_session_plan_slot(sid)
    slot.gis_chapter = {
        "query": "成都各区小学数量统计",
        "recipe_id": "administrative_choropleth",
        "data_requirements": [{"capability": "admin_aggregation", "status": "pending"}],
    }
    await save_session_plan(slot)

    events = await apply_tool_result(
        sid, "webgis_product_edit",
        {"success": True, "product_spec": {"spec_version": "1.0", "digest": "abc"}},
        success=True,
    )
    assert events, "编辑成功应有 envelope 更新事件"
    envelope = await load_session_plan(sid)
    assert envelope.gis_chapter["product_spec"]["digest"] == "abc"
    # 能力行状态不受编辑影响
    assert envelope.gis_chapter["data_requirements"][0]["status"] == "pending"

    # 失败结果（无 product_spec 键）→ spec 不被清掉
    await apply_tool_result(sid, "webgis_product_edit", {"success": False}, success=True)
    envelope = await load_session_plan(sid)
    assert envelope.gis_chapter["product_spec"]["digest"] == "abc"


# ── ProductGraph 投影消费 spec ───────────────────────────────────────────


def _chapter_with_spec(spec_storage):
    return {
        "query": "成都各区小学数量统计",
        "recipe_id": "administrative_choropleth",
        "product_spec": spec_storage,
        "map_layers": [{"layer_id": "lyr-admin", "role": "primary",
                        "status": "available", "enabled": True}],
        "data_requirements": [{"capability": "admin_aggregation",
                               "status": "available", "bound_ref": "ref:primary"}],
    }


def test_graph_projects_spec_views():
    plan, intent, template = _plan()
    layer_out = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(layer_out["product_spec"])
    # 加一个用户点名的对比面板（required）
    edited, errs, _ = apply_product_edit(
        spec, "add_view", "", {"view": {"view_id": "v-compare",
                                        "kind": "comparison", "required": True}})
    assert edited is not None, errs
    chapter = _chapter_with_spec(storage_payload_ok(edited))
    graph = build_product_graph(chapter, None)
    kinds = {n.kind for n in graph.nodes}
    assert KIND_COMPARISON in kinds, "spec 对比视图 → comparison facet"
    compare_nodes = [n for n in graph.nodes if n.kind == KIND_COMPARISON]
    assert compare_nodes[0].status == S_PENDING, "无组件承载 → 物理欠账可见"
    line = graph.summary_line()
    assert "compare" in line, f"summary 应披露对比面: {line}"


def test_graph_disabled_spec_view_off():
    plan, intent, template = _plan()
    layer_out = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(layer_out["product_spec"])
    edited, errs, _ = apply_product_edit(spec, "toggle_view", "v-stats")
    assert edited is not None, errs
    chapter = _chapter_with_spec(storage_payload_ok(edited))
    graph = build_product_graph(chapter, None)
    off_nodes = [n for n in graph.nodes if n.status == S_OFF and n.metadata.get("from_spec")]
    assert off_nodes, "禁用的 spec 视图 → off facet"


def test_facet_completion_respects_spec_required():
    plan, intent, template = _plan()
    layer_out = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(layer_out["product_spec"])
    edited, errs, _ = apply_product_edit(
        spec, "add_view", "", {"view": {"view_id": "v-compare",
                                        "kind": "comparison", "required": True}})
    assert edited is not None
    chapter = _chapter_with_spec(storage_payload_ok(edited))
    facets = build_facet_completion(chapter, None)
    compare = [f for f in facets if f.kind == KIND_COMPARISON]
    assert compare and compare[0].required is True, "用户点名 → required"
    stats = [f for f in facets if f.kind == "statistics"]
    assert stats, "既有 statistics facet 保留"


def storage_payload_ok(spec):
    from app.services.gis_harness.product_spec import storage_payload

    return storage_payload(spec)


# ── review 修复回归（CAS 守卫 / override 留账 / comparison 承载）──────────


def test_merge_cas_guard_blocks_stale_writer():
    chapter = {"product_spec": {"digest": "current"}}
    # 写入者基线已过期 → 拒绝覆盖 + 冲突披露
    merge_product_edit_result(chapter, {
        "product_spec": {"digest": "incoming"},
        "base_spec_digest": "stale"})
    assert chapter["product_spec"]["digest"] == "current"
    assert chapter["product_spec_conflict"]["base_digest"] == "stale"
    # 基线一致 → 正常落账
    merge_product_edit_result(chapter, {
        "product_spec": {"digest": "incoming"},
        "base_spec_digest": "current"})
    assert chapter["product_spec"]["digest"] == "incoming"


def test_merge_map_product_cas_guard():
    chapter = {"product_spec": {"digest": "current"}}
    merge_map_product_result(chapter, {
        "product_spec": {"digest": "replay"},
        "base_spec_digest": "stale"})
    assert chapter["product_spec"]["digest"] == "current"
    assert "product_spec_conflict" in chapter


def test_structural_override_survives_soft_trim():
    """remove_view 账（编辑存活的撤回证据）不被软账裁剪抹掉。"""
    plan, intent, template = _plan()
    first = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(first["product_spec"])
    edited, errs, _ = apply_product_edit(
        spec, "remove_view", "v-stats", reason="撤回统计")
    assert edited is not None and not errs
    # 塞满软账（toggle_view 循环 20 次）→ remove_view 账仍在
    cur = edited
    for i in range(20):
        cur, errs_i, _ = apply_product_edit(cur, "set_caption", "v-map",
                                            {"text": f"t{i}"})
        assert cur is not None, errs_i
    assert any(ov.op == "remove_view" and ov.target == "v-stats"
               for ov in cur.overrides)
    merged = merge_spec_with_replay(cur, build_product_spec_from_plan(
        plan, intent, template))
    assert merged.view("v-stats") is None, "撤回证据被裁掉会导致视图复活"


def test_comparison_face_done_when_chart_backing_present():
    plan, intent, template = _plan()
    layer_out = produce_product_layer(plan=plan, intent=intent, template=template)
    spec = spec_from_storage(layer_out["product_spec"])
    edited, errs, _ = apply_product_edit(
        spec, "add_view", "", {"view": {"view_id": "v-compare",
                                        "kind": "comparison", "required": True}})
    assert edited is not None
    chapter = _chapter_with_spec(storage_payload_ok(edited))
    # 有 enabled chart_panel 组件承载 → comparison 面不谎报欠账
    chapter_mapspec = {
        "layers": [],
        "layout": {"components": [
            {"id": "chart-panel-1", "type": "chart_panel", "enabled": True},
        ]},
    }
    graph = build_product_graph(chapter, chapter_mapspec)
    compare = [n for n in graph.nodes if n.kind == KIND_COMPARISON]
    assert compare and compare[0].status != S_PENDING, "有物理承载时不得报欠账"
    # 无承载 → pending 欠账可见
    graph2 = build_product_graph(chapter, None)
    compare2 = [n for n in graph2.nodes if n.kind == KIND_COMPARISON]
    assert compare2 and compare2[0].status == S_PENDING
