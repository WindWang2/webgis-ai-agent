"""CatalogTileService（W12）：目录瓦片服务唯一事实源的域内契约。

覆盖：能力驱动双路径（server_mvt / python_fallback）、bbox 视口翻译、
有界编码（不信任远端 limit）、空瓦片 204 语义、fingerprint 失效、
租户隔离、并发收敛、缓存 bypass 诚实降级。
"""
import asyncio
import gzip
from types import SimpleNamespace

import pytest

from app.services.data_fabric import tile_service as ts
from app.services.data_fabric.tile_cache import DfTileCache, TileBuildCoalescer
from app.services.data_fabric.tile_identity import tile_bounds_lonlat


def _point_feature(i, lon=104.0, lat=30.0):
    return {
        "type": "Feature",
        "id": i,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"i": i},
    }


def _tile_center(z, x, y):
    """瓦片包络中心（测试要素必须落在目标瓦片内，否则被裁剪为空瓦片）。"""
    min_lon, min_lat, max_lon, max_lat = tile_bounds_lonlat(z, x, y)
    return ((min_lon + max_lon) / 2.0, (min_lat + max_lat) / 2.0)


def _item(fingerprint="fp1"):
    return SimpleNamespace(id="item-1", name="layer_a", fingerprint=fingerprint)


def _ds(owner="owner-a", source_type="local_file"):
    return SimpleNamespace(id="src-1", org_id=None, owner_id=owner, source_type=source_type)


class _FakeVectorAdapter:
    """无 serve_mvt_tile 的矢量 adapter → 纯 Python fallback 路径。"""

    def __init__(self, features, query_log=None, delay=0.0):
        self._features = features
        self._query_log = query_log if query_log is not None else []
        self._delay = delay

    def query(self, dataset_id, spec):
        self._query_log.append((dataset_id, spec))
        if self._delay:
            import time

            time.sleep(self._delay)
        return SimpleNamespace(features=list(self._features))


class _ServerMvtAdapter(_FakeVectorAdapter):
    def serve_mvt_tile(self, dataset_id, z, x, y, timeout_s=30.0):
        self._query_log.append(("mvt", dataset_id, z, x, y))
        return b"\x1a\x00"  # 任意非空 MVT 字节（空结果返回 None 由调用方处理）


class _PassthroughVectorAdapter(_FakeVectorAdapter):
    """query() 原样透传 features 容器（不做 list() 拷贝）—— 拷贝路径取证用。"""

    def query(self, dataset_id, spec):
        self._query_log.append((dataset_id, spec))
        return SimpleNamespace(features=self._features)


class _IndexAccessProbe:
    """序列探针：记录单元素 __getitem__ 被访问到的最大下标。

    ``list(features)`` 式全量物化会经迭代协议逐个消费全部 N 个元素（探针
    触到 N-1）；``features[:CAP]`` 先切片只做切片访问，单元素下标永不越帽。
    """

    def __init__(self, features):
        self._features = features
        self.max_index_seen = -1

    def __len__(self):
        return len(self._features)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return self._features[key]
        if key >= len(self._features):
            raise IndexError(key)
        if key > self.max_index_seen:
            self.max_index_seen = key
        return self._features[key]


@pytest.fixture
def governed(monkeypatch):
    def _install(adapter):
        from app.services.data_fabric.manager import DataFabricManager

        monkeypatch.setattr(
            DataFabricManager, "_governed_adapter", classmethod(lambda cls, m: adapter)
        )
        return adapter

    return _install


def _service(max_entry_bytes=4 * 1024 * 1024):
    return ts.CatalogTileService(
        cache=DfTileCache(max_entries=64, max_bytes=64 * 1024 * 1024, max_entry_bytes=max_entry_bytes),
        coalescer=TileBuildCoalescer(),
    )


def test_fallback_encodes_and_caches(governed):
    adapter = governed(_FakeVectorAdapter([_point_feature(0), _point_feature(1)]))
    svc = _service()
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))
    assert result.source == "python_fallback" and result.cache_state == "miss"
    assert result.gz[:2] == b"\x1f\x8b", "回退路径产物必须经确定性 gzip"
    assert result.etag.startswith('"') and result.etag.endswith('"')
    # 第二次命中缓存，且不再触发 adapter 查询
    queries = len(adapter._query_log)
    hit = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))
    assert hit.cache_state == "hit" and hit.source == "cache"
    assert len(adapter._query_log) == queries


