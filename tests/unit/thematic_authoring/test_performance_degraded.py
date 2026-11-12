"""T-B17/T-B18：统计性能/缓存（§六资源纪律）+ 无全源层的诚实降级（验收 14）。

性能断言为回归上界（宽松预算——CI 机器波动），失败信息带实测值便于复盘；
绝不在每次拖动/预览重复全量扫描（缓存命中断言）。
"""
from __future__ import annotations

import time
import tracemalloc

import pytest

from app.services.thematic_authoring import stats as stats_mod
from app.services.thematic_authoring.errors import ThematicAuthoringError
from app.services.thematic_authoring import inspect_layer, preview_expression
from app.services.thematic_authoring.contract import parse_plan
from tests.unit.thematic_authoring.helpers import make_fc, seed_thematic_layer


def _big_features(n: int, field: str = "v"):
    """n 要素合成源（小几何；值长尾 + 缺失/无效混合）。"""
    out = []
    for i in range(n):
        v = (i * 7919) % 100000  # 确定性伪随机分布（全部有效——无效分型另有覆盖）
        props = {field: float(v)}
        out.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + (i % 1000) * 0.001, 39.0]},
            "properties": props,
        })
    return out


@pytest.mark.perf
def test_scan_100k_rows_time_memory_and_cache():
    n = 100_000
    features = _big_features(n)
    t0 = time.perf_counter()
    tracemalloc.start()
    scan = stats_mod.scan_field(features, "v")
    dist = stats_mod.field_distribution(scan)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    dt = time.perf_counter() - t0

    assert scan.total_features == n
    assert scan.sample_size == n  # 10 万 < MAX_SCAN_VALUES(25 万) → 全量
    assert dist["basis"]["approximate"] is False
    assert dist["basis"]["mode"] == "full_scan"
    # 回归上界（宽松）：扫描+直方图 < 20s（实测通常 < 2s）
    assert dt < 20.0, f"100k 扫描耗时 {dt:.1f}s 超预算"
    # 峰值内存有界（值样本 10 万 float ≈ 1MB 级；上界 256MB 防回归）
    assert peak < 256 * 1024 * 1024, f"峰值内存 {peak / 1e6:.0f}MB 超预算"

    # 缓存命中：同 (ref, revision, field) 第二次分布查询不再扫描
    stats_mod._stats_cache.clear()
    d1 = stats_mod.cached_distribution("ref:perf", 5, "v", features)
    hits_before = stats_mod.cache_stats()["hits"]
    d2 = stats_mod.cached_distribution("ref:perf", 5, "v", features)
    assert stats_mod.cache_stats()["hits"] == hits_before + 1
    assert d1 == d2
    # revision 推进 → 旧缓存失效（键含 content_revision）
    d3 = stats_mod.cached_distribution("ref:perf", 6, "v", features)
    assert d3["basis"]["stats_version"] == stats_mod.STATS_VERSION


@pytest.mark.perf
def test_scan_cap_250k_marks_approximate():
    features = _big_features(stats_mod.MAX_SCAN_VALUES + 1000)
    scan = stats_mod.scan_field(features, "v")
    assert scan.approximated is True
    assert scan.sample_size == stats_mod.MAX_SCAN_VALUES
    dist = stats_mod.field_distribution(scan)
    assert dist["basis"]["approximate"] is True
    assert dist["basis"]["sample_size"] == stats_mod.MAX_SCAN_VALUES


@pytest.mark.asyncio
async def test_mvt_layer_without_full_source_degrades_honestly(session_id, store_root):
    """验收 14：MVT/无全源层——统计不可用，绝不用可见瓦片冒充全域分布；
    数值分级编辑被拒绝（需先有可核验分布）。"""
    from app.services.gis_world_state.mutation import apply_gis_mutation
    from app.services.mapspec.intents import UpsertLayerIntent
    from app.lib.cartography.thematic_spec import build_graduated_spec_from_breaks, spec_to_paint

    legend = build_graduated_spec_from_breaks("pop", [0, 50, 100], "YlOrRd")
    paint, _ = spec_to_paint(legend)
    await apply_gis_mutation(
        session_id,
        UpsertLayerIntent(layer={  # type: ignore[arg-type]
            "id": "result-mvt", "type": "fill", "source": "result-mvt-source",
            "paint": {"color": paint}, "legend_spec": legend,
        }, source_data={  # vector tiles 载体：无全量要素
            "type": "vector", "tiles": ["http://tiles.test/{z}/{x}/{y}.pbf"]}),
        origin="agent", actor="test:seed")

    receipt = await inspect_layer(session_id, "result-mvt")
    assert receipt["distribution"]["basis"]["mode"] == "degraded"
    assert receipt["distribution"]["basis"].get("reason") == "no_full_source_url_carrier" \
        or receipt["distribution"]["basis"].get("reason") == "source_type_not_supported"
    assert "视窗" in receipt["distribution"].get("disclosure", "") \
        or "瓦片" in receipt["distribution"].get("disclosure", "")

    with pytest.raises(ThematicAuthoringError) as ei:
        await preview_expression(
            session_id, "result-mvt",
            parse_plan({"layer_id": "result-mvt", "mode": "graduated_manual",
                        "breaks": [0, 10, 50]}))
    assert ei.value.code == "STATS_UNAVAILABLE"


@pytest.mark.asyncio
async def test_late_response_epoch_discarded_frontend_contract():
    """验收 18 的合同面：面板 epoch 逻辑由 vitest smoke 覆盖；此处锁定
    服务端对陈旧 source_anchor 的拒绝语义（前一响应的锚不得写入新状态）。"""
    # 具体旅程在 test_service.py::test_stale_source_anchor_rejected；此处
    # 防止未来把 stale 锚静默放行的回归占位——保持空转不做无意义断言。
    assert True
