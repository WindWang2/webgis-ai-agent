"""Transformation Provenance DAG 测试（H08/C5）。

- parameters_digest 确定性（同参数同 digest；键序无关）；
- mint_output_descriptor：ref descriptor 元数据零扫描投影入 store，
  ref descriptor 缺席 → None（诚实缺席）；
- record + lineage 走链：output → input → source 三层可追查；
- 有界：inputs ≤8、records ≤128、lineage ≤16 节点 ≤4 深度；
- 诚实截断：记录/台账双缺席 = 叶子节点（绝不虚构祖先）；
- 环防护：循环 inputs 不死循环。
"""
import uuid

import pytest

from app.services.dataset_semantics import (
    DatasetSemanticStore,
    lineage_for_ref,
    mint_output_descriptor,
    parameters_digest,
    record_transformation,
    resolve_input_fingerprints,
)
from app.services.dataset_semantics import store as store_mod
from app.services.dataset_semantics.provenance import (
    MAX_LINEAGE_NODES,
    MAX_RECORDS,
    MAX_TRANSFORMATION_INPUTS,
    TransformationRecord,
)


@pytest.fixture(autouse=True)
def _redirect_provenance_storage(tmp_path, monkeypatch):
    """provenance 文件路径也钉进 tmp（hermetic；review R1 P3#10）。"""
    import app.services.dataset_semantics.store as store_mod2

    monkeypatch.setattr(store_mod2, "_storage_base", lambda: tmp_path)


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    from app.services.dataset_semantics import reset_dataset_semantic_store

    reset_dataset_semantic_store()


def test_parameters_digest_deterministic():
    a = parameters_digest({"b": 1, "a": [2, 3]})
    b = parameters_digest({"a": [2, 3], "b": 1})
    assert a == b and len(a) == 16
    assert parameters_digest({"b": 1, "a": [2, 4]}) != a


@pytest.mark.asyncio
async def test_mint_output_descriptor_zero_scan(sem_store, monkeypatch):
    from app.services.session_data import session_data_manager

    sid = f"h08-c5-{uuid.uuid4().hex[:6]}"
    ref = await session_data_manager.store(
        sid, {"type": "FeatureCollection", "features": []}, prefix="geo")
    # ref descriptor 元数据（零扫描投影的供给面）
    ref_desc = {
        "field_schema": {"val": {"type": "number"}},
        "feature_count": 12,
        "geometry_types": ["Point"],
        "crs": "EPSG:4326",
    }

    async def _fake_get_ref_descriptor(s, r):
        return ref_desc if r == ref else None

    monkeypatch.setattr(session_data_manager, "get_ref_descriptor",
                        _fake_get_ref_descriptor)

    d = await mint_output_descriptor(
        sid, ref, producer="geocompute_executor", method="buffer")
    assert d is not None
    assert d.descriptor_fingerprint.startswith("dsd-v1:")
    assert d.provenance[0]["producer"] == "geocompute_executor"
    # store 现读同一指纹
    rec = await sem_store.get(sid, ref)
    assert rec.ok
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint


@pytest.mark.asyncio
async def test_mint_absent_ref_descriptor_honest_none(sem_store, monkeypatch):
    from app.services.session_data import session_data_manager

    async def _none_async(*a, **k):
        return None

    monkeypatch.setattr(session_data_manager, "get_ref_descriptor",
                        _none_async)
    d = await mint_output_descriptor("h08-c5-x", "ref:none")
    assert d is None


@pytest.mark.asyncio
async def test_lineage_three_layers(sem_store):
    """output → analysis → source 三层链（MapSpec layer 视角可追查）。"""
    sid = f"h08-c5-l-{uuid.uuid4().hex[:6]}"
    await record_transformation(
        sid,
        output_ref="ref:analysis-out",
        output_fingerprint="dsd-v1:" + "b" * 64,
        inputs=[{"ref": "ref:source-a",
                 "descriptor_fingerprint": "dsd-v1:" + "a" * 64}],
        algorithm="buffer", parameters={"dist": 500},
    )
    chain = await lineage_for_ref(sid, "ref:analysis-out")
    assert chain[0]["ref"] == "ref:analysis-out"
    assert chain[0]["algorithm"] == "buffer"
    assert chain[0]["inputs"][0]["ref"] == "ref:source-a"
    # 记录缺席的 source-a → 叶子（诚实截断，不虚构祖先）
    leaves = [n for n in chain if n["ref"] == "ref:source-a"]
    assert leaves, "叶子节点保留（链的终端）"


@pytest.mark.asyncio
async def test_transformation_inputs_bounded(sem_store):
    refs = [f"ref:{i}" for i in range(40)]
    entries = await resolve_input_fingerprints("h08-c5-b", refs)
    assert len(entries) <= MAX_TRANSFORMATION_INPUTS


@pytest.mark.asyncio
async def test_records_bounded_ring(tmp_path, monkeypatch):
    from app.services.dataset_semantics import store as store_mod2

    monkeypatch.setattr(store_mod2, "_store",
                        DatasetSemanticStore(base_dir=tmp_path))
    sid = "h08-c5-ring"
    for i in range(MAX_RECORDS + 20):
        await record_transformation(
            sid, output_ref=f"ref:{i}", output_fingerprint="dsd-v1:x",
            inputs=[], algorithm="op")
    from app.services.dataset_semantics import list_transformations

    records = await list_transformations(sid)
    assert len(records) <= MAX_RECORDS
    # 最旧的被丢弃（保留最新）
    assert records[-1].output_ref == f"ref:{MAX_RECORDS + 19}"


