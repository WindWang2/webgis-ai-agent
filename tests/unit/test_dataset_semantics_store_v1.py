"""Dataset Semantics Store + Reuse 测试（ADR-0215 V4 验收矩阵）。

- 写读 / 内容寻址幂等 / 版本链 pruning（≤8）/ 指针级 no-op；
- fail-closed：head 损坏 / 载荷损坏 / 未知版本 / 指纹不符 → 显式错误码，
  绝不返回"猜出来的语义"；
- 跨进程一致性：新 store 实例（模拟另一进程）读到同一指纹；
- reuse 裁决：同指纹 valid / 异指纹 recompute / 历史回查给出字段级 delta /
  缺席 unknown（绝不把「不知道」当「没变」）。
"""
import json

import pytest

from app.lib.gis.dataset_descriptor import (
    CODE_CRS_CHANGED,
    CODE_UNCHANGED,
    GISDatasetDescriptor,
)
from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics.builder import (
    MAX_DESCRIPTOR_BYTES,
)
from app.services.dataset_semantics.reuse import (
    VERDICT_RECOMPUTE,
    VERDICT_STALE,
    VERDICT_UNKNOWN,
    VERDICT_VALID,
    evaluate_reuse,
    evaluate_reuse_with_history,
)
from app.services.dataset_semantics.store import (
    MAX_VERSIONS_PER_DATASET,
    DatasetSemanticStore,
    dataset_key_hash,
    migrate_payload,
)


def _descriptor(value=1, crs="EPSG:4326"):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=10 + value,
        geometry_types=["Polygon"], crs=crs,
        fields={"v": "number"}, numeric_fields=["v"],
        fields_status="explicit",
    )
    from app.services.dataset_semantics import derive_descriptor

    return derive_descriptor(
        p, dataset_key="ref:t",
        features=[{"properties": {"v": i + value}} for i in range(5)])


