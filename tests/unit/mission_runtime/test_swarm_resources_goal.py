"""Swarm partial completion, resources, goal revision."""
from __future__ import annotations

import pytest

from app.services.mission_runtime.store import TransitionRejected
from app.services.mission_runtime.swarm_bridge import DurableSwarmBridge


def test_partial_swarm_optional_failure_isolated(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    bridge = DurableSwarmBridge(runtime.store)
    run = bridge.begin_run(
        m.mission_id, org_id="1",
        task_descriptors=[
            {"task_id": "scout", "side_effect": "pure"},
            {"task_id": "optional_chart", "side_effect": "pure"},
        ],
        lease_epoch=ep, owner="w1",
    )
    bridge.settle(run.swarm_run_id, task_id="scout", status="succeeded",
                  produced_refs=["ref:data"], assignment_id="a1")
    bridge.settle(run.swarm_run_id, task_id="optional_chart", status="failed",
                  error_code="OPTIONAL_FAIL", assignment_id="a2")
    got = runtime.store.get_swarm_run(run.swarm_run_id)
    assert got.state == "partial"
    assert got.tasks["scout"].produced_refs == ["ref:data"]


def test_resource_exhausted_and_no_double_charge(runtime):
    m = runtime.create(
        org_id="1", user_id="u", root_goal="g",
        quota={"tokens": 10.0})
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    runtime.resources.commit_consumption(
        m.mission_id, lease_epoch=ep, owner="w1",
        dim="tokens", amount=6.0, idempotency_key="r1")
    # duplicate receipt must not double-charge
    runtime.resources.commit_consumption(
        m.mission_id, lease_epoch=ep, owner="w1",
        dim="tokens", amount=6.0, idempotency_key="r1")
    rec = runtime.store.get_mission(m.mission_id)
    assert rec.resource_budget.consumed["tokens"] == 6.0
    with pytest.raises(TransitionRejected):
        runtime.resources.reserve(
            m.mission_id, lease_epoch=ep, owner="w1",
            dim="tokens", amount=5.0)


def test_goal_revision_reuses_artifacts(runtime):
    m = runtime.create(org_id="1", user_id="u", root_goal="小学分布")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    runtime.artifacts.attach(
        m.mission_id, lease_epoch=ep, owner="w1",
        refs=["ref:schools", "ref:old-aoi"])
    # patch frontier completed
    rec = runtime.store.get_mission(m.mission_id)
    frontier = rec.frontier.model_dump()
    frontier["completed"] = ["t-schools", "t-aoi"]
    runtime.store.patch_mission(
        m.mission_id, lease_epoch=ep, owner="w1", frontier=frontier)
    m2 = runtime.revise_goal(
        m.mission_id, worker_id="w1",
        new_goal="加入中学，并比较主城区教育设施",
        invalidate_task_ids=["t-aoi"],
        retain_artifact_refs=["ref:schools"],
    )
    assert m2.goal_revision == 2
    assert "ref:schools" in m2.refs.artifact_refs
    assert "ref:old-aoi" not in m2.refs.artifact_refs
    assert "t-aoi" in m2.frontier.pending
    assert "t-schools" in m2.frontier.completed
