"""G3D01 D5：合成 DEM fixture —— 确定性/自描述/资格/terrarium 全链路。

验收锚点（工作单 §D）：一条可控 DEM 3D 地形路径 + 纯本地 synthetic
fixture；synthetic 资格 display_only（绝不冒充分析级）；数据面复用既有
fail-closed 管线（不建旁路）。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio

from app.lib.cartography.terrain_encoding import decode_terrarium
from app.services.raster_tile_service import (
    invalidate_raster_ref,
    render_terrarium_tile,
)
from app.services.synthetic_terrain import (
    SYNTHETIC_DEM_SOURCE_ID,
    build_synthetic_terrain_ref,
    generate_synthetic_elevation,
    synthetic_terrain_qualification,
    write_synthetic_dem,
)


class TestDeterminism:
    def test_same_seed_identical_bytes(self, tmp_path):
        a = write_synthetic_dem(tmp_path / "a.tif", seed=7)
        b = write_synthetic_dem(tmp_path / "b.tif", seed=7)
        assert a.read_bytes() == b.read_bytes()

    def test_different_seed_different_elevation(self):
        ea = generate_synthetic_elevation(seed=1)
        eb = generate_synthetic_elevation(seed=2)
        assert not np.array_equal(ea, eb)


class TestGeoTiffContract:
    def test_self_describing_synthetic_tags(self, tmp_path):
        path = write_synthetic_dem(tmp_path / "dem.tif")
        with rasterio.open(path) as src:
            assert src.crs is not None and src.crs.to_string() == "EPSG:4326"
            assert src.count == 1
            assert src.dtypes[0] == "float32"
            assert src.nodata == -9999.0
            assert src.tags()["SYNTHETIC_DEM"] == "1"
            assert src.tags()["SYNTHETIC_SEED"] == "42"
            # 合成标记必须波段级在场（误用时单波段读取也能发现）
            assert src.tags(1)["SYNTHETIC_DEM"] == "1"

    def test_elevation_range_plausible_for_display(self):
        dem = generate_synthetic_elevation()
        assert dem.min() >= 400.0
        assert dem.max() <= 1600.0
        assert np.isfinite(dem).all()


class TestQualification:
    def test_display_only_never_analysis_grade(self):
        qual = synthetic_terrain_qualification()
        assert qual["qualification"] == "display_only"
        assert qual["synthetic"] is True
        assert qual["reason_code"] == "SYNTHETIC_DEM_DISPLAY_ONLY"
        assert qual["remediation"]

    def test_profile_lock_enforces_synthetic_display_only(self):
        from app.lib.cartography.basemap_profiles import get_elevation_profile

        p = get_elevation_profile(SYNTHETIC_DEM_SOURCE_ID)
        assert p is not None and p.synthetic is True
        assert p.qualification == "display_only"


class TestTerrariumPipeline:
    """合成 DEM → render_terrarium_tile → decode —— 与真实会话 DEM 同管线。"""

    def test_tile_roundtrip_elevation_range(self, tmp_path):
        path = write_synthetic_dem(tmp_path / "dem.tif")
        # 成都市区 (104.05, 30.65) @ z=8 → x=201, y=105
        png = render_terrarium_tile(str(path), 8, 201, 105)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

        from PIL import Image

        img = Image.open(__import__("io").BytesIO(png)).convert("RGB")
        arr = np.asarray(img)
        elev = decode_terrarium(arr)
        # 演示窗口全部有效（无 nodata → 无全透明像素）；高程在合成范围内
        assert np.isfinite(elev).all()
        valid = elev[(arr.sum(axis=2) > 0)]
        assert valid.size > 0
        assert 400.0 <= valid.min() and valid.max() <= 1600.0

    def test_out_of_coverage_tile_is_transparent(self, tmp_path):
        """窗口外瓦片全透明（无数据 ≠ 0 高程 —— fail-closed 语义）。"""
        path = write_synthetic_dem(tmp_path / "dem.tif")
        png = render_terrarium_tile(str(path), 4, 0, 0)  # 太平洋区域
        from PIL import Image

        img = Image.open(__import__("io").BytesIO(png)).convert("RGBA")
        arr = np.asarray(img)
        assert (arr[:, :, 3] == 0).all()


class TestRefRegistration:
    def test_build_ref_registers_and_url_template(self, tmp_path):
        ref = build_synthetic_terrain_ref("sess-1", "ref-syn", tmp_path)
        assert ref.qualification == "display_only"
        assert ref.synthetic is True
        assert ref.terrain_tiles_url_template == (
            "/api/v1/layers/data/ref-syn/terrain-tiles/{z}/{x}/{y}.png"
        )
        assert (tmp_path / "ref-syn.synthetic-dem.tif").exists()
        # ref 登记后渲染可用，失效幂等
        assert len(render_terrarium_tile(ref.raster_path, 8, 201, 105)) > 0
        assert invalidate_raster_ref("sess-1", "ref-syn") >= 0

    def test_ref_content_hash_stable(self, tmp_path):
        r1 = build_synthetic_terrain_ref("s", "r", tmp_path / "one")
        r2 = build_synthetic_terrain_ref("s", "r", tmp_path / "two")
        assert r1.content_sha256 == r2.content_sha256


@pytest.mark.parametrize("zoom,x,y", [(8, 201, 105), (9, 402, 210), (10, 804, 421)])
def test_demo_bbox_tiles_renderable_across_zooms(tmp_path, zoom, x, y):
    """演示窗口在多 zoom 层级均可渲染（LOD 行为一致性）。"""
    path = write_synthetic_dem(tmp_path / "dem.tif")
    png = render_terrarium_tile(str(path), zoom, x, y)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