@pytest.fixture()
def store(tmp_path):
    return DatasetSemanticStore(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_put_get_roundtrip(store):
    d = _descriptor()
    put = await store.put("s1", "ref:t", d)
    assert put.ok and put.created
    rec = await store.get("s1", "ref:t")
    assert rec.status == "ok"
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint


@pytest.mark.asyncio
async def test_put_idempotent_same_fingerprint(store):
    d = _descriptor()
    first = await store.put("s1", "ref:t", d)
    again = await store.put("s1", "ref:t", _descriptor())
    assert first.created and not again.created
    assert (await store.list_versions("s1", "ref:t")) == [d.descriptor_fingerprint]


@pytest.mark.asyncio
async def test_version_history_and_pruning(store):
    fps = []
    for v in range(MAX_VERSIONS_PER_DATASET + 4):
        d = _descriptor(value=v + 1)
        put = await store.put("s1", "ref:t", d)
        assert put.ok
        fps.append(d.descriptor_fingerprint)
    versions = await store.list_versions("s1", "ref:t")
    assert len(versions) == MAX_VERSIONS_PER_DATASET
    assert versions[-1] == fps[-1]          # 最新版本在 head
    assert fps[0] not in versions           # 老版本被 pruning
    # head 可读，被 prune 的旧版本直读 → DESCRIPTOR_MISSING（诚实缺席）。
    rec_new = await store.get("s1", "ref:t")
    assert rec_new.ok
    rec_pruned = await store.get_by_fingerprint("s1", "ref:t", fps[0])
    assert rec_pruned.status == "missing"
    rec_kept = await store.get_by_fingerprint("s1", "ref:t", fps[-2])
    assert rec_kept.ok


@pytest.mark.asyncio
async def test_cross_process_read(tmp_path):
    d = _descriptor()
    writer = DatasetSemanticStore(base_dir=tmp_path)
    await writer.put("s1", "ref:t", d)
    reader = DatasetSemanticStore(base_dir=tmp_path)  # 模拟另一进程
    rec = await reader.get("s1", "ref:t")
    assert rec.ok
    assert rec.descriptor.descriptor_fingerprint == d.descriptor_fingerprint


@pytest.mark.asyncio
async def test_corrupt_payload_fail_closed(store, tmp_path):
    d = _descriptor()
    await store.put("s1", "ref:t", d)
    ddir = tmp_path / "s1" / "dataset_semantics" / dataset_key_hash("ref:t")
    payload_path = ddir / f"{d.descriptor_fingerprint}.json"
    payload_path.write_text("{corrupt json", encoding="utf-8")
    rec = await store.get("s1", "ref:t")
    assert rec.status == "corrupt"
    assert rec.reason_code == "DESCRIPTOR_STORE_CORRUPT"
    assert rec.descriptor is None


@pytest.mark.asyncio
async def test_fingerprint_mismatch_fail_closed(store, tmp_path):
    d = _descriptor()
    await store.put("s1", "ref:t", d)
    ddir = tmp_path / "s1" / "dataset_semantics" / dataset_key_hash("ref:t")
    payload_path = ddir / f"{d.descriptor_fingerprint}.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["feature_count"] = 99999   # 篡改语义但不改文件名身份
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    rec = await store.get("s1", "ref:t")
    assert rec.status == "corrupt"
    assert rec.reason_code == "DESCRIPTOR_FINGERPRINT_MISMATCH"


@pytest.mark.asyncio
async def test_corrupt_head_fail_closed(store, tmp_path):
    await store.put("s1", "ref:t", _descriptor())
    ddir = tmp_path / "s1" / "dataset_semantics" / dataset_key_hash("ref:t")
    (ddir / "head.json").write_text("not json", encoding="utf-8")
    rec = await store.get("s1", "ref:t")
    assert rec.status == "corrupt"


@pytest.mark.asyncio
async def test_missing_descriptor_honest(store):
    rec = await store.get("s1", "ref:nope")
    assert rec.status == "missing"
    assert rec.reason_code == "DESCRIPTOR_MISSING"


@pytest.mark.asyncio
async def test_key_mismatch_rejected(store):
    d = _descriptor()
    d = d.model_copy(update={"dataset_key": "ref:other"})
    put = await store.put("s1", "ref:t", d)
    assert not put.ok
    assert put.reason_code == "DATASET_KEY_MISMATCH"


@pytest.mark.asyncio
async def test_oversized_payload_rejected(store):
    d = _descriptor()
    oversized = GISDatasetDescriptor.model_construct(
        **{**d.model_dump(), "dataset_key": "ref:t"})
    # 构造一个超限载荷：直接绕过字段 cap 校验的路径不做 —— 用字节口径断言
    # store 的拒收分支（mock payload bytes 超限）。
    import app.services.dataset_semantics.store as store_mod

    original = store_mod.descriptor_payload_bytes
    store_mod.descriptor_payload_bytes = lambda _: MAX_DESCRIPTOR_BYTES + 1
    try:
        put = await store.put("s1", "ref:t", oversized)
        assert not put.ok
        assert put.reason_code == "DESCRIPTOR_TOO_LARGE"
    finally:
        store_mod.descriptor_payload_bytes = original


def test_migrate_payload_version_gate():
    d = _descriptor()
    ok = migrate_payload(d.to_dict())
    assert ok.status == "ok"
    bad = d.to_dict()
    bad["descriptor_version"] = 42
    rec = migrate_payload(bad)
    assert rec.status == "unsupported"
    assert rec.reason_code == "DESCRIPTOR_VERSION_UNSUPPORTED"
    corrupt = migrate_payload({"descriptor_version": 1, "fields": "not-a-list"})
    assert corrupt.status in ("ok", "corrupt")   # fields 形状被清洗，不炸不猜


# ── reuse 裁决 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reuse_same_fingerprint_valid(store):
    d = _descriptor()
    await store.put("s1", "ref:t", d)
    rec = await store.get("s1", "ref:t")
    decision = evaluate_reuse(d.descriptor_fingerprint, rec)
    assert decision.verdict == VERDICT_VALID
    assert CODE_UNCHANGED in decision.reason_codes
    assert decision.safely_reusable


@pytest.mark.asyncio
async def test_reuse_missing_recorded_unknown(store):
    d = _descriptor()
    await store.put("s1", "ref:t", d)
    rec = await store.get("s1", "ref:t")
    decision = evaluate_reuse("", rec)   # 旧 MapSpec 无指纹记录
    assert decision.verdict == VERDICT_UNKNOWN
    assert not decision.safely_reusable


@pytest.mark.asyncio
async def test_reuse_diff_fingerprint_recompute(store):
    d1 = _descriptor(value=1)
    d2 = _descriptor(value=2)
    await store.put("s1", "ref:t", d2)
    rec = await store.get("s1", "ref:t")
    decision = evaluate_reuse(d1.descriptor_fingerprint, rec)
    assert decision.verdict == VERDICT_RECOMPUTE


@pytest.mark.asyncio
async def test_reuse_history_delta_precise(store):
    d1 = _descriptor(crs="EPSG:4326")
    d2 = _descriptor(crs="EPSG:3857")
    await store.put("s1", "ref:t", d1)
    await store.put("s1", "ref:t", d2)
    rec = await store.get("s1", "ref:t")
    decision = await evaluate_reuse_with_history(
        d1.descriptor_fingerprint, rec,
        store=store, session_id="s1", dataset_key="ref:t")
    # 历史回查给出精确分类：CRS 变化 → stale/recompute + 稳定码。
    assert decision.verdict in (VERDICT_STALE, VERDICT_RECOMPUTE)
    assert CODE_CRS_CHANGED in decision.reason_codes
    assert decision.delta is not None


@pytest.mark.asyncio
async def test_reuse_missing_current_unknown(store):
    decision = evaluate_reuse("dsd-v1:abc", await store.get("s1", "ref:gone"))
    assert decision.verdict == VERDICT_UNKNOWN
