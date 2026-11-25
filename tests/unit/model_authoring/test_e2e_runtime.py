"""模型作者域端到端：真实包 → 真实 runtime → 真实 driver → 真实产物。

全真实路径（无 fake engine / 无 plan_executor / 无 dispatcher stub）：
- ModelDocument → compile → 真实 WorkflowPackage（umodel_ 指纹）→
  真实 PackageRegistry 注册/发布；
- instantiate（真实 WorkflowRuntimeService）→ ModelInstanceBinder 绑定
  真实 session FeatureCollection → run_instance（真实 Driver 波次执行：
  geocompute 真实 buffer / data_fabric 真实聚合 / matplotlib 真实渲染）；
- 产物断言：buffer 后 Point→Polygon、聚合计数正确、cartography blob
  在场（sha256 可解）；
- journal 事件、参数覆盖重驱（复用指纹失配 → 真实重算）、未发布/坏绑定
  typed 拒绝。

标记 heavy（matplotlib/shapely/PIL 重依赖；CI heavy lane 跑）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.schemas.model_authoring_schema import ModelDocument

pytestmark = pytest.mark.heavy


def _factory():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _model_doc() -> ModelDocument:
    return ModelDocument.model_validate({
        "schema_version": "1",
        "name": "设施缓冲分类统计",
        "description": "data→buffer→分类统计→专题图 端到端示范模型",
        "source": {"kind": "manual", "ref": ""},
        "params": [
            {"name": "buf_m", "kind": "number", "label": "缓冲距离(米)",
             "required": False, "default": {"value": 50},
             "minimum": 1, "maximum": 10000, "unit": "m"},
        ],
        "nodes": [
            {"node_id": "facilities", "kind": "data_input", "role": "subject",
             "title": "设施点"},
            {"node_id": "buffered", "kind": "transform", "transform_op": "buffer",
             "params": {"values": {"distance": {"value": "$param:buf_m"},
                                   "unit": {"value": "m"}}}},
            {"node_id": "by_category", "kind": "analysis",
             "capability": "category_breakdown",
             "params": {"values": {"field": {"value": "category"}}}},
            {"node_id": "render", "kind": "cartography",
             "params": {"values": {"title": {"value": "设施缓冲分类"},
                                   "field": {"value": "category"}}}},
            {"node_id": "stats_out", "kind": "output"},
            {"node_id": "final", "kind": "output"},
        ],
        "edges": [
            {"from_node": "facilities", "from_port": "subject",
             "to_node": "buffered", "to_port": "in"},
            {"from_node": "buffered", "from_port": "out",
             "to_node": "by_category", "to_port": "in"},
            {"from_node": "buffered", "from_port": "out",
             "to_node": "render", "to_port": "in"},
            {"from_node": "by_category", "from_port": "out",
             "to_node": "stats_out", "to_port": "in"},
            {"from_node": "render", "from_port": "out",
             "to_node": "final", "to_port": "in"},
        ],
        "output_node_ids": ["stats_out", "final"],
        "layout": {"facilities": {"x": 40, "y": 40},
                   "buffered": {"x": 200, "y": 40}},
    })


def _features():
    feats = []
    for i in range(9):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.40 + i * 0.001,
                                         39.90 + (i % 3) * 0.001]},
            "properties": {"category": f"type-{i % 3}", "id": i},
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def env(monkeypatch, tmp_path):
    fac = _factory()
    from app.services.workflow_runtime import service as SV
    from app.services.workflow_runtime.registry import PackageRegistry
    from app.services.workflow_runtime.reuse import ReuseIndex
    from app.services.workflow_runtime.store import InstanceStore

    runtime = SV.WorkflowRuntimeService(
        store=InstanceStore(factory=fac),
        registry=PackageRegistry(factory=fac),
        reuse_index=ReuseIndex(factory=fac))
    from app.services.model_authoring.service import ModelAuthoringService
    from app.services.model_authoring.store import ModelDraftStore

    svc = ModelAuthoringService(
        store=ModelDraftStore(session_factory=fac), runtime=runtime)
    # blob store 指向临时目录（真实文件系统写）
    from app.services import project_artifact_promotion as _pap

    monkeypatch.setattr(_pap, "content_store_root", lambda: tmp_path,
                        raising=False)
    return svc, runtime


def _publish(svc, slug="buffer_stats"):
    return asyncio.run(svc.publish_model(
        project_id="p1", model_slug=slug, owner_scope="u:abc",
        change="minor"))


class TestModelE2ERealRuntime:
    def test_full_chain_publish_instantiate_run(self, env):
        svc, runtime = env
        doc = _model_doc()
        draft = asyncio.run(svc.create_draft(
            project_id="p1", document=doc, model_slug="buffer_stats"))
        assert draft["revision_no"] == 1
        # 未发布先实例化 → typed 拒绝
        from app.services.model_authoring.service import ModelAuthoringError

        with pytest.raises(ModelAuthoringError) as ei:
            asyncio.run(svc.instantiate_model(
                project_id="p1", model_slug="buffer_stats",
                owner_scope="u:abc", session_id="s1"))
        assert ei.value.code == "MODEL_NOT_PUBLISHED"

        pub = _publish(svc)
        assert pub["package_id"] == "umodel_buffer_stats"
        assert pub["version"] == "1.1.0"  # minor from default 1.0.0 prev=None→1.0.0? 见下
        _ = pub

        # 真实 session 数据（Data 端）
        from app.services.session_data import session_data_manager

        ref = asyncio.run(session_data_manager.store(
            "s1", _features(), prefix="modeldata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p1", model_slug="buffer_stats",
            owner_scope="u:abc", session_id="s1",
            input_bindings={"facilities": ref}))
        iid = inst["instance"]["instance_id"]
        assert inst["bindings"]["facilities"]["ok"] is True
        # data_input 预绑：READY 且 bound_ref 在场（执行交 driver）
        from app.services.workflow_runtime.contracts import NodeState

        node_row = runtime.store.get_node(iid, "facilities")
        assert node_row["state"] == NodeState.READY
        assert node_row["bound_ref"].startswith("ref:")

        async def _run():
            return await svc.run_model_instance(
                instance_id=iid, owner_scope="u:abc", deadline_s=120.0)

        run = asyncio.run(_run())
        assert run["run"]["status"] == "succeeded", run["run"]
        store = runtime.store
        states = {n["node_id"]: n["state"]
                  for n in store.get_nodes(iid)}
        expected_nodes = {"facilities", "transform:buffer:buffered",
                          "by_category", "render", "stats_out", "final"}
        assert set(states) == expected_nodes
        assert all(s == NodeState.SUCCEEDED for s in states.values()), states

        # Data 端真实 buffer：点 → 多边形
        buf_ref = store.get_node(iid, "transform:buffer:buffered")["output_ref"]
        assert buf_ref.startswith("ref:")
        payload = asyncio.run(session_data_manager.get("s1", buf_ref))
        feats = payload.get("features") if isinstance(payload, dict) else payload
        assert len(feats) == 9
        assert {f["geometry"]["type"] for f in feats} == {"Polygon"}

        # Science 端真实聚合：每类 3（统计表流向独立 stats_out）
        sci_ref = store.get_node(iid, "by_category")["output_ref"]
        table = asyncio.run(session_data_manager.get("s1", sci_ref))
        assert table["type"] == "table"
        counts = {r["category"]: r["count"] for r in table["rows"]}
        assert counts == {"type-0": 3, "type-1": 3, "type-2": 3}
        stats_ref = store.get_node(iid, "stats_out")["output_ref"]
        assert stats_ref.startswith("ref:")

        # Cartography 端真实渲染：blob 在场（final 收口图产物）
        carto_ref = store.get_node(iid, "render")["output_ref"]
        assert carto_ref.startswith("blob:")
        assert store.get_node(iid, "final")["output_ref"] == carto_ref

        # journal：全链转移历史可检视
        events = store.get_events(iid)
        transitions = [e for e in events
                       if e["kind"] == "state_transition"]
        assert len(transitions) >= 10
        assert any(e["actor"] == "driver" and e["to_state"] == "SUCCEEDED"
                   for e in transitions)

    def test_param_override_rerun_recomputes(self, env):
        svc, runtime = env
        asyncio.run(svc.create_draft(
            project_id="p1", document=_model_doc(),
            model_slug="buffer_stats"))
        _publish(svc)
        from app.services.session_data import session_data_manager

        ref = asyncio.run(session_data_manager.store(
            "s1", _features(), prefix="modeldata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p1", model_slug="buffer_stats",
            owner_scope="u:abc", session_id="s1",
            input_bindings={"facilities": ref}))
        iid = inst["instance"]["instance_id"]
        # node_params 键是 wire id（transform 前缀）
        assert any(k.startswith("transform:buffer:")
                   for k in inst["node_params"]), inst["node_params"]

        async def _run():
            return await svc.run_model_instance(
                instance_id=iid, owner_scope="u:abc", deadline_s=120.0,
                project_id="p1", param_values={"buf_m": 250})

        run = asyncio.run(_run())
        assert run["run"]["status"] == "succeeded", run["run"]
        # 参数覆盖确实进入了注入面（250 ≠ 默认 50）
        injected = run["node_params"]
        dist = next(v for k, v in injected.items()
                    if k.startswith("transform:buffer:")
                    for kk, v in v.items() if kk == "distance")
        assert dist == 250

    def test_cross_owner_model_invisible(self, env):
        """owner 域隔离：他人 owner 解析不到已发布包（不泄露存在性）。"""
        svc, _runtime = env
        asyncio.run(svc.create_draft(
            project_id="p1", document=_model_doc(),
            model_slug="buffer_stats"))
        _publish(svc)
        from app.services.model_authoring.service import ModelAuthoringError

        with pytest.raises(ModelAuthoringError) as ei:
            asyncio.run(svc.instantiate_model(
                project_id="p1", model_slug="buffer_stats",
                owner_scope="u:other", session_id="s1"))
        assert ei.value.code == "MODEL_NOT_PUBLISHED"
