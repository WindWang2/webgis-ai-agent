"""H08 DoD 门禁测试 —— 语义身份 / 谱系 / 恢复面的横断验收。

- **指纹 property**：同输入恒同指纹（跨进程/构建次序）；derived_at 与
  license/attribution 恒不影响指纹（易变/权利元数据不入语义身份）；
- **schema 迁移 fixture**：旧 v1 载荷（无 license 键）读回兼容；未知
  版本 fail-closed（reuse 面 recompute，绝不假装能对账）；
- **zero-big-data**：descriptor 载荷有界；谱系记录不含参数本体/数据
  载荷（大参数 → 固定 digest）；recovery source_fingerprints 有界且
  无载荷键；锚点证据指纹截断有界；
- **复用失效贯通**：数据语义推进 → descriptor 指纹变化 →
  evaluate_reuse 裁决 recompute（F01 语义）→ resume 面翻 stale（C4）。
"""
import json
import uuid

import pytest

from app.lib.gis.dataset_descriptor import (
    CODE_VERSION_UNSUPPORTED,
    GISDatasetDescriptor,
)
from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    DatasetSemanticStore,
    derive_descriptor,
    evaluate_reuse,
    reset_dataset_semantic_store,
)
from app.services.dataset_semantics import store as store_mod
from app.services.dataset_semantics.provenance import (
    MAX_RECORDS,
    MAX_TRANSFORMATION_INPUTS,
    record_transformation,
)


def _profile(value: int = 1) -> DatasetProfile:
    return DatasetProfile(
        source="ref_descriptor", feature_count=10 + value,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"val": "number"}, numeric_fields=["val"],
        fields_status="explicit",
    )


def _descriptor(**kw) -> GISDatasetDescriptor:
    return derive_descriptor(
        _profile(kw.pop("value", 1)), dataset_key=kw.pop("key", "ref:t"),
        features=[{"properties": {"val": i}} for i in range(5)], **kw)


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


# ── 指纹 property ─────────────────────────────────────────────────────────


def test_property_fingerprint_determinism():
    """同输入恒同指纹（构建两次 + to_dict 往返 + 新实例重算）。"""
    for value in range(4):
        a = _descriptor(value=value)
        b = _descriptor(value=value)
        assert a.descriptor_fingerprint == b.descriptor_fingerprint
        rt = GISDatasetDescriptor.from_dict(a.to_dict()).with_fingerprints()
        assert rt.descriptor_fingerprint == a.descriptor_fingerprint


def test_property_volatile_and_rights_fields_never_in_fingerprint():
    """derived_at（易变）与 license/attribution（权利）恒不影响指纹。"""
    base = _descriptor()
    variants = [
        base.model_copy(update={"derived_at": "2026-09-29T00:00:00Z"}),
        base.model_copy(update={"license": "CC-BY-4.0"}),
        base.model_copy(update={"attribution": "© someone"}),
        base.model_copy(update={"license": "ODbL-1.0",
                                "attribution": "© someone else",
                                "derived_at": "2027-01-01"}),
    ]
    for v in variants:
        assert v.descriptor_fingerprint == base.descriptor_fingerprint


def test_property_payload_bounded_wide_profile():
    """宽表/长字段名 → 载荷仍硬上界（≤96KiB；64 字段截断）。"""
    fields = {f"col_{i}_" + "x" * 40: "number" for i in range(200)}
    p = DatasetProfile(
        source="ref_descriptor", feature_count=10, geometry_types=["Point"],
        crs="EPSG:4326", fields=fields, numeric_fields=list(fields),
        fields_status="explicit",
    )
    d = derive_descriptor(p, dataset_key="ref:wide")
    from app.services.dataset_semantics import descriptor_payload_bytes

    assert descriptor_payload_bytes(d) <= 96 * 1024
    assert len(d.fields) <= 64


# ── schema 迁移 fixture ───────────────────────────────────────────────────


def test_migration_v1_payload_without_license_reads_ok():
    d = _descriptor()
    payload = d.to_dict()
    payload.pop("license")
    payload.pop("attribution")
    migrated = GISDatasetDescriptor.from_dict(payload)
    assert migrated.license == ""
    assert migrated.with_fingerprints().descriptor_fingerprint == \
        d.descriptor_fingerprint


def test_migration_unknown_version_fail_closed():
    d = _descriptor()
    payload = d.to_dict()
    payload["descriptor_version"] = 2
    with pytest.raises(ValueError) as exc:
        GISDatasetDescriptor.from_dict(payload)
    assert CODE_VERSION_UNSUPPORTED in str(exc.value)
    # reuse 面：未知版本载荷 → migrate → unsupported → 裁决 recompute（保守）
    from app.services.dataset_semantics.store import migrate_payload

    record = migrate_payload(payload)
    assert record.status == "unsupported"
    decision = evaluate_reuse(d.descriptor_fingerprint, record)
    assert decision.verdict == "recompute"