def test_fallback_bbox_is_tile_envelope(governed):
    adapter = governed(_FakeVectorAdapter([_point_feature(0)]))
    svc = _service()
    asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 7, 100, 50))
    (_ds_id, spec), = adapter._query_log
    assert spec.bbox == list(tile_bounds_lonlat(7, 100, 50)), "视口切片必须翻译成瓦片经纬度包络"
    assert spec.limit == ts.TILE_FALLBACK_FEATURE_CAP


def test_fallback_empty_features_serves_none_204_semantics(governed):
    governed(_FakeVectorAdapter([]))
    svc = _service()
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 0, 0))
    assert result.gz is None and result.source == "python_fallback"


def test_fallback_caps_oversized_remote_result(governed):
    # 远端忽略 limit 是真实世界常态（#425）：本地必须再截断 —— 有界编码。
    lon, lat = _tile_center(10, 800, 400)
    features = [_point_feature(i, lon=lon + i * 1e-6, lat=lat) for i in range(ts.TILE_FALLBACK_FEATURE_CAP + 500)]
    governed(_FakeVectorAdapter(features))
    svc = _service(max_entry_bytes=1 << 30)
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 10, 800, 400))
    assert result.gz is not None
    # 瓦片内要素被截到帽：解出的 MVT 不可能包含超过帽的要素数（字节有界）。
    assert len(gzip.decompress(result.gz)) < 8 * 1024 * 1024


def test_fallback_truncates_by_slicing_without_full_copy(governed):
    # #425：截断必须「先切片后处理」—— 远端忽略 limit 返回超量结果时，
    # list(...) 全量拷贝把峰值物化放大到 O(远端全量)（(z,x,y) 视口平移风暴
    # 叠加）；探针断言单元素访问永不越过瓦片帽（只有切片被消费）。
    lon, lat = _tile_center(10, 800, 400)
    probe = _IndexAccessProbe(
        [_point_feature(i, lon=lon + i * 1e-6, lat=lat) for i in range(ts.TILE_FALLBACK_FEATURE_CAP + 50)]
    )
    governed(_PassthroughVectorAdapter(probe))
    svc = _service(max_entry_bytes=1 << 30)
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 10, 800, 400))
    assert result.gz is not None, "超量结果仍按帽截断服务（不因切片降级）"
    assert probe.max_index_seen < ts.TILE_FALLBACK_FEATURE_CAP, "截断前不得 O(N) 全量物化远端结果"


def test_fallback_enforces_result_hard_bounds(governed, monkeypatch):
    # 其余 _execute_remote_query 消费方（manager query 路径 / materialization）
    # 都随后 enforce_result_bounds —— 瓦片 fallback 不得例外：截断后仍超硬界
    # （巨几何撑爆字节界）→ typed 降级，绝不无界物化进编码器。
    from app.services.data_fabric import limits as df_limits
    from app.services.data_fabric.errors import ResultTooLargeError

    monkeypatch.setattr(df_limits, "max_response_bytes", lambda: 2048)
    lon, lat = _tile_center(5, 10, 12)
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"i": i, "blob": "x" * 4096},
        }
        for i in range(3)
    ]
    governed(_FakeVectorAdapter(features))
    svc = _service()
    with pytest.raises(ResultTooLargeError):
        asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))


def test_fallback_count_bound_aligned_to_slice_cap_not_global_env(
        governed, monkeypatch):
    # pre-landing review：DATA_FABRIC_MAX_FEATURES 可配到 [1000, 20000) ——
    # 若计数界仍取全局 max_features()，稠密 fallback 瓦片会从「截断服务」
    # 确定性翻成 ResultTooLargeError。计数界已显式对齐切片帽
    # TILE_FALLBACK_FEATURE_CAP：全局 env 压低不得影响 fallback 瓦片服务。
    from app.services.data_fabric import limits as df_limits

    monkeypatch.setattr(df_limits, "max_features", lambda: 3)
    lon, lat = _tile_center(5, 10, 12)
    features = [_point_feature(i, lon=lon + i * 1e-6, lat=lat) for i in range(5)]
    governed(_FakeVectorAdapter(features))
    svc = _service()
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))
    assert result.source == "python_fallback"
    assert result.gz is not None, (
        "全局 max_features 压低不得让 fallback 瓦片翻错（计数界=切片帽，"
        "不受 env 影响）"
    )


