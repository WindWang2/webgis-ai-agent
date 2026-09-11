"""Workflow V6 —— Phase C：取消/重试/补偿严格语义测试。

真实持久层（临时 SQLite）+ fake 节点执行器。覆盖验收面：
- retry：瞬时错误退避重排（next_ready_at 门 + RETRY_SCHEDULED journal）、
  预算耗尽（RETRY_EXHAUSTED）、确定性错误不重试、重试后成功且只提交一次；
- timeout：per-node 超时 → NODE_TIMEOUT → 可重试；
- 节点级取消：queued 旗标消费、running 旗标点燃 token、后代闭包传播；
- 补偿：取消/失败路径的半提交产物清理（handler 调用 + journal 证据），
  幂等键下重试不重复提交同一 artifact。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import compensation as CP
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import retry as RT
from app.services.workflow_runtime.driver import Driver
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.store import InstanceStore


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


_DAG: Dict[str, Any] = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False},
        {"node_id": "transform:buffer:subject", "kind": "transform",
         "optional": False},
        {"node_id": "transform:clip:subject", "kind": "transform",
         "optional": False},
        {"node_id": "output:zone", "kind": "output", "optional": False},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "transform:buffer:subject.output",
         "to": "transform:clip:subject.input"},
        {"from": "transform:clip:subject.output", "to": "output:zone.product"},
    ],
    "primary_output": "output:zone",
}


class Harness:
    """driver 测试台：可控执行序列 + 可观测输出。"""

    def __init__(self, factory, *, policy: RT.RetryPolicy | None = None,
                 node_timeout_s: float | None = None):
        self.store = InstanceStore(factory=factory)
        self.reuse = ReuseIndex(factory=factory)
        self.execs: List[str] = []
        self.artifacts: List[str] = []  # 每次执行"提交"的产物 ref
        self.script: List[Any] = []  # 按调用次序弹出的 outcome/Exception
        self.default: Any = None
        self.inst = self.store.create_instance(
            package_id="recipe-x", package_version="1.0.0",
            package_fingerprint="pf" * 16, owner_scope="u:abc",
            session_id="s1",
            node_specs=[{"node_id": n["node_id"], "optional": False}
                        for n in _DAG["nodes"]])
        self.driver = Driver(
            self.store, reuse_index=self.reuse, owner_scope="u:abc",
            deadline_s=10.0, plan_executor=self._executor,
            descriptor_probe=self._probe, retry_policy=policy,
            node_timeout_s=node_timeout_s)
        self.store.transition_node(
            self.inst["instance_id"], "data:subject", C.NodeState.READY,
            expected_from=C.NodeState.PENDING, reason="ROLE_BOUND",
            event="attach", patch={"bound_ref": "ref:data-1"})

    async def _probe(self, session_id, ref):
        return {"ref_id": ref, "feature_count": 3, "content_hash": "h" * 32,
                "content_revision": 1, "geometry_types": ["Point"]}

    async def _executor(self, node, input_refs, params, ctx):
        nid = node["node_id"]
        self.execs.append(nid)
        outcome = self.script.pop(0) if self.script else self.default
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None and getattr(outcome, "output_ref", ""):
            self.artifacts.append(outcome.output_ref)
        return outcome

    def run(self, **kw):
        return asyncio.run(self.driver.run(
            self.inst["instance_id"], _DAG, node_params={}, session_id="s1",
            run_token=kw.pop("run_token", "rt-1"),
            package_fingerprint="pf" * 16, **kw))


def _fail(code: str, ref: str = "", failure_class: str = ""):
    from app.services.workflow_runtime.adapters_geocompute import (
        GeoComputeNodeOutcome,
    )

    return GeoComputeNodeOutcome(ok=False, error_code=code, output_ref=ref,
                                 error_message="boom",
                                 failure_class=failure_class)


def _ok(ref: str):
    from app.services.workflow_runtime.adapters_geocompute import (
        GeoComputeNodeOutcome,
    )

    return GeoComputeNodeOutcome(ok=True, output_ref=ref, duration_ms=1)


# ── retry ────────────────────────────────────────────────────────────────

def test_transient_failure_requeues_with_backoff_gate(factory):
    h = Harness(factory, policy=RT.RetryPolicy(max_attempts=2,
                                               base_delay_s=0.01,
                                               jitter_ratio=0.0))
    h.default = _fail("NODE_TIMEOUT", failure_class="transient_remote")
    summary = h.run()
    # 首轮失败 → READY（退避门）；deadline 内门已过 → 第二次执行成功路径
    # （default 仍 fail → 第二次也 fail → 预算尽 → 实例 failed）
    states = h.store.get_node_states(h.inst["instance_id"])
    assert summary["status"] == C.InstanceStatus.FAILED
    assert h.execs.count("transform:buffer:subject") == 2  # 重试过一次
    events = h.store.get_events(h.inst["instance_id"])
    scheduled = [e for e in events if e["kind"] == C.EventKind.RETRY_SCHEDULED]
    exhausted = [e for e in events if e["kind"] == C.EventKind.RETRY_EXHAUSTED]
    assert scheduled and exhausted
    row = h.store.get_node(h.inst["instance_id"], "transform:buffer:subject")
    assert row["attempts"] == 2
    assert states["output:zone"] in (C.NodeState.PENDING, C.NodeState.CANCELLED)


def test_retry_succeeds_after_transient_failure(factory):
    """第一次失败 → 退避重排 → 第二次成功；且无 RETRY_EXHAUSTED。"""
    h = Harness(factory, policy=RT.RetryPolicy(max_attempts=3,
                                               base_delay_s=0.01,
                                               jitter_ratio=0.0))
    h.script = [_fail("DB_BUSY"), _ok("ref:buf-out")]
    h.default = _ok("ref:ok")
    summary = h.run()
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    assert h.execs.count("transform:buffer:subject") == 2
    events = h.store.get_events(h.inst["instance_id"])
    assert any(e["kind"] == C.EventKind.RETRY_SCHEDULED for e in events)
    assert not any(e["kind"] == C.EventKind.RETRY_EXHAUSTED for e in events)
    row = h.store.get_node(h.inst["instance_id"], "transform:buffer:subject")
    assert row["state"] == C.NodeState.SUCCEEDED
    assert row["attempts"] == 2


def test_deterministic_failure_never_retries(factory):
    h = Harness(factory, policy=RT.RetryPolicy(max_attempts=3))
    h.default = _fail("NODE_NOT_EXECUTABLE")
    h.run()
    assert h.execs.count("transform:buffer:subject") == 1
    events = h.store.get_events(h.inst["instance_id"])
    assert not any(e["kind"] == C.EventKind.RETRY_SCHEDULED for e in events)


def test_gate_blocks_dispatch_until_next_ready_at(factory):
    """退避门未到 → 不派发不误终态（等待中实例保持 running）。"""
    h = Harness(factory, policy=RT.RetryPolicy(max_attempts=2,
                                               base_delay_s=30.0,
                                               jitter_ratio=0.0))
    h.driver.deadline_s = 0.4  # 短 deadline：门（30s）内必然超时
    h.default = _fail("NODE_TIMEOUT")
    summary = h.run()
    row = h.store.get_node(h.inst["instance_id"], "transform:buffer:subject")
    assert row["state"] == C.NodeState.READY  # 重排队等待
    assert row["next_ready_at"]
    assert summary["status"] == C.InstanceStatus.RUNNING  # 等待 ≠ 终态


def test_error_taxonomy_classification():
    assert RT.error_retryable("NODE_TIMEOUT") is True
    assert RT.error_retryable("DB_BUSY") is True
    assert RT.error_retryable("NODE_NOT_EXECUTABLE") is False
    assert RT.error_retryable("CANCELLED") is False
    assert RT.error_retryable("SOMETHING_NEW") is False  # 未知保守不重试
    # geocompute failure_class 投影（ADR-0101 D5 白名单）
    assert RT.error_retryable("GEOCOMPUTE_ERROR",
                              "transient_remote") is True
    assert RT.error_retryable("GEOCOMPUTE_ERROR", "scientific") is False


def test_backoff_is_exponential_and_bounded():
    p = RT.RetryPolicy(base_delay_s=1.0, factor=2.0, max_delay_s=5.0,
                       jitter_ratio=0.0)
    assert p.delay_for(1) == 1.0
    assert p.delay_for(2) == 2.0
    assert p.delay_for(3) == 4.0
    assert p.delay_for(4) == 5.0  # 钳制
    p2 = RT.RetryPolicy(base_delay_s=1.0, factor=1.0, max_delay_s=10.0,
                        jitter_ratio=0.5)
    assert 0.5 <= p2.delay_for(1) <= 1.5


# ── per-node timeout ─────────────────────────────────────────────────────

def test_node_timeout_marks_retryable_failure(factory):
    h = Harness(factory, node_timeout_s=0.05,
                policy=RT.RetryPolicy(max_attempts=1))

    async def slow_executor(node, input_refs, params, ctx):
        await asyncio.sleep(1.0)
        return _ok("ref:never")

    h.driver.plan_executor = slow_executor
    summary = h.run()
    row = h.store.get_node(h.inst["instance_id"], "transform:buffer:subject")
    assert row["error_code"] == "NODE_TIMEOUT"
    assert summary["status"] == C.InstanceStatus.FAILED


# ── 节点级取消 ───────────────────────────────────────────────────────────

def test_node_cancel_flags_block_dispatch_and_cancel_queued(factory):
    """queued 取消：旗标置位 → 不派发 → CANCELLED；后代闭包一并取消。"""
    h = Harness(factory)
    h.default = _ok("ref:out")
    svc_flags = h.store.request_node_cancel(
        h.inst["instance_id"],
        ["transform:buffer:subject", "transform:clip:subject",
         "output:zone"], actor="test")
    assert len(svc_flags) == 3
    summary = h.run()
    assert summary["status"] == C.InstanceStatus.CANCELLED
    states = summary["states"]
    assert states["data:subject"] == C.NodeState.SUCCEEDED  # 上游不受影响
    for nid in ("transform:buffer:subject", "transform:clip:subject",
                "output:zone"):
        assert states[nid] == C.NodeState.CANCELLED
    assert "transform:buffer:subject" not in h.execs  # 零执行


def test_service_cancel_nodes_propagates_to_descendants(factory):
    """service.cancel_nodes：目标 + 后代闭包；上游不动；未知节点 422 语义。"""
    from types import SimpleNamespace

    from app.services.workflow_runtime.registry import PackageRegistry
    from app.services.workflow_runtime.service import WorkflowRuntimeError
    from app.services.workflow_runtime.service import WorkflowRuntimeService

    registry = PackageRegistry(factory=factory)
    registry.register(
        SimpleNamespace(
            package_id="recipe-x", version="1.0.0", schema_version="1",
            compiler_version="4.0.0", methodology_family="m",
            recipe_fingerprint="", methodology_fingerprint="",
            fingerprint="pf" * 16,
            to_bounded_dict=lambda: {"compiled_form": {"typed_dag": _DAG}},
        ),
        owner_scope="u:abc")
    svc = WorkflowRuntimeService(store=InstanceStore(factory=factory),
                                 registry=registry,
                                 reuse_index=ReuseIndex(factory=factory))
    store = svc.store
    inst = store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in _DAG["nodes"]])
    iid = inst["instance_id"]
    result = asyncio.run(svc.cancel_nodes(
        iid, ["transform:buffer:subject"], owner_scope="u:abc",
        actor="test"))
    assert result["requested"] == [
        "output:zone", "transform:buffer:subject", "transform:clip:subject"]
    assert set(result["flagged"]) == set(result["requested"])
    # 上游 data 节点未被殃及
    assert not store.get_node(iid, "data:subject")["cancel_requested"]
    kinds = [e["kind"] for e in store.get_events(iid)
             if e["kind"] == C.EventKind.NODE_CANCEL_REQUESTED]
    assert len(kinds) == 3
    # 未知节点 → typed 错误
    with pytest.raises(WorkflowRuntimeError):
        asyncio.run(svc.cancel_nodes(iid, ["nope:node"],
                                     owner_scope="u:abc"))


# ── 补偿 ─────────────────────────────────────────────────────────────────

def test_compensation_cleans_cancelled_partial_artifact(factory):
    """取消路径的半提交产物 → 默认 handler 清理 + COMPENSATION journal。"""
    cleaned: List[str] = []

    async def fake_delete(session_id, ref, detail):
        cleaned.append(ref)
        return True

    CP._handlers.get("ref:")
    CP.register_handler("ref:", fake_delete)
    try:
        h = Harness(factory)
        iid = h.inst["instance_id"]

        # clip 执行「中途」取消旗标到达 + 已 materialize 半提交产物 ——
        # 模拟 materialize 后才观察到取消的真实时序
        async def clip_executor(node, input_refs, params, ctx):
            await h._executor(node, input_refs, params, ctx)
            if node["node_id"] == "transform:clip:subject":
                h.store.update_instance(iid,
                                        fields={"cancel_requested": True})
                return _fail("CANCELLED", ref="ref:clip-x")
            return _ok(f"ref:out-{node['node_id']}")

        h.driver.plan_executor = clip_executor
        summary = h.run()
        assert summary["status"] == C.InstanceStatus.CANCELLED
        assert "ref:clip-x" in cleaned  # 半提交产物被补偿清理
        comp = [e for e in h.store.get_events(iid)
                if e["kind"] == C.EventKind.COMPENSATION]
        assert comp and comp[0]["node_id"] == "transform:clip:subject"
    finally:
        CP._handlers.pop("ref:", None)


def test_compensation_on_failure_with_partial_artifact(factory):
    cleaned: List[str] = []

    async def fake_delete(session_id, ref, detail):
        cleaned.append(ref)
        return True

    CP.register_handler("ref:", fake_delete)
    try:
        h = Harness(factory, policy=RT.RetryPolicy(max_attempts=1))
        h.default = _fail("NODE_FAILED", ref="ref:partial-x",
                          failure_class="partial_materialization")
        h.run()
        assert "ref:partial-x" in cleaned
    finally:
        CP._handlers.pop("ref:", None)


def test_compensation_failure_is_fail_open(factory):
    """handler 抛异常 → 补偿失败不倒灌取消路径（节点仍 CANCELLED）。"""

    async def boom(session_id, ref, detail):
        raise RuntimeError("cleanup failed")

    CP.register_handler("ref:", boom)
    try:
        h = Harness(factory)
        h.script = [_fail("CANCELLED", ref="ref:ghost")]
        h.default = _ok("ref:out")
        store = h.store
        iid = h.inst["instance_id"]
        store.update_instance(iid, fields={"cancel_requested": True})
        summary = h.run()
        assert summary["status"] == C.InstanceStatus.CANCELLED
        # 补偿失败无 COMPENSATION 事件（诚实：不假装清理成功）
        assert not [e for e in store.get_events(iid)
                    if e["kind"] == C.EventKind.COMPENSATION]
    finally:
        CP._handlers.pop("ref:", None)


def test_retry_does_not_duplicate_artifact(factory):
    """重试不重复提交同一 artifact：失败 attempt 的半提交产物被补偿，
    成功 attempt 只提交一次。"""
    cleaned: List[str] = []

    async def fake_delete(session_id, ref, detail):
        cleaned.append(ref)
        return True

    CP.register_handler("ref:", fake_delete)
    try:
        h = Harness(factory, policy=RT.RetryPolicy(max_attempts=3,
                                                   base_delay_s=0.01,
                                                   jitter_ratio=0.0))
        h.script = [
            _fail("NODE_TIMEOUT", ref="ref:attempt1-x"),   # 首次：半提交
            _ok("ref:buffer-final"),                        # 重试：成功
        ]
        h.default = _ok("ref:ok")
        summary = h.run()
        assert summary["status"] == C.InstanceStatus.SUCCEEDED
        # 第一次的半提交产物被清理，不会与第二次的提交共存
        assert "ref:attempt1-x" in cleaned
        submissions = [a for a in h.artifacts
                       if a.startswith("ref:buffer") or a == "ref:ok"]
        assert submissions.count("ref:buffer-final") == 1
        assert "ref:attempt1-x" not in submissions
    finally:
        CP._handlers.pop("ref:", None)


def test_compensation_failure_leaves_journal_evidence(factory):
    """handler 清理失败 → COMPENSATION_FAILED journal（诚实暴露）。"""

    async def boom(session_id, ref, detail):
        raise RuntimeError("cleanup exploded")

    CP.register_handler("ref:", boom)
    try:
        h = Harness(factory)
        iid = h.inst["instance_id"]

        # buffer 成功；clip 执行「中途」取消落地 + 半提交产物
        async def clip_executor(node, input_refs, params, ctx):
            await h._executor(node, input_refs, params, ctx)
            if node["node_id"] == "transform:clip:subject":
                h.store.update_instance(iid,
                                        fields={"cancel_requested": True})
                return _fail("CANCELLED", ref="ref:clip-x")
            return _ok(f"ref:out-{node['node_id']}")

        h.driver.plan_executor = clip_executor
        summary = h.run()
        assert summary["status"] == C.InstanceStatus.CANCELLED
        failed = [e for e in store_events(h.store, iid)
                  if e["kind"] == C.EventKind.COMPENSATION_FAILED]
        assert failed and failed[0]["payload"]["ref"] == "ref:clip-x"
        # 清理失败就没有 COMPENSATION 成功事件（不假装成功）
        assert not [e for e in store_events(h.store, iid)
                    if e["kind"] == C.EventKind.COMPENSATION]
    finally:
        CP._handlers.pop("ref:", None)


def store_events(store, iid):
    return store.get_events(iid)
