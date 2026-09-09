"""Workflow Runtime V5 —— Round 1 审查修复回归（C1/C2/C3/M1-M4）。

每条测试对应审查 finding；C1/C2/C3 在原实现上可复现（审查者已实测）。
"""
from __future__ import annotations

import asyncio
from typing import List

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.db_model import WorkflowNodeReuseRow  # noqa: F401
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import machine as M
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime.adapters_geocompute import (
    load_ref_features,
)
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.store import InstanceStore

_OWNER = "u:r1"
_SESSION = "s-r1"


def _chain_dag() -> dict:
    """data → t1(buffer) → t2(buffer) → output（两级可执行链）。"""
    return {
        "nodes": [
            {"node_id": "data:subject", "kind": "data_input",
             "role": "subject", "optional": False,
             "outputs": [{"name": "data"}]},
            {"node_id": "transform:buffer:t1", "kind": "transform",
             "optional": False, "parameters": [{"name": "distance"}],
             "inputs": [{"name": "input"}],
             "outputs": [{"name": "output"}]},
            {"node_id": "transform:buffer:t2", "kind": "transform",
             "optional": False,
             "inputs": [{"name": "input"}],
             "outputs": [{"name": "output"}]},
            {"node_id": "output:zone", "kind": "output", "optional": False,
             "inputs": [{"name": "product"}]},
        ],
        "edges": [
            {"from": "data:subject.data", "to": "transform:buffer:t1.input"},
            {"from": "transform:buffer:t1.output",
             "to": "transform:buffer:t2.input"},
            {"from": "transform:buffer:t2.output",
             "to": "output:zone.product"},
        ],
        "primary_output": "output:zone",
    }


class Env:
    def __init__(self, dag, *, descriptors=None):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool)
        Base.metadata.create_all(engine)
        fac = sessionmaker(bind=engine)
        self.svc = SV.WorkflowRuntimeService(
            store=InstanceStore(factory=fac),
            registry=SV.PackageRegistry(factory=fac),
            reuse_index=ReuseIndex(factory=fac))
        import hashlib
        import json as _json

        from app.services.gis_harness.workflow_v4.package import (
            WorkflowPackage,
        )

        compiled = {"typed_dag": dag, "parameters": []}
        pkg = WorkflowPackage(
            package_id="recipe-r1", version="1.0.0",
            methodology_family="proximity", recipe_fingerprint="r1",
            methodology_fingerprint="r1", compiled_form=compiled)
        pkg.fingerprint = hashlib.sha256(
            _json.dumps(compiled, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":")).encode("utf-8")).hexdigest()[:64]
        self.svc.registry.register(pkg, owner_scope=_OWNER, project_id="")
        self.descriptors = descriptors or {}
        self.exec_calls: List[str] = []

        from app.services.workflow_runtime.driver import Driver

        async def plan_executor(node, refs, params, ctx):
            self.exec_calls.append(node["node_id"])
            from app.services.workflow_runtime.adapters_geocompute import (
                GeoComputeNodeOutcome,
            )

            return GeoComputeNodeOutcome(
                ok=True, output_ref=f"ref:out-{node['node_id']}"
                f"-{params.get('distance', 'd')}-{len(self.exec_calls)}",
                duration_ms=1)

        async def probe(session_id, ref):
            return self.descriptors.get(ref, {
                "ref_id": ref, "feature_count": 3,
                "content_hash": "h" * 32, "content_revision": 1,
                "geometry_types": ["Point"]})

        self.driver = Driver(
            self.svc.store, reuse_index=self.svc.reuse_index,
            owner_scope=_OWNER, deadline_s=5.0,
            plan_executor=plan_executor, descriptor_probe=probe)
        self.probe = probe


async def _bind(svc, iid: str, ref: str) -> None:
    svc.store.transition_node(
        iid, "data:subject", C.NodeState.READY,
        expected_from=C.NodeState.PENDING, reason="ROLE_BOUND",
        event="attach", patch={"bound_ref": ref})


async def _store_points(session: str) -> str:
    """真实 session 数据（service 路径 = 真 driver + 真 descriptor）。"""
    from app.services.session_data import session_data_manager

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"i": i},
         "geometry": {"type": "Point", "coordinates": [116.3, 39.9]}}
        for i in range(4)]}
    return await session_data_manager.store(session, fc, prefix="r1")


def _run(env, iid, *, distance, t2_distance=150):
    return asyncio.run(env.svc.run_instance(
        iid, owner_scope=_OWNER, deadline_s=30.0,
        node_params={"transform:buffer:t1": {"distance": distance},
                     "transform:buffer:t2": {"distance": t2_distance}}))


