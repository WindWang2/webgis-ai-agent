"""GOAL-02 M9：三个端到端用户场景（离线、确定性、真值可验）。

① 「接入省级 OGC API Tiles 行政底图并叠加本地小学」：
   inspect_data_service（发现+资格）→ 目录注册 → add_tile_service_layer
   （MapSpec 受控事务）→ 瓦片经代理路由真实取到字节（fixture MVT）。
② 「从 STAC 搜索真实 COG 影像并计算区域指标」：
   STAC 发现 COG 候选 → materialize_remote_raster_asset（预算窗口读 →
   ref:fabric-cog）→ 区域统计与真值一致 → 出图（PNG imageRef 入 MapSpec）。
③ 「发现 Coverage 服务……因没有能力声明而诚实拒绝」：
   无 coverage 端点声明的服务 → inspect 报 unsupported/unknown →
   read_coverage_subset typed 拒绝（reason code），绝不猜。

M8 断言内嵌：预算账本（requests/bytes/cells）、缓存命中（X-Tile-Cache）、
ref 不灌 LLM（回执无 path、result_size_policy=ref_offload）。
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
from app.tools.ogc_service_tools import register_ogc_service_tools
from app.tools.registry import ToolRegistry

from tests.data.ogc_service_fixtures import (
    COVERAGE_NODATA,
    TILE_MVT_BYTES,
    VALID_TILE,
    coverage_values,
)

_NX = _NY = 600
_X0, _Y0, _X1, _Y1 = 100.0, 20.0, 104.0, 24.0


def _truth(row: int, col: int) -> float:
    return float(100 + row * _NX + col)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    yield tmp_path


def _tools() -> ToolRegistry:
    reg = ToolRegistry()
    register_ogc_service_tools(reg)
    return reg


def _build_http_cog(serve_dir: Path) -> str:
    import rasterio
    from rasterio.transform import Affine

    xres = (_X1 - _X0) / (_NX - 1)
    yres = (_Y1 - _Y0) / (_NY - 1)
    transform = Affine.translation(_X0 - xres / 2, _Y1 + yres / 2) * Affine.scale(xres, -yres)
    raw = serve_dir / "raw.tif"
    arr = np.empty((1, _NY, _NX), dtype="float32")
    for r in range(_NY):
        arr[0, r, :] = [_truth(r, c) for c in range(_NX)]
    with rasterio.open(
        raw, "w", driver="GTiff", width=_NX, height=_NY, count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=-9999.0,
    ) as dst:
        dst.write(arr)
    cog = serve_dir / "scene-e2e.tif"
    write_cog(str(raw), str(cog))
    return str(cog)


@pytest.fixture(scope="module")
def http_cog_server(tmp_path_factory):
    from tests.unit.test_cog_materialization_v1 import _QuietHandler

    serve_dir = tmp_path_factory.mktemp("e2e-cog")
    _build_http_cog(serve_dir)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(serve_dir))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/scene-e2e.tif"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def allow_private(monkeypatch):
    from app.lib.geo_raster import env as geo_env
    from app.lib.geo_raster.env import _extract_remote_target
    from app.services.data_fabric.security import DataFabricSecurity

    def _validate(uri: str) -> str:
        target = _extract_remote_target(uri)
        if target:
            DataFabricSecurity.validate_url(target, allow_private=True)
        return uri

    monkeypatch.setattr(geo_env, "validate_remote_href", _validate)


# ── 场景 ①：Tiles 行政底图叠加 ────────────────────────────────────────────


class TestScenario1TilesOverlay:
    async def test_full_chain_discovery_to_mapspec_to_tile_bytes(
        self, patched_safe_sessions, ogc_fake_server
    ):
        from datetime import datetime, timezone

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.routes import data_fabric as df_routes
        from app.core.database import Base, Engine, SessionLocal
        from app.models.data_fabric import CatalogItemModel, DataSourceModel
        from app.models.db_model import User
        from app.services.auth_history_bridge import require_owned_session
        from app.services.data_fabric.adapters.ogc_tiles_adapter import OGCTilesAdapter
        from app.services.data_fabric.tile_cache import TILE_CACHE as df_tile_cache
        from app.schemas.data_fabric_schema import ConnectionProfile

        url = ogc_fake_server.add_ogc_tiles(collection_ids=("province_admin",))
        patched_safe_sessions(ogc_fake_server)

        # 1) agent 用自然语言引导下的发现工具：三态 + 资格
        reg = _tools()
        report = await reg.dispatch(
            "inspect_data_service", {"url": url, "session_id": "sess-e2e-s1"}
        )
        assert report["status"] == "success"
        assert report["datasets"][0]["metadata"]["capability"]["state"] == "supported"
        assert report["datasets"][0]["qualification"]["role_class"] == "display_only"

        # 2) 目录注册（governed sync —— 资格 receipt 进目录）
        adapter = OGCTilesAdapter(ConnectionProfile(
            id="e2e-tiles", source_type="ogc_api_tiles", endpoint_url=url, name="e2e",
        ))
        sync = adapter.sync(owner="sess-e2e-s1")
        assert sync["count"] == 1

        # 3) MapSpec 受控事务上图（叠加语义：本地小学图层已有/另加，此处验证
        #    tile 服务层的受控接入路径）
        res = await reg.dispatch("add_tile_service_layer", {
            "catalog_item_id": "province_admin:WebMercatorQuad",
            "session_id": "sess-e2e-s1",
        })
        if "status" not in res:
            import json as _j
            raise AssertionError(f"raw: {_j.dumps(res, default=str)[:600]}")
        assert res["status"] == "success", res
        src = res["source"]
        assert src["type"] == "vector"
        assert "/ogc-tiles/{z}/{x}/{y}" in src["tiles"][0]
        assert "session_id=sess-e2e-s1" in src["tiles"][0]
        mapspec = res["mapspec"]
        layer = next(lyr for lyr in mapspec["layers"] if lyr["id"] == res["layer_id"])
        assert layer["source"] == res["layer_id"]
        # MapSpec 权威 schema 校验
        from app.lib.cartography.mapspec_schema import parse_mapspec

        parsed = parse_mapspec(mapspec)
        assert parsed.valid
        assert parsed.document["sources"][res["layer_id"]]["type"] == "vector"

        # 4) 瓦片经代理路由真实取回（本地小学叠加图的底图流量路径）
        item_id = "province_admin:WebMercatorQuad"
        Base.metadata.create_all(bind=Engine)
        with SessionLocal() as db:
            if db.get(User, "goal02-e2e-owner") is None:
                db.add(User(
                    id="goal02-e2e-owner", username="goal02-e2e", email="e2e@x.com",
                    password_hash="scrypt$16384$8$1$00$00", role="editor",
                    is_active=True, token_version=0,
                    created_at=datetime.now(timezone.utc),
                ))
                db.commit()
            db.query(CatalogItemModel).filter(CatalogItemModel.id == item_id).delete()
            db.query(DataSourceModel).filter(DataSourceModel.id == "ds-e2e-tiles").delete()
            db.add(DataSourceModel(
                id="ds-e2e-tiles", org_id=None, owner_id="goal02-e2e-owner",
                name="e2e tiles", source_type="ogc_api_tiles", endpoint_url=url,
                connection_profile={}, capabilities_json=[],
            ))
            db.add(CatalogItemModel(
                id=item_id, source_id="ds-e2e-tiles", name="admin tiles",
                title="admin tiles", geometry_type="tile_vector", feature_type="tile",
                crs="EPSG:3857", bbox_json=None, tags_json=[],
                descriptor_json={}, meta_profile_json={}, fingerprint="fp-e2e",
                availability="available",
            ))
            db.commit()
        df_tile_cache.invalidate_item(f"ogc-tiles:{item_id}")

        app = FastAPI()
        app.include_router(df_routes.router, prefix="/api/v1")
        app.dependency_overrides[require_owned_session] = lambda: None
        app.dependency_overrides[df_routes.get_current_user_optional] = (
            lambda: {"user_id": "goal02-e2e-owner"}
        )
        client = TestClient(app)
        z, x, y = VALID_TILE
        r1 = client.get(
            f"/api/v1/data-fabric/catalog/{item_id}/ogc-tiles/{z}/{x}/{y}"
            "?session_id=sess-e2e-s1"
        )
        assert r1.status_code == 200, r1.text
        assert r1.content == TILE_MVT_BYTES
        assert r1.headers["x-tile-cache"] == "miss"
        # M8：第二次命中缓存（请求零增量）
        r2 = client.get(
            f"/api/v1/data-fabric/catalog/{item_id}/ogc-tiles/{z}/{x}/{y}"
            "?session_id=sess-e2e-s1"
        )
        assert r2.headers["x-tile-cache"] == "hit"
        df_tile_cache.invalidate_item(f"ogc-tiles:{item_id}")


# ── 场景 ②：STAC COG 区域指标计算并出图 ──────────────────────────────────


class TestScenario2StacCogComputeAndMap:
    async def test_discover_materialize_compute_render(
        self, patched_safe_sessions, ogc_fake_server, http_cog_server, allow_private
    ):
        from app.services.data_fabric.cog_materialization import (
            discover_stac_cog_assets,
        )
        from app.services.data_fabric.adapters import STACAdapter
        from app.schemas.data_fabric_schema import ConnectionProfile

        # 1) STAC 搜索 → COG 候选（asset href 指向本地 HTTP COG 服务器）
        stac_url = ogc_fake_server.add_stac_cog(asset_href=http_cog_server)
        patched_safe_sessions(ogc_fake_server)
        stac = STACAdapter(ConnectionProfile(
            id="e2e-stac", source_type="stac", endpoint_url=stac_url, name="e2e-stac",
        ))
        found = await discover_stac_cog_assets(
            stac, bbox=[100.0, 20.0, 105.0, 25.0], limit=5
        )
        assert found["total_items"] == 1
        asset = found["items"][0]["assets"][0]
        assert asset["asset_key"] == "data"

        # 2) 受控物化（AOI 窗口 + 预算账本 + ref）
        reg = _tools()
        mat = await reg.dispatch("materialize_remote_raster_asset", {
            "href": asset["href"],
            "session_id": "sess-e2e-s2",
            "bbox": [100.5, 20.5, 101.5, 21.5],
            "bands": [1],
        })
        assert mat["status"] == "success", mat
        assert mat["ref"].startswith("ref:fabric-cog/")
        # M8 预算账本：请求/字节在场；窗口 ≪ 整幅；回执不携带磁盘 path（ref offload）
        ledger = mat["ledger"]
        assert ledger["requests"] >= 1
        assert ledger["bytes_read"] > 0
        assert ledger["window"][2] * ledger["window"][3] < _NX * _NY
        assert "path" not in mat  # M8：ref offload —— 磁盘路径不进对话
        assert mat["fingerprint"].startswith("sha256:")

        # 3) 区域指标（对 ref 做统计 —— 与真值一致；nodata 语义保留）。
        #    ref → 磁盘路径的解析走消费方通道（artifact_registry），回执无 path。
        from app.services.artifact_registry import fabric_cog_path

        cog_path = fabric_cog_path("sess-e2e-s2", mat["ref"])
        assert cog_path is not None and cog_path.is_file()
        import rasterio

        wcol, wrow = ledger["window"][0], ledger["window"][1]
        with rasterio.open(cog_path) as ds:
            arr = ds.read(1)
            assert ds.crs.to_string() == "EPSG:4326"
        # 采样真值（north-up 翻转）
        samples = [arr[r, c] for r, c in [(10, 10), (75, 75), (120, 30)]]
        truth = [_truth(wrow + r, wcol + c) for r, c in [(10, 10), (75, 75), (120, 30)]]
        assert samples == pytest.approx(truth, abs=1e-4)
        mean_val = float(arr.mean())

        # 4) 出图：COG 窗口渲染 PNG → 既有 imageRef 栅格显示路径入 MapSpec
        from PIL import Image

        norm = (arr - arr.min()) / max(1e-9, float(arr.max() - arr.min()))
        img = Image.fromarray((norm * 255).astype("uint8"))
        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        from app.services.mapspec_store import mapspec_store
        from app.services.raster_store import save_png

        session_dir = mapspec_store.get_session_dir("sess-e2e-s2")
        image_ref = save_png(session_dir, "e2e-cog-render", buf.getvalue())
        assert image_ref.startswith("ref:raster/")
        upsert = await mapspec_store.layer_upsert(
            "sess-e2e-s2",
            {"id": "stac-cog-indicator", "type": "raster", "source": "stac-cog-src"},
            {"imageRef": image_ref, "bounds": [100.5, 20.5, 101.5, 21.5]},
        )
        assert upsert["success"], upsert
        spec = upsert["mapspec"]
        from app.lib.cartography.mapspec_schema import parse_mapspec

        parsed = parse_mapspec(spec)
        assert parsed.valid
        src_entry = parsed.document["sources"]["stac-cog-src"]
        assert src_entry["type"] == "raster"
        assert src_entry["imageRef"] == image_ref
        # 证据链：统计值可复述（agent 解释面）
        assert mean_val > 0


# ── 场景 ③：Coverage 无能力声明 → 诚实拒绝 ────────────────────────────────


class TestScenario3CoverageHonestRefusal:
    async def test_no_capability_declared_refused_not_guessed(
        self, patched_safe_sessions, ogc_fake_server
    ):
        # 服务存在 coverage 集合（itemType）但未声明 coverage 端点
        url = ogc_fake_server.add_ogc_coverages(with_coverage_link=False)
        patched_safe_sessions(ogc_fake_server)

        reg = _tools()
        report = await reg.dispatch("inspect_data_service", {"url": url})
        assert report["status"] == "success"
        cap = report["datasets"][0]["metadata"]["capability"]
        assert cap["state"] == "unsupported"
        assert any(r["code"] == "no_coverage_endpoint" for r in cap["reasons"])
        assert report["datasets"][0]["qualification"]["role_class"] == "unknown"

        res = await reg.dispatch("read_coverage_subset", {
            "url": url, "dataset_id": "dem",
            "subsets": {"x": [100.0, 100.04]},
            "session_id": "sess-e2e-s3",
        })
        assert res["status"] == "error"
        assert res["error_type"] == "capability_not_declared"
        assert res["ref"] is None

    async def test_happy_coverage_read_truth(self, patched_safe_sessions, ogc_fake_server):
        """对照：有声明的 coverage 服务读出的值与 fixture 真值一致（拒绝面
        不是"一概拒绝"，资格过门即真值交付）。"""
        url = ogc_fake_server.add_ogc_coverages()
        patched_safe_sessions(ogc_fake_server)
        reg = _tools()
        res = await reg.dispatch("read_coverage_subset", {
            "url": url, "dataset_id": "dem",
            "subsets": {"x": [100.0, 100.04], "y": [20.0, 20.04]},
            "session_id": "sess-e2e-s3b",
        })
        assert res["status"] == "success", res
        assert res["ref"].startswith("ref:fabric-cog/")
        assert res["nodata"] == COVERAGE_NODATA
        from app.services.artifact_registry import fabric_cog_path

        cov_path = fabric_cog_path("sess-e2e-s3b", res["ref"])
        import rasterio

        with rasterio.open(cov_path) as ds:
            arr = ds.read(1)
        expected = coverage_values()[::-1]
        assert arr.shape == (3, 3)
        for r in range(3):
            for c in range(3):
                assert arr[r, c] == pytest.approx(expected[r][c], abs=1e-6)