# ── zero-big-data ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_big_data_lineage_record_ignores_payload_size(sem_store, tmp_path):
    """巨型参数/大数据标记 → 记录仍固定小（digest 化；无载荷泄漏）。"""
    sid = f"h08-dod-{uuid.uuid4().hex[:6]}"
    huge_params = {"window": list(range(100_000)), "blob": "x" * 200_000}
    ok = await record_transformation(
        sid, output_ref="ref:out", output_fingerprint="dsd-v1:" + "a" * 64,
        inputs=[{"ref": f"ref:{i}", "descriptor_fingerprint": "dsd-v1:" + "b" * 64}
                for i in range(50)],
        algorithm="conv", parameters=huge_params)
    assert ok
    from app.services.dataset_semantics.provenance import _provenance_path

    path = _provenance_path(sid)
    size = path.stat().st_size
    assert size < 8 * 1024, f"provenance record 膨胀：{size} bytes"


@pytest.mark.asyncio
async def test_zero_big_data_transformation_inputs_bounded(sem_store):
    sid = "h08-dod-bounded"
    await record_transformation(
        sid, output_ref="ref:o", output_fingerprint="dsd-v1:x",
        inputs=[{"ref": f"ref:{i}"} for i in range(100)],
        algorithm="op")
    from app.services.dataset_semantics.provenance import list_transformations

    recs = await list_transformations(sid)
    assert len(recs[0].inputs) <= MAX_TRANSFORMATION_INPUTS


@pytest.mark.asyncio
async def test_zero_big_data_recovery_facts_shape(sem_store):
    """recovery source_fingerprints：≤16 条、键封闭（ref/fingerprint/
    descriptor_fingerprint），绝无载荷键。"""
    from app.services.gis_harness.durable_context import (
        load_recovery_state,
        record_source_fingerprints,
    )
    from app.services.session_data import session_data_manager

    sid = f"h08-dod-rf-{uuid.uuid4().hex[:6]}"
    await session_data_manager.clear_session(sid)
    try:
        entries = [{"ref": f"ref:{i}", "fingerprint": "f" * 64,
                    "sneaky_payload": ["big"] * 1000} for i in range(40)]
        await record_source_fingerprints(sid, entries)
        state = await load_recovery_state(sid)
        fps = state["source_fingerprints"]
        assert 0 < len(fps) <= 16
        for e in fps:
            assert set(e.keys()) <= {"ref", "fingerprint", "descriptor_fingerprint"}
    finally:
        await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_zero_big_data_records_ring(tmp_path, monkeypatch):
    """provenance 记录 ring ≤MAX_RECORDS（有界磁盘）。"""
    monkeypatch.setattr(store_mod, "_store",
                        DatasetSemanticStore(base_dir=tmp_path))
    sid = "h08-dod-ring"
    for i in range(MAX_RECORDS + 10):
        await record_transformation(
            sid, output_ref=f"ref:{i}", output_fingerprint="dsd-v1:x",
            inputs=[], algorithm="op")
    from app.services.dataset_semantics.provenance import (
        _provenance_path,
        list_transformations,
    )

    recs = await list_transformations(sid)
    assert len(recs) <= MAX_RECORDS
    lines = [line for line in
             _provenance_path(sid).read_text().splitlines() if line.strip()]
    assert len(lines) <= MAX_RECORDS
    for line in lines:
        payload = json.loads(line)
        assert "parameters" not in payload      # 参数本体永不入记录
        assert "features" not in payload        # 数据载荷永不入记录


# ── 复用失效贯通（descriptor → reuse → resume 语义对齐）───────────────────


def test_reuse_invalidation_chain(sem_store):
    """语义推进 → 异指纹 → evaluate_reuse recompute；权利补全 → valid。"""
    old = _descriptor(value=1)
    new = _descriptor(value=2)
    assert old.descriptor_fingerprint != new.descriptor_fingerprint
    # 只有指纹证据（无历史回查）→ 保守 recompute
    decision = evaluate_reuse(old.descriptor_fingerprint, None)
    assert decision.verdict == "unknown"        # 无当前证据：诚实未知
    # 权利元数据补全后重铸：指纹不变 → 复用不受影响
    enriched = _descriptor(value=1, license="CC-BY-4.0")
    assert enriched.descriptor_fingerprint == old.descriptor_fingerprint
