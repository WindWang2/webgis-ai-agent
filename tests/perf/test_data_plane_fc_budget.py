"""数据面 FC 端点 ETag/304 的成本与确定性基准（extreme-scale v2）。

度量（合成 workload；本地基线棘轮，非 SLO）：
1. serialize+ETag 的确定性 —— 同一 FC 反复序列化必须恒同字节（mtime=0
   纪律的响应侧镜像：ETag 若随时间漂移，304 就退化成整包 200）。
2. 10k/100k 要素的 serialize+hash 成本 —— 304 省的是**网络传输**，
   服务端仍付一次序列化；此基准把这条边界钉进测试（预算核算的输入）。
3. `_etag_matches` 的 RFC 7232 语义（unit 级契约）。
"""

import hashlib

import pytest

from app.api.routes.layer import _etag_matches
from app.lib.geojson_serializer import serialize_geojson


def _fc(n: int) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {"type": "Point", "coordinates": [116 + (i % 1000) * 0.01, 39 + i // 1000 * 0.01]},
                "properties": {"i": i, "name": f"poi-{i}"},
            }
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_fc_serialize_etag_deterministic_across_calls():
    """确定性必须钉在**真实 ETag 计算路径**上：gzip(mtime=0) 字节的 sha256。

    教训（review P1）：gzip.compress 默认嵌时间 → 对未压缩 body 断言确定性
    钉不住生产 ETag（那才是漂移的对象）。
    """
    import gzip as _gzip

    fc = _fc(2_000)
    body_a = await serialize_geojson(fc, pretty=False)
    body_b = await serialize_geojson(fc, pretty=False)
    assert body_a == body_b
    gz_a = _gzip.compress(body_a, 6, mtime=0)
    gz_b = _gzip.compress(body_b, 6, mtime=0)
    assert gz_a == gz_b
    assert hashlib.sha256(gz_a).hexdigest()[:16] == hashlib.sha256(gz_b).hexdigest()[:16]


@pytest.mark.asyncio
async def test_fc_serialize_hash_cost_scales_linearly_10k_100k():
    async def _cost(n: int) -> float:
        import time
        fc = _fc(n)
        # warm
        await serialize_geojson(fc, pretty=False)
        t0 = time.perf_counter()
        body = await serialize_geojson(fc, pretty=False)
        hashlib.sha256(body).hexdigest()
        return (time.perf_counter() - t0) * 1000

    ms_10k = await _cost(10_000)
    ms_100k = await _cost(100_000)
    # 线性量纲（10x 要素 → ≤25x 成本；JSON 序列化近似线性，留抖动余量）。
    assert ms_100k < ms_10k * 25 + 50, (
        f"100k cost {ms_100k:.1f}ms vs 10k cost {ms_10k:.1f}ms — 超线性放大，"
        f"响应侧序列化路径退化"
    )


def test_etag_matches_rfc7232_semantics():
    assert _etag_matches(None, '"abc"') is False
    assert _etag_matches('', '"abc"') is False
    assert _etag_matches('"abc"', '"abc"') is True
    assert _etag_matches('"x", "abc"', '"abc"') is True
    assert _etag_matches('*', '"abc"') is True
    assert _etag_matches('"abd"', '"abc"') is False