# ── C1: 增量重算闭包（下游不得用旧上游产物复用解除）──────────────────────

def test_c1_stale_downstream_waits_for_upstream_recompute():
    dag = _chain_dag()
    env = Env(dag)
    session = f"{_SESSION}-c1"
    ref = asyncio.run(_store_points(session))
    inst = env.svc.instantiate("recipe-r1", owner_scope=_OWNER,
                               session_id=session)
    iid = inst["instance_id"]
    asyncio.run(_bind(env.svc, iid, ref))
    r1 = _run(env, iid, distance=100.0)
    assert r1["run"]["status"] == "succeeded"
    old_t1 = env.svc.store.get_node(iid, "transform:buffer:t1")["output_ref"]
    old_t2 = env.svc.store.get_node(iid, "transform:buffer:t2")["output_ref"]
    old_out = env.svc.store.get_node(iid, "output:zone")["output_ref"]
    # 上游参数变化 → 全链 STALE（V4 闭包）
    change = C.PendingChange(dimension="parameter", target_kind="parameter",
                             target="distance", source="api")
    applied = asyncio.run(env.svc.apply_changes(iid, [change],
                                                owner_scope=_OWNER))
    assert applied["applied"]
    r2 = _run(env, iid, distance=250.0)
    assert r2["run"]["status"] == "succeeded"
    # 下游必须拿到**新**产物（旧 ref 复用解除 = C1 缺陷形态）
    assert env.svc.store.get_node(iid, "transform:buffer:t1")["output_ref"] != old_t1
    assert env.svc.store.get_node(iid, "transform:buffer:t2")["output_ref"] != old_t2
    assert env.svc.store.get_node(iid, "output:zone")["output_ref"] != old_out
    # t2 真重算（attempts 增加，而非 REUSE_HIT 旧产物洗白）
    assert env.svc.store.get_node(iid, "transform:buffer:t2")["attempts"] >= 2
    # 全链复用证据非 stale（reuse 未被用来洗旧结果）
    for nid in ("transform:buffer:t1", "transform:buffer:t2"):
        assert not (env.svc.store.get_node(iid, nid)["reuse"] or {}).get(
            "reused"), nid


def test_c1_ready_node_with_unsettled_upstream_not_dispatched():
    """READY 节点上游未结算 → 不派发（防同波读旧产物）。"""
    dag = {
        "nodes": [{"node_id": "a"}, {"node_id": "b"}],
        "edges": [{"from": "a.out", "to": "b.in"}],
    }
    states = {"a": C.NodeState.STALE, "b": C.NodeState.READY}
    assert "b" not in M.ready_set(dag, states)
    states["a"] = C.NodeState.SUCCEEDED
    assert "b" in M.ready_set(dag, states)


# ── C2: 输入超界 typed 失败（绝不静默截断）───────────────────────────────

def test_c2_load_ref_features_reports_truncation():
    from app.services.session_data import session_data_manager

    async def scenario():
        session = "s-trunc"
        big = [{"i": i} for i in range(11)]
        small = [{"i": i} for i in range(5)]
        big_ref = await session_data_manager.store(session, big,
                                                   prefix="trunc")
        small_ref = await session_data_manager.store(session, small,
                                                     prefix="trunc-ok")
        # 超界：descriptor 预检短路（不复制载荷）→ truncated 即知
        feats, truncated = await load_ref_features(session, [big_ref],
                                                   max_rows=10)
        under, under_trunc = await load_ref_features(session, [small_ref],
                                                     max_rows=10)
        return feats, truncated, under, under_trunc

    feats, truncated, under, under_trunc = asyncio.run(scenario())
    assert truncated is True  # 截断被显式报告，绝不静默
    assert len(feats) <= 10
    assert under_trunc is False and len(under) == 5


def test_c2_driver_fails_node_on_truncated_input():
    """真实 geocompute 路径：截断输入 → INPUT_TRUNCATED（节点 FAILED）。"""
    from app.services.session_data import session_data_manager
    from app.services.workflow_runtime.adapters_geocompute import (
        ADAPTER_INLINE_ROW_CAP,
        GeoComputeNodeOutcome,
    )
    from app.services.workflow_runtime.driver import Driver

    async def scenario():
        session = "s-trunc-run"
        big = [{"i": i} for i in range(ADAPTER_INLINE_ROW_CAP + 5)]
        ref = await session_data_manager.store(session, big, prefix="big")
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False},
            poolclass=StaticPool)
        Base.metadata.create_all(engine)
        store = InstanceStore(factory=sessionmaker(bind=engine))
        driver = Driver(store, owner_scope=_OWNER)
        node = {"node_id": "transform:buffer:x", "kind": "transform"}
        outcome = await driver._execute_via_geocompute(
            node, [ref], {"distance": 1}, session, None, {})
        return outcome

    outcome = asyncio.run(scenario())
    assert isinstance(outcome, GeoComputeNodeOutcome)
    assert not outcome.ok
    assert outcome.error_code == "INPUT_TRUNCATED"


