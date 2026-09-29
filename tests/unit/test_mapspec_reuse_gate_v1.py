"""MapSpec 绑定复用门测试（H08 —— evaluate_reuse_with_history 生产接线）。

- 首次绑定：无记录面 → 无 descriptor_reuse 键（诚实缺席）；
- 同数据重绑定：valid → 不落键（载荷精益，指纹本身即证据）；
- 数据语义变化后重绑定：stale + 字段级 delta 落图面（历史回查精确归因）；
- license/attribution 从 descriptor 透传到 source 绑定。
"""

import pytest

from app.lib.gis.dataset_profile import DatasetProfile
from app.services.dataset_semantics import (
    DatasetSemanticStore,
    derive_descriptor,
    reset_dataset_semantic_store,
)
from app.services.dataset_semantics import store as store_mod


def _fc(n: int = 2):
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
             "properties": {"val": i}} for i in range(n)
        ],
    }


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


def _patch_mapspec(monkeypatch, persisted_spec):
    """mapspec_store 的持久化面（get_mapspec / _apply）双 fake。"""
    from app.services.mapspec_store import mapspec_store
    from app.services.mapspec import MapSpecResult

    intents = []

    async def _fake_apply(session_id, intent, **kwargs):
        intents.append(intent)
        return MapSpecResult(mapspec={"sources": {intent.source_id: intent.source}})

    async def _fake_get_mapspec(session_id):
        return persisted_spec

    monkeypatch.setattr(mapspec_store.engine, "apply_mutation", _fake_apply)
    monkeypatch.setattr(mapspec_store, "get_mapspec", _fake_get_mapspec)
    return intents


def _patch_inline(monkeypatch, stored_ref="ref:geojson-fake"):
    from app.services import session_data as session_data_mod

    async def _fake_store(session_id, payload, prefix="geojson"):
        return stored_ref

    def _sync_profile(geojson_data):
        return {
            "featureCount": 3,
            "geometryTypes": ["Point"],
            "fields": {"val": {"type": "number"}},
            "fields_status": "explicit",
            "bbox": [116.0, 39.0, 116.3, 39.0],
            "crs": "EPSG:4326",
        }

    monkeypatch.setattr(session_data_mod.session_data_manager,
                        "store", _fake_store)
    monkeypatch.setattr(
        "app.services.spatial_meta_profiler.profile_geojson_source",
        _sync_profile)
    return stored_ref


@pytest.mark.asyncio
async def test_first_binding_has_no_reuse_key(sem_store, monkeypatch):
    from app.services.mapspec_store import mapspec_store

    _patch_inline(monkeypatch)
    intents = _patch_mapspec(monkeypatch, persisted_spec=None)
    await mapspec_store.source_profile("h08-g1", "s1", _fc())
    src = intents[0].source
    assert src["descriptor_fingerprint"].startswith("dsd-v1:")
    assert "descriptor_reuse" not in src


@pytest.mark.asyncio
async def test_unchanged_rebinding_stays_clean(sem_store, monkeypatch):
    from app.services.mapspec_store import mapspec_store

    ref = _patch_inline(monkeypatch)
    sid = "h08-g2"
    d = _descriptor(1, ref)
    await sem_store.put(sid, ref, d)
    intents = _patch_mapspec(monkeypatch, persisted_spec={
        "sources": {"s1": {"descriptor_fingerprint": d.descriptor_fingerprint}},
    })
    await mapspec_store.source_profile(sid, "s1", _fc())
    assert intents[0].source["descriptor_fingerprint"] == d.descriptor_fingerprint
    assert "descriptor_reuse" not in intents[0].source


@pytest.mark.asyncio
async def test_semantic_change_surfaces_stale_verdict(sem_store, monkeypatch):
    """数据语义版本推进后重绑定：历史回查给出 stale + 字段级 delta。"""
    from app.services.mapspec_store import mapspec_store

    ref = _patch_inline(monkeypatch)
    sid = "h08-g3"
    old = _descriptor(1, ref)
    new = _descriptor(2, ref)  # feature_count / 值域证据变化 → 异指纹
    assert old.descriptor_fingerprint != new.descriptor_fingerprint
    await sem_store.put(sid, ref, old)
    await sem_store.put(sid, ref, new)  # head → new；old 仍在版本历史
    intents = _patch_mapspec(monkeypatch, persisted_spec={
        "sources": {"s1": {"descriptor_fingerprint": old.descriptor_fingerprint}},
    })
    await mapspec_store.source_profile(sid, "s1", _fc())
    src = intents[0].source
    assert src["descriptor_fingerprint"] == new.descriptor_fingerprint
    reuse = src.get("descriptor_reuse")
    assert reuse, "语义变化的重绑定必须携带复用裁决"
    assert reuse["verdict"] in ("stale", "recompute")
    assert reuse["recorded_fingerprint"].startswith("dsd-v1:")
    assert reuse["current_fingerprint"].startswith("dsd-v1:")


@pytest.mark.asyncio
async def test_license_propagates_to_source_binding(sem_store, monkeypatch):
    from app.services.mapspec_store import mapspec_store

    ref = _patch_inline(monkeypatch)
    sid = "h08-g4"
    d = _descriptor(1, ref)
    licensed = d.model_copy(update={"license": "CC-BY-4.0",
                                    "attribution": "© test"})
    await sem_store.put(sid, ref, licensed)
    intents = _patch_mapspec(monkeypatch, persisted_spec=None)
    await mapspec_store.source_profile(sid, "s1", _fc())
    src = intents[0].source
    assert src.get("license") == "CC-BY-4.0"
    assert src.get("attribution") == "© test"


@pytest.mark.asyncio
async def test_store_read_failure_degrades_without_key(sem_store, monkeypatch):
    """对账链路任一步异常 → 无 descriptor_reuse 键（不阻断制图主链）。"""
    from app.services.mapspec_store import mapspec_store

    ref = _patch_inline(monkeypatch)
    sid = "h08-g5"
    d = _descriptor(1, ref)
    await sem_store.put(sid, ref, d)

    async def _boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(
        "app.services.dataset_semantics.store.DatasetSemanticStore.get",
        _boom)
    intents = _patch_mapspec(monkeypatch, persisted_spec={
        "sources": {"s1": {"descriptor_fingerprint": d.descriptor_fingerprint}},
    })
    await mapspec_store.source_profile(sid, "s1", _fc())
    src = intents[0].source
    assert "descriptor_fingerprint" not in src  # 证据链降级 = 诚实缺席
    assert "descriptor_reuse" not in src
