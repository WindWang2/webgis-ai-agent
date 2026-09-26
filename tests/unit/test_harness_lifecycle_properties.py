"""F03 lifecycle property / chaos tests（ADR-0204-f03 D6 重放不变量）。

仓规生成式纪律（tests/fixtures/generative.py）：seeded ``random.Random``、
有界迭代、零新依赖（不用 hypothesis）—— 同 seed 同序列，失败可精确重放。

钉住的重放不变量（对任意触发序列 / 交错 / 迟到回调 / 重启 / supersede）：

1. INV-FROZEN   终态冻结：terminal 相位无出边；终态 status + 运行相位不共存。
2. INV-MONOTON  ``event_seq`` 信封内严格单调（跨重启 / supersede 亦然）。
3. INV-BOUNDED  phase_history / decisions 环有界。
4. INV-IDEM     因果事件按 event_id 幂等：重复回调 / 锁重试不双写。
5. INV-LATE     迟到事实归原 turn，不重开、不推进 successor。
6. INV-ISOLATE  多会话交错零串号（envelope 互不可见）。
"""
import random
import uuid

import pytest

from app.services.harness_kernel import get_runtime
from app.services.harness_kernel import metrics as hk_metrics
from app.services.harness_kernel.models import (
    EVENT_KINDS,
    MAX_DECISIONS,
    MAX_PHASE_HISTORY,
    PHASE_TRANSITIONS,
    TERMINAL_PHASES,
    STATUS_TO_TRIGGER,
)
from app.services.session_data import session_data_manager
from app.services.session_plan import load_session_plan


def _sid(tag: str = "prop") -> str:
    return f"sess-{tag}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _reset_hk_metrics():
    hk_metrics.reset_for_tests()
    yield
    hk_metrics.reset_for_tests()


@pytest.fixture
async def sid():
    session_id = _sid()
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


def _invariants(plan, *, seq_floor: int = 0) -> None:
    """对信封断言全部重放不变量（INV-FROZEN/MONOTON/BOUNDED）。"""
    seqs = [d.seq for d in plan.decisions if d.seq]
    assert seqs == sorted(seqs), "INV-MONOTON: event_seq 必须单调"
    assert all(s > seq_floor for s in seqs), "INV-MONOTON: seq 不得回退"
    for t in plan.turns:
        assert len(t.phase_history) <= MAX_PHASE_HISTORY, "INV-BOUNDED: phase_history"
        if t.status in TERMINAL_PHASES:
            assert t.phase in TERMINAL_PHASES, (
                f"INV-FROZEN: 终态 status={t.status} 配运行相位 {t.phase}"
            )
    assert len(plan.decisions) <= MAX_DECISIONS, "INV-BOUNDED: journal 环"


def _turn_record(plan, turn_id):
    return next((t for t in plan.turns if t.turn_id == turn_id), None)


# ── seeded generative：任意合法触发序列下的不变量 ───────────────────────────


def _legal_trigger_sequence(rng: random.Random, length: int) -> list[str]:
    """从转移表出发生成必然合法的触发序列（沿表随机游走，不含终态边）。"""
    triggers = sorted({t for (_s, t) in PHASE_TRANSITIONS})
    phase = "created"
    seq: list[str] = []
    for _ in range(length):
        moves = [
            t for t in triggers
            if (phase, t) in PHASE_TRANSITIONS
            and PHASE_TRANSITIONS[(phase, t)] not in TERMINAL_PHASES
        ]
        if not moves:
            break
        t = rng.choice(moves)
        seq.append(t)
        phase = PHASE_TRANSITIONS[(phase, t)]
    return seq


