"""Harness Kernel acceptance scenarios (ADR-0180) — deterministic E2E.

Scripted tool sequences through the real Pi dispatch seam (no LLM, no Pi
subprocess) covering the seven minimum acceptance scenarios:

1. 成都小学 → SessionPlan 创建 → 多工具步骤完成 → plan completed
2. 只看主城区 → 同信封更新（replaced，不 supersede 成无关新世界）
3. tool failure → step failed + retry，不重复已完成 side effect
4. SSE 断线重连 → plan 状态不倒退（信封单调 revision + 步骤保留）
5. Pi restart → GIS session 恢复（interrupted 检测 + resume）
6. legacy path 经 adapter 读写同一 SessionPlan 契约（host parity）
7. MapSpec / cartography runtime 共存工作，无双重锁/死锁（product finalize
   在 kernel evidence 同会话锁序列中完成）

确定性注记：``query_local_poi``/``get_local_admin_boundary`` 依赖本机数据
导入（gd_pois.gpkg），与既有 ``test_pi_session_plan_host.py`` 一致按条件
处理；能力命中/失败-重试链走 ``heatmap_data``（inline geojson，无数据依赖，
registry 有 tool→capability 映射，参数校验失败确定性产生 dispatch error）。
"""
import uuid

import pytest

from app.agent_pi_bridge import (
    PiToolRequest,
    _cleanup_turn_state,
    dispatch_tool,
    register_active_pi_turn,
    set_tool_registry,
    take_session_plan_sse,
    unregister_active_pi_turn,
)
from app.services.harness_kernel import get_runtime
from app.services.session_data import session_data_manager
from app.services.session_plan import (
    CANONICAL_PLAN_EVENT_NAMES,
    SESSION_PLAN_STEP,
    capabilities_hit_by_tool,
    format_session_plan_projection,
    load_session_plan,
)
from app.tools import init_tools
from app.tools.registry import ToolRegistry

QUERY = "成都市小学分布情况"

_POINTS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [104.06 + i * 0.01, 30.66]},
            "properties": {"name": f"school-{i}"},
        }
        for i in range(12)
    ],
}


@pytest.fixture
def registry():
    r = ToolRegistry()
    init_tools(r)
    set_tool_registry(r)
    return r


@pytest.fixture
async def sid():
    session_id = f"sess-hk-e2e-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


def _req(call_id: str, name: str, arguments: dict, sid: str) -> PiToolRequest:
    return PiToolRequest(
        toolCallId=call_id, name=name, arguments=arguments, sessionId=sid,
    )


async def _intent(sid: str, call_id: str = "tc-intent", query: str = QUERY):
    res = await dispatch_tool(_req(call_id, "webgis_map_intent", {"query": query}, sid))
    assert not res.isError, res.content
    take_session_plan_sse(call_id, sid)
    return res


async def _heatmap(sid: str, *, good: bool, call_id: str = "tc-heat", n: int = 12):
    """heatmap_data 经 webgis_execute（inline geojson，无本机数据依赖）。

    ``n`` 变化点数 → 参数不同：同一 session 内完全相同的 (tool, args) 会被
    ToolDispatchService 的 repeat-intercept 以 status=repeated 返回（既有
    设计：不重执行、不落计划证据）。
    """
    points = {
        "type": "FeatureCollection",
        "features": _POINTS["features"][:n],
    }
    if good:
        args = {"toolName": "heatmap_data", "arguments": {
            "geojson": points, "render_type": "native"}}
    else:
        args = {"toolName": "heatmap_data", "arguments": {"geojson": {"bad": 1}}}
    res = await dispatch_tool(_req(call_id, "webgis_execute", args, sid))
    sse = take_session_plan_sse(call_id, sid)
    return res, sse


async def _product(sid: str, call_id: str = "tc-product"):
    primary_ref = await session_data_manager.store(sid, _POINTS, prefix="geojson")
    res = await dispatch_tool(_req(
        call_id, "webgis_map_product",
        {"query": QUERY, "primary_ref": primary_ref, "title": "成都市小学分布"}, sid,
    ))
    assert not res.isError, res.content
    take_session_plan_sse(call_id, sid)
    return res


