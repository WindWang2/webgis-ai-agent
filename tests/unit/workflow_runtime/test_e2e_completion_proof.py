"""Workflow Runtime V5 —— 端到端完成证明（Epic §18，真实 GeoCompute 执行）。

场景（本地可复现，in-process，合成小数据 ~20 点）：

 1. 用户注册一个多步骤 GIS 工作流包（data → buffer(transform) → output）；
 2. 实例化并执行 —— buffer 节点经**真实 GeoExecutionEngine** 执行
    （buffer_smart 数值算子 + MATERIALIZE 落存 session ref）；
 3. 节点产出 artifacts（session ref + 指纹）；
 4. 用户仅修改地图表达（style change）→ workflow semantic diff 判定
    科学节点不重算（呈现态刷新，零执行）；
 5. 用户修改上游科学参数（buffer distance 100→250）→ 只重算受影响
    subtree（buffer+output），data:subject 不动；
 6. 参数复用指纹失效 → buffer 真重算（新距离生效，输出几何变化）；
 7. 全过程 durable evidence（实例行/节点行/决策环/复用索引）落库。

data_input 节点全程绑定不变 = 「未受影响输入零重算」的证明面。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime import service as SV
from app.services.workflow_runtime.reuse import ReuseIndex
from app.services.workflow_runtime.registry import PackageRegistry
from app.services.workflow_runtime.store import InstanceStore

_OWNER = "u:e2e"
_SESSION = "s-e2e-buffer"

# 手工注册的 e2e 包：buffer 参数名进 DAG 节点（recompute 种子元数据）。
_E2E_DAG = {
    "compiler_version": "4.0.0",
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False,
         "outputs": [{"name": "data", "artifact_type": ""}]},
        {"node_id": "transform:buffer:subject", "kind": "transform",
         "optional": False, "parameters": [{"name": "distance"}],
         "inputs": [{"name": "input", "artifact_type": ""}],
         "outputs": [{"name": "output", "artifact_type": ""}]},
        {"node_id": "output:zone", "kind": "output", "optional": False,
         "inputs": [{"name": "product", "artifact_type": ""}]},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "transform:buffer:subject.output",
         "to": "output:zone.product"},
    ],
    "primary_output": "output:zone",
}


def _synthetic_points(n: int = 20) -> dict:
    """小合成点集（北京城区 ~0.01° 网格；语义可证明：buffer 后成面）。"""
    feats = []
    for i in range(n):
        lon = 116.30 + (i % 5) * 0.002
        lat = 39.90 + (i // 5) * 0.002
        feats.append({
            "type": "Feature",
            "properties": {"id": i, "kind": "factory"},
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    fac = sessionmaker(bind=engine)
    svc = SV.WorkflowRuntimeService(
        store=InstanceStore(factory=fac),
        registry=PackageRegistry(factory=fac),
        reuse_index=ReuseIndex(factory=fac))
    # 注册 e2e 包（compiled_form 手工形态；指纹与 package.py 同规）
    import hashlib
    import json as _json

    from app.services.gis_harness.workflow_v4.package import WorkflowPackage

    pkg = WorkflowPackage(
        package_id="recipe-e2e-buffer", version="1.0.0",
        methodology_family="proximity", recipe_fingerprint="e2e",
        methodology_fingerprint="e2e",
        compiled_form={"typed_dag": _E2E_DAG, "parameters": []},
    )
    pkg.fingerprint = hashlib.sha256(
        _json.dumps(pkg.compiled_form, sort_keys=True, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")).hexdigest()[:64]
    svc.registry.register(pkg, owner_scope=_OWNER, project_id="")
    return {"svc": svc, "engine": engine, "fac": fac}


def _e2e_workflow(service, *, distance: float) -> dict:
    return {
        "transform:buffer:subject": {"distance": distance, "unit": "m"},
    }


async def _prepare_data() -> str:
    from app.services.session_data import session_data_manager

    session = f"{_SESSION}-{uuid.uuid4().hex[:6]}"
    ref = await session_data_manager.store(
        session, _synthetic_points(), prefix="e2e-points")
    return session, ref


def test_e2e_completion_proof(env):
    svc = env["svc"]

    session, data_ref = asyncio.run(_prepare_data())

    # ── 1+2. 实例化 + 数据绑定 + 真执行 ─────────────────────────────
    inst = svc.instantiate("recipe-e2e-buffer", owner_scope=_OWNER,
                           session_id=session)
    iid = inst["instance_id"]
    svc.store.transition_node(
        iid, "data:subject", C.NodeState.READY,
        expected_from=C.NodeState.PENDING, reason="ROLE_BOUND",
        event="attach", patch={"bound_ref": data_ref})
    result = _run(svc, iid, distance=100.0)
    assert result["run"]["status"] == "succeeded", result
    states = svc.store.get_node_states(iid)
    assert states == {
        "data:subject": C.NodeState.SUCCEEDED,
        "transform:buffer:subject": C.NodeState.SUCCEEDED,
        "output:zone": C.NodeState.SUCCEEDED,
    }, states

    # ── 3. 产物证据：buffer 输出是真实 session ref（面要素）─────────
    buf_ref = svc.store.get_node(iid, "transform:buffer:subject")["output_ref"]
    assert buf_ref and buf_ref.startswith("ref:")
    from app.services.session_data import session_data_manager

    payload = asyncio.run(session_data_manager.get(session, buf_ref))
    feats = _features_of(payload)
    assert feats, "buffer 输出必须有要素"
    assert feats[0]["geometry"]["type"] in ("Polygon", "MultiPolygon"), \
        "20 个点的 buffer 必须产出面几何"
    inst_row = svc.store.get_instance(iid, _OWNER)
    assert inst_row["status"] == "succeeded"
    assert svc.store.get_node(iid, "output:zone")["output_ref"]

    # ── 4. style change → 科学子图零触碰 ────────────────────────────
    attempts_before = _attempts(svc, iid)
    style = asyncio.run(svc.record_style_change(
        session, owner_scope=_OWNER, target="output:zone"))
    assert style is not None and style.get("applied")
    assert style["decision"]["style_only"] is True
    states_after_style = svc.store.get_node_states(iid)
    assert states_after_style == states, "style 变更不得改变任何节点状态"
    assert _attempts(svc, iid) == attempts_before  # 零执行

    # ── 5+6. 参数变化（distance 100→250）→ 精确子树真重算 ───────────
    change = C.PendingChange(dimension="parameter", target_kind="parameter",
                             target="distance", source="api")
    plan_view = asyncio.run(svc.recompute_plan(iid, [change],
                                               owner_scope=_OWNER))
    assert sorted(plan_view["would_mark_stale"]) == sorted([
        "transform:buffer:subject", "output:zone"])
    result2 = asyncio.run(svc.apply_changes(
        iid, [change], owner_scope=_OWNER))
    assert result2["applied"]
    # apply 只标 STALE；重算 = 调用方带新参数显式 run（执行参数语义显式）
    states_marked = svc.store.get_node_states(iid)
    assert states_marked["transform:buffer:subject"] == C.NodeState.STALE
    assert states_marked["output:zone"] == C.NodeState.STALE
    assert states_marked["data:subject"] == C.NodeState.SUCCEEDED
    run2 = _run(svc, iid, distance=250.0)
    assert run2["run"]["status"] == "succeeded"
    states2 = svc.store.get_node_states(iid)
    assert all(s == C.NodeState.SUCCEEDED for s in states2.values())
    # data:subject 未受影响：绑定与状态不变（零重算证明面）
    data_row = svc.store.get_node(iid, "data:subject")
    assert data_row["bound_ref"] == data_ref
    # 参数复用指纹失效 → buffer 真重算（输出 ref 变化，新几何生效）
    buf_ref2 = svc.store.get_node(iid, "transform:buffer:subject")["output_ref"]
    assert buf_ref2 and buf_ref2 != buf_ref
    payload2 = asyncio.run(session_data_manager.get(session, buf_ref2))
    assert _features_of(payload2), "新距离输出必须落存"
    # 节点行有重算后的 attempt 证据（attempts 增加）
    assert svc.store.get_node(iid, "transform:buffer:subject")["attempts"] >= 2

    # ── 7. durable evidence：决策环可解释 ───────────────────────────
    decisions = svc.store.get_instance(iid, _OWNER)["decisions"]
    assert len(decisions) >= 2
    style_decisions = [d for d in decisions if d.get("style_only")]
    param_decisions = [d for d in decisions if not d.get("style_only")]
    assert style_decisions and param_decisions
    # 输入节点复用指纹同判：同输入再次 STALE → 零重算复用解除
    svc.store.transition_node(iid, "transform:buffer:subject",
                              C.NodeState.STALE, reason="SAME_INPUT_TEST")
    run3 = _run(svc, iid, distance=250.0)
    assert run3["run"]["status"] == "succeeded"
    node3 = svc.store.get_node(iid, "transform:buffer:subject")
    assert node3["reuse"]["reused"] is True, "同参数同输入必须复用"
    assert node3["reuse"]["fingerprint_level"] == "content"


def _run(svc, iid: str, *, distance: float) -> dict:
    return asyncio.run(svc.run_instance(
        iid, owner_scope=_OWNER, deadline_s=60.0,
        node_params=_e2e_workflow(svc, distance=distance)))


def _features_of(payload) -> list:
    """MATERIALIZE 载荷形态：裸要素列表（或 FC dict）。"""
    if isinstance(payload, dict):
        return payload.get("features") or []
    if isinstance(payload, list):
        return payload
    return []


def _attempts(svc, iid: str) -> int:
    """节点 attempts 总和 = 真执行次数计数器（DB 证据，非内存假件）。"""
    return sum(n["attempts"] for n in svc.store.get_nodes(iid))
