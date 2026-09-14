"""执行图可靠性场景 —— resume / duplicate / restart / tenant / fallback（E4/E7）。

任务书场景映射：7（analysis 失败后 resume）、9（离线数据源 fallback 的
最小重算披露）、10（duplicate client retry 幂等）、进程重启（孤儿复位）、
跨租户复用隔离（E7 owner 域）。
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowNodeReuseRow  # noqa: F401
from app.services.gis_harness.plan_runtime import seed_recompute_from_failures
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.driver import Driver
from app.services.workflow_runtime.reuse import ReuseIndex, ReuseRecord
from app.services.workflow_runtime.retry import RetryPolicy
from app.services.workflow_runtime.store import InstanceStore


_DAG: Dict[str, Any] = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False,
         "outputs": [{"name": "data", "artifact_type": ""}]},
        {"node_id": "cap:analyze", "kind": "analysis",
         "capability": "spatial_stats", "optional": False,
         "inputs": [{"name": "input", "artifact_type": ""}],
         "outputs": [{"name": "output", "artifact_type": ""}]},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "cap:analyze.input"},
    ],
    "primary_output": "cap:analyze",
}


class _Env:
    """单实例测试环境（SQLite 真持久层 + fake 执行器）。"""

    def __init__(self, factory, *, fail_nodes: tuple = (),
                 max_attempts: int = 2):
        from app.services.workflow_runtime.adapters_geocompute import (
            GeoComputeNodeOutcome,
        )
        self._outcome = GeoComputeNodeOutcome
        self.exec_log: List[str] = []
        self.fail_nodes = set(fail_nodes)
        self.max_attempts = max_attempts
        self.store = InstanceStore(factory=factory)
        self.reuse = ReuseIndex(factory=factory)
        self.inst = self.store.create_instance(
            package_id="recipe-x", package_version="1.0.0",
            package_fingerprint="pf" * 16, owner_scope="u:abc",
            session_id="s1",
            node_specs=[{"node_id": n["node_id"], "optional": False}
                        for n in _DAG["nodes"]])
        self.store.transition_node(
            self.inst["instance_id"], "data:subject", C.NodeState.READY,
            expected_from=C.NodeState.PENDING, reason="ROLE_BOUND",
            event="attach", patch={"bound_ref": "ref:data-1"})

    def driver(self) -> Driver:
        env = self

        async def probe(session_id, ref):
            return {"ref_id": ref, "feature_count": 3,
                    "content_hash": "h" * 32, "content_revision": 1}

        async def plan_executor(node, input_refs, params, ctx):
            env.exec_log.append(node["node_id"])
            if node["node_id"] in env.fail_nodes:
                return env._outcome(
                    ok=False, error_code="NODE_TIMEOUT", error_message="x",
                    failure_class="transient_db")
            return env._outcome(
                ok=True, output_ref=f"ref:out-{node['node_id']}",
                duration_ms=5, rows_emitted=1)

        return Driver(self.store, reuse_index=self.reuse,
                      owner_scope="u:abc", deadline_s=5.0,
                      plan_executor=plan_executor, descriptor_probe=probe,
                      retry_policy=RetryPolicy(max_attempts=self.max_attempts))

    def run(self, token: str) -> Dict[str, Any]:
        return asyncio.run(self.driver().run(
            self.inst["instance_id"], _DAG, node_params={},
            session_id="s1", run_token=token,
            package_fingerprint="pf" * 16))


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_analysis_failure_then_resume_no_duplicate_upstream(factory):
    """场景7：analysis 失败（单次预算尽 → FAILED）→ 显式重排 → resume 成功；
    上游数据节点零重复执行。"""
    env = _Env(factory, fail_nodes=("cap:analyze",), max_attempts=1)
    summary = env.run("rt-1")
    assert summary["status"] == C.InstanceStatus.FAILED
    assert env.exec_log.count("data:subject") == 0  # 绑定节点不执行
    states = env.store.get_node_states(env.inst["instance_id"])
    assert states["cap:analyze"] == C.NodeState.FAILED

    # 显式指令（chat/REST 语义）：FAILED→READY 重排
    r = env.store.transition_node(
        env.inst["instance_id"], "cap:analyze", C.NodeState.READY,
        expected_from=C.NodeState.FAILED, reason="RERUN_REQUEUE",
        event="rerun")
    assert r.ok

    env.fail_nodes.clear()
    summary2 = env.run("rt-2")
    assert summary2["status"] == C.InstanceStatus.SUCCEEDED
    # resume 只补失败节点：失败 1 次 + 成功 1 次；上游零重复副作用
    assert env.exec_log.count("cap:analyze") == 2
    assert env.exec_log.count("data:subject") == 0


def test_duplicate_apply_changes_is_idempotent(factory):
    """场景10：同一变更集重复 apply —— 幂等，零重复副作用。"""
    env = _Env(factory)
    env.run("rt-1")  # 全链 SUCCEEDED
    assert env.exec_log == ["cap:analyze"]

    from app.services.workflow_runtime.recompute import ChangeApplier

    change = {"dimension": "parameter", "target_kind": "node",
              "target": "cap:analyze", "detail": "param v2"}
    applier = ChangeApplier(env.store, owner_scope="u:abc")
    for seq in (1, 2):
        result = asyncio.run(applier.apply(
            env.inst["instance_id"], _DAG,
            [C.PendingChange(**change)], seq=seq, source="intent_diff"))
        assert result["applied"] is True

    # STALE → run：复用裁决或重算一次（非两倍副作用）
    env.run("rt-2")
    assert env.exec_log.count("cap:analyze") <= 2  # rt-1 一次 + rt-2 至多一次


def test_orphan_running_reset_on_process_restart(factory):
    """进程重启：孤儿 RUNNING（租约已死）复位 READY → 重驱动结算。"""
    env = _Env(factory, fail_nodes=())
    store, inst = env.store, env.inst
    # 模拟崩溃现场：analyze 被旧 driver 认领 RUNNING 后进程死亡（1s 租约）
    store.transition_node(inst["instance_id"], "cap:analyze",
                          C.NodeState.READY, expected_from=C.NodeState.PENDING,
                          reason="DEPS_OK", event="driver")
    store.transition_node(inst["instance_id"], "cap:analyze",
                          C.NodeState.RUNNING, expected_from=C.NodeState.READY,
                          claim=True, claimed_by="dead-token",
                          reason="CHAT_DISPATCH", event="driver",
                          lease_ttl_s=1.0)
    _time.sleep(1.2)  # 租约过期 = 认领者死亡
    summary = env.run("rt-new")  # 新进程新 token
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    states = store.get_node_states(inst["instance_id"])
    assert states["cap:analyze"] == C.NodeState.SUCCEEDED


def test_reuse_index_never_crosses_owner(factory):
    """E7：同指纹跨 owner 域 —— 复用必 miss（宁可假 miss）。"""
    reuse = ReuseIndex(factory=factory)
    rec = ReuseRecord(
        owner_scope="u:alice", reuse_fingerprint="a" * 32,
        session_scope="", node_id="cap:analyze",
        package_fingerprint="pf" * 16, artifact_ref="ref:alice-out",
        artifact_session_id="s-alice", fingerprint_level="content",
        input_fingerprints={}, algorithm_id="alg1", params_fp="p1",
        env_fp="e1", source_instance_id="inst-alice")
    assert reuse.record(rec) is True
    hit_alice = reuse.find("u:alice", "a" * 32)
    hit_bob = reuse.find("u:bob", "a" * 32)
    assert hit_alice is not None
    assert hit_bob is None  # 跨租户绝不命中


def test_offline_fallback_seed_minimal_recompute():
    """场景9：数据行失败 → 最小重算披露（失败 + 下游闭包，其余复用）。"""
    chapter = {
        "plan_id": "p", "query": "q", "recipe_id": "r",
        "data_requirements": [
            {"capability": "adm_boundary", "status": "available",
             "depends_on": []},
            {"capability": "poi_query", "status": "failed", "depends_on": []},
        ],
        "analysis_steps": [
            {"capability": "district_stats", "status": "done",
             "depends_on": ["adm_boundary", "poi_query"]},
            {"capability": "density_heatmap", "status": "done",
             "depends_on": ["poi_query"]},
            {"capability": "boundary_chart", "status": "done",
             "depends_on": ["adm_boundary"]},
        ],
    }
    plan = seed_recompute_from_failures(chapter)
    # poi_query 失败 → 自身 + 下游 stats/heatmap 重算；boundary 链复用
    assert set(plan["recompute"]) == {
        "poi_query", "district_stats", "density_heatmap"}
    assert "boundary_chart" in plan["reuse"]
    assert "adm_boundary" in plan["reuse"]
