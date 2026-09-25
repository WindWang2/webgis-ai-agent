"""F03（ADR-0204-f03）：canonical lifecycle 生产语义测试。

覆盖四个新生产事实（全部确定性：内存 session store、假章节数据，无 LLM /
无 Pi 子进程）：

- ``map_mutated`` 事件：单笔/批发射、mutation_id 幂等、迟到归原 turn 不重开；
- ``refused`` 判定：零执行活动 + 未解决澄清问题（纯读，clean settle 降级）；
- ``aborted``/``refused`` 终态的 in-flight 步骤结算（skipped ≠ failed）；
- 投影 adapters：StageState/GoalNodeStatus 渲染 + terminal parity 判定。
"""
import uuid

import pytest

from app.services.harness_kernel import get_runtime
from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel import phase_adapter
from app.services.harness_kernel.models import (
    DECISION_KINDS,
    EVENT_KINDS,
    RESERVED_EVENT_KINDS,
)
from app.services.session_data import session_data_manager


def _sid() -> str:
    return f"sess-f03-{uuid.uuid4().hex[:8]}"


def _chapter(query: str, caps: list[str], *, clarification: dict | None = None) -> dict:
    intent: dict = {
        "scope": {"name": "成都市"},
        "subject": {"category": "小学"},
        "task": "分布情况",
    }
    if clarification is not None:
        intent["clarification"] = clarification
    return {
        "query": query,
        "recipe_id": "poi_distribution_overview",
        "plan_id": "plan-f03",
        "intent": intent,
        "data_requirements": [
            {
                "capability": cap,
                "purpose": f"获取{cap}",
                "status": "pending",
                "resolved_tool": f"tool_{cap}",
                "depends_on": [],
            }
            for cap in caps
        ],
        "analysis_steps": [],
    }


@pytest.fixture
async def sid():
    session_id = _sid()
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


@pytest.fixture(autouse=True)
def _reset_hk_metrics():
    hk_metrics.reset_for_tests()
    yield
    hk_metrics.reset_for_tests()


async def _plan(sid):
    from app.services.session_plan import load_session_plan

    return await load_session_plan(sid)


# ── map_mutated：发射 / 幂等 / 迟到归因 ─────────────────────────────────────


async def test_map_mutated_vocabulary_graduated():
    """map_mutated 从 RESERVED 转正为 EVENT_KINDS（发射缝已存在）。"""
    assert "map_mutated" in EVENT_KINDS
    assert "map_mutated" not in RESERVED_EVENT_KINDS
    assert set(DECISION_KINDS).issubset(set(EVENT_KINDS))


async def test_record_map_mutation_idempotent_and_attributed(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="画图")
    assert await rt.record_map_mutation(
        mutation_id="mut-1", revision=7, kind="PatchLayerStyleIntent",
        actor="pi", origin="agent", turn_id="t1",
    )
    plan = await _plan(sid)
    rows = [d for d in plan.decisions if d.kind == "map_mutated"]
    assert len(rows) == 1
    assert rows[0].turn_id == "t1"
    assert rows[0].causal_id == "mut-1"
    assert rows[0].detail["revision"] == 7
    # 同 mutation_id 重放（引擎幂等重试 / 锁重试）→ 不双写
    assert not await rt.record_map_mutation(
        mutation_id="mut-1", revision=7, kind="PatchLayerStyleIntent",
        actor="pi", origin="agent", turn_id="t1",
    )
    plan2 = await _plan(sid)
    assert len([d for d in plan2.decisions if d.kind == "map_mutated"]) == 1
    # 不同 mutation_id → 新事实
    assert await rt.record_map_mutation(
        mutation_id="mut-2", revision=8, kind="AddLayerIntent",
        actor="pi", origin="agent", turn_id="t1",
    )
    plan3 = await _plan(sid)
    assert len([d for d in plan3.decisions if d.kind == "map_mutated"]) == 2


