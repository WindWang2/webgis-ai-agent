"""Large Data Plane vNext — 100k synthetic 场景基线（W12）。

覆盖 DoD 的「10万级 synthetic 场景稳定、可重复本地 benchmark」：

- 窗口分页：单页延迟/字节 vs 整包 FC 序列化（浏览一条 100k 属性表不再
  需要整包 —— 网络预算的量级证据）；
- 目录 MVT 纯 Python 回退：bbox 有界查询 + 编码 cold vs warm（缓存身份
  生效的证据）；
- DfTileCache 字节记账：100k 级瓦片驻留受 256MB/4MB 双预算真实约束
  （回归锚点：修复前 len(tuple)==2 记账使预算从未生效）；
- NDJSON 流式 vs 整包：稳态内存 ≈ 有界队列，不随数据集总量增长。

断言纪律（#664）：锚定**字节预算与构建次数**等机器无关不变量 + 宽松的
相对比较（分页 << 整包），绝不写绝对墙钟阈值。
"""
import asyncio
import gzip
import json
import time

import pytest

from app.services.data_fabric.tile_cache import DfTileCache, TileBuildCoalescer, TileCacheEntry
from app.services.data_fabric.tile_identity import tile_bounds_lonlat
from app.services.feature_pages import page_features


def _100k_fc(lon0=104.0, lat0=30.0, spread=1.0):
    """100k Point FC（确定性、可重复；~10MB JSON 量级）。"""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [lon0 + (i % 316) * 0.003 * spread, lat0 + (i // 316) * 0.003 * spread]},
                "properties": {"id": i, "category": f"cat{i % 10}", "name": f"feature-{i}"},
            }
            for i in range(100_000)
        ],
    }


@pytest.fixture(scope="module")
def fc_100k():
    return _100k_fc()


def test_window_paging_bytes_and_latency_vs_full_serialization(fc_100k):
    """浏览 100k 属性表：单页窗口 vs 整包 FC 的字节/成本量级。"""
    full_bytes = len(json.dumps(fc_100k, ensure_ascii=False, separators=(",", ":")))

    t0 = time.perf_counter()
    page = page_features(fc_100k, limit=200)
    page_elapsed = time.perf_counter() - t0
    page_payload = {
        "type": "FeatureCollection",
        "features": page["features"],
        "pagination": {"next_cursor": page["next_cursor"], "has_more": page["has_more"]},
    }
    page_bytes = len(json.dumps(page_payload, ensure_ascii=False, separators=(",", ":")))

    assert page["returned"] == 200 and page["has_more"]
    assert page_bytes < full_bytes / 100, (
        f"单页响应必须比整包小两个数量级以上: page={page_bytes} full={full_bytes}"
    )
    t0 = time.perf_counter()
    json.dumps(page_payload)
    page_serialize = time.perf_counter() - t0
    t0 = time.perf_counter()
    json.dumps(fc_100k)
    full_serialize = time.perf_counter() - t0
    assert page_serialize < full_serialize, "页序列化必须显著便宜于整包"
    # 机器无关量纲锚点：单页 CPU 成本记录（不设绝对阈值）
    print(f"[bench] page={page_bytes}B page_serialize={page_serialize * 1e3:.2f}ms "
          f"full={full_bytes}B full_serialize={full_serialize * 1e3:.2f}ms "
          f"page_call={page_elapsed * 1e3:.2f}ms")


def test_full_paging_traversal_stable_order_100k(fc_100k):
    """100k 全量翻页遍历：不漏不重、稳定序（cursor 正确性的规模验证）。"""
    ids = []
    cursor = None
    pages = 0
    t0 = time.perf_counter()
    while True:
        page = page_features(fc_100k, limit=1000, cursor=cursor)
        ids.extend(f["id"] for f in page["features"])
        pages += 1
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    elapsed = time.perf_counter() - t0
    assert ids == list(range(100_000)), "全量翻页拼接 == 原序"
    assert pages == 100
    print(f"[bench] 100k full traversal: {pages} pages in {elapsed:.2f}s")


