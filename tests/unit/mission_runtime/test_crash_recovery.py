"""Crash recovery matrix + checkpoint resume."""
from __future__ import annotations

import datetime as _dt

import sqlalchemy as sa

from app.models.mission import GISMissionRow
from app.services.mission_runtime.contracts import (
    MissionState,
    OperationClass,
    SwarmTaskDurableState,
)
from app.services.mission_runtime.swarm_bridge import DurableSwarmBridge


def _expire(store, mission_id: str) -> None:
    with store._sf() as db:
        db.execute(
            sa.update(GISMissionRow)
            .where(GISMissionRow.mission_id == mission_id)
            .values(lease_expires_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) - _dt.timedelta(seconds=2))
        )
        db.commit()


def test_crash_before_task_launch(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    bridge = DurableSwarmBridge(runtime.store)
    run = bridge.begin_run(
        m.mission_id, org_id="1", goal_slice="g",
        task_descriptors=[{"task_id": "t1", "side_effect": "pure"}],
        lease_epoch=runtime.store.get_mission(m.mission_id).lease_epoch,
        owner="w1",
    )
    _expire(runtime.store, m.mission_id)
    result = runtime.resume(m.mission_id, worker_id="w2")
    assert result["ok"] is True
    plan = result["resume_plans"][0]
    assert "t1" in plan["retry"]
    assert run.swarm_run_id


def test_crash_during_task_pure_retries(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    bridge = DurableSwarmBridge(runtime.store)
    run = bridge.begin_run(
        m.mission_id, org_id="1",
        task_descriptors=[{"task_id": "t1", "side_effect": "pure"}],
        lease_epoch=ep, owner="w1",
    )
    bridge.mark_running(run.swarm_run_id, "t1", assignment_id="a1", side_effect="pure")
    _expire(runtime.store, m.mission_id)
    result = runtime.resume(m.mission_id, worker_id="w2")
    assert "t1" in result["resume_plans"][0]["retry"]


def test_crash_during_destructive_unresolved(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    bridge = DurableSwarmBridge(runtime.store)
    run = bridge.begin_run(
        m.mission_id, org_id="1",
        task_descriptors=[{"task_id": "tx", "side_effect": "destructive"}],
        lease_epoch=ep, owner="w1",
    )
    bridge.mark_running(run.swarm_run_id, "tx", assignment_id="a1", side_effect="destructive")
    got = runtime.store.get_swarm_run(run.swarm_run_id)
    assert got.tasks["tx"].state == SwarmTaskDurableState.RUNNING
    assert got.tasks["tx"].operation_class == OperationClass.DESTRUCTIVE_AT_MOST_ONCE
    _expire(runtime.store, m.mission_id)
    result = runtime.resume(m.mission_id, worker_id="w2")
    assert "tx" in result["unresolved"]
    # must NOT be in retry
    assert "tx" not in result["resume_plans"][0]["retry"]
    settled = runtime.store.get_swarm_run(run.swarm_run_id)
    assert settled.tasks["tx"].state == SwarmTaskDurableState.UNRESOLVED


def test_crash_after_receipt_commit_no_rerun(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    bridge = DurableSwarmBridge(runtime.store)
    run = bridge.begin_run(
        m.mission_id, org_id="1",
        task_descriptors=[
            {"task_id": "done", "side_effect": "pure"},
            {"task_id": "todo", "side_effect": "pure"},
        ],
        lease_epoch=ep, owner="w1",
    )
    bridge.settle(
        run.swarm_run_id, task_id="done", status="succeeded",
        produced_refs=["ref:art-1"], assignment_id="a-done")
    _expire(runtime.store, m.mission_id)
    result = runtime.resume(m.mission_id, worker_id="w2")
    plan = result["resume_plans"][0]
    assert "done" in plan["skip"]
    assert "todo" in plan["retry"]


def test_duplicate_receipt_idempotent(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    bridge = DurableSwarmBridge(runtime.store)
    run = bridge.begin_run(
        m.mission_id, org_id="1",
        task_descriptors=[{"task_id": "t1", "side_effect": "pure"}],
        lease_epoch=ep, owner="w1",
    )
    bridge.settle(
        run.swarm_run_id, task_id="t1", status="succeeded",
        produced_refs=["ref:x"], assignment_id="a1")
    bridge.settle(
        run.swarm_run_id, task_id="t1", status="succeeded",
        produced_refs=["ref:y"], assignment_id="a1")
    got = runtime.store.get_swarm_run(run.swarm_run_id)
    assert got.tasks["t1"].produced_refs == ["ref:x"]


def test_checkpoint_kill_recreate_resume(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    mid = m.mission_id
    # simulate process death: drop in-memory service, keep DB via same store
    store = runtime.store
    _expire(store, mid)
    from app.services.mission_runtime.service import MissionRuntimeService
    runtime2 = MissionRuntimeService(store=store)
    result = runtime2.resume(mid, worker_id="w-new")
    assert result["ok"] is True
    cps = store.list_checkpoints(mid)
    assert cps