async def test_map_mutated_late_callback_attributes_original_turn(sid):
    """回调迟到（turn 已终态）→ 事件仍归原 turn，detail.late=true，不重开。"""
    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="画图")
    await rt.end_turn("t1", host="pi", status="completed")
    await rt.begin_turn("t2", host="pi", message="下一问")
    assert await rt.record_map_mutation(
        mutation_id="mut-late", revision=9, kind="PatchLayerStyleIntent",
        actor="pi", origin="agent", turn_id="t1",
    )
    plan = await _plan(sid)
    rows = [d for d in plan.decisions if d.kind == "map_mutated"]
    assert len(rows) == 1
    assert rows[0].turn_id == "t1"  # 归原 turn，绝不落到 successor t2
    assert rows[0].detail["late"] is True
    t1 = next(t for t in plan.turns if t.turn_id == "t1")
    t2 = next(t for t in plan.turns if t.turn_id == "t2")
    assert t1.status == "completed" and t1.phase == "completed"  # 终态冻结
    assert t2.status == "running" and t2.phase == "understanding"  # successor 无污染


# ── refused：零执行活动 + 未解决澄清 ────────────────────────────────────────


async def test_refusal_candidate_open_clarification_zero_activity(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="画个图吧")
    # 注入带未解决澄清问题的章节（模拟 intent 工具此前写入）
    plan = await _plan(sid)
    plan.gis_chapter = _chapter(
        "画个图吧", [], clarification={
            "question": "要画哪个区域？", "status": "pending",
        },
    )
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)
    verdict = await rt.turn_refusal_candidate("t1")
    assert verdict is not None
    assert verdict["reason_code"] == "clarification_required"
    assert "区域" in verdict["question"]
    # 有执行活动后 → 不再是 refusal 候选
    await rt.end_turn("t1", host="pi", status="completed")
    assert await rt.turn_refusal_candidate("t1") is None  # 已终态


async def test_refusal_candidate_negative_cases(sid):
    rt = get_runtime(sid)
    # 无章节 → None
    await rt.begin_turn("t1", host="pi", message="hi")
    assert await rt.turn_refusal_candidate("t1") is None
    # 有澄清问题但 turn 有 tool 活动 → None
    plan = await _plan(sid)
    plan.gis_chapter = _chapter(
        "hi", [], clarification={"question": "哪个区域？", "status": "pending"},
    )
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)
    rec = next(t for t in plan.turns if t.turn_id == "t1")
    rec.tool_calls = 2
    await save_session_plan(rec and plan)
    assert await rt.turn_refusal_candidate("t1") is None
    # 澄清已 resolved → None
    plan2 = await _plan(sid)
    plan2.gis_chapter["intent"]["clarification"]["status"] = "resolved"
    rec2 = next(t for t in plan2.turns if t.turn_id == "t1")
    rec2.tool_calls = 0
    await save_session_plan(plan2)
    assert await rt.turn_refusal_candidate("t1") is None


async def test_refused_turn_settles_steps_as_skipped_not_failed(sid):
    """refused 终态下 in-flight 步骤落 skipped —— 执行前结束不是步失败。"""
    from app.services.harness_kernel.models import PlanStep
    from app.services.session_plan import save_session_plan

    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="q")
    plan = await _plan(sid)
    plan.gis_chapter = _chapter("q", ["cap_a"])
    plan.steps = [PlanStep(
        id="step-cap_a", capability="cap_a", tool="tool_cap_a",
        tool_binding=["tool_cap_a"], status="pending", created_at=0.0,
    )]
    await save_session_plan(plan)
    await rt.begin_step(
        tool_name="tool_cap_a", tool_call_id="call-1", turn_id="t1", host="pi",
    )
    plan = await _plan(sid)
    assert any(s.status == "running" for s in plan.steps)
    await rt.end_turn("t1", host="pi", status="refused")
    plan = await _plan(sid)
    t1 = next(t for t in plan.turns if t.turn_id == "t1")
    assert t1.status == "refused"
    assert t1.phase == "refused"  # 单一终态真相：无「终态 status + 运行相位」
    assert all(s.status == "skipped" for s in plan.steps if s.status != "pending")