async def _heatmap_capability(sid: str) -> str:
    """intent 章节里被 heatmap_data 命中的能力（运行时读，不硬编码）。"""
    plan = await load_session_plan(sid)
    hits = capabilities_hit_by_tool(plan, "heatmap_data")
    assert hits, "heatmap_data must hit a planned capability after intent"
    return hits[0]


# ── Scenario 1 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s1_chengdu_schools_plan_lifecycle(registry, sid):
    """意图 → 多工具步骤 → plan completed（turn 台账 + 步骤证据 + 投影）。"""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-s1", host="pi", message=QUERY)
    # 生产中 turn 必然在飞（stream_prompt 注册）；直调 dispatch 前注册同一
    # 身份，让 dispatch 侧 correlation 走真实路径。
    await register_active_pi_turn(sid, "turn-s1")

    await _intent(sid)
    cap = await _heatmap_capability(sid)
    heat, heat_sse = await _heatmap(sid, good=True)
    assert not heat.isError, heat.content
    await _product(sid)
    # 本机数据条件工具（有数据则同样计入步骤证据）。
    # 本机无导入数据时该工具确定性失败 —— 命中能力步的失败标记同样有效。
    await dispatch_tool(_req(
        "tc-boundary", "get_local_admin_boundary",
        {"name": "成都市", "level": "city"}, sid,
    ))
    take_session_plan_sse("tc-boundary", sid)

    plan = await load_session_plan(sid)
    assert plan is not None and plan.gis_chapter is not None
    steps = {s.capability: s for s in plan.steps if s.capability}
    assert cap in steps
    assert steps[cap].status == "succeeded"
    assert steps[cap].attempts >= 1
    assert steps[cap].latest_evidence().ref.startswith("ref:")
    assert steps[cap].turn_id == "turn-s1"
    # 产品里程碑步成功（webgis_map_product finalize）。
    product = next(s for s in plan.steps if s.id == "product")
    assert product.status == "succeeded"
    # turn 台账：1 turn，tool_calls ≥ 派发数。
    assert plan.turns[0].turn_id == "turn-s1"
    assert plan.turns[0].tool_calls >= 3
    # 决策日志：plan_created + 类型化 tool 事件（ADR-0204：step_marked 由
    # 幂等 tool_succeeded/tool_failed 取代，不再新发）。
    kinds = [d.kind for d in plan.decisions]
    assert "plan_created" in kinds and "tool_succeeded" in kinds

    # step 级 SSE 经既有 rendezvous 出现在线上（前端 plan 证据）。
    assert f"event: {SESSION_PLAN_STEP}" in heat_sse
    for name in CANONICAL_PLAN_EVENT_NAMES:
        assert f"event: {name}" not in heat_sse

    await unregister_active_pi_turn(sid, "turn-s1")
    await rt.end_turn("turn-s1", host="pi", status="completed")
    plan = await load_session_plan(sid)
    assert plan.recovery.last_turn_status == "completed"
    assert plan.recovery.checkpoint_id.startswith("cp-")

    # 投影含 kernel 行（模型可见的步骤证据）。
    text = format_session_plan_projection(plan)
    assert "[SessionPlan Steps]" in text


# ── Scenario 2 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s2_followup_same_goal_updates_same_envelope(registry, sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-a", host="pi", message=QUERY)
    await register_active_pi_turn(sid, "turn-a")
    await _intent(sid)
    cap = await _heatmap_capability(sid)
    await _heatmap(sid, good=True, call_id="tc-heat-a")
    plan0 = await load_session_plan(sid)
    assert plan0.replaced is False

    # 下一轮 follow-up（同目标、范围收窄）→ 原 plan 更新。
    await unregister_active_pi_turn(sid, "turn-a")
    await rt.end_turn("turn-a", host="pi", status="completed")
    _cleanup_turn_state(sid)  # 生产语义：新 turn 开始时清理上一 turn 去重集
    await rt.begin_turn("turn-b", host="pi", message="只看主城区")
    await register_active_pi_turn(sid, "turn-b")
    await _intent(sid, call_id="tc-intent2")

    plan1 = await load_session_plan(sid)
    assert plan1.envelope_id == plan0.envelope_id, "same-goal follow-up must stay in the same envelope"
    assert plan1.superseded is False
    assert plan1.replaced is True
    # 章节追踪的步骤随重物化保留（状态不回退）。
    steps1 = {s.capability: s.status for s in plan1.steps if s.capability}
    assert "poi_query" in steps1, "chapter-tracked steps survive the same-goal replace"
    # 计划外能力（registry 命中但章节未规划）的步骤不存活 —— 镜像
    # capability 层 ``_merge_progress`` 的丢弃语义（再次命中按需重建）。
    assert cap not in steps1
    assert plan1.revision > plan0.revision

    await unregister_active_pi_turn(sid, "turn-b")
    await rt.end_turn("turn-b", host="pi", status="completed")


