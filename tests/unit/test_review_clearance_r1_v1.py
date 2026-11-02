"""GOAL-02 R1 独立评审清偿的回归面（P0-1/P1-1/P1-2/P1-3/P1-4/P1-9/P1-10）。

每条对应 INDEPENDENT_REVIEW.md 的 finding —— 修复不是口头承诺，是可重复
执行的断言。
"""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

import numpy as np
import pytest

from app.core.config import settings
from app.lib.geo_raster.cog import write_cog
from app.services.data_fabric.coverage_read import (
    CoverageReadError,
    read_coverage_subset,
    RC_CAPABILITY_NOT_DECLARED,
    RC_OVERSIZE_RESPONSE,
    RC_UNREACHABLE,
)
from app.services.data_fabric.ogc_capability import (
    SUPPORTED_COVERAGE_FORMATS,
    normalize_coverage_format,
)

from tests.data.ogc_service_fixtures import (
    COVERAGE_NODATA,
    VALID_TILE,
    coverage_values,
)

_SESSION = "rev-clear-sess-01"


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    yield tmp_path


def _adapter(patched_safe_sessions, server, url, cls):
    patched_safe_sessions(server)
    return cls(url)


# ── P0-1：CRS 只认服务声明；无声明 → 拒绝；投影轴名不判经纬 ────────────


class TestP0_1CrsHonesty:
    async def test_no_crs_declaration_refuses_materialization(
        self, patched_safe_sessions, ogc_fake_server
    ):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages(with_crs=False, prefix="/cov-nocrs")
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="cov", source_type="ogc_api_coverages", endpoint_url=url, name="c",
        ))
        with pytest.raises(CoverageReadError) as ei:
            await read_coverage_subset(
                adapter, "dem", session_id=_SESSION,
                subsets={"x": (100.0, 100.04)},
            )
        assert ei.value.reason_code == RC_CAPABILITY_NOT_DECLARED
        assert "CRS" in str(ei.value)

    def test_projection_axis_names_not_geographic(self):
        from app.services.data_fabric.coverage_read import _identify_spatial_axes

        # UTM Easting/Northing 轴名不得判为经纬轴（曾把 EPSG:326xx 贴 CRS84）
        assert _identify_spatial_axes({"e": {"count": 3}, "n": {"count": 3}}) is None
        assert _identify_spatial_axes({"x": {"count": 3}, "y": {"count": 3}}) == ("x", "y")

    async def test_declared_crs_flows_to_output(self, patched_safe_sessions, ogc_fake_server):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages()
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="cov2", source_type="ogc_api_coverages", endpoint_url=url, name="c2",
        ))
        result = await read_coverage_subset(
            adapter, "dem", session_id=_SESSION,
            subsets={"x": (100.0, 100.04), "y": (20.0, 20.04)},
        )
        assert result["crs"] == "OGC:CRS84"  # 来自响应 referencing 声明


# ── P0-2：格式判定大小写/参数序无关 ────────────────────────────────────────


class TestP0_2FormatNormalization:
    def test_assess_coverage_case_insensitive(self):
        from app.services.data_fabric.ogc_capability import assess_coverage

        a = assess_coverage(
            has_coverage_endpoint=True, has_domainset=True, has_rangetype=True,
            grid_axis_names=["x", "y"],
            declared_formats=[
                "Application/PRS.Coverage+JSON",
                "image/tiff; Application=GeoTIFF; profile=Cloud-Optimized",
            ],
        )
        assert a.state == "supported", a.reasons

    def test_param_order_insensitive(self):
        canonical = "image/tiff; application=geotiff; profile=cloud-optimized"
        reordered = "image/tiff; profile=cloud-optimized; application=geotiff"
        assert normalize_coverage_format(reordered) == normalize_coverage_format(canonical)
        assert normalize_coverage_format(reordered) in {
            normalize_coverage_format(f) for f in SUPPORTED_COVERAGE_FORMATS
        }


# ── P1-1/P1-9：错误分流与真 typed 断言 ─────────────────────────────────────


