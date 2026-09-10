"""PlanRuntime（V7 ADR-0130 D2）回归锁：计划版本 / 重规划驱动 / 失败种子。

不变式：
1. compute_plan_fingerprint 覆盖 goal/rows/contract 三面 —— 任一变化即
   新指纹，同输入同指纹；
2. 版本推进：指纹变化 → version+1 + 历史（≤8）/回滚点（≤4）环形；
   指纹不变 → derive 返回 None（幂等）；
3. replan 生命周期：request_replan 置 pending + durable 记账 → 计划事实
   变化（版本推进）清除 pending（replan 已消费）；预算耗尽 → 诚实 abort；
4. 失败种子：failed 行 → recompute=失败行+下游闭包；非下游 satisfied →
   reuse；无失败 → 空 plan；
5. LOOP_BUDGETS.replan 与 request_replan 驱动点共存（预算无驱动 = 不可达
   预算，V6 R1 M4 红线 —— 测试钉死）。
"""
from __future__ import annotations

import shutil
import uuid
from typing import Any, Dict

import pytest

from app.services.gis_harness.durable_context import (
    LOOP_BUDGETS,
    load_recovery_state,
)
from app.services.gis_harness.plan_runtime import (
    PLAN_RUNTIME_KEY,
    REPLAN_LOOP,
    compute_plan_fingerprint,
    derive_plan_runtime,
    format_plan_runtime_line,
    request_replan,
    seed_recompute_from_failures,
)


def _chapter(
    *,
    step_status: str = "pending",
    depends: list | None = None,
) -> Dict[str, Any]:
    return {
        "plan_id": "plan9",
        "query": "成都学校分布分析",
        "data_requirements": [
            {"capability": "fetch_schools", "status": "complete"},
        ],
        "analysis_steps": [
            {"capability": "spatial_join", "status": step_status,
             "depends_on": list(depends or ["fetch_schools"])},
            {"capability": "admin_aggregation", "status": "pending",
             "depends_on": ["spatial_join"]},
        ],
    }


# ── 指纹 ─────────────────────────────────────────────────────────────────


def test_fingerprint_covers_goal_rows_contract():
    ch = _chapter()
    fp1 = compute_plan_fingerprint(ch)
    assert fp1 == compute_plan_fingerprint(ch)  # 同输入同指纹
    ch["query"] = "改成青羊区"
    assert compute_plan_fingerprint(ch) != fp1  # goal 变化
    ch2 = _chapter(step_status="complete")
    assert compute_plan_fingerprint(ch2) != fp1  # 行状态变化
    ch3 = _chapter()
    ch3["workflow_contract"] = {"obligations": [{"code": "X"}]}
    assert compute_plan_fingerprint(ch3) != fp1  # 契约变化


# ── 版本推进 ─────────────────────────────────────────────────────────────


def test_derive_version_advances_on_fingerprint_change():
    ch = _chapter()
    first = derive_plan_runtime(ch, reason="init", stored=None)
    assert first is not None and first.version == 1
    assert first.replan_pending is False
    # 指纹不变 → None（幂等）
    assert derive_plan_runtime(ch, reason="noop", stored=first.to_bounded_dict()) is None
    # 行变化 → version 2 + 历史（记录被替换版本与替换原因）+ 回滚点
    ch["analysis_steps"][0]["status"] = "complete"
    second = derive_plan_runtime(ch, reason="row_done", stored=first.to_bounded_dict())
    assert second is not None and second.version == 2
    assert [h.reason for h in second.history] == ["row_done"]
    assert second.history[0].version == 1
    assert second.history[0].fingerprint == first.fingerprint


def test_derive_history_and_rollback_bounded():
    ch = _chapter()
    block = derive_plan_runtime(ch, stored=None)
    for i in range(20):
        ch["query"] = f"goal v{i}"
        block = derive_plan_runtime(ch, reason=f"r{i}", stored=block.to_bounded_dict())
    assert block.version == 21
    assert len(block.history) == 8
    assert len(block.rollback_points) == 4


def test_version_advance_clears_replan_pending():
    ch = _chapter()
    block = derive_plan_runtime(ch, stored=None)
    stored = block.to_bounded_dict()
    stored["replan_pending"] = True
    stored["replan_reason"] = "repair unreachable"
    stored["replan_from_verdict"] = "NEEDS_REPAIR"
    ch["analysis_steps"][0]["status"] = "complete"  # 计划事实变化
    advanced = derive_plan_runtime(ch, stored=stored)
    assert advanced is not None
    assert advanced.replan_pending is False
    assert advanced.replan_reason == ""


# ── 失败种子最小重算 ─────────────────────────────────────────────────────


def test_seed_empty_without_failures():
    assert seed_recompute_from_failures(_chapter())["recompute"] == []
    assert seed_recompute_from_failures({})["recompute"] == []


def test_seed_failed_row_and_downstream_closure():
    ch = _chapter(step_status="failed")
    plan = seed_recompute_from_failures(ch)
    # spatial_join failed → recompute = spatial_join + admin_aggregation（下游）
    assert set(plan["recompute"]) == {"spatial_join", "admin_aggregation"}
    # fetch_schools satisfied 且不在污染面 → reuse
    assert plan["reuse"] == ["fetch_schools"]
    assert plan["explanations"]


