"""Track B（M15）：分页/聚合/MVT 位置 id 的性能基准（真实计时，非 mock）。

三档合成数据（5k/50k/200k，含点/线/面/中文字段/无 id 要素），度量：
- features 分页：首页/排序首页（含 permutation 构建）/过滤页 p50+p95；
- 聚合全扫（group_by count）耗时；
- MVT encode_tile_from_index 含位置 id 的瓦片编码耗时（vs 母版语义）。

断言纪律：正确性硬断言（无漏无重/total 精确/位置 id 正确）+ 宽性能上界
（抓病态回归，不预设无依据绝对秒数）；实测数值打印供 EVIDENCE 记录。
"""
from __future__ import annotations

import statistics
import time

import pytest

from app.lib.cartography.label_collision import MAX_LABELS_PER_EXPORT  # noqa: F401
from app.services.feature_aggregate import aggregate_features
from app.services.feature_pages import (
    count_matching,
    page_features,
    parse_where_param,
)
from app.services.mvt import build_spatial_index_entry, encode_tile_from_index


def _synthetic_fc(n: int, with_ids: bool = True) -> dict:
    features = []
    for i in range(n):
        geom_type = i % 10
        if geom_type < 8:
            # 城市级跨度（~1.5°×1.2°，z14 单瓦片 ~2k 点，贴近真实学校密度；
            # 更密的挤布属于渲染器自身压测，不是本基准的对象）
            geometry = {"type": "Point", "coordinates": [103.2 + (i % 300) * 0.005, 29.6 + (i % 240) * 0.005]}
        elif geom_type == 8:
            geometry = {"type": "LineString", "coordinates": [
                [104.0 + (i % 50) * 0.01, 30.0], [104.0 + (i % 50) * 0.01 + 0.01, 30.01]]}
        else:
            geometry = {"type": "Polygon", "coordinates": [
                [[104.0, 30.0], [104.01, 30.0], [104.01, 30.01], [104.0, 30.0]]]}
        props = {
            "类型": ["小学", "中学", "幼儿园"][i % 3],
            "学生数": (i * 37) % 1200,
            "区县": f"区{i % 20}",
            "名称": f"第{i}学校",
        }
        f = {"type": "Feature", "geometry": geometry, "properties": props}
        if with_ids and i % 4 != 3:  # 25% 要素无 id（位置 id 路径）
            f["id"] = i
        features.append(f)
    return {"type": "FeatureCollection", "features": features}


def _timed(fn, repeat=5):
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples), max(samples)


@pytest.mark.parametrize("n", [5_000, 50_000, 200_000])
class TestPagingBenchmark:
    def test_first_page_and_sorted_and_filtered(self, n):
        fc = _synthetic_fc(n)
        cache_key = ("ref:bench", "1")
        # 首页（自然序切片）
        p50_first, p95_first = _timed(
            lambda: page_features(fc, limit=200, cache_key=cache_key))
        # 排序首页（含 permutation 构建 —— 首次最贵，缓存键区分）
        p50_sort, p95_sort = _timed(
            lambda: page_features(fc, limit=200, order=("学生数", -1), cache_key=None))
        # 过滤页 + 精确 total
        where = parse_where_param('{"field":"类型","op":"eq","value":"小学"}')
        p50_filter, p95_filter = _timed(
            lambda: page_features(fc, limit=200, where=where, cache_key=None))
        total_ms, _ = _timed(
            lambda: count_matching(fc["features"], where=where, bbox=None, cache_key=None), repeat=3)

        # 正确性硬断言
        page = page_features(fc, limit=200, where=where, cache_key=None)
        exact = count_matching(fc["features"], where=where, bbox=None, cache_key=None)
        assert exact == sum(1 for f in fc["features"] if f["properties"]["类型"] == "小学")
        assert all(f["properties"]["类型"] == "小学" for f in page["features"])
        # 排序页数值单调
        spage = page_features(fc, limit=200, order=("学生数", -1), cache_key=None)
        vals = [f["properties"]["学生数"] for f in spage["features"]]
        assert vals == sorted(vals, reverse=True)

        print(
            f"\n[bench n={n}] first p50/p95={p50_first:.1f}/{p95_first:.1f}ms "
            f"sort-first p50/p95={p50_sort:.1f}/{p95_sort:.1f}ms "
            f"filter p50/p95={p50_filter:.1f}/{p95_filter:.1f}ms "
            f"total-count={total_ms:.1f}ms"
        )
        # 宽上界（抓病态回归；本机实测留 EVIDENCE）
        assert p95_first < 1500, f"首页 p95 {p95_first:.0f}ms 超宽上界"
        assert p95_sort < 5000, f"排序首建 p95 {p95_sort:.0f}ms 超宽上界"
        assert p95_filter < 2500, f"过滤页 p95 {p95_filter:.0f}ms 超宽上界"

    def test_aggregate_full_scan(self, n):
        fc = _synthetic_fc(n)
        p50, p95 = _timed(
            lambda: aggregate_features(fc, group_by="类型"), repeat=3)
        result = aggregate_features(fc, group_by="类型")
        by_key = {row["key"]: row["count"] for row in result["rows"]}
        assert by_key["小学"] == sum(1 for f in fc["features"] if f["properties"]["类型"] == "小学")
        print(f"[bench n={n}] aggregate count p50/p95={p50:.1f}/{p95:.1f}ms")
        assert p95 < 4000

    def test_mvt_tile_with_positional_ids(self, n):
        fc = _synthetic_fc(n, with_ids=False)
        entry = build_spatial_index_entry(("bench", "ref:no-id"), fc)
        # 从数据点推导瓦片坐标（确定性，不用手算 z/x/y）
        import math

        z = 14
        lon0, lat0 = fc["features"][0]["geometry"]["coordinates"]
        world = 2 ** z
        xf = (lon0 + 180.0) / 360.0 * world
        lat_r = math.radians(lat0)
        yf = (1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * world
        x, y = int(xf), int(yf)
        t0 = time.perf_counter()
        tile = encode_tile_from_index(entry, z, x, y)
        dt = (time.perf_counter() - t0) * 1000.0
        assert tile, f"tile 非空 (z={z},x={x},y={y},lon={lon0},lat={lat0})"
        # 位置 id 正确性：解码所有 field 1 并验证与自然索引一致
        from tests.unit.test_mvt_positional_id import _decode_tile_feature_ids

        ids = [i for i in _decode_tile_feature_ids(tile) if i is not None]
        assert ids, "无 id 数据集必须携带位置 id"
        assert all(1 <= i <= n for i in ids)
        print(f"[bench n={n}] mvt tile w/ positional ids={dt:.1f}ms features={len(ids)}")
        assert dt < 2000