async def test_seeded_random_sequences_preserve_invariants(sid):
    """随机（seeded）合法触发游走：每步后全部重放不变量成立。"""
    rt = get_runtime(sid)
    rng = random.Random(0xF03)
    turn_id = "t-walk"
    await rt.begin_turn(turn_id, host="pi", message="prop walk")
    for i in range(3):
        seq = _legal_trigger_sequence(rng, length=24)
        for trigger in seq:
            await rt.advance_turn_phase(turn_id, trigger, host="pi")
            plan = await load_session_plan(sid)
            record = _turn_record(plan, turn_id)
            assert record is not None
            assert record.phase in set(TERMINAL_PHASES) | {
                "created", "understanding", "planning", "qualifying",
                "executing", "observing", "verifying", "repairing", "replanning",
            }
            _invariants(plan)
        # 三轮游走间把 turn 收尾重开，游走再入（表的自环/回路可重复进入）
        status = "completed" if i % 2 == 0 else "failed"
        await rt.end_turn(turn_id, host="pi", status=status)
        plan = await load_session_plan(sid)
        rec = _turn_record(plan, turn_id)
        assert rec.phase == status and rec.status == status  # INV-FROZEN
        _invariants(plan)
        turn_id = f"t-walk-{i}"
        await rt.begin_turn(turn_id, host="pi", message="prop walk next")


async def test_illegal_triggers_never_move_phase_but_are_audited(sid):
    """随机非法 (phase, trigger) 对：相位不动、phase_refused 行落地、
    event_seq 仍单调（fail-closed 可观测、绝不静默也绝不致命）。"""
    rt = get_runtime(sid)
    rng = random.Random(0xF03B)
    turn_id = "t-refused"
    await rt.begin_turn(turn_id, host="pi", message="probe")
    plan = await load_session_plan(sid)
    for _ in range(12):
        record = _turn_record(plan, turn_id)
        current = record.phase
        illegal = [
            t for (src, _t), t in sorted(PHASE_TRANSITIONS.items())
            if src != current and t not in STATUS_TO_TRIGGER.values()
        ]
        if not illegal:
            break
        trigger = rng.choice(illegal)
        if PHASE_TRANSITIONS.get((current, trigger)) is not None:
            continue  # 随机到的碰巧合法 —— 跳过本轮
        await rt.advance_turn_phase(turn_id, trigger, host="pi")
        plan = await load_session_plan(sid)
        record = _turn_record(plan, turn_id)
        assert record.phase == current, "表外推进不得改变相位"
        _invariants(plan)
    plan = await load_session_plan(sid)
    assert any(d.kind == "phase_refused" for d in plan.decisions), (
        "fail-closed 必须留审计行"
    )


# ── 幂等 redelivery fuzz：重复回调 / 锁重试交错 ─────────────────────────────


async def test_duplicate_redelivery_fuzz_idempotent(sid):
    """seeded fuzz：tool 事件重复投递任意次 —— 事件行数与单投一致。"""
    rt = get_runtime(sid)
    rng = random.Random(0xF03C)
    await rt.begin_turn("t1", host="pi", message="fuzz")
    plan = await load_session_plan(sid)
    plan.gis_chapter = {
        "query": "fuzz", "plan_id": "p", "recipe_id": "r",
        "intent": {}, "data_requirements": [], "analysis_steps": [],
    }
    from app.services.session_plan import save_session_plan

    await save_session_plan(plan)
    deliveries: list[tuple[str, bool]] = []
    for i in range(16):
        call_id = f"call-{i % 6}"  # 6 个因果 id，随机重复投递
        success = rng.random() < 0.7
        deliveries.append((call_id, success))
        await rt.apply_tool_evidence(
            "webgis_map_intent" if i == 0 else "untracked_read_tool",
            {"ok": success},
            success=success,
            geojson_ref=f"ref:{call_id}" if success else None,
            tool_call_id=call_id,
            turn_id="t1",
            host="pi",
        )
    plan = await load_session_plan(sid)
    causal_ids = [d.event_id for d in plan.decisions if d.causal_id]
    assert len(causal_ids) == len(set(causal_ids)), "INV-IDEM: 因果事件不重复"
    _invariants(plan)
    # 同一 tool_call_id 的重复 (kind) 行不超过一次
    for call_id in {c for c, _s in deliveries}:
        started = [
            d for d in plan.decisions
            if d.kind == "tool_started" and d.causal_id == call_id
        ]
        assert len(started) <= 1