async def test_aborted_turn_settles_steps_as_skipped(sid):
    from app.services.harness_kernel.models import PlanStep
    from app.services.session_plan import save_session_plan

    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="q")
    plan = await _plan(sid)
    plan.gis_chapter = _chapter("q", ["cap_a"])
    plan.steps = [PlanStep(
        id="step-cap_a", capability="cap_a", tool="tool_cap_a",
        tool_binding=["tool_cap_a"], status="pending", created_at=0.0,
    )]
    await save_session_plan(plan)
    await rt.begin_step(
        tool_name="tool_cap_a", tool_call_id="call-1", turn_id="t1", host="pi",
    )
    await rt.end_turn("t1", host="pi", status="aborted")
    plan = await _plan(sid)
    t1 = next(t for t in plan.turns if t.turn_id == "t1")
    assert t1.status == "aborted" and t1.phase == "aborted"
    assert all(s.status in ("pending", "skipped") for s in plan.steps)


# ── 投影 adapters：StageState/GoalNodeStatus 渲染 + terminal parity ─────────


def test_render_views_total_and_stable():
    """canonical 终态在 Stage/Goal 方言中渲染稳定（total 映射）。"""
    statuses = [
        "running", "completed", "failed", "cancelled",
        "aborted", "refused", "interrupted",
    ]
    for st in statuses:
        assert phase_adapter.render_stage_view(st)
        assert phase_adapter.render_goal_view(st)
    assert phase_adapter.render_stage_view("completed") == "satisfied"
    assert phase_adapter.render_stage_view("aborted") == "blocked"
    assert phase_adapter.render_goal_view("aborted") == "blocked"
    assert phase_adapter.render_stage_view("unknown-x") == "pending"
    # 终态在 Stage 方言中绝不渲染成 active（终态冻结的方言一致性）
    for st in ("completed", "failed", "cancelled", "aborted", "refused", "interrupted"):
        assert phase_adapter.render_stage_view(st) != "active"


def test_terminal_parity_verdicts():
    """终态 canonical ↔ V7 相位 parity：completed→committed，其余→aborted。"""
    assert phase_adapter.terminal_parity("completed", "committed") is None
    for term in ("failed", "cancelled", "aborted", "refused", "interrupted"):
        assert phase_adapter.terminal_parity(term, "aborted") is None
    # 运行中不判定（任务级 ≠ turn 级）
    assert phase_adapter.terminal_parity("executing", "executing") is None
    assert phase_adapter.terminal_parity("executing", "observing") is None
    # V7 缺席 → 可观测但不判错
    assert phase_adapter.terminal_parity("completed", "") == "v7_absent"
    # 失真 → 带期望值的 reason
    assert phase_adapter.terminal_parity("completed", "executing") == (
        "expected_committed_got_executing"
    )
    # task 级已前进（后续 turn 重开任务）→ 报告而非漂移
    assert phase_adapter.terminal_parity("completed", "aborted") == "task_level_advanced"


async def test_lifecycle_parity_snapshot_reads_both_domains(sid):
    rt = get_runtime(sid)
    await rt.begin_turn("t1", host="pi", message="q")
    plan = await _plan(sid)
    plan.gis_chapter = _chapter("q", [])
    plan.gis_chapter["runtime_state"] = {"schema": "runtime_state.v1", "phase": "executing"}
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)
    await rt.end_turn("t1", host="pi", status="completed")
    snap = await rt.lifecycle_parity_snapshot("t1")
    assert snap is not None
    assert snap["phase"] == "completed"
    assert snap["status"] == "completed"
    assert snap["v7_phase"] == "executing"  # 本方向不改 V7 写面：如实读出
    assert phase_adapter.terminal_parity(snap["phase"], snap["v7_phase"]) == (
        "expected_committed_got_executing"
    )


# ── 状态映射单表（bridge 结算词汇的 kernel 侧视角）─────────────────────────


