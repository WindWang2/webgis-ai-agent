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


def test_unknown_side_effect_never_maps_to_pure():
    from app.services.mission_runtime.contracts import (
        OperationClass,
        operation_class_from_side_effect,
    )

    assert operation_class_from_side_effect("pure") == OperationClass.PURE
    assert operation_class_from_side_effect("") == OperationClass.PURE
    assert operation_class_from_side_effect("derived_external") == OperationClass.IDEMPOTENT
    assert (
        operation_class_from_side_effect("bogus_new_token")
        == OperationClass.DESTRUCTIVE_AT_MOST_ONCE
    )


def test_checkpoint_snapshot_rechecks_byte_bound():
    import json

    from app.services.mission_runtime import contracts as C

    cp = C.MissionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="m-1",
        mission_revision=1,
        goal_revision=1,
        state="running",
        refs=C.MissionRefs(
            artifact_refs=[f"ref:{'x' * 1000}-{i}" for i in range(C.MAX_REFS)]
        ),
        note="n" * 5000,
    )
    out = cp.to_bounded_dict()
    size = len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size <= C.MAX_CHECKPOINT_BYTES


def test_zero_quota_dimension_is_exhausted():
    from app.services.mission_runtime.contracts import MissionResourceBudget

    assert MissionResourceBudget(quota={"tokens": 0}).exhausted() == ["tokens"]
    assert MissionResourceBudget(quota={"tokens": -5}).exhausted() == ["tokens"]


def test_set_quota_clamped_to_server_ceiling(runtime):
    from app.services.mission_runtime.quota_policy import MISSION_CEILINGS

    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    rec = runtime.resources.set_quota(
        m.mission_id, lease_epoch=ep, owner="w1", quota={"tokens": 10**9})
    assert rec.resource_budget.quota["tokens"] == MISSION_CEILINGS["tokens"]


def test_budget_receipt_overflow_fails_closed(runtime):
    from app.services.mission_runtime import contracts as C

    m = runtime.create(
        org_id="1", user_id="u", root_goal="g", quota={"tokens": 1000.0})
    runtime.start(m.mission_id, worker_id="w1")
    ep = runtime.store.get_mission(m.mission_id).lease_epoch
    rec = runtime.store.get_mission(m.mission_id)
    refs = rec.refs.model_dump()
    refs["evidence_refs"] = [f"ref:e{i}" for i in range(C.MAX_REFS)]
    runtime.store.patch_mission(
        m.mission_id, lease_epoch=ep, owner="w1", refs=refs,
        expected_revision=rec.revision)
    with pytest.raises(TransitionRejected):
        runtime.resources.commit_consumption(
            m.mission_id, lease_epoch=ep, owner="w1", dim="tokens",
            amount=3.0, idempotency_key="k-new")
    after = runtime.store.get_mission(m.mission_id)
    assert after.resource_budget.consumed.get("tokens", 0.0) == 0.0
    assert "receipt:budget:k-new" not in after.refs.evidence_refs


def test_artifact_and_resource_methods_scope_org(runtime):
    m = runtime.create(org_id="org-a", user_id="u", root_goal="g")
    runtime.start(m.mission_id, worker_id="w1", org_id="org-a")
    ep = runtime.store.get_mission(m.mission_id, org_id="org-a").lease_epoch
    with pytest.raises(TransitionRejected):
        runtime.artifacts.attach(
            m.mission_id, lease_epoch=ep, owner="w1",
            refs=["ref:x"], org_id="org-b")
    assert runtime.artifacts.inventory(m.mission_id, org_id="org-b") == {}
    attached = runtime.artifacts.attach(
        m.mission_id, lease_epoch=ep, owner="w1",
        refs=["ref:x"], org_id="org-a")
    assert attached.refs.artifact_refs == ["ref:x"]
    assert runtime.resources.exhausted(m.mission_id, org_id="org-b") == []
    with pytest.raises(TransitionRejected):
        runtime.resources.reserve(
            m.mission_id, lease_epoch=ep, owner="w1", dim="tokens",
            amount=1.0, org_id="org-b")


def test_create_swarm_run_rejects_task_overflow(runtime):
    from app.services.mission_runtime import contracts as C

    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    tasks = {
        f"t{i}": {"task_id": f"t{i}", "state": "PENDING"}
        for i in range(C.MAX_SWARM_TASKS + 1)
    }
    with pytest.raises(TransitionRejected):
        runtime.store.create_swarm_run(m.mission_id, org_id="1", tasks=tasks)


def test_negative_release_reservation_does_not_inflate_reserved():
    from app.services.mission_runtime.contracts import MissionResourceBudget

    budget = MissionResourceBudget(
        quota={"tokens": 10.0}, reserved={"tokens": 5.0})
    budget.release_reservation("tokens", -3.0)
    assert budget.reserved["tokens"] == 5.0


def test_settle_swarm_task_missing_readback_is_typed_error(runtime, monkeypatch):
    from app.services.mission_runtime import contracts as C

    m = runtime.create(org_id="1", user_id="u", root_goal="g")
    run = runtime.store.create_swarm_run(
        m.mission_id, org_id="1",
        tasks={"t1": {"task_id": "t1", "state": "PENDING"}})
    monkeypatch.setattr(runtime.store, "get_swarm_run", lambda *a, **k: None)
    with pytest.raises(TransitionRejected):
        runtime.store.settle_swarm_task(
            run.swarm_run_id,
            C.SwarmTaskReceipt(
                task_id="t1", state=C.SwarmTaskDurableState.SUCCEEDED),
        )
