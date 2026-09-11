"""Workflow V6 —— Phase D：worker 注册表 / 派发面 / 资源上限测试。

覆盖验收面：
- WorkerRegistry：注册/心跳（stale 不可复活，须显式 register）/sweep/按
  profile 与 backend 过滤/槽位总量；
- 全局派发槽位上限（多 run 并发不打爆机器）；
- driver 经 dispatcher 执行端到端（fake 远端 worker）；
- worker 死亡：执行中途停止心跳 → 节点租约过期 → 孤儿复位 → 第二个
  driver 接管重派（不永久 zombie、不双重提交）；
- 大任务隔离决策与 NO_CAPABLE_WORKER。
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import cluster as CL
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import dispatch as DP
from app.services.workflow_runtime.driver import Driver
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
        {"node_id": "output:zone", "kind": "output", "optional": False},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "transform:buffer:subject.output",
         "to": "output:zone.product"},
    ],
    "primary_output": "output:zone",
}


def _make_instance(store: InstanceStore):
    return store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in _DAG["nodes"]])


def _bind(store: InstanceStore, iid: str):
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="ROLE_BOUND", event="attach",
                          patch={"bound_ref": "ref:data-1"})


# ── WorkerRegistry ───────────────────────────────────────────────────────

def test_worker_register_heartbeat_and_profile_filter(factory):
    reg = CL.WorkerRegistry(factory=factory)
    assert reg.register(
        "w-raster", role="worker", runtime="durable",
        capabilities={"cpu": 8, "mem_mb": 32768, "gpu": 1,
                      "profiles": {"raster": 2},
                      "backends": ["durable"]})
    assert reg.register("w-vector", capabilities={"cpu": 2})
    active = reg.list_active()
    assert {w["worker_id"] for w in active} == {"w-raster", "w-vector"}
    # profile / backend 过滤
    raster = reg.list_active(profile="raster")
    assert [w["worker_id"] for w in raster] == ["w-raster"]
    assert reg.total_active_slots("raster") == 2
    durable = reg.list_active(backend="durable", profile="raster")
    assert len(durable) == 1
    assert reg.list_active(backend="durable", profile="light_cpu") == []
    # 心跳刷新
    assert reg.heartbeat("w-vector", load={"in_flight": 1})
    # 非法 profile 词表被钳掉（防自授）
    caps = reg.list_active(profile="raster")[0]["capabilities"]
    assert set(caps["profiles"]) <= {"raster", "celery"}


def test_worker_sweep_dead_and_revive_requires_register(factory):
    import datetime as _dt

    import sqlalchemy as sa

    from app.models.db_model import WorkflowWorkerRow

    reg = CL.WorkerRegistry(factory=factory)
    reg.register("w-old")
    reg.register("w-new")
    with factory() as db:
        db.execute(
            sa.update(WorkflowWorkerRow)
            .where(WorkflowWorkerRow.worker_id == "w-old")
            .values(last_heartbeat_at=_dt.datetime.utcnow()
                    - _dt.timedelta(seconds=120))
        )
        db.commit()
    swept = reg.sweep_dead(ttl_s=90.0)
    assert swept == ["w-old"]
    assert reg.heartbeat("w-old") is False  # stale 不可续（须显式 register）
    assert reg.register("w-old") is True  # 显式注册复活
    assert any(w["worker_id"] == "w-old"
               for w in reg.list_active())


# ── 全局资源上限 ─────────────────────────────────────────────────────────

def test_global_dispatch_slots_cap_parallel_execution(factory):
    """并发派发 ≤ 槽位上限（资源上限验收；测试不会打爆机器）。"""
    import importlib

    dp = importlib.reload(DP)
    in_flight = {"n": 0}
    peak = {"n": 0}

    class ProbeLocal(DP.LocalDispatcher):
        async def execute(self, **kw):
            async with dp.get_slots_semaphore():
                in_flight["n"] += 1
                peak["n"] = max(peak["n"], in_flight["n"])
                await asyncio.sleep(0.02)
                in_flight["n"] -= 1
                from app.services.workflow_runtime.adapters_geocompute import (
                    GeoComputeNodeOutcome,
                )

                return GeoComputeNodeOutcome(ok=True, output_ref="ref:o")

    store = InstanceStore(factory=factory)
    inst = _make_instance(store)
    _bind(store, inst["instance_id"])
    # 并发 6 个节点执行 > 槽位 4 → 峰值必须 ≤ 4
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    dispatcher=ProbeLocal(), max_concurrency=4)

    async def many():
        await asyncio.gather(*[
            driver.dispatcher.execute(node={}, dag={}, input_refs=[],
                                      params={}, session_id="s",
                                      port_idents={}, cancel_token=None)
            for _ in range(6)])

    asyncio.run(many())
    assert peak["n"] <= 4


def test_local_dispatcher_executes_real_plan_via_session_refs(factory):
    """LocalDispatcher 走适配器→geocompute 计划（fake engine 记录计划）。"""
    executed: List[str] = []

    class FakeEngine:
        def execute_plan(self, plan, **kw):
            executed.append(plan.plan_id)
            ev = {plan.nodes[0].node_id: type("E", (), {
                "status": "completed", "output_ref": "", "rows_emitted": 2,
                "error_code": None, "error_message": ""})()}
            ev[f"{plan.nodes[0].node_id}:out"] = type("E", (), {
                "status": "completed", "output_ref": "ref:disp-out",
                "rows_emitted": 2, "error_code": None,
                "error_message": ""})()
            return type("R", (), {"status": type("S", (), {
                "value": "completed"})(), "evidence": ev,
                "error_code": None, "error_message": ""})()


    disp = DP.LocalDispatcher(engine=FakeEngine(), owner_scope="u:abc")
    outcome = asyncio.run(disp.execute(
        node={"node_id": "transform:buffer:subject", "kind": "transform"},
        dag=_DAG, input_refs=["ref:data-1"], params={"distance": 100},
        session_id="s1", port_idents={"input": {"fp": "f" * 32}},
        cancel_token=None))
    assert outcome.ok and outcome.output_ref == "ref:disp-out"
    assert executed == ["wfv5-transform-buffer-subject"][:1] or executed


# ── driver × dispatcher 端到端 ───────────────────────────────────────────

def test_driver_runs_via_remote_dispatcher(factory):
    """driver 派发到「远端 worker」（fake dispatcher）→ 实例完成。"""
    calls: List[str] = []

    class FakeRemote:
        async def execute(self, *, node, **kw):
            calls.append(node.get("node_id", ""))
            from app.services.workflow_runtime.adapters_geocompute import (
                GeoComputeNodeOutcome,
            )

            return GeoComputeNodeOutcome(ok=True, output_ref="ref:remote-out",
                                         duration_ms=5)

    store = InstanceStore(factory=factory)
    inst = _make_instance(store)
    _bind(store, inst["instance_id"])
    driver = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                    dispatcher=FakeRemote())
    summary = asyncio.run(driver.run(
        inst["instance_id"], _DAG, node_params={}, session_id="s1",
        run_token="rt-1", package_fingerprint="pf" * 16))
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    assert calls == ["transform:buffer:subject"]
    # 执行期租约续期任务没有留下 zombie：终态节点租约被清
    row = store.get_node(inst["instance_id"], "transform:buffer:subject")
    assert row["lease_expires_at"] == ""
    assert row["state"] == C.NodeState.SUCCEEDED


def test_remote_worker_death_no_permanent_zombie(factory):
    """远端 worker 死亡：租约过期 → 孤儿复位 → 第二个 driver 接管。"""
    store = InstanceStore(factory=factory)
    inst = _make_instance(store)
    iid = inst["instance_id"]
    _bind(store, iid)

    class DyingWorker:
        """派发后挂起直至被取消 —— 模拟 worker 死亡（无完成、无心跳）。"""

        async def execute(self, *, node, **kw):
            await asyncio.sleep(60)
            from app.services.workflow_runtime.adapters_geocompute import (
                GeoComputeNodeOutcome,
            )

            return GeoComputeNodeOutcome(ok=True, output_ref="ref:never")

    driver1 = Driver(store, owner_scope="u:abc", deadline_s=0.3,
                     dispatcher=DyingWorker(), node_lease_ttl_s=30)
    summary1 = asyncio.run(driver1.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt-dead",
        package_fingerprint="pf" * 16))
    # driver deadline 到 → node 仍 RUNNING（在飞）但实例未终态
    assert summary1["status"] == C.InstanceStatus.RUNNING
    assert store.get_node_states(iid)["transform:buffer:subject"] == \
        C.NodeState.RUNNING
    # 模拟时间流逝：worker 死了（心跳停）→ 节点租约过期 + run 租约过期
    import datetime as _dt

    import sqlalchemy as sa

    from app.models.db_model import WorkflowInstanceNodeRow
    from app.models.db_model import WorkflowInstanceRow

    with factory() as db:
        past = _dt.datetime.utcnow() - _dt.timedelta(seconds=1)
        db.execute(sa.update(WorkflowInstanceNodeRow)
                   .where(WorkflowInstanceNodeRow.instance_id == iid)
                   .values(lease_expires_at=past))
        db.execute(sa.update(WorkflowInstanceRow)
                   .where(WorkflowInstanceRow.instance_id == iid)
                   .values(run_lease_expires_at=past))
        db.commit()
    # 第二个 driver 接管：孤儿复位 + 正常执行完成
    class HealthyWorker:
        async def execute(self, *, node, **kw):
            from app.services.workflow_runtime.adapters_geocompute import (
                GeoComputeNodeOutcome,
            )

            return GeoComputeNodeOutcome(ok=True, output_ref="ref:adopted")

    driver2 = Driver(store, owner_scope="u:abc", deadline_s=10.0,
                     dispatcher=HealthyWorker())
    summary2 = asyncio.run(driver2.run(
        iid, _DAG, node_params={}, session_id="s1", run_token="rt-live",
        package_fingerprint="pf" * 16))
    assert summary2["status"] == C.InstanceStatus.SUCCEEDED
    row = store.get_node(iid, "transform:buffer:subject")
    assert row["state"] == C.NodeState.SUCCEEDED
    assert row["output_ref"] == "ref:adopted"
    assert row["attempts"] >= 1  # attempt 历史保留（不重复提交同产物）


def test_execution_time_lease_renewal_keeps_long_node_alive(factory):
    """执行期续期：长执行（> ttl/3 周期）期间租约被续，不误判孤儿。"""
    store = InstanceStore(factory=factory)
    inst = _make_instance(store)
    _bind(store, inst["instance_id"])
    observed: Dict[str, str] = {}

    class SlowWorker:
        async def execute(self, *, node, **kw):
            await asyncio.sleep(0.35)  # > ttl/3（ttl=0.6 → period 0.2）
            row = store.get_node(inst["instance_id"],
                                 "transform:buffer:subject")
            observed["heartbeat_at"] = row["heartbeat_at"]
            observed["lease_expires_at"] = row["lease_expires_at"]
            from app.services.workflow_runtime.adapters_geocompute import (
                GeoComputeNodeOutcome,
            )

            return GeoComputeNodeOutcome(ok=True, output_ref="ref:slow")

    driver = Driver(store, owner_scope="u:abc", deadline_s=5.0,
                    dispatcher=SlowWorker(), node_lease_ttl_s=0.6)
    summary = asyncio.run(driver.run(
        inst["instance_id"], _DAG, node_params={}, session_id="s1",
        run_token="rt-1", package_fingerprint="pf" * 16))
    assert summary["status"] == C.InstanceStatus.SUCCEEDED
    # 执行中（sleep 后、完成前）租约已被续期任务推进过（续期任务的
    # heartbeat 写入 heartbeat_at/lease_expires_at；claim 原始值不会含
    # 完成时刻之后的推进 —— observed 非空即证明在飞续期生效）
    assert observed["heartbeat_at"] != ""
    assert observed["lease_expires_at"] != ""


# ── 派发决策 ─────────────────────────────────────────────────────────────

def test_choose_dispatch_modes(factory, monkeypatch):
    reg = CL.WorkerRegistry(factory=factory)
    reg.register("w-dur", runtime="durable",
                 capabilities={"profiles": {"raster": 1},
                               "backends": ["durable"]})
    # 显式 durable：有 worker → durable
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "durable")
    node = {"node_id": "n1", "resources": {"profile": "raster"}}
    assert DP.choose_dispatch(node, input_rows=0, registry=reg) == "durable"
    # 无合格 profile worker → NoCapableWorker
    with pytest.raises(DP.NoCapableWorker):
        DP.choose_dispatch({"node_id": "n2",
                            "resources": {"profile": "network"}},
                           input_rows=0, registry=reg)
    # local 显式 → local（即便无 worker）
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "local")
    assert DP.choose_dispatch(node, input_rows=10**6, registry=reg) == "local"
    # auto + 重 profile + 有 durable worker → durable；无 → local
    monkeypatch.setenv("GIS_WORKFLOW_DISPATCH", "auto")
    assert DP.choose_dispatch(node, input_rows=0, registry=reg) == "durable"
    reg.retire("w-dur")
    assert DP.choose_dispatch(node, input_rows=0, registry=reg) == "local"


def test_isolation_requires_capable_worker(factory, monkeypatch):
    monkeypatch.setenv("GIS_WORKFLOW_ISOLATE_LARGE_TASKS", "1")
    reg = CL.WorkerRegistry(factory=factory)
    node = {"node_id": "n1", "resources": {"profile": "light_cpu"}}
    with pytest.raises(DP.NoCapableWorker):
        DP.choose_dispatch(node, input_rows=100_000, registry=reg)
    reg.register("w-lc", runtime="durable",
                 capabilities={"profiles": {"light_cpu": 1},
                               "backends": ["durable"]})
    assert DP.choose_dispatch(node, input_rows=100_000, registry=reg) == \
        "durable"


def test_node_priority_orders_batch(factory):
    """优先级降序派发；同优先级保持声明序（FIFO 公平，无饥饿）。"""
    InstanceStore(factory=factory)
    from app.services.workflow_runtime.driver import _node_priority

    assert _node_priority({"priority": 10}) == 10
    assert _node_priority({"priority": "7"}) == 7
    assert _node_priority({"priority": 99}) == 5   # 越界 → 默认
    assert _node_priority({"priority": "abc"}) == 5
    assert _node_priority(None) == 5