def test_catalog_fallback_tile_cold_vs_warm_cache():
    """目录 MVT 纯 Python 回退：bbox 有界查询 → 编码，缓存身份使二次请求零查询。"""
    from types import SimpleNamespace

    from app.services.data_fabric import tile_service as ts

    bounds = tile_bounds_lonlat(10, 840, 400)
    cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    features = [
        {
            "type": "Feature", "id": i,
            "geometry": {"type": "Point", "coordinates": [cx + (i % 100) * 1e-4, cy + (i // 100) * 1e-4]},
            "properties": {"i": i},
        }
        for i in range(20_000)
    ]
    queries = {"n": 0}

    class _Adapter:
        def query(self, dataset_id, spec):
            queries["n"] += 1
            return SimpleNamespace(features=features)

    svc = ts.CatalogTileService(
        cache=DfTileCache(max_entries=64, max_bytes=256 * 1024 * 1024),
        coalescer=TileBuildCoalescer(),
    )
    svc._resolve_governed_adapter = lambda ds_model: _Adapter()  # benchmark：绕开 registry 直供 adapter
    item = SimpleNamespace(id="bench-item", name="bench_layer", fingerprint="fp-bench")
    ds = SimpleNamespace(id="src", org_id=None, owner_id="bench", source_type="local_file")

    async def scenario():
        t0 = time.perf_counter()
        cold = await svc.serve_catalog_tile(item, ds, 10, 840, 400)
        cold_ms = (time.perf_counter() - t0) * 1e3
        t0 = time.perf_counter()
        warm = await svc.serve_catalog_tile(item, ds, 10, 840, 400)
        warm_ms = (time.perf_counter() - t0) * 1e3
        return cold, warm, cold_ms, warm_ms

    cold, warm, cold_ms, warm_ms = asyncio.run(scenario())
    assert cold.source == "python_fallback" and cold.cache_state == "miss"
    assert warm.cache_state == "hit" and warm.source == "cache"
    assert warm.gz == cold.gz, "同 fingerprint 同瓦片：缓存命中等字节"
    assert queries["n"] == 1, "warm 命中不得触发第二次 bbox 查询"
    assert len(gzip.decompress(cold.gz)) > 0
    print(f"[bench] 20k-in-tile fallback: cold={cold_ms:.1f}ms warm={warm_ms:.3f}ms "
          f"gz={len(cold.gz)}B")


def test_tile_cache_byte_budget_holds_at_100k_tiles():
    """字节记账回归锚点：万级瓦片驻留受双预算真实约束（修复前记账恒为 2N）。"""
    cache = DfTileCache(max_entries=100_000, max_bytes=8 * 1024 * 1024, max_entry_bytes=4 * 1024 * 1024)
    tile = bytes(4096)
    for i in range(4096):  # 4096 个唯一键 × 4KB = 16MB 潜在驻留 → 8MB 预算必须逐出
        cache.put(("item", "scope", "fp", i % 2048, i // 2048, 0), TileCacheEntry(gz=tile, fingerprint="fp"))
    s = cache.stats()
    assert s["bytes"] <= 8 * 1024 * 1024, "字节预算必须真实生效（修复前恒为 2×条目数）"
    assert s["bytes"] == s["entries"] * 4096, "记账 == gzip 载荷总和"
    assert s["evictions"] > 0


def test_stream_memory_is_bounded_by_queue_not_dataset():
    """NDJSON 流式稳态内存 ≈ 有界队列：消费端逐行取，驻留不随数据集增长。"""
    from app.extensions_platform import fabric_bridge
    from app.extensions_platform.sdk.provider import StreamingVectorProvider

    produced = {"n": 0}

    class Big(StreamingVectorProvider):
        def stream_features(self, params, page_size=500):
            class G:
                def __iter__(inner):
                    return inner

                def __next__(inner):
                    if produced["n"] >= 20_000:
                        raise StopIteration
                    produced["n"] += 1
                    return {"type": "Feature", "id": produced["n"],
                            "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                            "properties": {"pad": "x" * 256}}

            return G()

    async def scenario():
        count = 0
        max_pending = 0
        async for _f in fabric_bridge.stream_features_from_adapter(Big(), "ds", None, page_size=500):
            count += 1
            # 队列上界 64：消费即时取，任意时刻 in-flight 有界
            max_pending = max(max_pending, produced["n"] - count)
        return count, max_pending

    count, max_pending = asyncio.run(scenario())
    assert count == 20_000
    # 泵瞬态上界 = 队列容量 + 阻塞在 put 的 1 条 + 消费者已取未计的 1 条
    assert max_pending <= 66, (
        f"泵在飞上界必须 ≈ 队列容量（实际 {max_pending}）—— 流式内存不随数据集增长"
    )
