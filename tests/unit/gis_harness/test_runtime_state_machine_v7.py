"""HarnessRuntime 状态机（V7 / ADR-0130 D1）回归锁。

不变式：
1. 阶段 = 权威事实的确定性派生 —— 同输入同阶段，删块重建值不变；
2. 封闭词表：阶段 12 态、触发封闭、转移表穷举可测；
3. 转移记录环形有界（≤MAX_TRANSITIONS），非法表外组合可观测
   （DERIVED_OUTSIDE_TABLE）不静默；
4. suspended 是覆盖旗标：turn_settled 置位、其余触发清除；
5. commit 标记：幂等（同成品 revision no-op）、成品 revision 前进自动失效；
6. 服务入口：gate 幂等跳过、事实变化打破门、锁内漂移守卫；
7. 投影行有界（≤480）。
"""
from __future__ import annotations

import shutil
import uuid
from typing import Any, Dict

import pytest

from app.services.gis_harness.runtime_state_machine import (
    LEGAL_TRANSITIONS,
    MAX_TRANSITIONS,
    RUNTIME_STATE_KEY,
    RuntimePhase,
    TRIGGERS,
    RuntimeStateBlock,
    commit_runtime_context,
    derive_runtime_phase,
    derive_runtime_state,
    format_runtime_line,
    runtime_phase_of,
    validate_transition,
)

# ── 章节构造器（最小 MapProductPlan dict 形状）──────────────────────────


