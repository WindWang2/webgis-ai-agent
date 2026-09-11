"""Workflow V6 —— Phase H 验收：真实 GIS workflow 贯穿 Data/Science/Cartography。

全真实路径（无 plan_executor、无 dispatcher、无 fake engine）：
- data_input：session store 落真实 FeatureCollection（10 要素，3 类别）；
- transform:buffer：真实 geo_processor buffer（shapely 几何）；
- cap:category_breakdown：真实 data_fabric compute_aggregates（SQL 对齐）；
- cartography:render：真实 matplotlib 渲染 → generate_map_pdf A4 合成 →
  内容寻址 blob 落存（PDF 字节 + sha256）；
- output：绑定透传收口。

标记 heavy（matplotlib/shapely/PIL 重依赖；CI heavy lane 跑）。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.workflow_runtime import contracts as C
from app.services.workflow_runtime.driver import Driver
from app.services.workflow_runtime.store import InstanceStore

pytestmark = pytest.mark.heavy


@pytest.fixture
def factory():
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        yield sessionmaker(bind=engine)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


_DAG = {
    "nodes": [
        {"node_id": "data:subject", "kind": "data_input", "role": "subject",
         "optional": False},
        {"node_id": "transform:buffer:subject", "kind": "transform",
         "optional": False, "params": {"distance": 50, "unit": "m"}},
        {"node_id": "cap:category_breakdown", "kind": "analysis",
         "capability": "category_breakdown", "optional": False},
        {"node_id": "cartography:render", "kind": "cartography",
         "optional": False, "params": {"title": "测试专题图", "field":
                                       "category"}},
        {"node_id": "output:zone", "kind": "output", "optional": False},
    ],
    "edges": [
        {"from": "data:subject.data", "to": "transform:buffer:subject.input"},
        {"from": "data:subject.data", "to": "cap:category_breakdown.input"},
        {"from": "transform:buffer:subject.output",
         "to": "cartography:render.input"},
        {"from": "cartography:render.output", "to": "output:zone.product"},
    ],
    "primary_output": "output:zone",
}


def _real_feature_collection():
    """真实 GeoJSON（WGS84 附近 10 点、3 类别；小而真）。"""
    features = []
    for i in range(10):
        lon, lat = 116.40 + i * 0.001, 39.90 + (i % 3) * 0.001
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [lon, lat]},
            "properties": {"category": f"type-{i % 3}", "id": i},
        })
    return {"type": "FeatureCollection", "features": features}


def test_real_gis_workflow_data_science_cartography(factory, monkeypatch,
                                                    tmp_path):
    from app.services.session_data import session_data_manager

    # blob store 指向临时目录（真实文件系统写；不污染项目目录）
    from app.services import project_artifact_promotion as _pap

    monkeypatch.setattr(_pap, "content_store_root", lambda: tmp_path,
                        raising=False)

    # 先落真实 session 载荷（Data 端），再单次绑定（READY→READY 非法，
    # 绑定必须发生在 PENDING→READY 的同一次转移）
    ref = asyncio.run(session_data_manager.store(
        "s1", _real_feature_collection(), prefix="wfv6data"))

    store = InstanceStore(factory=factory)
    inst = store.create_instance(
        package_id="recipe-x", package_version="1.0.0",
        package_fingerprint="pf" * 16, owner_scope="u:abc", session_id="s1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in _DAG["nodes"]])
    iid = inst["instance_id"]
    store.transition_node(iid, "data:subject", C.NodeState.READY,
                          expected_from=C.NodeState.PENDING,
                          reason="ROLE_BOUND", event="attach",
                          patch={"bound_ref": ref[:96]})

    async def _main():
        # 单事件循环：seed → run → 校验（memory session store 与 loop
        # 绑定 —— 跨 loop get 会静默 cache-miss，生产为同一长驻 loop）
        driver = Driver(store, owner_scope="u:abc", deadline_s=120.0)
        summary = await driver.run(
            iid, _DAG, node_params={}, session_id="s1", run_token="rt-real",
            package_fingerprint="pf" * 16)
        assert summary["status"] == C.InstanceStatus.SUCCEEDED, summary
        states = summary["states"]
        assert all(s == C.NodeState.SUCCEEDED for s in states.values())

        # Data 端：buffer 产物是真实要素（点 → 多边形）
        buffer_ref = store.get_node(iid, "transform:buffer:subject")[
            "output_ref"]
        assert buffer_ref.startswith("ref:")
        payload = await session_data_manager.get("s1", buffer_ref)
        feats = payload.get("features") if isinstance(payload, dict)             else payload
        assert len(feats) == 10
        geom_types = {f["geometry"]["type"] for f in feats}
        assert geom_types == {"Polygon"}  # 真实 buffer：点 → 多边形

        # Science 端：聚合表是真实统计（每类 4/3/3）
        sci_ref = store.get_node(iid, "cap:category_breakdown")["output_ref"]
        assert sci_ref.startswith("ref:")
        table = await session_data_manager.get("s1", sci_ref)
        assert table["type"] == "table"
        counts = {r["category"]: r["count"] for r in table["rows"]}
        assert counts == {"type-0": 4, "type-1": 3, "type-2": 3}
        return carto_ref_of(store, iid)

    def carto_ref_of(store, iid):
        ref_ = store.get_node(iid, "cartography:render")["output_ref"]
        assert ref_.startswith("blob:")
        return ref_

    asyncio.run(_main())

    # journal：全链转移历史可检视
    events = store.get_events(iid)
    transitions = [e for e in events
                   if e["kind"] == C.EventKind.STATE_TRANSITION]
    assert len(transitions) >= 8  # 5 节点 × claim+complete
    assert any(e["actor"] == "driver" and e["to_state"] == "SUCCEEDED"
               for e in transitions)
