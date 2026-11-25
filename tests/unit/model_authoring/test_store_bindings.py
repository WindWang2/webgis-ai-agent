"""模型草稿 store / service 参数裁决 / 实例绑定单元测试。

临时 SQLite（StaticPool :memory:）承载真实 SQLAlchemy CAS 行为；
bindings 用注入的 runtime stub（哑 store + 真 session_data），不触 driver。
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.schemas.model_authoring_schema import (
    ModelDocument,
    ModelEdge,
    ModelNode,
    ModelParamSpec,
)
from app.services.model_authoring.bindings import (
    BindingError,
    ModelInstanceBinder,
)
from app.services.model_authoring.service import (
    ModelAuthoringError,
    resolve_node_params,
    validate_param_values,
)
from app.services.model_authoring.store import (
    ModelDraftStore,
    ModelStoreError,
)


def _sqlite_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def factory():
    return _sqlite_factory()


@pytest.fixture
def store(factory):
    return ModelDraftStore(session_factory=factory)


def _doc(name: str = "测试模型") -> ModelDocument:
    return ModelDocument(
        name=name,
        source={"kind": "manual"},
        nodes=[
            ModelNode(node_id="src", kind="data_input", role="facility"),
            ModelNode(node_id="out1", kind="output"),
        ],
        edges=[ModelEdge(from_node="src", to_node="out1")],
        output_node_ids=["out1"],
    )


# ── store：CRUD + CAS ─────────────────────────────────────────────────


class TestModelDraftStore:
    def test_create_and_get(self, store):
        doc = _doc()
        row = store.create_draft(
            project_id="p1", model_slug="poi_stats", document=doc.model_dump(mode="json"),
            document_fingerprint=doc.document_fingerprint(), name=doc.name,
            source_kind="manual")
        assert row["revision_no"] == 1
        got = store.get_draft("p1", "poi_stats")
        assert got is not None and got["name"] == "测试模型"
        assert got["document"]["schema_version"] == "1"

    def test_slug_unique_in_project(self, store):
        doc = _doc()
        store.create_draft(
            project_id="p1", model_slug="poi_stats", document=doc.model_dump(mode="json"),
            document_fingerprint=doc.document_fingerprint(), name=doc.name)
        with pytest.raises(ModelStoreError) as ei:
            store.create_draft(
                project_id="p1", model_slug="poi_stats",
                document=doc.model_dump(mode="json"),
                document_fingerprint=doc.document_fingerprint(), name=doc.name)
        assert ei.value.code == "DRAFT_EXISTS"

    def test_same_slug_other_project_ok(self, store):
        doc = _doc()
        store.create_draft(
            project_id="p1", model_slug="poi_stats", document=doc.model_dump(mode="json"),
            document_fingerprint=doc.document_fingerprint(), name=doc.name)
        row = store.create_draft(
            project_id="p2", model_slug="poi_stats", document=doc.model_dump(mode="json"),
            document_fingerprint=doc.document_fingerprint(), name=doc.name)
        assert row["project_id"] == "p2"

    def test_invalid_slug_rejected(self, store):
        doc = _doc()
        with pytest.raises(ModelStoreError) as ei:
            store.create_draft(
                project_id="p1", model_slug="Bad-Slug", document=doc.model_dump(mode="json"),
                document_fingerprint=doc.document_fingerprint(), name=doc.name)
        assert ei.value.code == "INVALID_SLUG"

    def test_cas_update_conflict(self, store):
        doc = _doc()
        store.create_draft(
            project_id="p1", model_slug="poi_stats", document=doc.model_dump(mode="json"),
            document_fingerprint=doc.document_fingerprint(), name=doc.name)
        doc2 = _doc("改名")
        with pytest.raises(ModelStoreError) as ei:
            store.update_document(
                project_id="p1", model_slug="poi_stats",
                document=doc2.model_dump(mode="json"),
                document_fingerprint=doc2.document_fingerprint(),
                expected_revision=99)
        assert ei.value.code == "REVISION_CONFLICT"
        updated = store.update_document(
            project_id="p1", model_slug="poi_stats",
            document=doc2.model_dump(mode="json"),
            document_fingerprint=doc2.document_fingerprint(),
            expected_revision=1)
        assert updated["revision_no"] == 2
        assert updated["document_fingerprint"] != doc.document_fingerprint()

    def test_cas_delete_and_publication_pointer(self, store):
        doc = _doc()
        store.create_draft(
            project_id="p1", model_slug="poi_stats", document=doc.model_dump(mode="json"),
            document_fingerprint=doc.document_fingerprint(), name=doc.name)
        updated = store.record_publication(
            project_id="p1", model_slug="poi_stats",
            package_id="umodel_poi_stats", version="1.1.0", expected_revision=1)
        assert updated["latest_published_package_id"] == "umodel_poi_stats"
        assert store.delete_draft(
            project_id="p1", model_slug="poi_stats", expected_revision=2)
        assert store.get_draft("p1", "poi_stats") is None

    def test_document_too_large_rejected(self, store):
        doc = _doc()
        dump = doc.model_dump(mode="json")
        dump["description"] = "x" * 300_000
        with pytest.raises(ModelStoreError) as ei:
            store.create_draft(
                project_id="p1", model_slug="big", document=dump,
                document_fingerprint=doc.document_fingerprint(),
                name=doc.name)
        assert ei.value.code == "DOCUMENT_TOO_LARGE"


# ── 参数裁决（user > default；非法拒绝）────────────────────────────────


class TestParamResolution:
    def _params(self):
        return [
            ModelParamSpec(name="buf", kind="number", required=True,
                           default={"value": 800}, minimum=0, maximum=5000),
            ModelParamSpec(name="fld", kind="field", required=False,
                           input_node="src"),
            ModelParamSpec(name="win", kind="time_window", required=False,
                           default={"value": "2024-01-01/2024-12-31"}),
        ]

    def test_defaults_and_override(self):
        vals = validate_param_values(self._params(), {})
        assert vals["buf"] == 800
        assert vals["win"] == "2024-01-01/2024-12-31"
        assert "fld" not in vals  # 可选缺省诚实缺席
        vals2 = validate_param_values(self._params(), {"buf": 1000, "fld": "count"})
        assert vals2["buf"] == 1000 and vals2["fld"] == "count"

    def test_required_missing_rejected(self):
        ps = [ModelParamSpec(name="buf", kind="number", required=True)]
        with pytest.raises(ModelAuthoringError) as ei:
            validate_param_values(ps, {})
        assert ei.value.code == "REQUIRED_PARAM_MISSING"

    def test_unknown_and_type_and_range(self):
        ps = self._params()
        with pytest.raises(ModelAuthoringError) as ei:
            validate_param_values(ps, {"nope": 1})
        assert ei.value.code == "UNKNOWN_PARAM"
        with pytest.raises(ModelAuthoringError) as ei:
            validate_param_values(ps, {"buf": "abc"})
        assert ei.value.code == "PARAM_TYPE_MISMATCH"
        with pytest.raises(ModelAuthoringError) as ei:
            validate_param_values(ps, {"buf": 9999})
        assert ei.value.code == "PARAM_OUT_OF_RANGE"
        with pytest.raises(ModelAuthoringError) as ei:
            validate_param_values(ps, {"win": "not-a-window"})
        assert ei.value.code == "PARAM_TYPE_MISMATCH"

    def test_node_params_reference_resolution(self):
        doc = ModelDocument(
            name="t", source={"kind": "manual"},
            params=self._params(),
            nodes=[
                ModelNode(node_id="src", kind="data_input", role="facility"),
                ModelNode(
                    node_id="buf1", kind="transform", transform_op="buffer",
                    params={"values": {"distance": {"value": "$param:buf"},
                                       "fixed": {"value": "m"}}}),
                ModelNode(node_id="out1", kind="output"),
            ],
            edges=[ModelEdge(from_node="src", to_node="buf1"),
                   ModelEdge(from_node="buf1", to_node="out1")],
            output_node_ids=["out1"],
        )
        np = resolve_node_params(doc, {"buf": 1200})
        assert np["buf1"]["distance"] == 1200
        assert np["buf1"]["fixed"] == "m"
        assert "src" not in np
        # 可选参数未提供 → 键省略
        np2 = resolve_node_params(doc, {})
        assert np2["buf1"]["distance"] == 800


# ── bindings：真实 store + 真 session_data ────────────────────────────


class _StubRuntime:
    """哑 runtime：store 来自 factory；_instance_dag 返回注入 DAG。"""

    def __init__(self, factory, dag):
        from app.services.workflow_runtime.store import InstanceStore

        self.store = InstanceStore(factory=factory)
        self._dag = dag

    async def _instance_dag(self, inst):
        return self._dag


def _dag():
    return {
        "nodes": [
            {"node_id": "data:src", "kind": "data_input"},
            {"node_id": "output:out", "kind": "output"},
        ],
        "edges": [],
    }


def _make_instance(factory, dag):
    from app.services.workflow_runtime.store import InstanceStore

    store = InstanceStore(factory=factory)
    return store.create_instance(
        package_id="umodel_x", package_version="1.0.0",
        package_fingerprint="f" * 64, owner_scope="u:1",
        session_id="sess-1",
        node_specs=[{"node_id": n["node_id"], "optional": False}
                    for n in dag["nodes"]])


class TestBindings:
    async def test_bind_ok_and_passthrough_ready(self, factory):
        dag = _dag()
        inst = _make_instance(factory, dag)
        rt = _StubRuntime(factory, dag)
        from app.services.session_data import session_data_manager

        ref = await session_data_manager.store(
            "sess-1", {"features": [{"id": 1}]}, "poi")
        binder = ModelInstanceBinder(rt)
        out = await binder.bind_inputs(
            inst["instance_id"], owner_scope="u:1",
            session_id="sess-1",
            bindings={"data:src": ref})
        assert out["data:src"]["ok"] is True
        # 同 ref 重复绑定 = 幂等
        out2 = await binder.bind_inputs(
            inst["instance_id"], owner_scope="u:1",
            session_id="sess-1",
            bindings={"data:src": ref})
        assert out2["data:src"]["code"] == "OK_IDEMPOTENT"

    async def test_ref_not_found_honest(self, factory):
        dag = _dag()
        inst = _make_instance(factory, dag)
        rt = _StubRuntime(factory, dag)
        binder = ModelInstanceBinder(rt)
        out = await binder.bind_inputs(
            inst["instance_id"], owner_scope="u:1",
            session_id="sess-other",
            bindings={"data:src": "ghost-ref"})
        assert out["data:src"]["ok"] is False
        assert out["data:src"]["code"] == "REF_NOT_FOUND"

    async def test_not_data_input_rejected(self, factory):
        dag = _dag()
        inst = _make_instance(factory, dag)
        rt = _StubRuntime(factory, dag)
        binder = ModelInstanceBinder(rt)
        from app.services.session_data import session_data_manager

        ref = await session_data_manager.store(
            "sess-1", {"features": [{"id": 1}]}, "poi")
        out = await binder.bind_inputs(
            inst["instance_id"], owner_scope="u:1",
            session_id="sess-1",
            bindings={"output:out": ref})
        assert out["output:out"]["code"] == "NOT_DATA_INPUT"

    async def test_empty_bindings_rejected(self, factory):
        dag = _dag()
        inst = _make_instance(factory, dag)
        rt = _StubRuntime(factory, dag)
        binder = ModelInstanceBinder(rt)
        with pytest.raises(BindingError) as ei:
            await binder.bind_inputs(
                inst["instance_id"], owner_scope="u:1",
                session_id="sess-1", bindings={})
        assert ei.value.code == "NO_BINDINGS"

    async def test_instance_not_found(self, factory):
        dag = _dag()
        rt = _StubRuntime(factory, dag)
        binder = ModelInstanceBinder(rt)
        with pytest.raises(BindingError) as ei:
            await binder.bind_inputs(
                "ghost", owner_scope="u:1",
                session_id="sess-1",
                bindings={"data:src": "r"})
        assert ei.value.code == "INSTANCE_NOT_FOUND"
