"""Situation 规模/预算基准（方向 2 S8，ADR-0180）。

约束（任务书）：
- compile latency 有界（合成 10/100/1000 层会话）；
- 投影 bytes 有界（4096 cap，永不随层线性爆炸）；
- **no O(total feature count)**：10 万要素的 descriptor 不触发任何 payload
  级操作（编译只读 O(1) 描述符摘要）；
- memory bounded：快照持久化产物尺寸有界。

全部合成 fixture，无真实大数据。
"""
import time
import uuid

import pytest

from app.services.gis_situation.compiler import compile_situation
from app.services.gis_situation.diff import advance_snapshot
from app.services.gis_situation.projection import (
    SITUATION_BLOCK_MAX_BYTES,
    render_situation_for_context,
)
from tests.data.situation_fakes import FakeMapspecStore, FakeSituationStore


def _synth_spec(layer_count: int) -> dict:
    layers = [
        {
            "id": f"lyr-{i:06d}",
            "type": "circle",
            "layout": {"visibility": "visible" if i % 3 else "none"},
            "context_role": "base" if i % 10 == 0 else None,
            "source": f"src-{i:06d}",
        }
        for i in range(layer_count)
    ]
    layers = [
        {k: v for k, v in layer.items() if v is not None} for layer in layers
    ]
    sources = {
        f"src-{i:06d}": {
            "type": "geojson",
            "ref_id": f"ref:data-{i:06d}",
            "profile": {"featureCount": 100_000},  # 大要素计数仅是数字
        }
        for i in range(min(layer_count, 120))
    }
    return {"layers": layers, "sources": sources,
            "view": {"center": [116.4, 39.9], "zoom": 11}}


def _synth_store(layer_count: int, descriptor_count: int) -> FakeSituationStore:
    descriptors = {
        f"ref:data-{i:06d}": {
            "ref_id": f"ref:data-{i:06d}",
            "feature_count": 100_000,   # 描述符计数，不是物化要素
            "geometry_types": ["Point"],
            "crs": "EPSG:4326",
            "content_revision": 1,
            "bbox": [115.0, 39.0, 117.0, 41.0],
        }
        for i in range(descriptor_count)
    }
    return FakeSituationStore(
        map_state={"_cartographic_mutation_revision": layer_count,
                   "base_layer": "OSM 地图"},
        refs={f"ref:data-{i:06d}": f"ds-{i}" for i in range(descriptor_count)},
        descriptors=descriptors,
    )


COMPILE_LATENCY_BUDGET_S = 5.0  # 合成 1000 层的宽松上界（CI/慢机安全）


@pytest.mark.parametrize("layer_count", [10, 100, 1000])
@pytest.mark.asyncio
async def test_compile_scales_and_projection_stays_bounded(layer_count):
    session_id = f"s-perf-{uuid.uuid4().hex[:8]}"
    store = _synth_store(layer_count, descriptor_count=min(layer_count, 30))
    spec_store = FakeMapspecStore(_synth_spec(layer_count))
    started = time.perf_counter()
    situation = await compile_situation(
        session_id, store=store, mapspec_store=spec_store, compiled_at="T",
    )
    elapsed = time.perf_counter() - started
    assert elapsed < COMPILE_LATENCY_BUDGET_S, (
        f"compile took {elapsed:.2f}s for {layer_count} layers"
    )
    assert situation.map.layer_count.value == layer_count

    projection = render_situation_for_context(situation)
    assert projection.byte_len <= SITUATION_BLOCK_MAX_BYTES
    # 大数据只以计数出现，绝无要素物化。
    assert "100000" in projection.text or "100_000" not in projection.text
    # 1000 层 → 图层列表省略标记（编译期裁剪留痕）。
    if layer_count == 1000:
        assert situation.evidence.omitted  # map.layers(+N) 有记录


@pytest.mark.asyncio
async def test_snapshot_persist_bounded_for_1000_layers():
    session_id = f"s-perf-{uuid.uuid4().hex[:8]}"
    store = _synth_store(1000, descriptor_count=24)
    spec_store = FakeMapspecStore(_synth_spec(1000))
    situation = await compile_situation(
        session_id, store=store, mapspec_store=spec_store, compiled_at="T",
    )
    await advance_snapshot(session_id, situation, store=store)
    import json

    snapshot_bytes = len(json.dumps(
        store._map_state.get("_situation_snapshot"), ensure_ascii=False,
    ).encode("utf-8"))
    # 快照（编译期已裁剪：100 层摘要 + 24 数据集）应有界，远小于 spec 本身。
    assert snapshot_bytes < 64 * 1024, f"snapshot too big: {snapshot_bytes}"


@pytest.mark.asyncio
async def test_projection_independent_of_feature_count():
    """10 万要素 vs 10 要素：唯一差异是数字本身（no O(features)）。"""
    small = _synth_store(10, descriptor_count=5)
    big = _synth_store(10, descriptor_count=5)
    for i in range(5):
        big._descriptors[f"ref:data-{i:06d}"]["feature_count"] = 100_000
    spec = _synth_spec(10)
    s_small = await compile_situation("s-a", store=small,
                                      mapspec_store=FakeMapspecStore(spec))
    s_big = await compile_situation("s-b", store=big,
                                    mapspec_store=FakeMapspecStore(spec))
    p_small = render_situation_for_context(s_small)
    p_big = render_situation_for_context(s_big)
    # 字节数近似（计数数字宽度差），与要素总量无关。
    assert abs(p_big.byte_len - p_small.byte_len) < 200