# ── Scenario 3 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s3_failure_marks_step_retry_without_side_effect_duplication(registry, sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-s3", host="pi", message=QUERY)
    await _intent(sid)
    cap = await _heatmap_capability(sid)

    # 确定性失败：参数校验错误 → dispatch error → 命中能力步标 failed。
    failed, _ = await _heatmap(sid, good=False, call_id="tc-heat-bad")
    assert failed.isError
    plan = await load_session_plan(sid)
    step = next(s for s in plan.steps if s.capability == cap)
    assert step.status == "failed"
    assert step.attempts == 1
    assert step.latest_evidence().error

    # 重试成功 → succeeded；失败证据保留，成功证据绑定 ref（无重复注册）。
    ok, _ = await _heatmap(sid, good=True, call_id="tc-heat-retry")
    assert not ok.isError, ok.content
    plan = await load_session_plan(sid)
    step = next(s for s in plan.steps if s.capability == cap)
    assert step.status == "succeeded"
    assert step.attempts == 2
    refs = [e.ref for e in step.evidence if e.ref]
    assert refs and all(r == refs[0] for r in refs)
    assert any(e.error for e in step.evidence), "failed attempt stays journaled"
    assert step.latest_evidence().tool_call_id == "tc-heat-retry"

    await rt.end_turn("turn-s3", host="pi", status="completed")


# ── Scenario 4 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s4_sse_disconnect_reconnect_plan_state_monotonic(registry, sid):
    """断线只影响事件缓冲：服务端信封 revision 单调、步骤不回退；step 级
    SSE 经既有 rendezvous 缓存（resume buffer 捕获同一字节流）。"""
    rt = get_runtime(sid)
    await rt.begin_turn("turn-s4", host="pi", message=QUERY)
    await register_active_pi_turn(sid, "turn-s4")
    await _intent(sid)
    cap = await _heatmap_capability(sid)
    await _heatmap(sid, good=True)
    plan_mid = await load_session_plan(sid)
    rev_mid = plan_mid.revision

    # 断线重连 = 新 turn：清理去重集后同一调用不再被 repeat-intercept。
    _cleanup_turn_state(sid)
    ok, cached = await _heatmap(sid, good=True, call_id="tc-heat-reconnect", n=20)
    assert not ok.isError, ok.content
    assert f"event: {SESSION_PLAN_STEP}" in cached

    plan_after = await load_session_plan(sid)
    assert plan_after.revision >= rev_mid
    assert plan_after.envelope_id == plan_mid.envelope_id
    steps = {s.capability: s.status for s in plan_after.steps if s.capability}
    assert steps.get(cap) == "succeeded", "plan state must not regress across reconnect"
    # 重连后 hydrate 同一信封（GET 投影源不变）。
    hydrated = await rt.hydrate()
    assert hydrated.envelope_id == plan_mid.envelope_id
    await unregister_active_pi_turn(sid, "turn-s4")


# ── Scenario 5 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s5_pi_restart_recovers_gis_session(registry, sid):
    rt = get_runtime(sid)
    await rt.begin_turn("turn-r1", host="pi", message=QUERY)
    await register_active_pi_turn(sid, "turn-r1")
    await _intent(sid)
    cap = await _heatmap_capability(sid)
    await _heatmap(sid, good=True)
    # Pi/进程死亡：turn 未 settle（无 end_turn）。新进程 hydrate 同一 Redis
    # 信封 → interrupted 对账 + resume 计数。
    await unregister_active_pi_turn(sid, "turn-r1")
    rt2 = get_runtime(sid)
    await rt2.begin_turn("turn-r2", host="pi", message="继续")
    plan = await load_session_plan(sid)
    statuses = {t.turn_id: t.status for t in plan.turns}
    assert statuses["turn-r1"] == "interrupted"
    assert statuses["turn-r2"] == "running"
    assert plan.recovery.resume_count == 1
    # 已完成步骤证据完整保留（不重放已确认的副作用）。
    steps = {s.capability: s.status for s in plan.steps if s.capability}
    assert steps.get(cap) == "succeeded"
    # 恢复投影行提示（模型续跑指引）。
    text = format_session_plan_projection(plan)
    assert "[SessionPlan Recovery]" in text
    await rt2.end_turn("turn-r2", host="pi", status="completed")


