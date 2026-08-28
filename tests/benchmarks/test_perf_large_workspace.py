"""大 workspace 性能基准（C6，perf lane）：50/100 层、100+ 轮长会话。

专业 GIS 最容易暴露架构问题的场景之一是图层数量：per-layer 成本若是
O(layers)（每层 mutation 重序列化全 spec / QA 全层扫描无短路），50-100 层
会把交互延迟放大到可感知。本套件锁定：

1. mutation（patch_layer_presentation）在 50/100 层下的成本上界；
2. 语义检查（evaluate_cartography_semantics）在 100 层下有界；
3. GISWorldState 快照在 100 层下有界且截断生效；
4. 100+ 次 mutation 的 provenance 环形上限（64）不越界；
5. cartographic_fingerprint 在 100 层下有界。

断言采用宽松墙钟上限（防回归）+ 结构断言（截断/上限）——确定性计数
断言优先，墙钟只作上界护栏。

Run:
    .venv/bin/python -m pytest -m perf --no-cov tests/benchmarks/test_perf_large_workspace.py -q
Unfiltered runs self-skip per tests/conftest.py #664.
"""
import asyncio
import time
import uuid

import pytest

from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.gis_world_state import build_world_state, get_provenance
from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    PatchLayerPresentationIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import mapspec_store_instance
from app.services.session_data import session_data_manager


def _layer(i: int) -> dict:
    kind = ("circle", "fill", "line")[i % 3]
    return {
        "id": f"layer-{i:03d}",
        "type": kind,
        "source": "src-large",
        "context_role": "result" if i % 10 == 0 else "intermediate",
        "paint": {},
        "layout": {},
    }


def _geojson(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.001, 30.6]},
                "properties": {"id": i, "score": float(i % 50)},
            }
            for i in range(n)
        ],
    }


async def _build_workspace(session_id: str, layer_count: int) -> None:
    """N 层共享一个 inline source（专业场景：一个大数据集派生多层）。"""
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(
        session_id,
        UpsertLayerIntent(layer=_layer(0), source_data=_geojson(200)),
    )
    spec = await mapspec_store_instance.get_mapspec(session_id)
    assert len(spec["layers"]) == 1
    # 后续层直接复用同一 source（pipeline 对 source_data=None 不重写 entry）
    for i in range(1, layer_count):
        await engine.apply_mutation(
            session_id, UpsertLayerIntent(layer=_layer(i))
        )
    spec = await mapspec_store_instance.get_mapspec(session_id)
    assert len(spec["layers"]) == layer_count


@pytest.mark.perf
@pytest.mark.parametrize("layer_count", [50, 100])
def test_perf_mutation_cost_scales_subquadratically(layer_count):
    """50/100 层下单层 presentation mutation 的墙钟上界（防 O(N²) 回归）。"""
    session_id = f"perf-ws-{layer_count}-{uuid.uuid4().hex[:8]}"

    async def run():
        await _build_workspace(session_id, layer_count)
        engine = MapSpecLifecycleEngine()
        start = time.perf_counter()
        for i in range(0, layer_count, max(1, layer_count // 10)):
            res = await engine.apply_mutation(
                session_id,
                PatchLayerPresentationIntent(layer_id=f"layer-{i:03d}", visible=False),
            )
            assert not res.is_error
        return time.perf_counter() - start

    elapsed = asyncio.run(run())
    # 护栏：10 次 mutation 在 100 层下 < 8s（含磁盘 save：每次 mutation 落
    # 盘整 spec + revision sidecar——这是当前持久化模型的真实成本基线）。
    ceiling = 8.0 if layer_count == 100 else 4.0
    assert elapsed < ceiling, f"{layer_count} layers: 10 mutations took {elapsed:.2f}s"
    print(f"[perf-ws] {layer_count} layers, 10 mutations: {elapsed:.3f}s")


@pytest.mark.perf
def test_perf_semantics_and_fingerprint_bounded_at_100_layers():
    session_id = f"perf-ws-qa-{uuid.uuid4().hex[:8]}"

    async def run():
        await _build_workspace(session_id, 100)
        return await mapspec_store_instance.get_mapspec(session_id)

    spec = asyncio.run(run())

    start = time.perf_counter()
    report = evaluate_cartography_semantics(spec)
    semantics_s = time.perf_counter() - start

    start = time.perf_counter()
    fp = cartographic_fingerprint(spec)
    fp_s = time.perf_counter() - start

    assert len(report.checks) > 0
    assert fp
    # 护栏：100 层 QA < 12s、指纹 < 2s
    assert semantics_s < 12.0, f"semantics at 100 layers: {semantics_s:.2f}s"
    assert fp_s < 2.0, f"fingerprint at 100 layers: {fp_s:.2f}s"
    print(f"[perf-ws] 100 layers: semantics={semantics_s:.3f}s fingerprint={fp_s:.3f}s")


@pytest.mark.perf
def test_perf_world_state_snapshot_bounded_and_truncated():
    session_id = f"perf-ws-snap-{uuid.uuid4().hex[:8]}"

    async def run():
        await _build_workspace(session_id, 120)  # 超过摘要上限 100
        return await build_world_state(session_id)

    snapshot = asyncio.run(run())
    # 摘要截断：layers ≤ 100，但 layer_count_total 如实
    assert len(snapshot["layers"]) <= 100
    assert snapshot["layer_count_total"] == 120
    # payload 不变量
    import json

    assert "FeatureCollection" not in json.dumps(snapshot)
    # revision 已推进（每层 upsert +1）
    assert snapshot["revision"] >= 120


@pytest.mark.perf
def test_perf_provenance_ring_bounded_over_long_session():
    """150 次 mutation（模拟 100+ 轮会话）：provenance 环形上限 64 生效。"""
    session_id = f"perf-ws-prov-{uuid.uuid4().hex[:8]}"

    async def run():
        engine = MapSpecLifecycleEngine()
        await engine.apply_mutation(
            session_id,
            UpsertLayerIntent(layer=_layer(0), source_data=_geojson(50)),
        )
        from app.services.gis_world_state import apply_gis_mutation

        state = await session_data_manager.get_map_state(session_id)
        revision = int(state.get("_cartographic_mutation_revision", 0) or 0)
        for i in range(150):
            res = await apply_gis_mutation(
                session_id,
                PatchLayerPresentationIntent(
                    layer_id="layer-000", visible=(i % 2 == 0)
                ),
                origin="user",
                actor="perf",
                expected_revision=revision,
            )
            assert not res.is_error, f"mutation {i} failed: {res.error_msg}"
            revision = res.mutation_revision
        return await get_provenance(session_id)

    entries = asyncio.run(run())
    assert len(entries) <= 64
    # 环形保留的是最近决策（尾部 150 号决策在内）
    assert entries[-1]["revision"] >= 150