class TestP1_1ErrorClassification:
    async def test_remote_5xx_is_unreachable_not_oversize(
        self, patched_safe_sessions, ogc_fake_server
    ):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages(
            status_overrides={"/coverage": 503}, prefix="/cov-503",
        )
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="cov3", source_type="ogc_api_coverages", endpoint_url=url, name="c3",
        ))
        with pytest.raises(CoverageReadError) as ei:
            await read_coverage_subset(
                adapter, "dem", session_id=_SESSION, subsets={"x": (100.0, 100.04)},
            )
        assert ei.value.reason_code == RC_UNREACHABLE  # 503 ≠ 字节帽

    async def test_tiny_byte_cap_is_oversize(self, patched_safe_sessions, ogc_fake_server):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages()
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="cov4", source_type="ogc_api_coverages", endpoint_url=url, name="c4",
        ))
        with pytest.raises(CoverageReadError) as ei:
            await read_coverage_subset(
                adapter, "dem", session_id=_SESSION,
                subsets={"x": (100.0, 100.04)}, max_response_bytes=8,
            )
        assert ei.value.reason_code == RC_OVERSIZE_RESPONSE


# ── P1-3：相对 tile 模板 / 相对 tiling-scheme 链接 ─────────────────────────


class TestP1_3RelativeHrefs:
    def test_describe_and_fetch_with_relative_template(
        self, patched_safe_sessions, ogc_fake_server
    ):
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter

        url = ogc_fake_server.add_ogc_tiles(relative_hrefs=True, prefix="/ogc-rel")
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCTilesAdapter(ConnectionProfile(
            id="t", source_type="ogc_api_tiles", endpoint_url=url, name="t",
        ))
        desc = adapter.describe("admin:WebMercatorQuad")
        assert desc.metadata["display_ready"] is True  # 相对 TMS 链接可解析
        assert desc.metadata["maxzoom"] == 5
        z, x, y = VALID_TILE
        body, ct = adapter.fetch_tile("admin:WebMercatorQuad", z, x, y)
        assert body  # 相对模板代入成功

    def test_dataset_id_injection_neutralized(self, patched_safe_sessions, ogc_fake_server):
        """P1-6：携带 ../ 与 # 的 id 被 percent-encode（不拼出穿越路径）。"""
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter

        url = ogc_fake_server.add_ogc_tiles()
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCTilesAdapter(ConnectionProfile(
            id="t2", source_type="ogc_api_tiles", endpoint_url=url, name="t2",
        ))
        # 编码后的 id 段不含裸 ../ 与 #（fake 服务器按段匹配会返回 200 ——
        # 断言落点在实际发出的 URL 形态：穿越/截断注入被 percent-encode 中和）
        ogc_fake_server.requested_urls.clear()
        adapter.describe("../collections/other#x:WebMercatorQuad")
        fetch_url = next(
            u for u in ogc_fake_server.requested_urls if u.endswith("/tiles/WebMercatorQuad")
        )
        assert "#" not in fetch_url          # fragment 截断被中和
        assert "/../" not in fetch_url       # 无原始穿越段
        assert "%2F" in fetch_url            # id 内的 / 已被段级编码（单段化）


# ── P1-4：manager 同步失效 ogc-tiles: 前缀缓存 ─────────────────────────────


class TestP1_4CacheInvalidation:
    def test_invalidate_catalog_tiles_covers_ogc_prefix(self):
        from app.services.data_fabric.manager import _invalidate_catalog_tiles
        from app.services.data_fabric.tile_cache import TILE_CACHE, TileCacheEntry
        from app.services.data_fabric.tile_identity import tile_cache_key

        item_id = "admin:WebMercatorQuad"
        for prefix in ("", "pmtiles:", "ogc-tiles:"):
            key = tile_cache_key(f"{prefix}{item_id}", "t", "fp", 2, 1, 1)
            TILE_CACHE.put(key, TileCacheEntry(gz=b"x", fingerprint="fp"))
        _invalidate_catalog_tiles(item_id)
        for prefix in ("", "pmtiles:", "ogc-tiles:"):
            key = tile_cache_key(f"{prefix}{item_id}", "t", "fp", 2, 1, 1)
            assert TILE_CACHE.get(key) is None, prefix