# ── 多会话 chaos：交错 + 迟到 + 重启 + supersede ────────────────────────────


async def test_multi_session_chaos_isolation_and_late_attribution():
    """两会话交错：迟到回调跨 successor、重启中断、supersede ——
    会话隔离、终态冻结、迟到归原 turn 全部保持。"""
    sid_a, sid_b = _sid("chaos-a"), _sid("chaos-b")
    try:
        rt_a, rt_b = get_runtime(sid_a), get_runtime(sid_b)
        await rt_a.begin_turn("a1", host="pi", message="A1")
        await rt_b.begin_turn("b1", host="pi", message="B1")
        await rt_a.end_turn("a1", host="pi", status="completed")
        await rt_b.begin_turn("b2", host="pi", message="B2（successor）")
        # 迟到回调：a1 的工具结果在 a1 终态 + B 会话推进后才到
        await rt_a.record_late_callback(
            tool_name="slow_tool", tool_call_id="call-a1-late",
            callback_turn_id="a1", active_turn_id="b2", host="pi",
        )
        await rt_b.end_turn("b2", host="pi", status="failed")
        # 重启路径：a 会话再次 begin_turn（无 running turn → 无中断）
        await rt_a.begin_turn("a2", host="pi", message="A2")
        # supersede：b 会话换目标（envelope 重建迁移 seq）
        await rt_b.apply_tool_evidence(
            "webgis_map_intent",
            {"chapter": {"query": "B-新目标", "plan_id": "p2", "recipe_id": "r2",
                          "intent": {}, "data_requirements": [],
                          "analysis_steps": []}},
            success=True,
            tool_call_id="call-b-intent-2",
            turn_id="b2",
            host="pi",
        )
        plan_a, plan_b = (
            await load_session_plan(sid_a),
            await load_session_plan(sid_b),
        )
        # INV-ISOLATE：a 的事件不进 b 的 envelope，反之亦然
        a_turns = {t.turn_id for t in plan_a.turns}
        b_turns = {t.turn_id for t in plan_b.turns}
        assert "a1" in a_turns and "a2" in a_turns
        assert not (a_turns & b_turns), "会话间零串号"
        # INV-LATE：迟到事件归 a1，b2/a2 无污染
        late_rows = [
            d for d in plan_a.decisions
            if d.kind == "tool_late" and d.causal_id == "call-a1-late"
        ]
        assert len(late_rows) == 1 and late_rows[0].turn_id == "a1"
        a1 = _turn_record(plan_a, "a1")
        assert a1.status == "completed" and a1.phase == "completed"
        # INV-FROZEN/MONOTON 双信封
        _invariants(plan_a)
        _invariants(plan_b)
    finally:
        await session_data_manager.clear_session(sid_a)
        await session_data_manager.clear_session(sid_b)


async def test_restart_marks_running_turn_interrupted_with_phase_walk(sid):
    """restart/resume 不变量：仍 running 的旧 turn 走表终态化（interrupted），
    新 turn 的 begin 事件 seq 仍单调。"""
    rt = get_runtime(sid)
    await rt.begin_turn("t-crash", host="pi", message="会话重启前")
    plan = await load_session_plan(sid)
    seq_before = int(plan.event_seq or 0)
    await rt.begin_turn("t-next", host="pi", message="重启后首个 turn")
    plan = await load_session_plan(sid)
    crashed = _turn_record(plan, "t-crash")
    assert crashed.status == "interrupted"
    assert crashed.phase == "interrupted"  # 相位走表终态化，无混合真相
    assert int(plan.event_seq or 0) >= seq_before  # INV-MONOTON
    assert plan.recovery.resumed_from_turn_id == "t-crash"
    _invariants(plan)


def test_event_vocabulary_closed_under_emit_paths():
    """全部生产发射的 kind 都在 EVENT_KINDS 封闭词表内（含 F03 map_mutated）。"""
    for kind in (
        "phase_changed", "phase_refused", "tool_late", "map_mutated",
        "tool_started", "tool_succeeded", "tool_failed", "goal_evaluated",
    ):
        assert kind in EVENT_KINDS
