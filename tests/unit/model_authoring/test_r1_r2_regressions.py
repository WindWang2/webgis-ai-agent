"""R1/R2 修复回归网（探针结论固化为套件测试）。

覆盖：OUTPUT_KIND_INVALID、publish pointer_stale（幂等收尾）、
PARAMS_UNRESOLVABLE（run/instantiate 双路）、REF_TOO_LONG、
PARAM_NOT_FOUND、IntegrityError→DRAFT_EXISTS 归一、run 实例归属项目
派生（跨项目草稿不泄漏）、primary_output wire id。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.schemas.model_authoring_schema import ModelDocument
from app.services.model_authoring.compile import compile_document
from app.services.model_authoring.service import (
    ModelAuthoringError,
    ModelAuthoringService,
)
from app.services.model_authoring.store import (
    ModelDraftStore,
    ModelStoreError,
)
from app.services.model_authoring.validate import validate_document

pytestmark = pytest.mark.heavy


def _fac():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _doc() -> ModelDocument:
    return ModelDocument.model_validate({
        "schema_version": "1",
        "name": "回归网模型",
        "source": {"kind": "manual", "ref": ""},
        "params": [
            {"name": "secret_field", "kind": "string", "required": False,
             "default": {"value": "alice-default"}},
        ],
        "nodes": [
            {"node_id": "facilities", "kind": "data_input", "role": "subject"},
            {"node_id": "buffered", "kind": "transform", "transform_op": "buffer"},
            {"node_id": "by_cat", "kind": "analysis",
             "capability": "category_breakdown"},
            {"node_id": "stats_out", "kind": "output"},
        ],
        "edges": [
            {"from_node": "facilities", "from_port": "subject",
             "to_node": "buffered", "to_port": "in"},
            {"from_node": "buffered", "from_port": "out",
             "to_node": "by_cat", "to_port": "in"},
            {"from_node": "by_cat", "from_port": "out",
             "to_node": "stats_out", "to_port": "in"},
        ],
        "output_node_ids": ["stats_out"],
    })


def _env(monkeypatch, tmp_path):
    fac = _fac()
    from app.services.workflow_runtime import service as SV
    from app.services.workflow_runtime.registry import PackageRegistry
    from app.services.workflow_runtime.reuse import ReuseIndex
    from app.services.workflow_runtime.store import InstanceStore

    runtime = SV.WorkflowRuntimeService(
        store=InstanceStore(factory=fac),
        registry=PackageRegistry(factory=fac),
        reuse_index=ReuseIndex(factory=fac))
    svc = ModelAuthoringService(
        store=ModelDraftStore(session_factory=fac), runtime=runtime)
    from app.services import project_artifact_promotion as _pap

    monkeypatch.setattr(_pap, "content_store_root", lambda: tmp_path,
                        raising=False)
    return svc, runtime


class TestOutputKindGate:
    def test_output_node_ids_non_output_rejected(self):
        doc = _doc().model_copy(update={"output_node_ids": ["buffered"]})
        findings = validate_document(doc)
        assert any(f.code == "OUTPUT_KIND_INVALID" and f.severity == "error"
                   for f in findings)
        compiled, cf = compile_document(doc, model_slug="kindgate")
        assert compiled is None
        assert any(f.code == "OUTPUT_KIND_INVALID" for f in cf)

    def test_primary_output_is_wire_id(self):
        doc = _doc()
        compiled, _ = compile_document(doc, model_slug="wireid")
        assert compiled is not None
        assert compiled.package.compiled_form["typed_dag"][
            "primary_output"] == "stats_out"


class TestPointerStale:
    def test_conflicting_pointer_writes_stale_receipt(self, monkeypatch, tmp_path):
        """持续 CAS 冲突 → 成功返回 + pointer_stale=True + 包已发布。"""
        svc, runtime = _env(monkeypatch, tmp_path)
        asyncio.run(svc.create_draft(
            project_id="p1", document=_doc(), model_slug="m_stale"))
        pub = asyncio.run(svc.publish_model(
            project_id="p1", model_slug="m_stale", owner_scope="u:a"))
        assert pub["pointer_stale"] is False
        # 推进 revision（模拟并发编辑赢得 CAS），再让 record_publication
        # 恒冲突：指向不存在的 revision → 走幂等收尾路径。
        from app.services.model_authoring import store as ST

        orig = ST.ModelDraftStore.record_publication

        def _always_conflict(self, **kwargs):
            raise ModelStoreError("REVISION_CONFLICT", "forced")

        monkeypatch.setattr(ST.ModelDraftStore, "record_publication",
                            _always_conflict)
        pub2 = asyncio.run(svc.publish_model(
            project_id="p1", model_slug="m_stale", owner_scope="u:a",
            change="patch"))
        assert pub2["pointer_stale"] is True
        row = runtime.registry.resolve(
            "umodel_m_stale", owner_scope="u:a", require_published=True)
        assert row is not None and row["status"] == "published"
        _ = orig


class TestParamsUnresolvable:
    def test_instantiate_missing_draft_with_values_rejected(
            self, monkeypatch, tmp_path):
        svc, _rt = _env(monkeypatch, tmp_path)
        asyncio.run(svc.create_draft(
            project_id="p1", document=_doc(), model_slug="m_pu"))
        asyncio.run(svc.publish_model(
            project_id="p1", model_slug="m_pu", owner_scope="u:a"))
        draft = asyncio.run(svc.get_draft("p1", "m_pu"))
        asyncio.run(svc.delete_draft(
            project_id="p1", model_slug="m_pu",
            expected_revision=int(draft["revision_no"])))
        # 无草稿无参数：包默认可实例化（P3-4/R1 P3-7 语义）
        inst = asyncio.run(svc.instantiate_model(
            project_id="p1", model_slug="m_pu", owner_scope="u:a",
            session_id="s1"))
        assert inst["instance"]["instance_id"]
        # 无草稿但给了参数：typed 拒绝
        with pytest.raises(ModelAuthoringError) as ei:
            asyncio.run(svc.instantiate_model(
                project_id="p1", model_slug="m_pu", owner_scope="u:a",
                session_id="s1", param_values={"secret_field": "x"}))
        assert ei.value.code == "PARAMS_UNRESOLVABLE"


class TestRunProjectDerivation:
    def test_run_uses_instance_project_not_caller(self, monkeypatch, tmp_path):
        """跨项目同 slug 草稿不泄漏（R1 P1-1 探针形态固化为回归测试）。"""
        svc, runtime = _env(monkeypatch, tmp_path)
        asyncio.run(svc.create_draft(
            project_id="p_alice", document=_doc(), model_slug="m_leak"))
        asyncio.run(svc.publish_model(
            project_id="p_alice", model_slug="m_leak", owner_scope="u:a"))
        # 受害项目同 slug 草稿：秘密 default
        victim_doc = _doc().model_copy(deep=True)
        victim_doc.params[0].default.value = "VICTIM-SECRET-TOKEN"
        asyncio.run(svc.create_draft(
            project_id="p_victim", document=victim_doc, model_slug="m_leak"))
        from app.services.session_data import session_data_manager

        ref = asyncio.run(session_data_manager.store(
            "s1", {"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point",
                                                 "coordinates": [0, 0]},
                 "properties": {"category": "a"}}]}, prefix="regdata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p_alice", model_slug="m_leak", owner_scope="u:a",
            session_id="s1", input_bindings={"facilities": ref}))
        run = asyncio.run(svc.run_model_instance(
            instance_id=inst["instance"]["instance_id"], owner_scope="u:a",
            project_id="p_victim", param_values={}, deadline_s=60.0))
        # node_params 解析自实例归属项目（p_alice）文档，victim 秘密不出现
        assert "VICTIM-SECRET-TOKEN" not in repr(run.get("node_params"))


class TestStoreAndBinderGuards:
    def test_integrity_error_normalized(self, monkeypatch):
        """select-then-insert 竞态（存在性检查放行、commit 撞唯一约束）
        → 归一 typed DRAFT_EXISTS 而非裸 IntegrityError。"""
        import sqlalchemy as sa
        from sqlalchemy.orm import Session

        fac = _fac()
        store = ModelDraftStore(session_factory=fac)
        doc = _doc()

        def _boom(self):
            raise sa.exc.IntegrityError(
                "INSERT", {}, Exception("UNIQUE constraint failed"))

        monkeypatch.setattr(Session, "commit", _boom)
        with pytest.raises(ModelStoreError) as ei:
            store.create_draft(
                project_id="p1", model_slug="race",
                document=doc.model_dump(mode="json"),
                document_fingerprint=doc.document_fingerprint(),
                name=doc.name)
        assert ei.value.code == "DRAFT_EXISTS"

    def test_ref_too_long_typed(self, monkeypatch, tmp_path):
        """>96 字符 ref → 入口 typed 拒绝（形状检查先于 DB/dag 工作）。"""
        _svc, runtime = _env(monkeypatch, tmp_path)
        from app.services.model_authoring.bindings import (
            BindingError,
            ModelInstanceBinder,
        )

        runtime.store.create_instance(
            package_id="umodel_x", package_version="1.0.0",
            package_fingerprint="f" * 64, owner_scope="u:a",
            session_id="s1", node_specs=[{"node_id": "facilities",
                                          "optional": False}])
        binder = ModelInstanceBinder(runtime)
        with pytest.raises(BindingError) as ei:
            asyncio.run(binder.bind_inputs(
                "nonexistent", owner_scope="u:a", session_id="s1",
                bindings={"facilities": "ref:" + "x" * 120}))
        assert ei.value.code == "REF_TOO_LONG"
        with pytest.raises(BindingError) as ei2:
            asyncio.run(binder.bind_inputs(
                "nonexistent", owner_scope="u:a", session_id="s1",
                bindings={"facilities": "   "}))
        assert ei2.value.code == "EMPTY_REF"

    def test_param_not_found_patch(self, monkeypatch, tmp_path):
        svc, _rt = _env(monkeypatch, tmp_path)
        asyncio.run(svc.create_draft(
            project_id="p1", document=_doc(), model_slug="m_pf"))
        from app.tools.model_authoring_tools import _PatchError, _apply_patch
        from app.schemas.model_authoring_schema import (
            ModelEdge,
            ModelLayoutPos,
            ModelNode,
            ModelParamSpec,
        )

        draft = asyncio.run(svc.get_draft("p1", "m_pf"))
        doc = ModelDocument.model_validate(draft["document"])
        with pytest.raises(_PatchError) as ei:
            _apply_patch(doc, [{"op": "remove_param", "name": "ghost"}],
                         ModelNode, ModelEdge, ModelParamSpec,
                         ModelLayoutPos)
        assert ei.value.code == "PARAM_NOT_FOUND"