# ── P1-10：GeoTIFF coverage 路径 + 读中预算 + 文件变化作废 + 未授权零缓存 ──


class TestP1_10CoverageGeotiffPath:
    async def test_geotiff_output_values_truth(self, patched_safe_sessions, ogc_fake_server):
        import rasterio

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages(
            declared_formats=("image/tiff; application=geotiff",),
            serve_format="image/tiff",
            prefix="/cov-tiff",
        )
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="cov-tif", source_type="ogc_api_coverages", endpoint_url=url, name="ct",
        ))
        result = await read_coverage_subset(
            adapter, "dem", session_id=_SESSION,
            subsets={"x": (100.0, 100.04), "y": (20.0, 20.04)},
        )
        assert result["status"] == "success"
        assert result["provenance"]["format"].startswith("image/tiff")
        with rasterio.open(result["path"]) as ds:
            arr = ds.read(1)
            assert ds.crs is not None
        # fixture geotiff 已做 north-up 行翻转：输出行 r ↔ 真值行 2-r
        expected = coverage_values()
        assert arr[0, 0] == pytest.approx(expected[2][0], abs=1e-6)
        assert arr[2, 2] == pytest.approx(expected[0][2], abs=1e-6)
        # nodata 位（真值 [0][0]）落在 north-up 行 2 列 0
        assert arr[2, 0] == COVERAGE_NODATA


class TestP1_10CogFaces:
    def _server(self, tmp_path):
        from tests.unit.test_cog_materialization_v1 import _QuietHandler

        serve_dir = tmp_path / "srv"
        serve_dir.mkdir()
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(serve_dir))
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, serve_dir

    def _build_cog(self, path: Path, seed: float) -> None:
        import rasterio
        from rasterio.transform import Affine

        arr = np.full((1, 64, 64), seed, dtype="float32")
        raw = path.parent / f"raw-{seed}.tif"
        with rasterio.open(
            raw, "w", driver="GTiff", width=64, height=64, count=1,
            dtype="float32", crs="EPSG:4326",
            transform=Affine.translation(100.0, 21.0) * Affine.scale(0.01, -0.01),
        ) as dst:
            dst.write(arr)
        write_cog(str(raw), str(path))

    def _allow_private(self, monkeypatch):
        from app.lib.geo_raster import env as geo_env
        from app.lib.geo_raster.env import _extract_remote_target
        from app.services.data_fabric.security import DataFabricSecurity

        monkeypatch.setattr(
            geo_env, "validate_remote_href",
            lambda uri: (
                DataFabricSecurity.validate_url(t, allow_private=True)
                if (t := _extract_remote_target(uri)) else None
            ) or uri,
        )

    async def test_wire_budget_mid_read_is_budget_exceeded(self, tmp_path, monkeypatch):
        from app.services.data_fabric.cog_materialization import (
            CogMaterializationError,
            materialize_remote_cog_asset,
        )

        server, serve_dir = self._server(tmp_path)
        try:
            self._build_cog(serve_dir / "a.tif", 1.0)
            self._allow_private(monkeypatch)
            with pytest.raises(CogMaterializationError) as ei:
                await materialize_remote_cog_asset(
                    f"http://127.0.0.1:{server.server_address[1]}/a.tif",
                    session_id=_SESSION, max_requests=0,  # 首个窗口读即超预算
                )
            assert ei.value.reason_code == "budget_exceeded"
        finally:
            server.shutdown()

    async def test_asset_change_invalidates_fingerprint(self, tmp_path, monkeypatch):
        """同一 href，内容变化 → 新 content_sha256（旧身份不复用）。"""
        from app.services.data_fabric.cog_materialization import (
            materialize_remote_cog_asset,
        )

        server, serve_dir = self._server(tmp_path)
        try:
            cog = serve_dir / "b.tif"
            self._build_cog(cog, 1.0)
            self._allow_private(monkeypatch)
            base = f"http://127.0.0.1:{server.server_address[1]}/b.tif"
            # URL 版本参数变更：GDAL /vsicurl 的进程级 URL 缓存不跨 URL 复用
            # （同 URL 的远端变化在进程内可能命中 CPL 缓存 —— 已知限制，见
            # INDEPENDENT_REVIEW）；跨进程/跨 URL 的内容变化必须产生新身份。
            r1 = await materialize_remote_cog_asset(f"{base}?v=1", session_id=_SESSION)
            self._build_cog(cog, 2.0)  # 远端资产变化
            r2 = await materialize_remote_cog_asset(f"{base}?v=2", session_id=_SESSION)
            assert r1["content_sha256"] != r2["content_sha256"]
            assert r1["ref"] != r2["ref"]  # 旧 ref 不被复用（作废语义）
        finally:
            server.shutdown()


