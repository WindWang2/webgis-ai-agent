"""G42 服务语义测试 —— busy / 活跃复用 / no_progress / 选择校验 / typed 错误。

对 ``service`` 层做隔离语义测试：权威状态读取以最小替身注入（monkeypatch
``_load_state``），其余（store/DB/绑定矩阵）走真实实现。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.services.gis_harness.goal_repair_execution import service
from app.services.gis_harness.goal_repair_execution.actions import (
    RepairOwnerContext,
)
from app.services.gis_harness.goal_repair_execution.contracts import (
    GoalRepairErrorCode,
)


@pytest.fixture(autouse=True)
def _tables():
    from app.core.database import Base, get_engine, SessionLocal
    from app.models.db_model import AnalysisTask
    import contextlib

    Base.metadata.create_all(bind=get_engine())
    with contextlib.closing(SessionLocal()) as db:
        db.query(AnalysisTask).filter(
            AnalysisTask.task_type == "goal_repair_attempt").delete()
        db.commit()
    yield


class _Plan:
    def __init__(self, chapter: Dict[str, Any]):
        self.gis_chapter = chapter
        self.user_goal = "test goal"


def _seed_state(
    *,
    verdict="partial",
    fmt="gpkg",
    action="run_delivery",
    delivery_expected=True,
    revision=5,
    obs_seq=2,
    with_bound_ref=True,
    evidence_receipts: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Any, Dict[str, Any], Dict[str, Any], int, int]:
    requirements = [{
        "requirement_id": f"export:{fmt}", "kind": "export",
        "verdict": "partial", "missing": ["delivery_receipt_absent"],
        "next_action": action, "export_format": fmt,
        "delivery_expected": delivery_expected,
    }]
    goal_block = {"verdict": verdict, "signal": "continue",
                  "requirements": requirements}
    plan_actions = [{
        "rule": "delivery_receipt_absent", "requirement_id": f"export:{fmt}",
        "action": action, "side_effect": "write", "path": "data_delivery.service",
        "budget_class": "repair",
        "expected_new_evidence": "delivery_receipt@status==completed",
    }]
    repair_plan = {"goal_id": "g1", "verdict": verdict, "signal": "continue",
                   "actions": plan_actions, "blockers": [],
                   "progress_rule": "new_evidence_required_per_round"}
    chapter: Dict[str, Any] = {
        "intent": {"task": "交付", "scope": {"name": "成都"}},
        "map_product": {
            "goal_satisfaction": goal_block,
            "goal_repair_plan": repair_plan,
            "rows_fingerprint": "rows-fp-1",
        },
        "export_receipts": evidence_receipts or [],
    }
    if with_bound_ref:
        chapter["analysis_steps"] = [{
            "capability": "buffer", "status": "done",
            "bound_ref": "ref:geojson-seed"}]
    return _Plan(chapter), chapter, chapter["map_product"], revision, obs_seq


def _patch_state(monkeypatch, **kwargs):
    state = _seed_state(**kwargs)
    async def fake_load_state(session_id: str):
        return state
    monkeypatch.setattr(service, "_load_state", fake_load_state)
    return state


def _ctx(session_id="s-sem"):
    return RepairOwnerContext(session_id=session_id, user_id="u1")


async def _preview(monkeypatch, **kwargs):
    _patch_state(monkeypatch, **kwargs)
    return await service.build_repair_preview("s-sem", ctx=_ctx())


@pytest.mark.asyncio
async def test_preview_builds_versioned_bindings(monkeypatch):
    preview = await _preview(monkeypatch)
    assert preview["goal_id"] == "g1"
    assert preview["verdict"] == "partial"
    assert preview["plan_version"]
    assert len(preview["actions"]) == 1
    row = preview["actions"][0]
    assert row["action"] == "run_delivery"
    # 交付预览失败（替身会话无真实 ref）→ 诚实 ack_note，不猜词表
    assert row["disposition"] in ("needs_acknowledgment", "blocked")


@pytest.mark.asyncio
async def test_satisfied_verdict_typed_error(monkeypatch):
    with pytest.raises(GoalRepairErrorCode) as ei:
        await _preview(monkeypatch, verdict="satisfied")
    assert ei.value.code == "GOAL_SATISFIED"


@pytest.mark.asyncio
async def test_missing_plan_typed_error(monkeypatch):
    state = _seed_state()
    state[1]["map_product"].pop("goal_repair_plan")
    async def fake_load_state(session_id):
        return state
    monkeypatch.setattr(service, "_load_state", fake_load_state)
    with pytest.raises(GoalRepairErrorCode) as ei:
        await service.build_repair_preview("s-sem", ctx=_ctx())
    assert ei.value.code == "PLAN_NOT_FOUND"


@pytest.mark.asyncio
async def test_invalid_action_selection_rejected(monkeypatch):
    _patch_state(monkeypatch)
    with pytest.raises(GoalRepairErrorCode) as ei:
        await service.start_repair_attempt(
            "s-sem", selected_action_ids=["a99"], ctx=_ctx(),
            session_busy_check=False)
    assert ei.value.code == "INVALID_ACTION_IDS"


@pytest.mark.asyncio
async def test_ui_entry_busy_when_active_pi_turn(monkeypatch):
    _patch_state(monkeypatch)
    monkeypatch.setattr(
        "app.agent_pi_bridge.get_active_turn_entry",
        lambda sid: object(),
    )
    with pytest.raises(GoalRepairErrorCode) as ei:
        await service.start_repair_attempt(
            "s-sem", ctx=_ctx(), session_busy_check=True)
    assert ei.value.code == "SESSION_BUSY"


@pytest.mark.asyncio
async def test_active_attempt_reused_on_double_start(monkeypatch):
    _patch_state(monkeypatch)
    async def noop(job_id):
        return None
    monkeypatch.setattr(service, "run_attempt_job", noop)
    first = await service.start_repair_attempt(
        "s-sem", ctx=_ctx(), session_busy_check=False)
    assert first.get("already_running") is not True
    second = await service.start_repair_attempt(
        "s-sem", ctx=_ctx(), session_busy_check=False)
    assert second.get("already_running") is True
    assert second["attempt_id"] == first["attempt_id"]


@pytest.mark.asyncio
async def test_no_progress_refusal_on_same_evidence(monkeypatch):
    _patch_state(monkeypatch)
    async def noop(job_id):
        return None
    monkeypatch.setattr(service, "run_attempt_job", noop)
    created = await service.start_repair_attempt(
        "s-sem", ctx=_ctx(), session_busy_check=False)
    # 终态化上一 attempt 且记录同证据签名（模拟完成）
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(select(AnalysisTask).where(
            AnalysisTask.id == int(created["attempt_id"]))).scalar_one()
        row.status = "completed"
        row.result_summary = {"outcome": "completed"}
        db.commit()
    # 证据签名在 attempt summary 缺席 → 不触发 no_progress；
    # 写入同签名后再启动 → typed NO_PROGRESS。
    state = _seed_state()
    async def fake_load_state(session_id):
        return state
    monkeypatch.setattr(service, "_load_state", fake_load_state)
    sig = service._evidence_signature(state[1], state[2], 5, 2)
    from app.services.gis_harness.goal_repair_execution import store
    assert store.write_summary_patch(
        int(created["attempt_id"]), {"evidence_signature": sig})
    with pytest.raises(GoalRepairErrorCode) as ei:
        await service.start_repair_attempt(
            "s-sem", ctx=_ctx(), session_busy_check=False)
    assert ei.value.code == "NO_PROGRESS"


@pytest.mark.asyncio
async def test_new_evidence_allows_new_attempt(monkeypatch):
    _patch_state(monkeypatch)
    async def noop(job_id):
        return None
    monkeypatch.setattr(service, "run_attempt_job", noop)
    created = await service.start_repair_attempt(
        "s-sem", ctx=_ctx(), session_busy_check=False)
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    # 终态化 + 同证据签名 → 拒绝
    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(select(AnalysisTask).where(
            AnalysisTask.id == int(created["attempt_id"]))).scalar_one()
        row.status = "completed"
        row.result_summary = {"outcome": "completed",
                              "evidence_signature": "sig-old"}
        db.commit()
    # 证据变化（新导出回执到达）→ 允许新 attempt
    state = _seed_state(
        evidence_receipts=[{"format": "gpkg", "revision": "9"}])
    async def fake_load_state(session_id):
        return state
    monkeypatch.setattr(service, "_load_state", fake_load_state)
    # 直接校验签名比较面（新签名 ≠ 旧签名 → 不再命中 no_progress 分支）
    sig_new = service._evidence_signature(state[1], state[2], 5, 2)
    assert sig_new != "sig-old"
    attempt = await service.start_repair_attempt(
        "s-sem", ctx=_ctx(), session_busy_check=False)
    assert attempt["attempt_id"] != created["attempt_id"]
