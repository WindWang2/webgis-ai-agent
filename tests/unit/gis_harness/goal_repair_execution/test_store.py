"""G42 WP03 —— attempt 持久化测试（单 claim / 显式重试父链 / 读模型）。"""
from __future__ import annotations

import pytest

from app.services.gis_harness.goal_repair_execution import store


@pytest.fixture(autouse=True)
def _tables():
    from app.core.database import Base, get_engine, SessionLocal
    from app.models.db_model import AnalysisTask
    import contextlib

    Base.metadata.create_all(bind=get_engine())
    # 测试间清埋本域行（隔离 DB 为 session 级共享）。
    with contextlib.closing(SessionLocal()) as db:
        db.query(AnalysisTask).filter(
            AnalysisTask.task_type == "goal_repair_attempt").delete()
        db.commit()
    yield


def _submit(session_id="s-store", goal_id="g1", plan_version="v1",
            selected=("a1",), retry_expected_reuse=False):
    return store.submit_attempt_row(
        session_id=session_id, goal_id=goal_id, plan_version=plan_version,
        plan={"goal_id": goal_id, "actions": []},
        bindings=[{"action_id": "a1", "action": "reobserve"}],
        prerequisites={"goal_fingerprint": "fp"},
        selected_action_ids=list(selected),
        acknowledged_losses=[],
        owner={"session_id": session_id, "user_id": "u1", "entry": "ui"},
    )


def test_submit_creates_row_and_view_roundtrip():
    created = _submit()
    assert created["reused"] is False
    view = created["view"]
    assert view["attempt_id"] and view["status"] in ("pending", "running")
    assert view["goal_id"] == "g1"
    assert view["plan_version"] == "v1"
    # dispatch_spec 落库（执行快照）但不出现在视图
    spec = store.read_dispatch_spec(int(view["attempt_id"]))
    assert spec is not None
    assert spec["prerequisites"]["goal_fingerprint"] == "fp"
    assert spec["selected_action_ids"] == ["a1"]
    assert "dispatch_spec" not in view


def test_idempotent_double_submit_same_active_claim():
    first = _submit()
    second = _submit()
    assert second["reused"] is True
    assert second["view"]["attempt_id"] == first["view"]["attempt_id"]


def test_active_attempt_discovery():
    assert store.find_active_attempt("s-store", "g1") is None
    created = _submit()
    active = store.find_active_attempt("s-store", "g1")
    assert active is not None
    assert active["attempt_id"] == created["view"]["attempt_id"]
    # 其它 goal 不受影响
    assert store.find_active_attempt("s-store", "g2") is None


def test_terminal_attempt_releases_claim_and_retry_creates_new():
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    first = _submit()
    job_id = int(first["view"]["attempt_id"])
    # 显式终态（模拟执行完成）
    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(select(AnalysisTask).where(
            AnalysisTask.id == job_id)).scalar_one()
        row.status = "completed"
        row.result_summary = {"outcome": "completed",
                              "evidence_signature": "sig-1"}
        db.commit()
    assert store.find_active_attempt("s-store", "g1") is None

    retry = _submit()
    assert retry["reused"] is False
    assert retry["view"]["attempt_id"] != first["view"]["attempt_id"]
    # 父链由调用方传入（服务层读取最近终态 attempt）
    views = store.find_attempts("s-store", "g1")
    assert len(views) == 2


def test_ensure_attempt_typed_not_found():
    from app.services.gis_harness.goal_repair_execution.contracts import (
        GoalRepairErrorCode,
    )

    with pytest.raises(GoalRepairErrorCode) as ei:
        store.ensure_attempt("not-a-number")
    assert ei.value.code == "ATTEMPT_NOT_FOUND"
    with pytest.raises(GoalRepairErrorCode):
        store.ensure_attempt(999999)


def _mark_completed(attempt_id: int) -> None:
    from app.core.database import SessionLocal
    from app.models.db_model import AnalysisTask
    from sqlalchemy import select
    import contextlib

    with contextlib.closing(SessionLocal()) as db:
        row = db.execute(select(AnalysisTask).where(
            AnalysisTask.id == attempt_id)).scalar_one()
        row.status = "completed"
        db.commit()


def test_find_attempts_not_crowded_out_by_other_goals():
    """同会话其它 goal 的更新行不得把本 goal 的活跃 attempt 挤出 limit。"""
    target = _submit(goal_id="g-target")
    for i in range(9):
        _submit(goal_id=f"g-other-{i}")
    active = store.find_active_attempt("s-store", "g-target")
    assert active is not None
    assert active["attempt_id"] == target["view"]["attempt_id"]


def test_next_retry_no_ignores_prefixed_goal_ids():
    """goal_id/plan_version 互为前缀时不得互相多计 retry_no。"""
    first = _submit(goal_id="g1", plan_version="v1")
    _mark_completed(int(first["view"]["attempt_id"]))
    other = _submit(goal_id="g1:v1", plan_version="v1")
    _mark_completed(int(other["view"]["attempt_id"]))
    assert store._next_retry_no("s-store", "g1", "v1") == 1
