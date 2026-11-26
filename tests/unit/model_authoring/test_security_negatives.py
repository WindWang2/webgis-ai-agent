"""安全负例面测试（WP6）：恶意图 / 注入 / CAS 并发 / 跨租户 / 不可变 / 越权。

负例清单（每条都是 typed 拒绝，无旁路）：

- 33 节点 / 65 边文档 → pydantic ValidationError（编辑器上限先拦）；
- 非法 capability（路径穿越 fuzz 串 "../../etc"、"llm_exfil"、"a;b"）→
  validate CAPABILITY_UNKNOWN 且 publish 拒绝（MODEL_INVALID）；
- 非法 role（SQL 注入串 "; DROP TABLE"）→ validate ROLE_UNKNOWN error
  （ModelNode.role 无词法约束是有意的：词表裁决在 validate，与
  DATA_ROLES 单一事实源对齐）；
- 巨型参数值（>256B）→ BoundedValue 拒；$param 引用未声明参数 →
  validate PARAM_REF_UNKNOWN error；
- CAS 并发：两个并发 update_document 同 expected_revision → 恰一个成功
  一个 REVISION_CONFLICT；
- 跨租户：owner A 发布的包 owner B 实例化 → MODEL_NOT_PUBLISHED；
  list_model_catalog 对他 owner 不泄露版本行（versions 空）；
- 发布后不可变：patch 草稿再 publish patch → 新版本 v1.1.1；旧版本
  resolve("1.1.0") 指纹不变（历史版本不被改写）；
- 越权绑定：绑定他人 session 的 ref → 回执 REF_NOT_FOUND（与不存在
  ref 同码 —— 不泄露存在性）；
- deadline 预算：run(deadline_s=0.05)（服务层 clamp 到 1.0s）→ 运行
  诚实非终态/非成功，外层 asyncio.wait_for(30s) 保证断言不挂死（heavy）。
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.schemas.model_authoring_schema import (
    BoundedValue,
    ModelDocument,
    ModelNodeParams,
)
from app.services.model_authoring.contracts import (
    F_CAPABILITY_UNKNOWN,
    F_PARAM_REF_UNKNOWN,
    F_ROLE_UNKNOWN,
    SEVERITY_ERROR,
)
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


def _factory():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _minimal_doc(**overrides) -> ModelDocument:
    """合法最小模型（data_input → output），供逐负例打补丁。"""
    payload = {
        "schema_version": "1",
        "name": "安全负例基线模型",
        "source": {"kind": "manual", "ref": ""},
        "nodes": [
            {"node_id": "src", "kind": "data_input", "role": "subject"},
            {"node_id": "out", "kind": "output"},
        ],
        "edges": [
            {"from_node": "src", "from_port": "subject",
             "to_node": "out", "to_port": "in"},
        ],
        "output_node_ids": ["out"],
    }
    payload.update(overrides)
    return ModelDocument.model_validate(payload)


def _analysis_doc(capability: str) -> ModelDocument:
    """带非法 capability 的模型（其余结构合法）。"""
    return ModelDocument.model_validate({
        "schema_version": "1",
        "name": "恶意能力注入样张",
        "source": {"kind": "manual", "ref": ""},
        "nodes": [
            {"node_id": "src", "kind": "data_input", "role": "subject"},
            {"node_id": "evil", "kind": "analysis", "capability": capability},
            {"node_id": "out", "kind": "output"},
        ],
        "edges": [
            {"from_node": "src", "from_port": "subject",
             "to_node": "evil", "to_port": "in"},
            {"from_node": "evil", "from_port": "out",
             "to_node": "out", "to_port": "in"},
        ],
        "output_node_ids": ["out"],
    })


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
    svc = ModelAuthoringService(
        store=ModelDraftStore(session_factory=fac), runtime=runtime)
    from app.services import project_artifact_promotion as _pap

    monkeypatch.setattr(_pap, "content_store_root", lambda: tmp_path,
                        raising=False)
    return svc, runtime


# ── 恶意图：结构上限 ─────────────────────────────────────────────────────


class TestMaliciousGraphShapes:
    def test_33_nodes_rejected_by_pydantic(self):
        nodes = [{"node_id": "src", "kind": "data_input", "role": "subject"}]
        nodes += [{"node_id": f"t{i:02d}", "kind": "transform",
                   "transform_op": "buffer"} for i in range(31)]
        nodes.append({"node_id": "out", "kind": "output"})
        assert len(nodes) == 33
        with pytest.raises(ValidationError) as ei:
            ModelDocument.model_validate({
                "schema_version": "1", "name": "越界",
                "source": {"kind": "manual", "ref": ""},
                "nodes": nodes, "edges": [], "output_node_ids": []})
        assert "nodes exceed 32" in str(ei.value)

    def test_65_edges_rejected_by_pydantic(self):
        nodes = [{"node_id": "src", "kind": "data_input", "role": "subject"}]
        nodes += [{"node_id": f"t{i:02d}", "kind": "transform",
                   "transform_op": "buffer"} for i in range(30)]
        nodes.append({"node_id": "out", "kind": "output"})  # 32 节点恰在上限
        chain_edges = [
            {"from_node": "src", "from_port": "subject",
             "to_node": "t00", "to_port": "in"}]
        chain_edges += [
            {"from_node": f"t{i:02d}", "from_port": "out",
             "to_node": f"t{i + 1:02d}", "to_port": "in"}
            for i in range(29)]
        chain_edges.append({"from_node": "t29", "from_port": "out",
                            "to_node": "out", "to_port": "in"})
        assert len(chain_edges) == 31
        # 重复边灌到 65（文档层不去重 —— 上限先拦）
        edges = (chain_edges * 3)[:65]
        with pytest.raises(ValidationError) as ei:
            ModelDocument.model_validate({
                "schema_version": "1", "name": "边越界",
                "source": {"kind": "manual", "ref": ""},
                "nodes": nodes, "edges": edges, "output_node_ids": []})
        assert "edges exceed 64" in str(ei.value)

    @pytest.mark.parametrize("capability", [
        "../../etc", "llm_exfil", "a;b--", "admin_aggregation; DROP",
        "%2e%2e%2fadmin", "中文名",
    ])
    def test_fuzz_capability_rejected(self, capability: str):
        """fuzz capability → CAPABILITY_UNKNOWN error（词表裁决，无旁路）。"""
        doc = _analysis_doc(capability)
        findings = validate_document(doc)
        errs = [f for f in findings if f.severity == SEVERITY_ERROR]
        assert any(e.code == F_CAPABILITY_UNKNOWN and e.node_id == "evil"
                   for e in errs), errs
        compiled, _ = compile_document(doc, model_slug="evil_cap")
        assert compiled is None, "error findings must block compilation"


class TestPublishGateRejectsInvalid:
    def test_publish_rejects_unknown_capability(self, env):
        """error 级 finding 未清零 → publish typed 拒绝 MODEL_INVALID。"""
        svc, _runtime = env
        doc = _analysis_doc("../../etc")
        asyncio.run(svc.create_draft(
            project_id="p-sec", document=doc, model_slug="evil_cap"))
        with pytest.raises(ModelAuthoringError) as ei:
            asyncio.run(svc.publish_model(
                project_id="p-sec", model_slug="evil_cap",
                owner_scope="u:sec"))
        assert ei.value.code == "MODEL_INVALID"
        codes = {f["code"] for f in ei.value.extra.get("findings", [])}
        assert F_CAPABILITY_UNKNOWN in codes


# ── 非法 role / 参数注入 ─────────────────────────────────────────────────


class TestRoleAndParamInjection:
    def test_sql_injection_role_rejected(self):
        """role="; DROP TABLE" 不在 DATA_ROLES → ROLE_UNKNOWN error。"""
        doc = ModelDocument.model_validate({
            "schema_version": "1",
            "name": "注入 role 样张",
            "source": {"kind": "manual", "ref": ""},
            "nodes": [
                {"node_id": "src", "kind": "data_input",
                 "role": "; DROP TABLE users;--"},
                {"node_id": "out", "kind": "output"},
            ],
            "edges": [{"from_node": "src", "from_port": "; DROP TABLE users;--",
                       "to_node": "out", "to_port": "in"}],
            "output_node_ids": ["out"],
        })
        findings = validate_document(doc)
        errs = [f for f in findings if f.severity == SEVERITY_ERROR]
        assert any(e.code == F_ROLE_UNKNOWN and e.node_id == "src"
                   for e in errs), errs

    def test_oversized_param_value_rejected_by_bounded_value(self):
        """>256B 参数值 → BoundedValue 拒（模型参数不是数据载体）。"""
        with pytest.raises(ValidationError) as ei:
            BoundedValue(value="A" * 300)
        assert "256" in str(ei.value)

    def test_oversized_node_param_rejected_in_document(self):
        # 节点级 params 同样被 BoundedValue 拦（文档面无旁路）
        with pytest.raises(ValidationError):
            ModelNodeParams.model_validate({"values": {
                "blob": {"value": "B" * 300}}})

    def test_undeclared_param_ref_rejected(self):
        """$param 引用未声明参数 → PARAM_REF_UNKNOWN error。"""
        doc = ModelDocument.model_validate({
            "schema_version": "1",
            "name": "悬空参数引用样张",
            "source": {"kind": "manual", "ref": ""},
            "nodes": [
                {"node_id": "src", "kind": "data_input", "role": "subject"},
                {"node_id": "buf", "kind": "transform", "transform_op": "buffer",
                 "params": {"values": {"distance": {"value": "$param:ghost"}}}},
                {"node_id": "out", "kind": "output"},
            ],
            "edges": [
                {"from_node": "src", "from_port": "subject",
                 "to_node": "buf", "to_port": "in"},
                {"from_node": "buf", "from_port": "out",
                 "to_node": "out", "to_port": "in"},
            ],
            "output_node_ids": ["out"],
        })
        findings = validate_document(doc)
        errs = [f for f in findings if f.severity == SEVERITY_ERROR]
        assert any(e.code == F_PARAM_REF_UNKNOWN and e.param == "ghost"
                   for e in errs), errs


# ── CAS 并发 ─────────────────────────────────────────────────────────────


class TestCasConcurrency:
    def test_concurrent_updates_exactly_one_wins(self, env):
        """两个并发 update_document 同 expected_revision → 恰一成功一冲突。"""
        svc, _runtime = env
        doc_a = _minimal_doc(name="版本 A")
        asyncio.run(svc.create_draft(
            project_id="p-cas", document=doc_a, model_slug="cas_model"))
        doc_b = _minimal_doc(name="版本 B")

        async def _race():
            return await asyncio.gather(
                asyncio.to_thread(
                    lambda: svc.store.update_document(
                        project_id="p-cas", model_slug="cas_model",
                        document=doc_b.model_dump(mode="json"),
                        document_fingerprint=doc_b.document_fingerprint(),
                        name=doc_b.name, expected_revision=1)),
                asyncio.to_thread(
                    lambda: svc.store.update_document(
                        project_id="p-cas", model_slug="cas_model",
                        document=doc_b.model_dump(mode="json"),
                        document_fingerprint=doc_b.document_fingerprint(),
                        name=doc_b.name + "!", expected_revision=1)),
                return_exceptions=True)

        results = asyncio.run(_race())
        outcomes = []
        for r in results:
            if isinstance(r, Exception):
                assert isinstance(r, ModelStoreError), r
                assert r.code == "REVISION_CONFLICT"
                outcomes.append("conflict")
            else:
                assert r["revision_no"] == 2
                outcomes.append("win")
        assert sorted(outcomes) == ["conflict", "win"], outcomes
        final = asyncio.run(svc.get_draft("p-cas", "cas_model"))
        assert final["revision_no"] == 2


# ── 跨租户 / 目录不泄露 ──────────────────────────────────────────────────


class TestCrossTenant:
    def test_other_owner_cannot_instantiate_or_read_versions(self, env):
        """owner A 发布；owner B 实例化 → MODEL_NOT_PUBLISHED；
        B 的目录视图不泄露 A 的版本行。"""
        svc, _runtime = env
        doc = _minimal_doc(name="租户 A 私有模型")
        asyncio.run(svc.create_draft(
            project_id="p-ct", document=doc, model_slug="tenant_model"))
        pub = asyncio.run(svc.publish_model(
            project_id="p-ct", model_slug="tenant_model",
            owner_scope="u:aaa"))
        assert pub["version"] == "1.1.0"

        with pytest.raises(ModelAuthoringError) as ei:
            asyncio.run(svc.instantiate_model(
                project_id="p-ct", model_slug="tenant_model",
                owner_scope="u:bbb", session_id="s-bbb"))
        assert ei.value.code == "MODEL_NOT_PUBLISHED"

        catalog_b = asyncio.run(svc.list_model_catalog(
            project_id="p-ct", owner_scope="u:bbb"))
        entry_b = next(e for e in catalog_b
                       if e["model_slug"] == "tenant_model")
        assert entry_b.get("versions", []) == [], (
            "other owner must not see version rows")
        catalog_a = asyncio.run(svc.list_model_catalog(
            project_id="p-ct", owner_scope="u:aaa"))
        entry_a = next(e for e in catalog_a
                       if e["model_slug"] == "tenant_model")
        assert [v["version"] for v in entry_a["versions"]] == ["1.1.0"]


# ── 发布后不可变 ─────────────────────────────────────────────────────────


class TestPublishedImmutability:
    def test_patch_and_republish_keeps_old_fingerprint(self, env):
        svc, runtime = env
        doc = _minimal_doc(name="不可变基线")
        asyncio.run(svc.create_draft(
            project_id="p-im", document=doc, model_slug="immutable_model"))
        v1 = asyncio.run(svc.publish_model(
            project_id="p-im", model_slug="immutable_model",
            owner_scope="u:im", change="minor"))
        assert v1["version"] == "1.1.0"
        fp_v1 = v1["fingerprint"]

        # 改草稿（真实 CAS 更新）再发 patch 版
        draft = asyncio.run(svc.get_draft("p-im", "immutable_model"))
        doc2 = _minimal_doc(name="不可变基线（改）")
        asyncio.run(asyncio.to_thread(
            svc.store.update_document,
            project_id="p-im", model_slug="immutable_model",
            document=doc2.model_dump(mode="json"),
            document_fingerprint=doc2.document_fingerprint(),
            name=doc2.name,
            expected_revision=int(draft["revision_no"])))
        v2 = asyncio.run(svc.publish_model(
            project_id="p-im", model_slug="immutable_model",
            owner_scope="u:im", change="patch"))
        assert v2["version"] == "1.1.1"
        assert v2["fingerprint"] != fp_v1

        # 历史版本不被改写：resolve 1.1.0 指纹仍 = 首发指纹
        row = asyncio.run(asyncio.to_thread(
            runtime.registry.resolve, "umodel_immutable_model",
            owner_scope="u:im", version="1.1.0"))
        assert row["fingerprint"] == fp_v1
        row2 = asyncio.run(asyncio.to_thread(
            runtime.registry.resolve, "umodel_immutable_model",
            owner_scope="u:im", version="1.1.1"))
        assert row2["fingerprint"] == v2["fingerprint"]


# ── 越权绑定 ─────────────────────────────────────────────────────────────


class TestUnauthorizedBinding:
    def test_foreign_session_ref_not_found_no_existence_leak(self, env):
        """绑定他人 session 的 ref → REF_NOT_FOUND（与不存在 ref 同码）。"""
        svc, _runtime = env
        doc = _minimal_doc()
        asyncio.run(svc.create_draft(
            project_id="p-bd", document=doc, model_slug="bind_model"))
        asyncio.run(svc.publish_model(
            project_id="p-bd", model_slug="bind_model",
            owner_scope="u:own"))

        from app.services.session_data import session_data_manager

        foreign_ref = asyncio.run(session_data_manager.store(
            "s-owner-a", {"type": "FeatureCollection", "features": []},
            prefix="secdata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p-bd", model_slug="bind_model",
            owner_scope="u:own", session_id="s-attacker-b",
            input_bindings={"src": foreign_ref}))
        receipt = inst["bindings"]["src"]
        assert receipt["ok"] is False
        assert receipt["code"] == "REF_NOT_FOUND"
        assert receipt["bound_ref"] == ""

        # 与「纯粹不存在的 ref」不可区分（不泄露存在性）
        inst2 = asyncio.run(svc.instantiate_model(
            project_id="p-bd", model_slug="bind_model",
            owner_scope="u:own", session_id="s-attacker-c",
            input_bindings={"src": "ref:totally-made-up-9999"}))
        assert inst2["bindings"]["src"]["code"] == "REF_NOT_FOUND"


# ── deadline 预算（heavy：真实 driver）──────────────────────────────────


@pytest.mark.heavy
class TestDeadlineBudget:
    def test_tiny_deadline_honest_non_success_and_bounded(self, env):
        """deadline 预算：run(deadline_s=0.05)（服务层 clamp 至 1.0s）→
        32 节点串链 × 2 万要素点（首个 buffer 就远超 1.0s —— 真实超预算，
        任何机器上都不可能完成）→ driver 诚实停机（running，绝不洗成
        succeeded），外层 wait_for(30s) 兜底断言不挂死。"""
        svc, runtime = env
        doc = ModelDocument.model_validate({
            "schema_version": "1",
            "name": "deadline 预算样张（串行链）",
            "source": {"kind": "manual", "ref": ""},
            "nodes": (
                [{"node_id": "input_fc", "kind": "data_input",
                  "role": "subject"}]
                + [{"node_id": f"buf_{i:02d}", "kind": "transform",
                    "transform_op": "buffer",
                    "params": {"values": {"distance": {"value": 5},
                                          "unit": {"value": "m"}}}}
                   for i in range(29)]
                + [{"node_id": "agg", "kind": "analysis",
                    "capability": "category_breakdown",
                    "params": {"values": {"field": {"value": "category"}}}},
                   {"node_id": "stats_out", "kind": "output"}]),
            "edges": (
                [{"from_node": "input_fc", "from_port": "subject",
                  "to_node": "buf_00", "to_port": "in"}]
                + [{"from_node": f"buf_{i:02d}", "from_port": "out",
                    "to_node": f"buf_{i + 1:02d}", "to_port": "in"}
                   for i in range(28)]
                + [{"from_node": "buf_28", "from_port": "out",
                    "to_node": "agg", "to_port": "in"},
                   {"from_node": "agg", "from_port": "out",
                    "to_node": "stats_out", "to_port": "in"}]),
            "output_node_ids": ["stats_out"],
        })
        assert len(doc.nodes) == 32
        asyncio.run(svc.create_draft(
            project_id="p-dl", document=doc, model_slug="deadline_chain"))
        asyncio.run(svc.publish_model(
            project_id="p-dl", model_slug="deadline_chain",
            owner_scope="u:dl"))
        from app.services.session_data import session_data_manager

        feats = [{"type": "Feature",
                  "geometry": {"type": "Point",
                               "coordinates": [116.40 + i * 0.0001,
                                               39.90 + (i % 3) * 0.0001]},
                  "properties": {"category": f"c{i % 3}",
                                 "district": "east" if i % 2 else "west"}}
                 for i in range(20000)]
        ref = asyncio.run(session_data_manager.store(
            "s-dl", {"type": "FeatureCollection", "features": feats},
            prefix="secdata"))
        inst = asyncio.run(svc.instantiate_model(
            project_id="p-dl", model_slug="deadline_chain",
            owner_scope="u:dl", session_id="s-dl",
            input_bindings={"input_fc": ref}))
        iid = inst["instance"]["instance_id"]

        async def _run():
            return await svc.run_model_instance(
                instance_id=iid, owner_scope="u:dl",
                deadline_s=0.05,           # 服务层 clamp → 1.0s
                project_id="p-dl", param_values={})

        t0 = time.perf_counter()
        run = asyncio.run(asyncio.wait_for(_run(), timeout=30.0))
        wall_s = time.perf_counter() - t0
        assert wall_s < 30.0, f"run did not return in time: {wall_s:.1f}s"
        assert run["run"]["status"] != "succeeded", run["run"]
        # 诚实性：deadline 命中后实例不谎报终态成功
        final = asyncio.run(asyncio.to_thread(
            runtime.store.get_instance, iid, "u:dl"))
        assert final["status"] != "succeeded", final["status"]