def test_capability_driven_server_mvt_path(governed):
    adapter = governed(_ServerMvtAdapter([_point_feature(0)]))
    svc = _service()
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(source_type="postgis"), 5, 10, 12))
    assert result.source == "server_mvt", "具备 serve_mvt_tile 的 adapter 必须走高性能路径"
    assert adapter._query_log and adapter._query_log[0][0] == "mvt"
    assert result.gz[:2] == b"\x1f\x8b"


def test_fingerprint_change_rotates_cache_key(governed):
    governed(_FakeVectorAdapter([_point_feature(0)]))
    svc = _service()
    r1 = asyncio.run(svc.serve_catalog_tile(_item("fp1"), _ds(), 5, 10, 12))
    r2 = asyncio.run(svc.serve_catalog_tile(_item("fp2"), _ds(), 5, 10, 12))
    assert r1.cache_state == "miss" and r2.cache_state == "miss", "数据版本变化必须重建，绝不复用陈旧瓦片"
    assert r1.etag != r2.etag


def test_tenant_isolation_no_cross_scope_sharing(governed):
    adapter = governed(_FakeVectorAdapter([_point_feature(0)]))
    svc = _service()
    a = asyncio.run(svc.serve_catalog_tile(_item(), _ds(owner="a"), 5, 10, 12))
    b = asyncio.run(svc.serve_catalog_tile(_item(), _ds(owner="b"), 5, 10, 12))
    assert a.cache_state == "miss" and b.cache_state == "miss", "租户域入键：不同归属绝不共享条目"
    assert len(adapter._query_log) == 2


def test_oversize_tile_honest_bypass(governed):
    lon, lat = _tile_center(5, 10, 12)
    governed(_FakeVectorAdapter([_point_feature(i, lon=lon, lat=lat) for i in range(200)]))
    svc = _service(max_entry_bytes=64)
    result = asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))
    assert result.cache_state == "bypass", "超单条上限：服务响应但不驻留（诚实降级）"
    assert result.gz is not None


def test_concurrent_same_tile_coalesces_to_one_query(governed):
    adapter = governed(_FakeVectorAdapter([_point_feature(0)], delay=0.03))
    svc = _service()

    async def scenario():
        results = await asyncio.gather(
            *(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12) for _ in range(30))
        )
        states = {r.cache_state for r in results}
        assert "hit" in states or "miss" in states
        assert all(r.gz == results[0].gz for r in results)
        assert len(adapter._query_log) == 1, "并发同键必须收敛为一次构建"

    asyncio.run(scenario())


def test_stats_snapshot_exposes_observability(governed):
    governed(_FakeVectorAdapter([_point_feature(0)]))
    svc = _service()
    asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))
    asyncio.run(svc.serve_catalog_tile(_item(), _ds(), 5, 10, 12))
    s = svc.stats()
    assert s["hits"] >= 1 and s["misses"] >= 1
    assert s["bytes"] > 0 and {"entries", "evictions", "oversize_rejects", "inflight"} <= set(s)


def test_identity_module_etag_is_content_and_version_addressed():
    from app.services.data_fabric.tile_identity import compute_tile_etag

    e1 = compute_tile_etag(b"body", "fp1")
    assert e1 == compute_tile_etag(b"body", "fp1"), "同内容同版本 → 恒同 ETag"
    assert e1 != compute_tile_etag(b"body2", "fp1")
    assert e1 != compute_tile_etag(b"body", "fp2"), "版本参与摘要：fingerprint 变化 ETag 必变"
    assert e1.startswith('"') and e1.endswith('"') and len(e1) == 18


def test_identity_tile_bounds_sane():
    b000 = tile_bounds_lonlat(0, 0, 0)
    assert b000[0] == -180.0 and b000[2] == 180.0
    assert b000[1] == pytest.approx(-85.05112878, abs=1e-6)
    assert b000[3] == pytest.approx(85.05112878, abs=1e-6)
    b = tile_bounds_lonlat(4, 8, 8)
    assert -180 <= b[0] < b[2] <= 180 and -90 < b[1] < b[3] <= 90
