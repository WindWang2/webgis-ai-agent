"""Recovery durable facts: source_fingerprints 生产者测试（H08/C3）。

- record_source_fingerprints 经 context_bridge 两函数升级
  {ref, fingerprint} → {ref, fingerprint, descriptor_fingerprint}；
- 无 store 记录的 ref 诚实缺席 descriptor_fingerprint（不虚构）；
- 有界 ≤16；store 故障不抛（additive 证据面）；
- ingest 生产点：ingest 成功 → recovery state 带descriptor 指纹；
- 投影贯通：derive_context_layers 的 data 域透传 descriptor_fingerprint。
"""
import uuid

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    DatasetSemanticStore,
    derive_descriptor,
    reset_dataset_semantic_store,
)
from app.services.dataset_semantics import store as store_mod
from app.services.gis_harness.durable_context import (
    MAX_SOURCE_FINGERPRINTS,
    load_recovery_state,
    new_recovery_state,
    record_source_fingerprints,
)


def _descriptor(value: int, key: str):
    p = DatasetProfile(
        source="ref_descriptor", feature_count=10 + value,
        geometry_types=["Polygon"], crs="EPSG:4326",
        fields={"val": "number"}, numeric_fields=["val"],
        fields_status="explicit",
    )
    return derive_descriptor(
        p, dataset_key=key,
        features=[{"properties": {"val": i + value}} for i in range(5)])


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(store_mod, "_store", store)
    yield store
    reset_dataset_semantic_store()


@pytest.fixture()
async def session():
    from app.services.session_data import session_data_manager

    sid = f"h08-c3-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)


@pytest.mark.asyncio
async def test_record_augments_with_descriptor_fingerprint(sem_store, session):

    ref = "ref:abc"
    d = _descriptor(1, ref)
    await sem_store.put(session, ref, d)
    await record_source_fingerprints(session, [
        {"ref": ref, "fingerprint": "sha256:content"},
    ])
    state = await load_recovery_state(session)
    fps = state["source_fingerprints"]
    assert len(fps) == 1
    assert fps[0]["ref"] == ref
    assert fps[0]["fingerprint"] == "sha256:content"
    assert fps[0]["descriptor_fingerprint"] == d.descriptor_fingerprint


@pytest.mark.asyncio
async def test_record_without_store_record_honest_absence(sem_store, session):
    ref = "ref:no-descriptor"
    await record_source_fingerprints(session, [
        {"ref": ref, "fingerprint": "sha256:x"},
    ])
    state = await load_recovery_state(session)
    entry = state["source_fingerprints"][0]
    assert entry["ref"] == ref
    assert "descriptor_fingerprint" not in entry


@pytest.mark.asyncio
async def test_record_bounded(sem_store, session):
    entries = [{"ref": f"ref:{i}", "fingerprint": "f"} for i in range(40)]
    await record_source_fingerprints(session, entries)
    state = await load_recovery_state(session)
    assert len(state["source_fingerprints"]) <= MAX_SOURCE_FINGERPRINTS


@pytest.mark.asyncio
async def test_record_survives_store_crash(sem_store, session, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(
        "app.services.dataset_semantics.store.DatasetSemanticStore.get",
        _boom)
    # 不抛 = 证据面降级（主链不阻断）
    await record_source_fingerprints(session, [
        {"ref": "ref:x", "fingerprint": "f"},
    ])
    state = await load_recovery_state(session)
    assert state["source_fingerprints"][0]["ref"] == "ref:x"


@pytest.mark.asyncio
async def test_ingest_produces_durable_facts(sem_store):
    """生产点接线：ingest 成功 → recovery state 带该 ref 的语义指纹。"""
    from app.services.data_ingest.pipeline import (
        get_ingest_pipeline,
        reset_ingest_pipeline,
    )

    sid = f"h08-c3-ing-{uuid.uuid4().hex[:6]}"
    fc = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
             "properties": {"val": i}} for i in range(4)
        ],
    }
    try:
        result = await get_ingest_pipeline().ingest(sid, fc, crs="EPSG:4326")
    finally:
        reset_ingest_pipeline()
    assert result.ok
    state = await load_recovery_state(sid)
    fps = {e["ref"]: e for e in state["source_fingerprints"]}
    assert result.ref_id in fps
    assert fps[result.ref_id]["descriptor_fingerprint"] == \
        result.profile_summary["descriptor_fingerprint"]


@pytest.mark.asyncio
async def test_data_domain_projection_carries_descriptor_fingerprint(sem_store, session):
    """投影贯通：durable facts → context_layers data 域（既有读面终于有数）。"""
    from app.services.gis_harness.context_layers import (
        derive_context_layers,
    )
    from app.services.session_data import session_data_manager

    ref = "ref:proj"
    d = _descriptor(3, ref)
    await sem_store.put(session, ref, d)
    await record_source_fingerprints(session, [
        {"ref": ref, "fingerprint": "sha256:p"},
    ])
    recovery = await load_recovery_state(session)
    state = derive_context_layers({}, recovery=recovery)
    data_block = state.domains["data"].payload
    items = data_block["refs"]
    assert items and items[0]["ref"] == ref
    # data 域投影是展示级 digest（_bounded_str 截 48）—— 前缀一致即贯通。
    assert items[0]["descriptor_fingerprint"] == d.descriptor_fingerprint[:48]
    _ = session_data_manager


def test_new_recovery_state_has_empty_source_fingerprints():
    state = new_recovery_state()
    assert state["source_fingerprints"] == []
