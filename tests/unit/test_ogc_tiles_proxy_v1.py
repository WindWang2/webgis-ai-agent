"""GOAL-02 M2：OGC Tiles 受控瓦片获取/代理 —— fetch_tile 治理面 + 缓存/ETag。

fetch_tile 是代理的远端半边：SSRF 同源模板、display 资格门、zoom 矩阵
校验、单瓦片字节帽、404/401/429 typed 映射。代理服务层验证缓存身份与
内容寻址 ETag。
"""
from types import SimpleNamespace

import pytest

from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter
from app.services.data_fabric.errors import (
    InvalidQueryError,
    QueryUnsupportedError,
    SourceBadResponseError,
    SourceUnreachableError,
)
from app.services.data_fabric.security import DataFabricSecurityError
from app.services.data_fabric.ogc_tiles_proxy import (
    OgcTilesProxyService,
    build_mapspec_source,
)
from app.services.data_fabric.tile_cache import TILE_CACHE as df_tile_cache
from app.services.data_fabric.tile_identity import compute_tile_etag
from tests.data.ogc_service_fixtures import (
    MEDIA_MVT,
    TILE_MVT_BYTES,
    TMS_CRS84_URI,
    VALID_TILE,
)


def _adapter(patched_safe_sessions, server, url) -> OGCTilesAdapter:
    patched_safe_sessions(server)
    return OGCTilesAdapter(
        ConnectionProfile(source_type="ogc_api_tiles", endpoint_url=url, name="proxy_test")
    )