def _chapter(
    *,
    plan_id: str = "plan7",
    query: str = "成都学校分布",
    req_status: str = "pending",
    step_status: str = "pending",
    product: Dict[str, Any] | None = None,
    stale_stages: int = 0,
    plan_runtime: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    chapter: Dict[str, Any] = {
        "plan_id": plan_id,
        "query": query,
        "data_requirements": [
            {"capability": "cap_req_0", "status": req_status},
        ],
        "analysis_steps": [
            {"capability": "cap_step_0", "status": step_status},
        ],
    }
    if product is not None:
        chapter["map_product"] = product
    if stale_stages:
        chapter["workflow_instance"] = {
            "stages": [
                {"capability": f"s{i}", "state": "stale"} for i in range(stale_stages)
            ]
        }
    if plan_runtime is not None:
        chapter["plan_runtime"] = plan_runtime
    return chapter


def _ready_product(*, checked_revision: int = 3) -> Dict[str, Any]:
    return {
        "status": "complete",
        "product_verdict": "READY",
        "task_complete": True,
        "checked_revision": checked_revision,
    }


# ── 词表封闭 ─────────────────────────────────────────────────────────────


def test_phase_vocabulary_closed():
    assert len(RuntimePhase) == 12
    assert all(p.value for p in RuntimePhase)
    assert all(t for t in TRIGGERS)
    assert len(set(TRIGGERS)) == len(TRIGGERS)


def test_transition_table_wellformed():
    for (src, trigger), dst in LEGAL_TRANSITIONS.items():
        assert src in {p.value for p in RuntimePhase}, (src, trigger)
        assert dst in {p.value for p in RuntimePhase}, (dst, trigger)
        assert trigger in TRIGGERS, (src, trigger)
    # 主线完整可走：idle → … → committed
    assert validate_transition("idle", "intent_parsed", "intent_resolved")
    assert validate_transition("intent_resolved", "plan_compiled", "plan_ready")
    assert validate_transition("plan_ready", "execution_started", "executing")
    assert validate_transition("executing", "execution_settled", "critiquing")
    assert validate_transition("critiquing", "verdict_ready", "finalizing")
    assert validate_transition("finalizing", "context_committed", "committed")


def test_validate_transition_fail_closed():
    assert not validate_transition("idle", "context_committed", "committed")
    assert not validate_transition("committed", "execution_started", "executing")


# ── 派生规则 ─────────────────────────────────────────────────────────────


def test_derive_idle_and_intent():
    assert derive_runtime_phase({}) == RuntimePhase.IDLE.value
    assert derive_runtime_phase({"query": "x"}) == (
        RuntimePhase.INTENT_RESOLVED.value)
    # 只有 plan_id 无行（组件-only）→ executing（执行面）
    assert derive_runtime_phase({"plan_id": "p"}) == (
        RuntimePhase.EXECUTING.value)


def test_derive_plan_ready_executing_critiquing():
    ch = _chapter()
    assert derive_runtime_phase(ch) == RuntimePhase.PLAN_READY.value
    ch["analysis_steps"][0]["status"] = "complete"
    assert derive_runtime_phase(ch) == RuntimePhase.EXECUTING.value
    ch["data_requirements"][0]["status"] = "complete"
    # DAG 终态、无成品 → critiquing
    assert derive_runtime_phase(ch) == RuntimePhase.CRITIQUING.value


def test_derive_priority_committed_over_all():
    ch = _chapter(product=_ready_product())
    ch[RUNTIME_STATE_KEY] = {
        "schema": "runtime_state.v1",
        "context_commit": {"product_checked_revision": 3},
    }
    assert derive_runtime_phase(ch) == RuntimePhase.COMMITTED.value
    # READY 但未提交 → finalizing
    ch2 = _chapter(product=_ready_product())
    assert derive_runtime_phase(ch2) == RuntimePhase.FINALIZING.value


def test_derive_aborted_requires_unresolved_and_exhausted():
    product = {"status": "needs_repair", "product_verdict": "NEEDS_REPAIR"}
    ch = _chapter(req_status="complete", step_status="complete", product=product)
    loops = {"deepen": 0, "requalify": 0, "repair": 0}
    # 预算有余 → 不 abort（落 repairing 观察面由下游分支判）
    assert derive_runtime_phase(
        ch, recovery_loops={"deepen": 1, "repair": 1}) != (
        RuntimePhase.ABORTED.value)
    # 全 0 → aborted
    assert derive_runtime_phase(ch, recovery_loops=loops) == (
        RuntimePhase.ABORTED.value)
    # READY + 预算尽 ≠ aborted（任务已完成）
    ch_done = _chapter(product=_ready_product())
    assert derive_runtime_phase(ch_done, recovery_loops=loops) == (
        RuntimePhase.FINALIZING.value)


def test_derive_repairing_observing_replanning():
    repairable = {
        "status": "needs_repair",
        "product_verdict": "NEEDS_REPAIR",
        "repair_plan": {"actions": [{"code": "fix_layer"}, {"code": "fix_comp"}]},
    }
    ch = _chapter(req_status="complete", step_status="complete", product=repairable)
    assert derive_runtime_phase(ch) == RuntimePhase.REPAIRING.value
    # 无可修复项、等再取证 → observing
    render_only = {
        "status": "needs_repair",
        "product_verdict": "NEEDS_REPAIR",
        "summary": "2 render findings await runtime re-observation",
    }
    ch2 = _chapter(req_status="complete", step_status="complete", product=render_only)
    assert derive_runtime_phase(ch2) == RuntimePhase.OBSERVING.value
    # replan 挂起 → replanning（优先于 repairing）
    ch3 = _chapter(req_status="complete", step_status="complete",
                   product=repairable, plan_runtime={"replan_pending": True})
    assert derive_runtime_phase(ch3) == RuntimePhase.REPLANNING.value


def test_derive_recomputing_debt():
    ch = _chapter(step_status="complete", stale_stages=2)
    # 有进展 + stale 债 → recomputing
    assert derive_runtime_phase(ch) == RuntimePhase.RECOMPUTING.value


def test_derive_deterministic_and_rebuildable():
    ch = _chapter(step_status="complete", product={
        "status": "complete", "product_verdict": "READY_WITH_WARNINGS",
        "task_complete": True, "checked_revision": 1,
    })
    a = derive_runtime_phase(ch)
    b = derive_runtime_phase(ch)
    assert a == b
    # 删块重建等价（runtime_phase_of 对缺块章节现场派生）
    assert runtime_phase_of(ch) == a


def test_derive_loops_projection_into_state():
    ch = _chapter()
    block = derive_runtime_state(
        ch, trigger="plan_compiled", recovery_loops={"repair": 2, "deepen": 1})
    assert block.loops_remaining == {"repair": 2, "deepen": 1}


# ── 转移记录 ─────────────────────────────────────────────────────────────


def test_transitions_recorded_and_bounded():
    ch = _chapter()
    block = RuntimeStateBlock()
    # 事实随触发推进（派生器只认事实，不认触发本身）。
    steps = (
        ("plan_compiled", None),
        ("execution_started", "running"),
        ("execution_progressed", "complete"),
        ("execution_settled", "complete"),
    )
    for i, (trigger, step_status) in enumerate(steps):
        if step_status is not None:
            ch["analysis_steps"][0]["status"] = step_status
        block = derive_runtime_state(
            ch, trigger=trigger, stored=block.to_bounded_dict(),
            recovery_loops={})
    # 行事实：req 仍 pending + step complete → executing（非终态）
    assert block.phase == RuntimePhase.EXECUTING.value
    assert [t.trigger for t in block.transitions] == [
        "plan_compiled", "execution_started",
        "execution_progressed", "execution_settled"]
    # 环形上界（交替触发 → 每次都记录；验证环形裁剪最老记录）
    for i in range(MAX_TRANSITIONS + 8):
        trigger = "execution_progressed" if i % 2 else "observation_received"
        block = derive_runtime_state(
            ch, trigger=trigger, stored=block.to_bounded_dict(),
            recovery_loops={})
    assert len(block.transitions) == MAX_TRANSITIONS
    # 同触发 + 同阶段 → 去重（不刷记录）
    before = len(block.transitions)
    rev = block.phase_revision
    block = derive_runtime_state(
        ch, trigger=block.last_trigger, stored=block.to_bounded_dict(),
        recovery_loops={})
    assert len(block.transitions) == before
    assert block.phase_revision == rev


def test_outside_table_transition_observable_not_silent():
    # stored 阶段 committed、行事实部分推进 → 派生 executing（表外组合：
    # committed → executing 无合法边）——照常记录但 reason_code 标记，
    # 不静默丢失。
    stored = {
        "schema": "runtime_state.v1",
        "phase": "committed",
        "phase_revision": 3,
    }
    ch = _chapter(step_status="complete")
    block = derive_runtime_state(
        ch, trigger="execution_progressed", stored=stored, recovery_loops={})
    assert block.phase == RuntimePhase.EXECUTING.value
    last = block.transitions[-1]
    assert last.reason_code == "DERIVED_OUTSIDE_TABLE"
    assert last.from_phase == "committed"


def test_same_phase_no_transition_record():
    ch = _chapter()
    first = derive_runtime_state(ch, trigger="plan_compiled", recovery_loops={})
    again = derive_runtime_state(
        ch, trigger="plan_compiled", stored=first.to_bounded_dict(),
        recovery_loops={})
    # 同阶段 + 同触发：revision 不前进、不新增记录
    assert again.phase_revision == first.phase_revision
    assert len(again.transitions) == 1


# ── suspended 旗标 ───────────────────────────────────────────────────────


def test_suspended_settle_and_clear():
    ch = _chapter(step_status="complete")
    settled = derive_runtime_state(
        ch, trigger="execution_settled", turn_settled=True, recovery_loops={})
    assert settled.suspended is True
    resumed = derive_runtime_state(
        ch, trigger="observation_received", turn_settled=False,
        stored=settled.to_bounded_dict(), recovery_loops={})
    assert resumed.suspended is False
    # 终态不挂起
    done = _chapter(product=_ready_product())
    done_block = derive_runtime_state(
        done, trigger="verdict_ready", turn_settled=True, recovery_loops={})
    assert done_block.suspended is False


# ── commit 标记 ──────────────────────────────────────────────────────────


def test_commit_marker_invalidated_by_product_advance():
    stored = {
        "schema": "runtime_state.v1",
        "phase": "finalizing",
        "phase_revision": 5,
        "context_commit": {"product_checked_revision": 3},
    }
    # 成品 revision 已推进到 4 → 旧标记失效
    ch = _chapter(product=_ready_product(checked_revision=4))
    block = derive_runtime_state(
        ch, trigger="verdict_ready", stored=stored, recovery_loops={})
    assert block.context_commit.product_checked_revision == 0
    # revision 一致 → 保留
    ch_same = _chapter(product=_ready_product(checked_revision=3))
    block_same = derive_runtime_state(
        ch_same, trigger="verdict_ready", stored=stored, recovery_loops={})
    assert block_same.context_commit.product_checked_revision == 3


# ── 投影行 ───────────────────────────────────────────────────────────────


def test_format_runtime_line_bounded_and_informative():
    ch = _chapter()
    ch[RUNTIME_STATE_KEY] = {
        "schema": "runtime_state.v1",
        "phase": "executing",
        "phase_revision": 7,
        "suspended": True,
        "loops_remaining": {"repair": 2, "deepen": 1},
        "recompute_debt": 3,
    }
    line = format_runtime_line(ch)
    assert line.startswith("[GIS Runtime] phase=executing rev=7")
    assert "suspended=true" in line
    assert "debt=stale:3" in line
    assert len(line) <= 480
    # 缺块零漂移
    assert format_runtime_line({}) == ""


def test_gate_fingerprint_stable_and_sensitive():
    from app.services.gis_harness.runtime_state_machine import (
        _input_gate_fingerprint,
    )
    ch = _chapter()
    g1 = _input_gate_fingerprint(ch, {"repair": 2}, 0)
    g2 = _input_gate_fingerprint(ch, {"repair": 2}, 0)
    assert g1 == g2
    assert _input_gate_fingerprint(ch, {"repair": 1}, 0) != g1
    assert _input_gate_fingerprint(ch, {"repair": 2}, 1) != g1


# ── 服务入口（真实 session store，无 Redis —— 内存实现）──────────────────


@pytest.fixture()
async def clean_session():
    sid = f"rsm-svc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    try:
        from app.lib.data.large_data import BASE_STORAGE_DIR

        d = BASE_STORAGE_DIR / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001 — 清理失败不影响测试结果
        pass


async def _save_plan(sid: str, chapter: Dict[str, Any]) -> None:
    from app.services.session_plan import SessionPlan, save_session_plan

    await save_session_plan(SessionPlan(
        envelope_id=f"env-{chapter.get('plan_id') or 'x'}",
        session_id=sid,
        user_goal=str(chapter.get("query") or "goal"),
        gis_chapter=chapter,
    ))


@pytest.mark.asyncio
async def test_service_persists_and_gate_skips(clean_session):
    from app.services.session_plan import load_session_plan

    from app.services.gis_harness.runtime_state_machine import (
        maybe_update_runtime_state,
    )

    ch = _chapter()
    await _save_plan(clean_session, ch)
    block = await maybe_update_runtime_state(
        clean_session, trigger="plan_compiled", reason="test")
    assert block is not None
    assert block["schema"] == "runtime_state.v1"
    assert block["phase"] == "plan_ready"
    fresh = await load_session_plan(clean_session)
    assert fresh.gis_chapter[RUNTIME_STATE_KEY]["phase"] == "plan_ready"
    # 门：同输入 → 跳过
    again = await maybe_update_runtime_state(
        clean_session, trigger="plan_compiled", reason="test")
    assert again is None


@pytest.mark.asyncio
async def test_service_fact_change_breaks_gate(clean_session):
    from app.services.session_plan import load_session_plan

    from app.services.gis_harness.runtime_state_machine import (
        maybe_update_runtime_state,
    )

    ch = _chapter()
    await _save_plan(clean_session, ch)
    first = await maybe_update_runtime_state(
        clean_session, trigger="plan_compiled", reason="t0")
    assert first is not None
    # 行推进 → 门打破 → executing
    plan = await load_session_plan(clean_session)
    plan.gis_chapter["analysis_steps"][0]["status"] = "complete"
    await _save_plan(clean_session, plan.gis_chapter)
    second = await maybe_update_runtime_state(
        clean_session, trigger="execution_progressed", reason="t1")
    assert second is not None
    assert second["phase"] == "executing"
    assert second["phase_revision"] > first["phase_revision"]


@pytest.mark.asyncio
async def test_service_suspended_on_turn_settle(clean_session):
    from app.services.gis_harness.runtime_state_machine import (
        maybe_update_runtime_state,
    )

    ch = _chapter(step_status="complete")
    await _save_plan(clean_session, ch)
    block = await maybe_update_runtime_state(
        clean_session, trigger="execution_settled",
        turn_settled=True, reason="turn")
    assert block is not None
    assert block["suspended"] is True


@pytest.mark.asyncio
async def test_commit_context_idempotent_and_gated(clean_session):
    from app.services.gis_harness.runtime_state_machine import (
        maybe_update_runtime_state,
    )

    ch = _chapter(product=_ready_product())
    await _save_plan(clean_session, ch)
    await maybe_update_runtime_state(
        clean_session, trigger="verdict_ready", reason="final")
    first = await commit_runtime_context(
        clean_session, domains_digest="abc123", anchor_saved=False)
    assert first is not None
    assert first["context_commit"]["product_checked_revision"] == 3
    assert first["context_commit"]["domains_digest"] == "abc123"
    # 幂等：同成品 no-op
    again = await commit_runtime_context(clean_session, domains_digest="abc123")
    assert again is None
    # 非 READY 章节 → 拒绝
    ch2 = _chapter()
    await _save_plan(clean_session, ch2)
    assert await commit_runtime_context(clean_session) is None