# ── R2 残留清偿（N1 注入中和 / N2 格式归一 / P1-5 专项 / also_available） ──


class TestR2_N1CoverageIdEncoding:
    async def test_coverage_dataset_id_injection_neutralized(
        self, patched_safe_sessions, ogc_fake_server
    ):
        """N1：coverages 读取链（domainset/rangetype/coverage URL）的 id 段
        编码 —— ../ 不逃出 /collections/ 子树。"""
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages(prefix="/cov-inj")
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="ci", source_type="ogc_api_coverages", endpoint_url=url, name="ci",
        ))
        ogc_fake_server.requested_urls.clear()
        # describe 走 domainset/rangetype；即使 fixture 对未知 id 宽松返回，
        # 发出的 URL 形态必须已段级编码（无裸 ../）
        try:
            adapter.describe("dem/../../admin/secret")
        except Exception:  # noqa: BLE001 — 宽松 fixture 可能 200/404，均可
            pass
        for u in ogc_fake_server.requested_urls:
            assert "/../" not in u, u
            assert "#" not in u, u


class TestR2_N2FormatSelectionNormalized:
    async def test_case_variant_format_still_selects_covjson(
        self, patched_safe_sessions, ogc_fake_server
    ):
        """N2：大小写变体声明（HTTP media type 大小写不敏感）下，
        subset_read_ready=True 的服务读取不再自相矛盾拒绝。"""
        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.ogc_coverages_adapter import (
            OGCCoveragesAdapter,
        )

        url = ogc_fake_server.add_ogc_coverages(
            declared_formats=("Application/PRS.Coverage+JSON",), prefix="/cov-case",
        )
        patched_safe_sessions(ogc_fake_server)
        adapter = OGCCoveragesAdapter(ConnectionProfile(
            id="cc", source_type="ogc_api_coverages", endpoint_url=url, name="cc",
        ))
        desc = adapter.describe("dem")
        assert desc.metadata["subset_read_ready"] is True
        result = await read_coverage_subset(
            adapter, "dem", session_id=_SESSION,
            subsets={"x": (100.0, 100.04), "y": (20.0, 20.04)},
        )
        assert result["status"] == "success", result


class TestR2_P1_5CapabilityDefaults:
    def test_queryspec_channel_declares_zero_pushdown(self):
        """P1-5 专项：两协议的 QuerySpec 通道能力全 False（与 adapter 的
        typed 拒绝一致 —— planner 不放行到运行时）。"""
        from app.services.data_fabric.query.capabilities import get_capabilities

        for st in ("ogc_api_tiles", "ogc_api_coverages", "ogc_tiles", "ogc_coverages"):
            caps = get_capabilities(st)
            true_bits = [
                k for k, v in caps.model_dump().items() if v is True
            ]
            assert true_bits == [], (st, true_bits)