# ── C3: 完成边界 drain（门控写反已修）────────────────────────────────────

def test_c3_pending_changes_drained_at_completion_boundary():
    dag = _chain_dag()
    env = Env(dag)
    session = f"{_SESSION}-c3"
    ref = asyncio.run(_store_points(session))
    inst = env.svc.instantiate("recipe-r1", owner_scope=_OWNER,
                               session_id=session)
    iid = inst["instance_id"]
    asyncio.run(_bind(env.svc, iid, ref))
    r1 = _run(env, iid, distance=100.0)
    assert r1["run"]["status"] == "succeeded"
    # 模拟 RUNNING 期间 defer 的 pending（真实 defer 路径已有单测）
    env.svc.store.update_instance(
        iid, owner_scope=_OWNER,
        fields={"pending_changes": [{
            "dimension": "style", "target_kind": "style",
            "target": "output:zone", "detail": "", "source": "style_hook"}]})
    r2 = _run(env, iid, distance=100.0)
    assert r2["run"]["status"] == "succeeded"
    inst2 = env.svc.store.get_instance(iid, _OWNER)
    assert inst2["pending_changes"] == []  # 完成边界已 drain
    assert any(d.get("style_only") for d in inst2["decisions"])


# ── M1: READY 节点绑定失败 → BLOCKED（不再 CAS 卡死）─────────────────────

def test_m1_ready_node_binding_failure_goes_blocked():
    dag = {
        "nodes": [
            {"node_id": "data:subject", "kind": "data_input",
             "role": "subject", "optional": False,
             "outputs": [{"name": "data"}]},
            {"node_id": "transform:clip:x", "kind": "transform",
             "optional": False,
             "inputs": [{"name": "input",
                         "artifact_type": "density_surface"}],
             "outputs": [{"name": "output"}]},
        ],
        "edges": [{"from": "data:subject.data",
                   "to": "transform:clip:x.input"}],
    }
    env = Env(dag)
    inst = env.svc.instantiate("recipe-r1", owner_scope=_OWNER,
                               session_id=_SESSION)
    iid = inst["instance_id"]
    # data 先结算为 SUCCEEDED（好 descriptor），transform READY（模拟
    # 重算/恢复路径就绪态），随后上游换成类型失配产物
    asyncio.run(_bind(env.svc, iid, "ref:data-1"))
    env.svc.store.transition_node(
        iid, "transform:clip:x", C.NodeState.READY,
        expected_from=C.NodeState.PENDING, reason="RECOVERY_PATH")
    env.descriptors["ref:data-1"] = {
        "ref_id": "ref:data-1", "feature_count": 3,
        "artifact_type": "feature_collection",
        "content_hash": "h" * 32, "content_revision": 1,
        "geometry_types": ["Point"]}
    r = asyncio.run(env.driver.run(
        iid, dag, node_params={}, session_id=_SESSION, run_token="rt-1",
        package_fingerprint="pf" * 16, package_id="recipe-r1"))
    states = env.svc.store.get_node_states(iid)
    # READY 态类型失配 → BLOCKED（而非 CAS 卡死空转到 deadline）
    assert states["transform:clip:x"] == C.NodeState.BLOCKED
    assert r["status"] == "failed"  # 诚实终态


# ── M2: 跨会话探测自毁修复 + anonymous 同域 ──────────────────────────────

