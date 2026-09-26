"""F08 / ADR-0214：driver 侧预算生命周期与计划准入门（集成）。

验收面：
- 计划准入：enforce+违规 → 实例快速失败 RESOURCE_BUDGET_EXCEEDED（诚实
  终态 + run lease 释放）；observe → journal 披露但执行继续；limits 空 →
  行为完全不变；
- 节点预算：成功/失败/取消（deadline abandon）路径恰好一次归还；
- enforce 拒绝 → 节点 typed FAILED（RESOURCE_BUDGET_EXCEEDED 不可重试）；
- durable 元数据：driver 向 dispatcher 传 run_id/node_attempt/
  node_deadline_s/resource_envelope（#1408 管道喂入点）。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.governor.contract import (
    AdmissionDecision,
    ResourceDecision,
)
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.adapters_geocompute import (
    GeoComputeNodeOutcome,
)
from app.services.workflow_runtime.driver import Driver
from app.services.workflow_runtime.governor_link import (
    NodeGovernorLink,
    reset_node_governor_link_for_tests,
)
from app.services.workflow_runtime.store import InstanceStore


@pytest.fixture
def factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def _restore_link():
    yield
    reset_node_governor_link_for_tests()


@pytest.fixture(autouse=True)
def _surface_on(monkeypatch):
    """本文件测 governor link 接线：覆盖 conftest 的默认关闭。"""
    monkeypatch.setenv("GIS_WORKFLOW_GOVERNOR", "1")


_DAG: Dict[str, Any] = {
    "nodes": [
        {"node_id": "t1", "kind": "transform", "optional": False},
        {"node_id": "t2", "kind": "transform", "optional": False},
    ],
    # 串行链：节点分波执行，规避 StaticPool sqlite 跨线程并发写竞态
    # （并发面由 test_v6_cluster_dispatch 的槽位测试覆盖）。
    "edges": [{"from": "t1", "to": "t2"}],
    "primary_output": "",
}


def _make_store(factory) -> InstanceStore:
    return InstanceStore(factory=factory)


def _make_instance(store: InstanceStore, dag: Dict[str, Any]):
    return store.create_instance(
        package_id="recipe-f08", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in dag["nodes"]])


def _ok(ref="ref:o"):
    return GeoComputeNodeOutcome(ok=True, output_ref=ref, duration_ms=3)


class FakeRetries:
    def __init__(self):
        self.charged = 0

    def charge(self, *a, **kw):
        self.charged += 1

    def retry_allowed(self, *a, **kw):
        return True, ""


class FakeGovernor:
    """最小 governor 假件（只观测 admit/complete/charge 交互）。"""

    def __init__(self, *, decision=AdmissionDecision.ACCEPT,
                 enforce=True):
        from app.services.governor.config import GovernorConfig, GovernorMode

        self.config = GovernorConfig()
        self.config.mode = (GovernorMode.ENFORCE if enforce
                            else GovernorMode.OBSERVE)
        self.decision = decision
        self.admits = []
        self.completed = 0
        self.retries = FakeRetries()

    async def admit_and_reserve(self, demand, **kw):
        from app.services.governor.contract import (
            ResourceReservation,
        )

        self.admits.append(demand)
        allowed = self.decision in (AdmissionDecision.ACCEPT,
                                    AdmissionDecision.ACCEPT_WITH_LIMITS,
                                    AdmissionDecision.DEFER)
        if allowed:
            res = ResourceReservation(session_id=demand.session_id,
                                      subsystem=demand.subsystem)
            return ResourceDecision(decision=self.decision,
                                    mode=self.config.mode.value), res, None
        return ResourceDecision(decision=self.decision,
                                reasons=["hard_budget:session:"
                                         "memory_bytes:5g>4g"],
                                mode=self.config.mode.value), None, None

    async def complete(self, reservation, ticket, **kw):
        self.completed += 1


# ── 计划级准入门 ─────────────────────────────────────────────────────────

def test_plan_gate_off_is_pure_noop(factory, monkeypatch):
    from app.services.governor.contract import Dimension
    from app.services.workflow_runtime import plan_feasibility as PF

    monkeypatch.setenv("GIS_WORKFLOW_PLAN_ADMISSION", "off")
    monkeypatch.setattr(PF, "workflow_plan_limits",
                        lambda: {Dimension.WALL_TIME_S: 0.001})
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=lambda *a, **kw: _async(_ok()))
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "succeeded"


def _async(v):
    async def _c():
        return v
    return _c()


def test_plan_gate_enforce_blocks_instance(factory, monkeypatch):
    from app.services.governor.contract import Dimension
    from app.services.workflow_runtime import plan_feasibility as PF

    monkeypatch.setenv("GIS_WORKFLOW_PLAN_ADMISSION", "enforce")
    monkeypatch.setattr(PF, "workflow_plan_limits",
                        lambda: {Dimension.WALL_TIME_S: 0.001})
    store = _make_store(factory)
    inst = _make_instance(store, _DAG)
    iid = inst["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=lambda *a, **kw: _async(_ok()))
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "failed"
    assert summary["reason"] == "RESOURCE_BUDGET_EXCEEDED"
    assert summary["plan_admission"]["violations"]
    final = store.get_instance(iid, "u:abc")
    assert final["status"] == "failed"
    assert final["error_code"] == "RESOURCE_BUDGET_EXCEEDED"
    # run lease 已释放（重驱不被 busy 挡住）
    assert store.acquire_run_lease(iid, owner_scope="u:abc",
                                   token="rt2") is True


def test_plan_gate_observe_discloses_but_runs(factory, monkeypatch):
    from app.services.governor.contract import Dimension
    from app.services.workflow_runtime import plan_feasibility as PF

    monkeypatch.delenv("GIS_WORKFLOW_PLAN_ADMISSION", raising=False)
    monkeypatch.setattr(PF, "workflow_plan_limits",
                        lambda: {Dimension.WALL_TIME_S: 0.001})
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=lambda *a, **kw: _async(_ok()))
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "succeeded"
    events = store.get_events(iid)
    assert any(e["kind"] == C.EventKind.PLAN_ADMISSION for e in events)


def test_plan_gate_no_limits_no_behavior_change(factory, monkeypatch):
    from app.services.workflow_runtime import plan_feasibility as PF

    monkeypatch.setattr(PF, "workflow_plan_limits", lambda: {})
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=lambda *a, **kw: _async(_ok()))
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "succeeded"
    events = store.get_events(iid)
    assert not any(e["kind"] == C.EventKind.PLAN_ADMISSION for e in events)


# ── 节点预算生命周期 ─────────────────────────────────────────────────────

def _install_link(governor):
    reset_node_governor_link_for_tests(NodeGovernorLink(governor=governor))
    return governor


def test_node_budget_released_on_success(factory):
    g = _install_link(FakeGovernor())
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=lambda *a, **kw: _async(_ok()))
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "succeeded"
    assert len(g.admits) == 2  # 两个 transform 节点各一次
    assert g.completed == 2    # 恰好一次归还/节点


def test_node_budget_released_on_failure(factory):
    g = _install_link(FakeGovernor())
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]

    def fail_exec(*a, **kw):
        return _async(GeoComputeNodeOutcome(ok=False,
                                            error_code="SCIENCE_FAIL",
                                            failure_class="deterministic"))

    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=fail_exec)
    asyncio.run(driver.run(iid, _DAG, node_params={},
                           session_id="s1", run_token="rt1"))
    # 串行链：t1 失败（deterministic 不可重试）→ t2 上游未结算不派发。
    # 失败节点同样恰好归还一次。
    assert g.completed == 1


def test_node_budget_released_on_deadline_abandon(factory):
    """deadline 打断在飞节点 → 预算仍恰好归还（绝不泄漏）。"""
    g = _install_link(FakeGovernor())
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]

    async def hang(*a, **kw):
        await asyncio.sleep(30)
        return _ok()

    driver = Driver(store, owner_scope="u:abc", deadline_s=0.3,
                    plan_executor=lambda *a, **kw: hang())
    asyncio.run(driver.run(iid, _DAG, node_params={},
                           session_id="s1", run_token="rt1"))
    # 串行链：t1 在飞被打断（归还），t2 未派发
    assert g.completed == 1


def test_enforce_rejection_fails_node_without_execution(factory):
    g = _install_link(FakeGovernor(decision=AdmissionDecision.REJECT,
                                   enforce=True))
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]
    executed = {"n": 0}

    async def exec_probe(*a, **kw):
        executed["n"] += 1
        return _ok()

    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=exec_probe)
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert executed["n"] == 0          # 拒绝 = 不执行
    assert g.completed == 0            # 无预留无归还
    assert summary["status"] == "failed"
    row = store.get_node(iid, "t1")
    assert row["error_code"] == "RESOURCE_BUDGET_EXCEEDED"
    # 不可重试：退避门不会把它送回 READY
    assert row["state"] == C.NodeState.FAILED


def test_dispatcher_receives_durable_metadata(factory):
    """driver → dispatcher 的 run_id/attempt/deadline/envelope 喂入。"""
    received: Dict[str, Any] = {}

    class ProbeDispatcher:
        async def execute(self, **kw):
            received.update(kw)
            return _ok()

    _install_link(FakeGovernor())
    store = _make_store(factory)
    dag = {"nodes": [{"node_id": "t1", "kind": "transform",
                      "optional": False}],
           "edges": [], "primary_output": ""}
    iid = _make_instance(store, dag)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    dispatcher=ProbeDispatcher())
    summary = asyncio.run(driver.run(
        iid, dag, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "succeeded"
    assert received["run_id"] == iid
    assert received["node_attempt"] == 1
    assert received["resource_envelope"] is None or isinstance(
        received["resource_envelope"], dict)


def test_estimate_derived_worker_deadline(factory):
    """无显式超时时，worker 硬超时由估算 wall max ×2+30 派生。"""
    received: Dict[str, Any] = {}

    class ProbeDispatcher:
        async def execute(self, **kw):
            received.update(kw)
            return _ok()

    _install_link(FakeGovernor())
    store = _make_store(factory)
    dag = {"nodes": [{"node_id": "t1", "kind": "transform",
                      "resources": {"estimated_wall_s": 60},
                      "optional": False}],
           "edges": [], "primary_output": ""}
    iid = _make_instance(store, dag)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=600.0,
                    dispatcher=ProbeDispatcher())
    asyncio.run(driver.run(iid, dag, node_params={},
                           session_id="s1", run_token="rt1"))
    dl = received["node_deadline_s"]
    # estimated_wall_s=60 → wall range hi=120 → 派生 120×2+30 = 270s；
    # 与 run 剩余（≈600s）取紧者
    assert dl is not None
    assert 240.0 <= dl <= 300.0

# ── review gate 回归（P0-1 / P1-1 / P1-2）─────────────────────────────────

def test_node_not_executable_releases_budget(factory):
    """review P0-1 回归：NODE_NOT_EXECUTABLE 提前 return 必须归还预留。

    无 plan_executor/dispatcher 的裸 transform 节点 → 无已接线执行路径
    → typed 失败，且 governor complete 恰好一次（泄漏将使 reservation
    永久滞留 live 表）。"""
    g = _install_link(FakeGovernor())
    store = _make_store(factory)
    dag = {"nodes": [{"node_id": "t1", "kind": "transform",
                      "optional": False}],
           "edges": [], "primary_output": ""}
    iid = _make_instance(store, dag)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0)
    summary = asyncio.run(driver.run(
        iid, dag, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "failed"
    row = store.get_node(iid, "t1")
    assert row["error_code"] == "NODE_NOT_EXECUTABLE"
    assert g.admits and g.completed == 1  # 恰好一次归还（P0-1 前为 0）


def test_kill_switch_disables_plan_gate(factory, monkeypatch):
    """review P1-1 回归：GIS_WORKFLOW_GOVERNOR=0 时 plan gate 整体直通。"""
    from app.services.governor.contract import Dimension
    from app.services.workflow_runtime import plan_feasibility as PF

    monkeypatch.setenv("GIS_WORKFLOW_GOVERNOR", "0")  # 覆盖文件级 fixture
    monkeypatch.setenv("GIS_WORKFLOW_PLAN_ADMISSION", "enforce")
    monkeypatch.setattr(PF, "workflow_plan_limits",
                        lambda: {Dimension.WALL_TIME_S: 0.001})
    store = _make_store(factory)
    iid = _make_instance(store, _DAG)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    plan_executor=lambda *a, **kw: _async(_ok()))
    summary = asyncio.run(driver.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt1"))
    assert summary["status"] == "succeeded"


def test_explicit_timeout_not_cut_by_estimate(factory):
    """review P1-2 回归：显式 node_timeout_s 恒胜，估算不切割用户配置。"""
    received: Dict[str, Any] = {}

    class ProbeDispatcher:
        async def execute(self, **kw):
            received.update(kw)
            return _ok()

    _install_link(FakeGovernor())
    store = _make_store(factory)
    dag = {"nodes": [{"node_id": "t1", "kind": "transform",
                      "resources": {"estimated_wall_s": 60},
                      "optional": False}],
           "edges": [], "primary_output": ""}
    iid = _make_instance(store, dag)["instance_id"]
    driver = Driver(store, owner_scope="u:abc", deadline_s=600.0,
                    node_timeout_s=50.0, dispatcher=ProbeDispatcher())
    asyncio.run(driver.run(iid, dag, node_params={},
                           session_id="s1", run_token="rt1"))
    # 50s（显式）与 run 剩余（≈600）取紧者 = 50；估算派生 270 不得覆盖
    assert received["node_deadline_s"] == pytest.approx(50.0, abs=2.0)