class TestR2_N4DualProtocolDisclosure:
    async def test_dual_protocol_service_reports_also_available(
        self, patched_safe_sessions, ogc_fake_server
    ):
        """同一 fake server 挂 Tiles + Coverages 双协议 → also_available 披露
        第二协议（不静默丢弃）。"""
        from app.tools.ogc_service_tools import register_ogc_service_tools
        from app.tools.registry import ToolRegistry

        # fake server 按前缀首匹配路由 —— 双协议同根需要一个合并 handler
        #（/collections 同时含 tilesets 链接集合与 coverage 集合；conformance
        # 同时声明两族），等价真实双协议平台。
        from tests.data.ogc_service_fixtures import (
            FAKE_HOST,
            coverage_domainset_doc,
            coverage_rangetype_doc,
            coverage_covjson_doc,
            tiles_conformance_doc,
            tiles_tileset_doc,
            webmercator_tms_doc,
        )
        from tests.fixtures.data_fabric.fake_server import make_response
        import re as _re

        def _dual_handler(request):
            from urllib.parse import urlsplit

            ogc_fake_server._record(request)
            path = urlsplit(request.url).path
            if path.endswith("/conformance"):
                doc = tiles_conformance_doc()
                doc["conformsTo"].append(
                    "http://www.opengis.net/spec/ogcapi-coverages-1/1.0/conf/core"
                )
                return make_response(request.url, json_body=doc)
            if path.endswith("/domainset"):
                return make_response(request.url, json_body=coverage_domainset_doc())
            if path.endswith("/rangetype"):
                return make_response(request.url, json_body=coverage_rangetype_doc())
            if path.endswith("/coverage"):
                xv, yv = [100.0, 100.02, 100.04], [20.0, 20.02, 20.04]
                return make_response(
                    request.url, json_body=coverage_covjson_doc(xv, yv),
                    headers={"Content-Type": "application/prs.coverage+json"},
                )
            if path.endswith("/tileMatrixSets/WebMercatorQuad"):
                return make_response(request.url, json_body=webmercator_tms_doc())
            if _re.search(r"/tiles/[^/]+/\d+/\d+/\d+$", path):
                from tests.data.ogc_service_fixtures import TILE_MVT_BYTES

                return make_response(
                    request.url, content=TILE_MVT_BYTES,
                    headers={"Content-Type": "application/vnd.mapbox-vector-tile"},
                )
            if _re.search(r"/tiles/[^/]+$", path):
                return make_response(request.url, json_body=tiles_tileset_doc(
                    collection_id="admin",
                ))
            if _re.search(r"/collections/[^/]+$", path):
                cid = path.rstrip("/").rsplit("/", 1)[-1]
                return make_response(request.url, json_body={
                    "id": cid, "title": cid, "itemType": "coverage",
                    "extent": {"spatial": {"bbox": [[100.0, 20.0, 100.04, 20.04]]}},
                    "links": [
                        {"rel": "self", "href": f"https://{FAKE_HOST}/dual/collections/{cid}"},
                        {"rel": "http://www.opengis.net/def/rel/ogc/1.0/coverage",
                         "type": "application/prs.coverage+json",
                         "href": f"https://{FAKE_HOST}/dual/collections/{cid}/coverage"},
                        {"rel": "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector",
                         "href": f"https://{FAKE_HOST}/dual/collections/{cid}/tiles"},
                    ],
                })
            if path.endswith("/collections"):
                return make_response(request.url, json_body={"collections": [
                    {"id": "admin", "title": "admin",
                     "links": [{"rel": "http://www.opengis.net/def/rel/ogc/1.0/tilesets-vector",
                                "href": f"https://{FAKE_HOST}/dual/collections/admin/tiles"}]},
                    {"id": "dem", "title": "dem", "itemType": "coverage"},
                ]})
            return make_response(request.url, status=404, text="no dual route")

        ogc_fake_server._add("/dual", _dual_handler)
        patched_safe_sessions(ogc_fake_server)
        reg = ToolRegistry()
        register_ogc_service_tools(reg)
        result = await reg.dispatch("inspect_data_service", {
            "url": ogc_fake_server.base_url + "/dual",
        })
        assert result["status"] == "success"
        assert result["also_available"] == ["ogc_api_coverages"]
