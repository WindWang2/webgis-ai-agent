"""内置领域模板测试（WP6）：validate/compile 契约 + 指纹确定性 + 真实 driver。

覆盖面：

- 四个内置模板逐个 ``validate_document`` 零 error 级 finding（发布门契约）；
- 每模板 ``compile_document`` 成功（CompiledModel 非 None；package_id =
  ``umodel_<template_id>``）；
- 指纹确定性：同模板两次生成 → 两次编译指纹一致（原型非单例）；
- ``from_template`` 实例化改名后 ``template_id`` / ``source`` 保持
  （模板血统是审计事实）；
- facility_admin_count 真实 driver 端到端（heavy）：发布 → 实例化 →
  绑定真实 session FeatureCollection → run → SUCCEEDED，聚合行数正确
  （镜像 test_e2e_runtime 的真实栈 fixture，无任何 fake）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.services.model_authoring.compile import (
    MODEL_PACKAGE_PREFIX,
    compile_document,
)
from app.services.model_authoring.contracts import SEVERITY_ERROR
from app.services.model_authoring.templates import (
    BUILTIN_TEMPLATES,
    TEMPLATE_ENV_SAMPLE_STATS,
    TEMPLATE_FACILITY_ADMIN_COUNT,
    TEMPLATE_FACILITY_SERVICE_AREA,
    TEMPLATE_IDS,
    TEMPLATE_TWO_PERIOD_COMPARE,
    from_template,
    template_documents,
)
from app.services.model_authoring.validate import validate_document


# ── 静态契约：validate / compile / 指纹 / from_template ─────────────────


class TestTemplateStaticContract:
    def test_four_builtin_templates_present(self):
        assert len(BUILTIN_TEMPLATES) == 4
        expected = (
            TEMPLATE_FACILITY_ADMIN_COUNT,
            TEMPLATE_FACILITY_SERVICE_AREA,
            TEMPLATE_ENV_SAMPLE_STATS,
            TEMPLATE_TWO_PERIOD_COMPARE,
        )
        assert TEMPLATE_IDS == expected
        assert tuple(t().template_id for t in BUILTIN_TEMPLATES) == expected

    @pytest.mark.parametrize("template_id", list(TEMPLATE_IDS))
    def test_template_validates_zero_error(self, template_id: str):
        """逐模板：validate 零 error 级 finding（error 阻断编译/发布）。"""
        doc = from_template(template_id)
        assert doc.source.kind == "template"
        assert doc.source.ref == template_id
        assert doc.template_id == template_id
        findings = validate_document(doc)
        errors = [f for f in findings if f.severity == SEVERITY_ERROR]
        assert errors == [], (
            f"template {template_id} must validate clean, got: "
            f"{[(e.code, e.node_id, e.detail) for e in errors]}")

    @pytest.mark.parametrize("template_id", list(TEMPLATE_IDS))
    def test_template_compiles(self, template_id: str):
        """逐模板：compile_document 成功且包 id = umodel_<template_id>。"""
        doc = from_template(template_id)
        compiled, findings = compile_document(
            doc, model_slug=template_id, version="1.0.0")
        assert compiled is not None, (
            f"template {template_id} must compile; findings: "
            f"{[f.to_bounded() for f in findings]}")
        assert compiled.package.package_id == MODEL_PACKAGE_PREFIX + template_id
        assert compiled.package.fingerprint
        assert compiled.node_id_map, "wire bridge must be present"

    @pytest.mark.parametrize("template_id", list(TEMPLATE_IDS))
    def test_template_fingerprint_deterministic(self, template_id: str):
        """原型非单例：两次独立生成同一模板 → 编译指纹一致。"""
        fp_a = compile_document(
            from_template(template_id), model_slug=template_id,
            version="1.0.0")[0].package.fingerprint
        fp_b = compile_document(
            from_template(template_id), model_slug=template_id,
            version="1.0.0")[0].package.fingerprint
        assert fp_a == fp_b

    def test_template_documents_returns_fresh_instances(self):
        """template_documents() 每次返回全新实例（互不别名）。"""
        docs_a = template_documents()
        docs_b = template_documents()
        assert len(docs_a) == len(docs_b) == 4
        for a, b in zip(docs_a, docs_b):
            assert a is not b
            assert a.document_fingerprint() == b.document_fingerprint()

    def test_from_template_rename_keeps_lineage(self):
        """实例化改名后 template_id/source 保持（血统不被覆盖）。"""
        doc = from_template(
            TEMPLATE_FACILITY_ADMIN_COUNT, name="我的学校统计模型",
            description="自定义描述")
        assert doc.name == "我的学校统计模型"
        assert doc.description == "自定义描述"
        assert doc.template_id == TEMPLATE_FACILITY_ADMIN_COUNT
        assert doc.source.kind == "template"
        assert doc.source.ref == TEMPLATE_FACILITY_ADMIN_COUNT
        # 原型词表不受实例化影响（admin_field 参数仍在）
        assert any(p.name == "admin_field" for p in doc.params)

    def test_from_template_unknown_id_rejected(self):
        with pytest.raises(ValueError):
            from_template("not_a_template")

    def test_table_products_only_reach_output_sinks(self):
        """运行时能力边界：聚合表产物只进 output 汇点（绝不喂计算端口）。

        descriptor 契约 admin_aggregate_table feature_count=0 —— 喂任何
        required=True 端口会被绑定门拒绝；模板纪律 = 聚合输出一律接
        output 汇点。
        """
        for doc in template_documents():
            sinks = set(doc.output_node_ids)
            for edge in doc.edges:
                src = next(
                    n for n in doc.nodes
                    if n.node_id == edge.from_node)
                if src.kind == "analysis":
                    dst = next(
                        n for n in doc.nodes
                        if n.node_id == edge.to_node)
                    assert dst.kind == "output", (
                        f"{doc.template_id}: analysis output must feed an "
                        f"output sink, got {dst.kind}:{dst.node_id}")
                    assert edge.to_node in sinks


# ── 真实 driver 端到端（heavy）：facility_admin_count ────────────────────


def _factory():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _facility_features():
    """9 个设施点：district ∈ {east, west}（5/4），category 三类各 3。"""
    feats = []
    for i in range(9):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [116.40 + i * 0.001,
                                         39.90 + (i % 3) * 0.001]},
            "properties": {"category": f"type-{i % 3}",
                           "district": "east" if i % 2 == 0 else "west",
                           "id": i},
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """真实栈 fixture（镜像 test_e2e_runtime）：临时 SQLite 工厂注入 +
    blob root 指向 tmp_path（cartography 真实落盘）。"""
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
    from app.services import project_artifact_promotion as _pap

    monkeypatch.setattr(_pap, "content_store_root", lambda: tmp_path,
                        raising=False)
    return svc, runtime


@pytest.mark.heavy
class TestFacilityAdminCountRealDriver:
    def test_publish_instantiate_run_succeeds(self, env):
        """A 模板全链：发布 → 实例化 → 真实 FC 绑定 → 真实 driver → SUCCEEDED。"""
        svc, runtime = env
        doc = from_template(TEMPLATE_FACILITY_ADMIN_COUNT)
        draft = asyncio.run(svc.create_draft(
            project_id="p-tpl", document=doc,
            model_slug=TEMPLATE_FACILITY_ADMIN_COUNT))
        assert draft["revision_no"] == 1
        assert draft["template_id"] == TEMPLATE_FACILITY_ADMIN_COUNT
        assert draft["source_kind"] == "template"

        pub = asyncio.run(svc.publish_model(
            project_id="p-tpl", model_slug=TEMPLATE_FACILITY_ADMIN_COUNT,
            owner_scope="u:tpl"))
        assert pub["package_id"] == \
            MODEL_PACKAGE_PREFIX + TEMPLATE_FACILITY_ADMIN_COUNT

        # 真实 session 数据（Data 端）
        from app.services.session_data import session_data_manager

        ref = asyncio.run(session_data_manager.store(
            "s-tpl", _facility_features(), prefix="tpldata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p-tpl", model_slug=TEMPLATE_FACILITY_ADMIN_COUNT,
            owner_scope="u:tpl", session_id="s-tpl",
            input_bindings={"facilities": ref}))
        iid = inst["instance"]["instance_id"]
        assert inst["bindings"]["facilities"]["ok"] is True
        # admin_field 参数经 $param 引用落 default（district）
        assert inst["params_resolved"]["admin_field"] == "district"

        # project_id + param_values={} → 文档 default（district）经
        # resolve_node_params 注入（绕开 runtime _node_params 的
        # p.get("value")=None 覆盖缺陷 —— 见汇报中的契约问题 #3）。
        run = asyncio.run(svc.run_model_instance(
            instance_id=iid, owner_scope="u:tpl", deadline_s=120.0,
            project_id="p-tpl", param_values={}))
        assert run["run"]["status"] == "succeeded", run["run"]

        store = runtime.store
        states = {n["node_id"]: n["state"] for n in store.get_nodes(iid)}
        expected = {"facilities", "admin_stats", "render",
                    "stats_out", "map_out"}
        assert set(states) == expected, states
        from app.services.workflow_runtime.contracts import NodeState

        assert all(s == NodeState.SUCCEEDED for s in states.values()), states

        # Science 端真实聚合：east 5 / west 4（district 分组）
        sci_ref = store.get_node(iid, "admin_stats")["output_ref"]
        assert sci_ref.startswith("ref:")
        table = asyncio.run(session_data_manager.get("s-tpl", sci_ref))
        assert table["type"] == "table"
        counts = {r["district"]: r["count"] for r in table["rows"]}
        assert counts == {"east": 5, "west": 4}
        assert store.get_node(iid, "stats_out")["output_ref"] == sci_ref

        # Cartography 端真实渲染：blob 在场并被汇点收口
        carto_ref = store.get_node(iid, "render")["output_ref"]
        assert carto_ref.startswith("blob:")
        assert store.get_node(iid, "map_out")["output_ref"] == carto_ref

    def test_admin_field_param_override_changes_grouping(self, env):
        """参数覆盖（admin_field=category）经 node_params 真实进入聚合。"""
        svc, runtime = env
        doc = from_template(TEMPLATE_FACILITY_ADMIN_COUNT)
        asyncio.run(svc.create_draft(
            project_id="p-tpl", document=doc,
            model_slug=TEMPLATE_FACILITY_ADMIN_COUNT))
        asyncio.run(svc.publish_model(
            project_id="p-tpl", model_slug=TEMPLATE_FACILITY_ADMIN_COUNT,
            owner_scope="u:tpl"))
        from app.services.session_data import session_data_manager

        ref = asyncio.run(session_data_manager.store(
            "s-tpl2", _facility_features(), prefix="tpldata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p-tpl", model_slug=TEMPLATE_FACILITY_ADMIN_COUNT,
            owner_scope="u:tpl", session_id="s-tpl2",
            input_bindings={"facilities": ref},
            param_values={"admin_field": "category"}))
        assert inst["params_resolved"]["admin_field"] == "category"
        # 覆盖值出现在注入面（wire id 键）
        assert inst["node_params"]["admin_stats"] == {"admin_field": "category"}
        run = asyncio.run(svc.run_model_instance(
            instance_id=inst["instance"]["instance_id"],
            owner_scope="u:tpl", deadline_s=120.0,
            project_id="p-tpl", param_values={"admin_field": "category"}))
        assert run["run"]["status"] == "succeeded", run["run"]
        sci_ref = runtime.store.get_node(
            inst["instance"]["instance_id"], "admin_stats")["output_ref"]
        table = asyncio.run(session_data_manager.get("s-tpl2", sci_ref))
        counts = {r["category"]: r["count"] for r in table["rows"]}
        assert counts == {"type-0": 3, "type-1": 3, "type-2": 3}
