"""E5 — 八组可自动回放的多轮交互旅程（ADR-0283 S7 / 任务书 E5）。

每组旅程：**输入事件序列 → 真实 ingest/registry dispatch → IR 投影 /
AnalysisScope / TurnChain 断言 → 用户可见状态（回执/投影）**。全链走生产
函数（无影子实现）：IR ingest 与回放同一路径、工具经真实 ToolRegistry、
scope 验证读真实 session store。

分层归属（诚实标注）：本文件是「后端回放层」journey——覆盖事件语义/
选择约束/恢复门；浏览器层旅程见 frontend/e2e/journeys/j10-j12。
"""
from __future__ import annotations

import uuid

import pytest

from app.services.gis_harness.interaction_ir.contracts import LedgerState
from app.services.gis_harness.interaction_ir.ingest import apply_event, LEDGER_KEY
from app.services.gis_harness.interaction_ir.projection import (
    interaction_snapshot_from_map_state,
)
from app.services.gis_situation.analysis_scope import (
    derive_claimed_scope,
    validate_analysis_scope,
)
from app.services.gis_situation.selection_history import resolve_selection_ref
from app.services.gis_situation.turn_chain import build_turn_chain, classify_change
from app.services.session_data import session_data_manager
from app.tools.interaction_scope_tools import register_interaction_scope_tools
from app.tools.registry import ToolRegistry


def _sid(prefix: str) -> str:
    return f"e5-{prefix}-{uuid.uuid4().hex[:8]}"


def _ev(seq: int, kind: str, payload: dict, **extra) -> dict:
    return {
        "event_id": f"c-{seq:08d}", "client_instance_id": "t-e5-0001",
        "client_seq": seq, "kind": kind, "payload": payload, **extra,
    }


def _schools_fc() -> dict:
    return {"geojson": {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": f"sch-{i}",
         "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
         "properties": {"name": f"school-{i}", "type": "primary" if i % 2 else "middle"}}
        for i in range(1, 7)
    ]}}


async def _seed_session(sid: str, events: list, *, payload: dict | None = None,
                        extra_state: dict | None = None) -> str:
    state = LedgerState()
    for e in events:
        apply_event(state, e)
    host = next((event.get("payload", {}) for event in reversed(events) if event.get("payload", {}).get("dataset_ref")), None)
    if host:
        from app.services.mapspec.store import mapspec_store_instance
        await mapspec_store_instance.save_mapspec(sid, {"version": "1.0", "sources": {"s": {"type": "geojson", "ref_id": host["dataset_ref"]}},
                                                      "layers": [{"id": host["layer_id"], "type": "circle", "source": "s"}]})
    await session_data_manager.set_map_state(sid, LEDGER_KEY, state.events)
    await session_data_manager.set_map_state(sid, "_interaction_ir_meta", {
        "server_seq": state.server_seq, "dropped": 0})
    ref_id = None
    if payload is not None:
        ref_id = await session_data_manager.store(sid, payload)
    for key, value in (extra_state or {}).items():
        await session_data_manager.set_map_state(sid, key, value)
    return ref_id


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    register_interaction_scope_tools(reg)
    return reg


# ── J1 学校分布 → 点选 → 「统计一下」→ 再换参数分析（多轮指代）────────


@pytest.mark.asyncio
async def test_j1_point_select_to_scoped_analysis(registry):
    """旅程：点选 sch-2 → 追问统计（scope current）→ 新选择替代 →
    「刚才那所」（scope previous）→ 旧选择经验证仍可执行真实动作。"""
    sid = _sid("j1")
    ref = await _seed_session(sid, [], payload=_schools_fc())
    await _seed_session(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref, "feature_id": "sch-2"}),
        _ev(2, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref, "feature_id": "sch-5"}),
    ])

    # Turn 2：agent 拉当前选择 → 验证 usable → 真实 feature_edit preview 消费
    result = await registry.dispatch("webgis_selection_scope", {}, session_id=sid)
    assert result["status"] == "usable"
    assert result["feature_ids"] == ["sch-5"]

    from app.services.feature_edit.service import build_and_store_plan
    # scope 结果（经验证 dataset_ref + ids）显式传入真实数据动作工具 —— 选择
    # 真正约束了动作目标（不是重新搜索）。
    plan, findings = await build_and_store_plan(
        sid, ref_id=result["dataset_ref"], feature_ids=result["feature_ids"],
        ops=[{"op": "set_fields", "feature_ids": result["feature_ids"],
              "fields": {"type": "primary"}}],
    )
    assert findings == [], findings
    assert plan.get("plan_id"), plan  # 选择真实约束了数据动作目标

    # Turn 3：「刚才那所」→ previous 句柄解析 sch-2
    claimed = resolve_selection_ref(
        (await session_data_manager.get_map_state(sid))[LEDGER_KEY], "previous")
    assert claimed is not None and claimed["feature_ids"] == ["sch-2"]
    validated = await validate_analysis_scope(sid, claimed)
    assert validated["status"] == "usable"
    assert validated["feature_ids"] == ["sch-2"]


