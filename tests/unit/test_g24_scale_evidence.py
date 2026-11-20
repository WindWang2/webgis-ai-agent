"""G24 / P7：大图层证据 —— 一次点击与总数据量解耦 + 几何 fixture 覆盖。

证据锚点：
- A24/backend：10万级点数据集上，spatial index 在场时单要素权威回查走
  feature_id_index（O(1)），**不触发线性扫描**（计数器钉住 _find_feature_by_id
  调用次数 = 0）；响应字节 = 单要素（< 4KB），与 100k 要素数据集（MB 级）
  解耦；
- A08/A10：带洞多边形/多部件几何可编码进瓦片（跨瓦片同 id 多件）；
- 档位：默认 100k（~10MB 级 FC）；1M 档位以 G24_MILLION=1 显式开启
  （资源分档披露，不默认跑）。
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import layer as layer_routes
from app.services.mvt import (
    build_spatial_index_entry,
    encode_tile,
    spatial_index_cache,
)
from app.services.session_data import MemorySessionStore

TIER_MILLION = os.environ.get("G24_MILLION") == "1"
N_POINTS = 1_000_000 if TIER_MILLION else 100_000


def _points_fc(n: int):
    feats = []
    for i in range(n):
        feats.append({
            "type": "Feature",
            "id": i + 1,
            "geometry": {"type": "Point", "coordinates": [100.0 + (i % 1000) * 0.001, 20.0 + (i // 1000) * 0.001]},
            "properties": {"i": i, "name": f"pt-{i}"},
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(layer_routes.router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _stub_ownership(app):
    from app.services.auth_history_bridge import require_owned_session

    app.dependency_overrides[require_owned_session] = lambda: MagicMock()


@pytest.mark.asyncio
async def test_single_pick_lookup_is_indexed_and_bounded_bytes(client, monkeypatch):
    """A24：100k（或 1M 档）数据集单要素回查 = O(1) 索引 + 单要素字节。"""
    sid = "sess-g24-scale-0001"
    fc = _points_fc(N_POINTS)
    dataset_bytes = len(str(fc))
    store = MemorySessionStore()
    monkeypatch.setattr(layer_routes, "session_data_manager", store)
    ref = await store.store(sid, {"geojson": fc}, prefix="data")

    # 预构建 MVT 空间索引（瓦片服务常态路径）—— 回查优先走它
    t0 = time.perf_counter()
    entry = build_spatial_index_entry((sid, ref), {"geojson": fc})
    build_s = time.perf_counter() - t0
    spatial_index_cache._entries[(sid, ref)] = entry  # 与 get_or_build 同效注入
    try:
        idx = entry.feature_id_index()
        assert len(idx) == N_POINTS  # 每要素一个链内 id（顶层 id）

        # 线性扫描计数器：index 路径绝不触发
        scan_calls = {"n": 0}
        orig = layer_routes._find_feature_by_id

        def counting(*args, **kwargs):
            scan_calls["n"] += 1
            return orig(*args, **kwargs)

        monkeypatch.setattr(layer_routes, "_find_feature_by_id", counting)

        t1 = time.perf_counter()
        r = await client.get(
            f"/api/v1/layers/data/{ref}/feature/{N_POINTS}?session_id={sid}")
        lookup_s = time.perf_counter() - t1

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == N_POINTS
        assert scan_calls["n"] == 0, "索引在场时不得线性全扫（P7 硬约束）"
        # 传输解耦：单要素响应 ≪ 数据集（4KB 上界 vs MB 级全量）
        assert len(r.content) < 4096, f"单要素响应 {len(r.content)}B 超上界"
        assert dataset_bytes > 1_000_000  # 数据集本身是 MB 级（证据前提）
        # 记录证据（不做硬时限断言 —— CI 抖动只报不卡）
        print(
            f"\n[G24 P7] tier={N_POINTS} dataset={dataset_bytes/1e6:.1f}MB "
            f"index_build={build_s*1000:.0f}ms indexed_lookup={lookup_s*1000:.1f}ms "
            f"response={len(r.content)}B linear_scans={scan_calls['n']}"
        )
    finally:
        spatial_index_cache._entries.pop((sid, ref), None)


# ─── 几何 fixture：洞 / 多部件 / 跨瓦片 ────────────────────────────────────

def _decode_tile_ids(data: bytes):
    """最小 MVT 解码：layer 'data' 的 [(feature_id|None)]（field 1 aware）。"""
    def varint(buf, pos):
        result = 0
        shift = 0
        while True:
            b = buf[pos]
            pos += 1
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result, pos
            shift += 7

    def iter_fields(buf):
        pos = 0
        while pos < len(buf):
            key, pos = varint(buf, pos)
            num, wire = key >> 3, key & 7
            if wire == 0:
                value, pos = varint(buf, pos)
            else:
                length, pos = varint(buf, pos)
                value = buf[pos:pos + length]
                pos += length
            yield num, wire, value

    for num, _wire, layer_buf in iter_fields(data):
        if num != 3:
            continue
        ids = []
        for fnum, fwire, fval in iter_fields(layer_buf):
            if fnum == 2 and fwire == 2:  # Feature
                fid = None
                for fn2, fw2, fv2 in iter_fields(fval):
                    if fn2 == 1 and fw2 == 0:
                        fid = fv2
                ids.append(fid)
        return ids
    return []


POLY_WITH_HOLE = {
    "type": "Feature",
    "id": "poly-holey",
    "geometry": {
        "type": "Polygon",
        "coordinates": [
            [[116.30, 39.85], [116.60, 39.85], [116.60, 40.05], [116.30, 40.05], [116.30, 39.85]],
            [[116.40, 39.90], [116.50, 39.90], [116.50, 39.97], [116.40, 39.97], [116.40, 39.90]],
        ],
    },
    "properties": {"name": "holey"},
}

MULTI_PART = {
    "type": "Feature",
    "id": "multi-part",
    "geometry": {
        "type": "MultiPolygon",
        "coordinates": [
            [[[116.0, 39.9], [116.1, 39.9], [116.1, 40.0], [116.0, 40.0], [116.0, 39.9]]],
            [[[116.8, 39.9], [116.9, 39.9], [116.9, 40.0], [116.8, 40.0], [116.8, 39.9]]],
        ],
    },
    "properties": {"name": "multipart"},
}


def test_polygon_with_hole_encodes_and_cross_tile_pieces_share_absence_of_fid():
    """A08 fixture：带洞面编码非空；有链内 id → 无 field-1 位置 id（寻址走属性）。"""
    fc = {"type": "FeatureCollection", "features": [POLY_WITH_HOLE]}
    tile = encode_tile(fc, 10, 843, 387)  # 覆盖该面的瓦片（116.45, 39.95 → z10 x843 y387）
    assert tile, "带洞面必须产出几何"
    ids = _decode_tile_ids(tile)
    assert ids == [None], "有链内 id 的要素不编码位置 id（字节 parity 语义）"


def test_multipart_polygon_appears_in_both_tiles():
    """A10 fixture：MultiPolygon 两部件落在相邻瓦片 —— 两瓦片都非空。

    （跨瓦片去重由客户端按链内 id 收敛 —— brush/candidateIdsFromHits 已钉。）"""
    fc = {"type": "FeatureCollection", "features": [MULTI_PART]}
    # z10：116.05 → x842，116.85 → x844（同一 y=387）
    t1 = encode_tile(fc, 10, 842, 387)
    t2 = encode_tile(fc, 10, 844, 387)
    assert t1 and t2, "多部件的两半各在自己瓦片可见"


@pytest.mark.skipif(not TIER_MILLION, reason="1M 档位需 G24_MILLION=1（资源分档披露）")
@pytest.mark.asyncio
async def test_million_tier_lookup_bounded(client, monkeypatch):
    """A24（1M 档）：同上证据在百万点档位复跑。"""
    sid = "sess-g24-scale-1m"
    fc = _points_fc(1_000_000)
    store = MemorySessionStore()
    monkeypatch.setattr(layer_routes, "session_data_manager", store)
    ref = await store.store(sid, {"geojson": fc}, prefix="data")
    entry = build_spatial_index_entry((sid, ref), {"geojson": fc})
    spatial_index_cache._entries[(sid, ref)] = entry
    try:
        r = await client.get(f"/api/v1/layers/data/{ref}/feature/999999?session_id={sid}")
        assert r.status_code == 200
        assert r.json()["id"] == 999999
        assert len(r.content) < 4096
        print(f"\n[G24 P7] million tier: response={len(r.content)}B")
    finally:
        spatial_index_cache._entries.pop((sid, ref), None)