def test_hk_turn_status_extended_mapping():
    """F03 结算映射：abort_source 优先于失败族；error 归失败族。"""
    from app.agent_pi_bridge import _hk_turn_status

    assert _hk_turn_status(
        cancelled=False, timed_out=False, send_failed=False,
        process_died=False, abort_source="policy",
    ) == "aborted"
    assert _hk_turn_status(
        cancelled=False, timed_out=False, send_failed=False,
        process_died=False, abort_source="system",
    ) == "aborted"
    assert _hk_turn_status(
        cancelled=False, timed_out=False, send_failed=False,
        process_died=False, abort_source="user",
    ) == "cancelled"
    # 显式 CancelledError（客户端断开）优先于 abort 来源
    assert _hk_turn_status(
        cancelled=True, timed_out=False, send_failed=False,
        process_died=False, abort_source="policy",
    ) == "cancelled"
    # 未分类异常归失败族（P1：不再伪装成 completed）
    assert _hk_turn_status(
        cancelled=False, timed_out=False, send_failed=False,
        process_died=False, error=True,
    ) == "failed"
    # 超时 + 用户取消 → cancelled（用户意图优先于机械超时）
    assert _hk_turn_status(
        cancelled=False, timed_out=True, send_failed=False,
        process_died=False, abort_source="user",
    ) == "cancelled"


# ── mutation facade → kernel 发射缝（真实引擎接线）──────────────────────────


async def test_apply_gis_mutation_emits_map_mutated_to_kernel_envelope(sid):
    """生产发射缝接线：apply_gis_mutation 成功 → 信封出现 map_mutated，
    归因信 envelope.turn_id；批路径恰一条批级事件。"""
    from app.services.mapspec.lifecycle_engine import (
        PatchLayerPresentationIntent,
        UpsertLayerIntent,
        mapspec_lifecycle_engine,
    )
    from app.services.gis_world_state import (
        apply_gis_mutation,
        apply_gis_mutation_batch,
    )

    # 先落一层（走引擎直通，不产生 kernel 事件）
    await mapspec_lifecycle_engine.apply_mutation(
        sid,
        UpsertLayerIntent(
            layer={"id": "l1", "type": "circle", "source": "s1",
                   "paint": {"color": "#112233"}},
            source_data={"type": "FeatureCollection", "features": []},
        ),
    )
    await get_runtime(sid).begin_turn("t-mut", host="pi", message="改样式")
    res = await apply_gis_mutation(
        sid,
        PatchLayerPresentationIntent(layer_id="l1", visible=False),
        origin="agent",
        actor="pi",
        turn_id="t-mut",
    )
    assert not res.is_error
    plan = await _plan(sid)
    rows = [d for d in plan.decisions if d.kind == "map_mutated"]
    assert len(rows) == 1 and rows[0].turn_id == "t-mut"
    # 批路径：一个事务 → 恰一条批级 map_mutated（独立 mutation_id）
    batch_res = await apply_gis_mutation_batch(
        sid,
        [PatchLayerPresentationIntent(layer_id="l1", visible=True)],
        origin="agent",
        actor="pi",
        turn_id="t-mut",
    )
    assert batch_res.committed
    plan = await _plan(sid)
    rows = [d for d in plan.decisions if d.kind == "map_mutated"]
    assert len(rows) == 2
    assert {r.detail.get("kind") for r in rows} == {
        "PatchLayerPresentationIntent", "GISMutationBatch",
    }


async def test_map_mutated_emit_skips_silently_without_envelope():
    """无 SessionPlan 信封的会话（纯前端 user 会话）：发射为 no-op，
    不强制创建信封（envelope 生命周期归 begin_turn 所有）。"""
    from app.services.mapspec.lifecycle_engine import (
        PatchLayerPresentationIntent,
        UpsertLayerIntent,
        mapspec_lifecycle_engine,
    )
    from app.services.gis_world_state import apply_gis_mutation

    sid = f"sess-noenv-{uuid.uuid4().hex[:8]}"
    try:
        await mapspec_lifecycle_engine.apply_mutation(
            sid,
            UpsertLayerIntent(
                layer={"id": "l1", "type": "circle", "source": "s1",
                       "paint": {"color": "#112233"}},
                source_data={"type": "FeatureCollection", "features": []},
            ),
        )
        res = await apply_gis_mutation(
            sid,
            PatchLayerPresentationIntent(layer_id="l1", visible=False),
            origin="agent", actor="pi",
        )
        assert not res.is_error
        from app.services.session_plan import load_session_plan

        assert await load_session_plan(sid) is None  # 信封未被副作用创建
    finally:
        await session_data_manager.clear_session(sid)