def test_m2_reuse_probe_uses_artifact_session_not_current():
    """产物属会话 A；当前 run 会话 B —— 探测必须用 artifact_session_id
    （旧实现用当前会话探测 → 必失败 → 误删活缓存并重算）。"""
    dag = _chain_dag()
    env = Env(dag)
    inst = env.svc.instantiate("recipe-r1", owner_scope=_OWNER,
                               session_id="s-origin")
    iid = inst["instance_id"]
    asyncio.run(_bind(env.svc, iid, "ref:data-1"))
    r1 = asyncio.run(env.driver.run(
        iid, dag, node_params={"transform:buffer:t1": {"distance": 100},
                               "transform:buffer:t2": {"distance": 150}},
        session_id="s-origin", run_token="rt-1",
        package_fingerprint="pf" * 16, package_id="recipe-r1"))
    assert r1["status"] == "succeeded"
    calls_first = list(env.exec_calls)
    for nid in ("transform:buffer:t1", "transform:buffer:t2",
                "output:zone"):
        env.svc.store.transition_node(iid, nid, C.NodeState.STALE,
                                      reason="TEST")
    r2 = asyncio.run(env.driver.run(
        iid, dag, node_params={"transform:buffer:t1": {"distance": 100},
                               "transform:buffer:t2": {"distance": 150}},
        session_id="s-other", run_token="rt-2",
        package_fingerprint="pf" * 16, package_id="recipe-r1"))
    assert r2["status"] == "succeeded"
    node = env.svc.store.get_node(iid, "transform:buffer:t1")
    assert (node["reuse"] or {}).get("reused") is True
    assert env.exec_calls == calls_first  # 未误删未重算


def test_m2_anonymous_domain_find_scoped_by_session(env=None):
    from app.services.workflow_runtime.reuse import ReuseIndex

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    index = ReuseIndex(factory=sessionmaker(bind=engine))
    from app.services.workflow_runtime.reuse import ReuseRecord

    rec = ReuseRecord(
        owner_scope="anonymous", reuse_fingerprint="aa" * 16,
        session_scope="sess-a", node_id="n", package_fingerprint="p",
        artifact_ref="ref:x", artifact_session_id="sess-a",
        fingerprint_level="content", input_fingerprints={},
        algorithm_id="a", params_fp="p", env_fp="e")
    assert index.record(rec)
    # 他匿名会话不可见；同会话可见；真实用户域（scope 空条目）全员可见
    assert index.find("anonymous", "aa" * 16,
                      session_scope="sess-b") is None
    assert index.find("anonymous", "aa" * 16,
                      session_scope="sess-a") is not None


# ── M3: 残留取消旗标的重驱（不再立即 cancelled）──────────────────────────

def test_m3_residual_cancel_flag_cleared_on_rerun():
    dag = _chain_dag()
    env = Env(dag)
    session = f"{_SESSION}-m3"
    ref = asyncio.run(_store_points(session))
    inst = env.svc.instantiate("recipe-r1", owner_scope=_OWNER,
                               session_id=session)
    iid = inst["instance_id"]
    asyncio.run(_bind(env.svc, iid, ref))
    assert _run(env, iid, distance=100.0)["run"]["status"] == "succeeded"
    env.svc.store.update_instance(
        iid, owner_scope=_OWNER, fields={"cancel_requested": True})
    change = C.PendingChange(dimension="parameter", target_kind="parameter",
                             target="distance", source="api")
    asyncio.run(env.svc.apply_changes(iid, [change], owner_scope=_OWNER))
    r2 = _run(env, iid, distance=250.0)
    assert r2["run"]["status"] == "succeeded"  # 非 cancelled
    assert env.svc.store.get_instance(iid, _OWNER)["status"] == "succeeded"


# ── M4: 级别聚合最低水位（空身份端口不被掩盖）────────────────────────────

def test_m4_eligibility_rejects_absent_input_identity():
    """空身份端口（fp 缺席）→ 复用必 miss（R1-M4 红线的守卫面）。"""
    from app.services.workflow_runtime.reuse import (
        ReuseRecord,
        evaluate_eligibility,
    )

    rec = ReuseRecord(
        owner_scope="u:x", reuse_fingerprint="ab" * 16, session_scope="",
        node_id="n", package_fingerprint="p", artifact_ref="ref:x",
        artifact_session_id="s", fingerprint_level="content",
        # 存量条目含空 fp 端口（历史缺陷形态）
        input_fingerprints={"a": {"level": "content", "fp": "x",
                                  "content_revision": "1"},
                            "b": {"level": "content", "fp": "",
                                  "content_revision": ""}},
        algorithm_id="a", params_fp="p", env_fp="e")
    ok, why = evaluate_eligibility(
        rec, package_fingerprint="p",
        current_inputs={"a": {"fp": "x", "content_revision": "1"},
                        "b": {"fp": "", "content_revision": ""}},
        descriptor_probe=None)
    assert not ok and why == "input_identity_absent:b"


def _get_rows(env):
    with env.svc.store._factory() as db:
        return list(db.query(WorkflowNodeReuseRow).all())