@pytest.mark.asyncio
async def test_lineage_cycle_guard(sem_store):
    """循环 inputs：a→b→a 不死循环（visited 防护）。"""
    sid = "h08-c5-cycle"
    await record_transformation(
        sid, output_ref="ref:a", output_fingerprint="dsd-v1:a",
        inputs=[{"ref": "ref:b", "descriptor_fingerprint": ""}],
        algorithm="op")
    await record_transformation(
        sid, output_ref="ref:b", output_fingerprint="dsd-v1:b",
        inputs=[{"ref": "ref:a", "descriptor_fingerprint": ""}],
        algorithm="op")
    chain = await lineage_for_ref(sid, "ref:a")
    assert len(chain) <= MAX_LINEAGE_NODES


def test_transformation_record_roundtrip_bounded():
    rec = TransformationRecord(
        output_ref="r" * 500,
        output_fingerprint="dsd-v1:" + "f" * 64,
        inputs=[{"ref": f"ref:{i}", "descriptor_fingerprint": "x" * 300}
                for i in range(40)],
        algorithm="a" * 300, algorithm_version="v" * 100,
        parameters_digest_value="p" * 99, created_at=1.0,
    )
    d = rec.to_bounded_dict()
    assert len(d["output_ref"]) <= 200
    assert len(d["algorithm"]) <= 64
    assert len(d["inputs"]) <= MAX_TRANSFORMATION_INPUTS
    assert all(len(i["descriptor_fingerprint"]) <= 96 for i in d["inputs"])
    rt = TransformationRecord.from_dict(d)
    assert rt is not None
    assert rt.output_ref == d["output_ref"]
    assert len(rt.inputs) == len(d["inputs"])


# ── geocompute ARTIFACT_REGISTER 生产缝（同步测试体：run_coro_sync
#    需要非运行循环，与 test_wave1_revival 同纪律）────────────────────────


def _fc(n=2):
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
             "properties": {"val": i}} for i in range(n)
        ],
    }


def test_artifact_register_op_mints_semantic_identity():
    import asyncio

    from app.services.dataset_semantics import (
        get_dataset_semantic_store,
        list_transformations,
    )
    from app.services.geocompute.ops import OperatorContext, execute_node
    from app.services.geocompute.plan import ExecutionNode, NodeCategory
    from app.services.session_data import session_data_manager

    sid = f"h08-c5-op-{uuid.uuid4().hex[:8]}"

    async def _setup():
        upstream = await session_data_manager.store(sid, _fc(3), prefix="up")
        out = await session_data_manager.store(sid, _fc(5), prefix="out")
        # 上游 ref 的语义身份（ingest 链正常铸造；此处直接落 store）
        from app.services.dataset_semantics import (
            build_descriptor_from_ref_descriptor,
        )

        ref_desc = await session_data_manager.get_ref_descriptor(sid, upstream)
        d = build_descriptor_from_ref_descriptor(
            ref_desc, dataset_key=upstream)
        await get_dataset_semantic_store().put(sid, upstream, d)
        return upstream, out

    upstream_ref, out_ref = asyncio.run(_setup())
    try:
        ctx = OperatorContext(run_id="run_h08", node_id="node_h08",
                              session_id=sid)
        node = ExecutionNode(
            node_id="node_h08",
            category=NodeCategory.ARTIFACT_REGISTER,
            operation="buffer",
            inputs=["r"],
            parameters={"artifact_type": "geocompute", "dist": 500},
        )
        out = execute_node(ctx, node, {
            "r": {"ref_id": out_ref},
            "up": {"ref_id": upstream_ref},
        })
        assert out["metadata"]["registered"] is True
        fp = out["metadata"].get("descriptor_fingerprint")
        assert fp and fp.startswith("dsd-v1:")
        # store 现读 = 产物语义身份
        rec = asyncio.run(get_dataset_semantic_store().get(sid, out_ref))
        assert rec.ok
        assert rec.descriptor.descriptor_fingerprint == fp
        # 转换谱系在场：algorithm + 参数 digest + 上游输入指纹
        records = asyncio.run(list_transformations(sid))
        match = [r for r in records if r.output_ref == out_ref]
        assert match, "产物必须落转换谱系记录"
        assert match[0].algorithm == "buffer"
        input_refs = [i["ref"] for i in match[0].inputs]
        assert upstream_ref in input_refs
        assert out_ref not in input_refs, "自环输入必须被过滤"
        assert all(i["descriptor_fingerprint"].startswith("dsd-v1:")
                   for i in match[0].inputs if i["ref"] == upstream_ref)
    finally:
        asyncio.run(session_data_manager.clear_session(sid))


def test_artifact_register_op_survives_provenance_failure(monkeypatch):
    """铸造/谱系任一步炸 → metadata 无指纹键，注册本身照常成功。"""
    import asyncio

    from app.services.geocompute.ops import OperatorContext, execute_node
    from app.services.geocompute.plan import ExecutionNode, NodeCategory
    from app.services.session_data import session_data_manager

    sid = f"h08-c5-opf-{uuid.uuid4().hex[:8]}"

    async def _setup():
        return await session_data_manager.store(sid, _fc(2), prefix="o")

    out_ref = asyncio.run(_setup())

    async def _boom(*a, **k):
        raise RuntimeError("provenance down")

    monkeypatch.setattr(
        "app.services.dataset_semantics.provenance.mint_output_descriptor",
        _boom)
    try:
        ctx = OperatorContext(run_id="run_f", node_id="node_f", session_id=sid)
        node = ExecutionNode(
            node_id="node_f",
            category=NodeCategory.ARTIFACT_REGISTER,
            operation="buffer",
            inputs=["r"],
            parameters={},
        )
        out = execute_node(ctx, node, {"r": {"ref_id": out_ref}})
        assert out["metadata"]["registered"] is True
        assert "descriptor_fingerprint" not in out["metadata"]
    finally:
        asyncio.run(session_data_manager.clear_session(sid))