# ── Scenario 6 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s6_legacy_host_reads_writes_same_contract(registry, sid):
    """legacy CanonicalPlan 经 adapter 投影进同一 SessionPlan 契约（语义
    parity：目标/步骤状态/revision 台账），CanonicalPlan 仍是 legacy 源真。"""
    from app.services.chat.plan_orchestrator import Plan, PlanStep as OrchStep
    from app.services.harness_kernel import legacy_adapter
    from app.services.planning.models import CanonicalPlan, CanonicalStep, StepStatus
    from app.services.planning.store import plan_store

    await legacy_adapter.begin_turn(sid, "turn-leg-1", message=QUERY)

    orch_plan = Plan(
        intent=QUERY,
        domains=["poi"],
        steps=[
            OrchStep(n=1, goal="查询成都小学 POI", tool_family="poi"),
            OrchStep(n=2, goal="生成热力图", tool_family="analysis", done=True),
        ],
    )
    events = await legacy_adapter.project_orchestrator_plan(sid, orch_plan, turn_id="turn-leg-1")
    assert events and all(e.event == SESSION_PLAN_STEP for e in events)

    # advance_step 打勾后的 canonical flush 镜像。
    canonical = CanonicalPlan(
        plan_id="plan-leg",
        session_id=sid,
        intent=QUERY,
        domains=["poi"],
        steps=[
            CanonicalStep(id="s1", n=1, goal="查询成都小学 POI", status=StepStatus.completed),
            CanonicalStep(id="s2", n=2, goal="生成热力图", status=StepStatus.completed),
        ],
    )
    await plan_store.save(canonical)
    await legacy_adapter.project_canonical_flush(sid, canonical)

    plan = await load_session_plan(sid)
    steps = {s.id: s for s in plan.steps}
    assert set(steps) == {"l1", "l2"}
    assert all(s.status == "succeeded" for s in steps.values())
    assert all(s.host == "chatengine" for s in steps.values())
    assert plan.user_goal == QUERY
    # 同一契约的 kernel 语义（revision/turn/decision 台账）也在 legacy 侧生效。
    assert plan.revision >= 2
    assert plan.recovery.last_turn_id == "turn-leg-1"
    kinds = [d.kind for d in plan.decisions]
    assert "legacy_projected" in kinds

    await legacy_adapter.end_turn(sid, "turn-leg-1", status="completed")
    plan = await load_session_plan(sid)
    assert plan.recovery.last_turn_status == "completed"
    plan_store.clear_cache()


# ── Scenario 7 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s7_mapspec_cartography_no_double_lock_deadlock(registry, sid):
    """kernel evidence（持会话锁）与 MapSpec finalize / cartography runtime
    触发（同一回调序列）共存：全链完成即无死锁；finalization SSE 正常。"""
    import asyncio

    rt = get_runtime(sid)
    await rt.begin_turn("turn-s7", host="pi", message=QUERY)
    await _intent(sid)
    await _heatmap(sid, good=True)
    await _product(sid)  # product dispatch 在 bridge 内触发 finalize

    # 并发下无死锁：一个 checkpoint 与一个读侧工具同时在飞。
    await asyncio.wait_for(
        asyncio.gather(
            rt.checkpoint(reason="concurrent", host="pi", turn_id="turn-s7"),
            dispatch_tool(_req(
                "tc-s7-status", "webgis_cartography_status", {}, sid,
            )),
        ),
        timeout=60.0,
    )
    plan = await load_session_plan(sid)
    product = next(s for s in plan.steps if s.id == "product")
    assert product.status == "succeeded"
    assert plan.gis_chapter.get("recipe_id")
    await rt.end_turn("turn-s7", host="pi", status="completed")