class TestFetchTile:
    def test_happy_path_mvt(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        z, x, y = VALID_TILE
        body, content_type = adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        assert body == TILE_MVT_BYTES
        assert content_type == MEDIA_MVT

    def test_remote_404_is_none(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        # fixture 只存在 VALID_TILE=(2,1,1)
        assert adapter.fetch_tile("admin:WebMercatorQuad", 2, 0, 0) is None

    def test_zoom_outside_declared_range_is_none(self, patched_safe_sessions, ogc_fake_server):
        """z 超出服务声明 TMS 范围（0..5）→ 诚实空瓦片，不发请求。"""
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        adapter.describe("admin:WebMercatorQuad")  # 元数据先就位
        before = len(ogc_fake_server.requested_urls)
        assert adapter.fetch_tile("admin:WebMercatorQuad", 9, 0, 0) is None
        assert len(ogc_fake_server.requested_urls) == before  # 零增量：不发瓦片请求

    def test_matrix_bounds_invalid(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        with pytest.raises(InvalidQueryError):
            adapter.fetch_tile("admin:WebMercatorQuad", 2, 4, 0)  # x >= 2^z

    def test_oversize_tile_rejected(self, patched_safe_sessions, ogc_fake_server):
        """压缩/大瓦片炸弹：Content-Length 预检 + 流式帽 typed 拒绝。"""
        url = ogc_fake_server.add_ogc_tiles(oversize_tile_bytes=True, prefix="/ogc-big")
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        z, x, y = VALID_TILE
        with pytest.raises(SourceBadResponseError):
            adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)

    def test_non_display_tileset_refused_with_reasons(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles(tms_uri=TMS_CRS84_URI, prefix="/ogc-crs84")
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        z, x, y = VALID_TILE
        with pytest.raises(QueryUnsupportedError) as ei:
            adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        assert "tms_not_displayable" in str(ei.value)

    def test_401_maps_to_auth_refusal(self, patched_safe_sessions, ogc_fake_server):
        z, x, y = VALID_TILE
        url = ogc_fake_server.add_ogc_tiles(
            status_overrides={f"/{z}/{y}/{x}": 401}, prefix="/ogc-401"
        )
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        with pytest.raises(SourceUnreachableError) as ei:
            adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        assert "401" in str(ei.value)

    def test_429_maps_to_rate_limited(self, patched_safe_sessions, ogc_fake_server):
        z, x, y = VALID_TILE
        url = ogc_fake_server.add_ogc_tiles(
            status_overrides={f"/{z}/{y}/{x}": 429}, prefix="/ogc-429"
        )
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        with pytest.raises(SourceBadResponseError) as ei:
            adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        assert "429" in str(ei.value)

    def test_redirect_to_loopback_blocked_by_ssrf(self, patched_safe_sessions, ogc_fake_server):
        """302 → 127.0.0.1：FakeFabricAdapter 逐跳 SSRF 校验必须拦下。"""
        z, x, y = VALID_TILE
        url = ogc_fake_server.add_ogc_tiles(
            redirect_hops=["http://127.0.0.1:8080/evil"], prefix="/ogc-redir"
        )
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        with pytest.raises(
            (DataFabricSecurityError, SourceBadResponseError, SourceUnreachableError)
        ):
            adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)

    def test_describe_cache_bounds_metadata_calls(self, patched_safe_sessions, ogc_fake_server):
        """60s describe 缓存：连续瓦片获取不重复拉 tileset/TMS 元数据。"""
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        z, x, y = VALID_TILE
        adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        meta_calls = len(
            [u for u in ogc_fake_server.requested_urls if "tileMatrixSets" in u or u.endswith("WebMercatorQuad")]
        )
        adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        meta_calls_2 = len(
            [u for u in ogc_fake_server.requested_urls if "tileMatrixSets" in u or u.endswith("WebMercatorQuad")]
        )
        assert meta_calls_2 == meta_calls  # 元数据调用零增量


class TestProxyService:
    def test_cache_miss_then_hit_with_etag(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)

        proxy = OgcTilesProxyService()
        item = SimpleNamespace(id="admin:WebMercatorQuad", fingerprint="fp-ogc-1")
        ds = SimpleNamespace(org_id=None, owner_id=None, source_type="ogc_api_tiles")
        # 治理解析 seam：直接给出受治理 adapter（route 测试走 manager）。
        proxy._resolve_governed_adapter = lambda ds_model: adapter

        z, x, y = VALID_TILE
        import asyncio

        r1 = asyncio.run(proxy.serve_tile(item, ds, z, x, y))
        assert r1.body == TILE_MVT_BYTES
        assert r1.cache_state == "miss"
        assert r1.etag == compute_tile_etag(TILE_MVT_BYTES, "fp-ogc-1")
        r2 = asyncio.run(proxy.serve_tile(item, ds, z, x, y))
        assert r2.cache_state == "hit"
        assert r2.etag == r1.etag
        df_tile_cache.invalidate_item("ogc-tiles:admin:WebMercatorQuad")

    def test_fingerprint_switches_cache_identity(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        proxy = OgcTilesProxyService()
        proxy._resolve_governed_adapter = lambda ds_model: adapter
        ds = SimpleNamespace(org_id=None, owner_id=None)
        z, x, y = VALID_TILE
        import asyncio

        r1 = asyncio.run(proxy.serve_tile(SimpleNamespace(id="admin:WebMercatorQuad", fingerprint="fp-a"), ds, z, x, y))
        r2 = asyncio.run(proxy.serve_tile(SimpleNamespace(id="admin:WebMercatorQuad", fingerprint="fp-b"), ds, z, x, y))
        assert r2.cache_state == "miss"  # 指纹变化 → 新键
        assert r1.etag != r2.etag
        df_tile_cache.invalidate_item("ogc-tiles:admin:WebMercatorQuad")


class TestBuildMapspecSource:
    def test_vector_tileset(self, patched_safe_sessions, ogc_fake_server):
        url = ogc_fake_server.add_ogc_tiles()
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        desc = adapter.describe("admin:WebMercatorQuad")
        src = build_mapspec_source(
            desc.metadata, item_id="admin:WebMercatorQuad", session_id="sess-12345678"
        )
        assert src["type"] == "vector"
        assert src["minzoom"] == 0 and src["maxzoom"] == 5
        tpl = src["tiles"][0]
        assert "{z}/{x}/{y}" in tpl
        assert "session_id=sess-12345678" in tpl
        # item_id 段级编码（: → %3A；FastAPI 路由自动解码，与 fixture 一致）
        assert tpl.startswith("/api/v1/data-fabric/catalog/admin%3AWebMercatorQuad/ogc-tiles/")

    def test_raster_tileset_uses_fabric_url(self, patched_safe_sessions, ogc_fake_server):
        from tests.data.ogc_service_fixtures import MEDIA_PNG

        url = ogc_fake_server.add_ogc_tiles(media_type=MEDIA_PNG)
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        desc = adapter.describe("admin:WebMercatorQuad")
        src = build_mapspec_source(desc.metadata, item_id="admin:WebMercatorQuad", session_id="sess-12345678")
        assert src["type"] == "data_fabric"
        assert src["catalog_item_id"] == "admin:WebMercatorQuad"
        assert "{z}/{x}/{y}" in src["url"]

    def test_not_display_ready_refused(self, patched_safe_sessions, ogc_fake_server):
        from app.services.data_fabric.errors import UnsupportedSourceError

        url = ogc_fake_server.add_ogc_tiles(tms_uri=TMS_CRS84_URI, prefix="/ogc-nodisp")
        adapter = _adapter(patched_safe_sessions, ogc_fake_server, url)
        desc = adapter.describe("admin:WebMercatorQuad")
        with pytest.raises(UnsupportedSourceError) as ei:
            build_mapspec_source(desc.metadata, item_id="x", session_id="sess-12345678")
        assert "tms_not_displayable" in str(ei.value)