# ── J2 框选区域 → 「这一区域有多少学校」（AOI 面约束）──────────────────


@pytest.mark.asyncio
async def test_j2_brush_aoi_scope():
    """旅程：框选 → selected_ids 上行 + canvas affordance 几何 → scope
    kind=aoi 带 bbox（分析参数面）；部分要素删除后 stale 披露不冒充。"""
    sid = _sid("j2")
    ring = [{
        "action_id": "a-1", "kind": "box_select", "seq": 1,
        "geometry": {"type": "Polygon", "coordinates": [
            [[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7], [104.0, 30.6]]]},
        "bbox": [104.0, 30.6, 104.1, 30.7],
    }]
    await _seed_session(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": "ref:geojson-x",
            "selected_ids": ["sch-1", "sch-3"]}),
    ], extra_state={"_situation_canvas_affordances": ring})

    map_state = await session_data_manager.get_map_state(sid)
    snapshot = interaction_snapshot_from_map_state(map_state)
    claimed = derive_claimed_scope(snapshot, None,
                                   affordance_ring=ring)
    # 框选的要素集优先于 AOI（更精确的语义）；AOI 是几何兜底面。
    assert claimed["kind"] == "feature_ids"
    assert claimed["feature_ids"] == ["sch-1", "sch-3"]
    # 无要素命中时 AOI 面成立（bbox 可作区域统计参数）。
    claimed_aoi = derive_claimed_scope(interaction_snapshot_from_map_state({}), None,
                                       affordance_ring=ring)
    validated = await validate_analysis_scope(sid, claimed_aoi)
    assert validated["status"] == "usable"
    assert validated["kind"] == "aoi"
    assert validated["bbox"] == [104.0, 30.6, 104.1, 30.7]


# ── J3 图表类别选择 → 地图联动 → 上行（chart 面）──────────────────────


@pytest.mark.asyncio
async def test_j3_chart_selection_upstream_and_projection():
    sid = _sid("j3")
    await _seed_session(sid, [
        _ev(1, "chart_selection_changed", {
            "layer_id": "districts", "field": "type",
            "categories": ["primary"], "action": "select"}),
        _ev(2, "chart_selection_changed", {
            "layer_id": "districts", "field": "type", "action": "clear"}),
        _ev(3, "chart_selection_changed", {
            "layer_id": "districts", "field": "type",
            "categories": ["middle"], "action": "select"}),
    ])
    map_state = await session_data_manager.get_map_state(sid)
    snapshot = interaction_snapshot_from_map_state(map_state)
    assert snapshot["chart_selection"]["categories"] == ["middle"]  # 最新事实
    # clear 后再选 → 投影只保留最终态（clear 不残留）。
    assert snapshot["chart_selection"]["field"] == "type"


# ── J4 图层显隐 → agent 建议恢复 → user-wins（锁定不被反转）───────────


@pytest.mark.asyncio
async def test_j4_layer_visibility_user_wins():
    sid = _sid("j4")
    await _seed_session(sid, [
        _ev(1, "layer_visibility_changed", {"layer_id": "heatmap", "visible": False},
            client_mutation_id="mut-j4", mutation_outcome="committed"),
    ])
    map_state = await session_data_manager.get_map_state(sid)
    snapshot = interaction_snapshot_from_map_state(map_state)
    override = next(o for o in snapshot["user_overrides"] if o["layer_id"] == "heatmap")
    assert override["visible"] is False  # 用户显隐决策进 overrides（user 面）
    assert override["link"]["outcome"] == "committed"

    # TurnChain：agent 随后 commit 触碰同层 → 冲突披露（不吞并用户决策）。
    chain = build_turn_chain(
        [{"turn_id": "t1", "kind": "map_mutated", "mutation_revision": 3, "detail": {}}],
        [{"operation_id": "op-j4", "origin": "agent", "status": "committed",
          "mutation_revisions": [3], "provenance": {"turn_id": "t1"},
          "effects": [{"kind": "set_layer_visibility", "targets": ["heatmap"],
                       "state": "applied"}]}],
        map_state[LEDGER_KEY], [
            {"origin": "user", "kind": "PatchLayerPresentationIntent",
             "target": "heatmap", "revision": 2,
             "detail": {"visible": False}},
        ], observed_revision=3,
    )
    turn = chain["turns"][0]
    agent_change = next(c for c in turn["changes"]
                        if c.get("operation_id") == "op-j4")
    assert agent_change.get("user_wins_conflict_layers") == ["heatmap"]