def test_seed_multiple_failures_union():
    ch = _chapter(step_status="failed")
    ch["data_requirements"][0]["status"] = "failed"
    plan = seed_recompute_from_failures(ch)
    assert set(plan["recompute"]) >= {"spatial_join", "admin_aggregation"}


# ── 投影行 ───────────────────────────────────────────────────────────────


def test_format_line_bounded_and_zero_drift():
    assert format_plan_runtime_line({}) == ""
    ch = _chapter(step_status="failed")
    block = derive_plan_runtime(ch, stored=None)
    ch[PLAN_RUNTIME_KEY] = block.to_bounded_dict()
    ch[PLAN_RUNTIME_KEY]["replan_pending"] = True
    line = format_plan_runtime_line(ch)
    assert line.startswith("[GIS PlanRuntime] v=1")
    assert "replan=pending" in line
    assert "min-rerun=" in line
    assert len(line) <= 480


# ── replan 预算与驱动共存（红线）────────────────────────────────────────


def test_replan_loop_has_budget_and_driver():
    assert REPLAN_LOOP in LOOP_BUDGETS
    assert LOOP_BUDGETS[REPLAN_LOOP] >= 1


# ── 服务入口（真实 session store）────────────────────────────────────────


@pytest.fixture()
async def clean_session():
    sid = f"prt-svc-{uuid.uuid4().hex[:8]}"
    from app.services.session_data import session_data_manager

    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    try:
        from app.lib.data.large_data import BASE_STORAGE_DIR

        d = BASE_STORAGE_DIR / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:  # noqa: BLE001
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
async def test_service_version_advance_and_gate(clean_session):
    from app.services.session_plan import load_session_plan

    from app.services.gis_harness.plan_runtime import maybe_advance_plan_version

    ch = _chapter()
    await _save_plan(clean_session, ch)
    first = await maybe_advance_plan_version(clean_session, reason="init")
    assert first is not None and first["version"] == 1
    # 指纹不变 → None
    assert await maybe_advance_plan_version(clean_session, reason="noop") is None
    # 行变化 → 版本 2
    plan = await load_session_plan(clean_session)
    plan.gis_chapter["analysis_steps"][0]["status"] = "complete"
    await _save_plan(clean_session, plan.gis_chapter)
    second = await maybe_advance_plan_version(clean_session, reason="row_done")
    assert second is not None and second["version"] == 2


@pytest.mark.asyncio
async def test_replan_request_and_budget_exhaustion(clean_session):
    from app.services.gis_harness.plan_runtime import maybe_advance_plan_version

    ch = _chapter()
    await _save_plan(clean_session, ch)
    await maybe_advance_plan_version(clean_session, reason="init")

    # 有预算 → 置 pending + durable 记账
    result = await request_replan(
        clean_session, reason="unrepairable errors", from_verdict="FAILED")
    assert result["verdict"] == "replan"
    assert result["replan_pending"] is True
    recovery = await load_recovery_state(clean_session)
    assert recovery["loops"]["replan"] == 1
    # 计划事实变化 → 版本推进清除 pending
    result2 = await request_replan(
        clean_session, reason="again", from_verdict="FAILED")
    # 预算 1 次已用尽 → 诚实 abort
    assert result2["verdict"] == "abort_with_disclosure"
    assert "exhausted" in result2["reason"]


@pytest.mark.asyncio
async def test_state_machine_replanning_phase_via_driver(clean_session):
    """驱动点 → REPLANNING 阶段 → 计划变化 → 退回（全链集成）。"""
    from app.services.gis_harness.plan_runtime import maybe_advance_plan_version
    from app.services.gis_harness.runtime_state_machine import (
        maybe_update_runtime_state,
    )
    from app.services.session_plan import load_session_plan

    ch = _chapter()
    ch["map_product"] = {
        "status": "needs_repair",
        "product_verdict": "BLOCKED_BY_METHOD",
        "repair_plan": {"actions": []},
    }
    await _save_plan(clean_session, ch)
    await maybe_advance_plan_version(clean_session, reason="init")
    block = await maybe_update_runtime_state(
        clean_session, trigger="repair_exhausted_replan", reason="final")
    assert block is not None
    # 修复不可达（无 actions）+ 无 replan pending → critiquing（未请求前）
    # 请求 replan → pending → 阶段翻 replanning
    result = await request_replan(clean_session, reason="blocked by method",
                                  from_verdict="BLOCKED_BY_METHOD")
    assert result["verdict"] == "replan"
    plan = await load_session_plan(clean_session)
    plan.gis_chapter["map_product"]["status"] = "needs_repair"
    await _save_plan(clean_session, plan.gis_chapter)
    block2 = await maybe_update_runtime_state(
        clean_session, trigger="repair_exhausted_replan", reason="final",
        force=True)
    assert block2["phase"] == "replanning"
    # 计划事实变化 → replan 消费 → 阶段退回
    plan = await load_session_plan(clean_session)
    plan.gis_chapter["query"] = "改看锦江区学校"
    await _save_plan(clean_session, plan.gis_chapter)
    block3 = await maybe_update_runtime_state(
        clean_session, trigger="replan_committed", reason="user edit",
        force=True)
    assert block3["phase"] != "replanning"
    recovery = await load_recovery_state(clean_session)
    assert recovery["loops"]["replan"] == 1  # 记账持久