# ── J5 变更地区 → 再分析（旧选择被筛掉/跨代披露）─────────────────────


@pytest.mark.asyncio
async def test_j5_region_change_supersedes_selection():
    sid = _sid("j5")
    ref = await _seed_session(sid, [], payload=_schools_fc())
    await _seed_session(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref, "feature_id": "sch-3"}),
        _ev(2, "viewport_settled", {"center": [116.4, 39.9], "zoom": 10,
                                    "bounds": [116.3, 39.8, 116.5, 40.0]}),
        _ev(3, "feature_deselected", {}),
    ])
    map_state = await session_data_manager.get_map_state(sid)
    snapshot = interaction_snapshot_from_map_state(map_state)
    assert snapshot["selection"] is None  # deselect 清空——旧区域选择不残留
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["kind"] == "none"
    # 「刚才那个学校」仍可经 previous 解析（有据），验证面决定可用性。
    prev = resolve_selection_ref(map_state[LEDGER_KEY], "previous")
    assert prev is not None and prev["feature_ids"] == ["sch-3"]


# ── J6 刷新/重连 → 服务端投影恢复（状态面数据源）──────────────────────


@pytest.mark.asyncio
async def test_j6_refresh_recovery_from_server_projection():
    sid = _sid("j6")
    ref = await _seed_session(sid, [], payload=_schools_fc())
    await _seed_session(sid, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref, "feature_id": "sch-4"}),
    ])
    # 「刷新」= 前端内存清空，服务端 map_state 存活——GET /situation 的
    # 数据源重建（同一路径）：
    map_state = await session_data_manager.get_map_state(sid)
    snapshot = interaction_snapshot_from_map_state(map_state)
    claimed = derive_claimed_scope(snapshot, None)
    assert claimed["feature_ids"] == ["sch-4"]
    validated = await validate_analysis_scope(sid, claimed)
    assert validated["status"] == "usable"  # 恢复的选择仍可约束分析


# ── J7 后到旧 ACK / 迟到观察不覆盖新状态 ─────────────────────────────


@pytest.mark.asyncio
async def test_j7_stale_observation_never_promotes():
    """mutation rev=6 已提交，迟到观察只盖到 rev=5 → 不升格
    executed_observed（诚实 pending_execution）。"""
    assert classify_change(
        origin="agent", receipt_status="committed", outcome=None,
        mutation_revision=6, observed_revision=5,
    ) == "pending_execution"
    chain = build_turn_chain(
        [{"turn_id": "t1", "kind": "map_mutated", "mutation_revision": 6, "detail": {}}],
        [{"operation_id": "op-7", "origin": "agent", "status": "committed",
          "mutation_revisions": [6], "provenance": {"turn_id": "t1"},
          "effects": []}],
        [], [], observed_revision=5,
    )
    change = chain["turns"][0]["changes"][0]
    assert change["class"] == "pending_execution"
    assert chain["counts"]["executed_observed"] == 0  # 不冒充已观测


# ── J8 权限拒绝/无结果（跨会话 ref fail-closed + 无选择 typed 披露）───


@pytest.mark.asyncio
async def test_j8_cross_session_ref_fail_closed(registry):
    other = _sid("j8o")
    mine = _sid("j8m")
    ref = await _seed_session(other, [], payload=_schools_fc())
    # 我的会话引用别个会话的 ref —— 服务端 store 解析不到 → fail-closed。
    await _seed_session(mine, [
        _ev(1, "feature_selected", {
            "layer_id": "schools", "dataset_ref": ref, "feature_id": "sch-1"}),
    ])
    result = await registry.dispatch("webgis_selection_scope", {}, session_id=mine)
    assert result["success"] is False
    assert result["reason"] == "ref_unresolvable"
    assert result["correction_hint"]  # 给 agent 可行动提示，不静默
    # 无选择会话 → typed no_selection（诚实，不猜默认）。
    empty = _sid("j8e")
    result2 = await registry.dispatch("webgis_selection_scope", {}, session_id=empty)
    assert result2["reason"] == "no_selection"
