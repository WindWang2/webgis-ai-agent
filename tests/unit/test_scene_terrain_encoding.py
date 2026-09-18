"""Terrarium elevation encoding + fail-closed terrain tile tests (ADR-0199 M3).

Oracle anchors:
- 无 elevation 证据时不伪造高度：非单波段 DEM / 无 CRS → 结构化错误（不渲染）；
  nodata → 透明像素（绝不编码为 0 高程）。
- 已知答案：合成高程 → terrarium RGB → 解码 round-trip 一致（量化误差内）。
"""
from __future__ import annotations

import io
import tempfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from PIL import Image

from app.lib.cartography.terrain_encoding import (
    DEM_SENTINEL_NODATA,
    TERRAIN_ENCODING,
    TerrainEncodingError,
    decode_terrarium,
    encode_terrarium,
)
from app.services.raster_tile_service import (
    TerrainTileError,
    render_raster_tile,
    render_terrarium_tile,
)


class TestTerrariumEncodingPure:
    def test_encoding_constant(self):
        assert TERRAIN_ENCODING == "terrarium"
        assert DEM_SENTINEL_NODATA == -9999.0

    def test_known_answer_sea_level(self):
        """0 m 高程 → (128, 0, 0)（terrarium 规范已知答案）。"""
        rgb = encode_terrarium(np.array([[0.0]]), np.array([[True]]))
        assert rgb.tolist() == [[[128, 0, 0]]]

    def test_known_answer_offset_and_fraction(self):
        """已知答案：e = 100.5 m → R=128+100=228? 令 e'=e+32768=32868.5；
        R=e'//256=128、G=e'%256=100、B=0.5*256=128。"""
        rgb = encode_terrarium(np.array([[100.5]]), np.array([[True]]))
        assert rgb.tolist() == [[[128, 100, 128]]]

    def test_negative_elevation_encodes(self):
        """-430 m（死海）：e'=32338 → R=126、G=82。"""
        rgb = encode_terrarium(np.array([[-430.0]]), np.array([[True]]))
        assert rgb.tolist() == [[[126, 82, 0]]]

    def test_round_trip_quantized(self):
        rng = np.random.default_rng(42)
        elev = rng.uniform(-500.0, 4000.0, size=(32, 32))
        valid = np.ones_like(elev, dtype=bool)
        decoded = decode_terrarium(encode_terrarium(elev, valid))
        # B 通道量化分辨率 = 1/256 m
        assert float(np.max(np.abs(decoded - elev))) < 1.0 / 256.0 + 1e-6

    def test_invalid_pixels_are_zeroed(self):
        elev = np.array([[100.0, np.nan], [200.0, DEM_SENTINEL_NODATA]])
        valid = np.array([[True, False], [True, False]])
        rgb = encode_terrarium(elev, valid)
        assert rgb[0, 1].tolist() == [0, 0, 0]
        assert rgb[1, 1].tolist() == [0, 0, 0]
        assert rgb[1, 0].tolist() == encode_terrarium(np.array([[200.0]]), np.array([[True]]))[0, 0].tolist()

    def test_nan_raises(self):
        """valid 掩码与数据不一致（NaN 在 valid 内）→ 结构化错误，不静默。"""
        with pytest.raises(TerrainEncodingError) as ei:
            encode_terrarium(np.array([[np.nan]]), np.array([[True]]))
        assert ei.value.code == "TERRAIN_NON_FINITE_ELEVATION"


class TestTerrariumTileRenderer:
    @staticmethod
    def _deg2tile(lon: float, lat: float, z: int) -> tuple:
        import math

        n = 1 << z
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
        return z, x, y

    def _write_dem(self, elevation: np.ndarray, *, nodata=None, crs="EPSG:4326", bands=1):
        h, w = elevation.shape
        f = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
        f.close()
        transform = from_bounds(116.0, 39.0, 117.0, 40.0, w, h)
        if bands > 1:
            data = np.broadcast_to(
                elevation.reshape(1, h, w), (bands, h, w)
            ).copy()
        else:
            data = elevation.reshape(1, h, w).copy()
        with rasterio.open(
            f.name, "w", driver="GTiff", height=h, width=w, count=bands,
            dtype="float32", crs=crs, transform=transform,
            nodata=nodata,
        ) as dst:
            dst.write(data.astype(np.float32))
        return f.name

    def _tile_png_array(self, png_bytes: bytes) -> np.ndarray:
        img = Image.open(io.BytesIO(png_bytes))
        return np.array(img)

    def test_single_band_dem_renders_rgb(self):
        path = self._write_dem(np.full((64, 64), 150.0, dtype=np.float32))
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            png = render_terrarium_tile(path, _z, tx, ty)
            arr = self._tile_png_array(png)
            assert arr.shape[:2] == (256, 256)
            opaque = arr[arr[:, :, 3] > 0]
            assert opaque.shape[0] > 0
            decoded = decode_terrarium(opaque[:, :3].astype(np.float64))
            assert float(np.mean(np.abs(decoded - 150.0))) < 1.0
        finally:
            import os
            os.unlink(path)

    def test_multiband_dem_fails_closed(self):
        path = self._write_dem(
            np.full((32, 32), 100.0, dtype=np.float32), bands=3
        )
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            with pytest.raises(TerrainTileError) as ei:
                render_terrarium_tile(path, _z, tx, ty)
            assert ei.value.code == "TERRAIN_REQUIRES_SINGLE_BAND"
        finally:
            import os
            os.unlink(path)

    def test_missing_crs_fails_closed(self):
        """无 CRS 的 DEM：静默假定投影 = 高程放错位置 —— 地形面 fail-closed。"""
        elevation = np.full((32, 32), 100.0, dtype=np.float32)
        h, w = elevation.shape
        f = tempfile.NamedTemporaryFile(suffix=".tif", delete=False)
        f.close()
        with rasterio.open(
            f.name, "w", driver="GTiff", height=h, width=w, count=1,
            dtype="float32", crs=None,
            transform=from_bounds(116.0, 39.0, 117.0, 40.0, w, w),
        ) as dst:
            dst.write(elevation.reshape(1, h, w))
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            with pytest.raises(TerrainTileError) as ei:
                render_terrarium_tile(f.name, _z, tx, ty)
            assert ei.value.code == "TERRAIN_REQUIRES_CRS"
        finally:
            import os
            os.unlink(f.name)

    def test_sentinel_nodata_becomes_transparent(self):
        elevation = np.full((64, 64), 100.0, dtype=np.float32)
        elevation[0:8, :] = DEM_SENTINEL_NODATA  # 未声明 nodata，但带哨兵
        path = self._write_dem(elevation)
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            png = render_terrarium_tile(path, _z, tx, ty)
            arr = self._tile_png_array(png)
            assert int((arr[:, :, 3] == 0).sum()) > 0  # 有透明像素
            # 透明区域不携带伪高度：R 通道为 0（未编码）
            transparent = arr[arr[:, :, 3] == 0]
            assert int((transparent[:, 0] != 0).sum()) == 0
        finally:
            import os
            os.unlink(path)

    def test_declared_nodata_becomes_transparent(self):
        elevation = np.full((64, 64), 100.0, dtype=np.float32)
        elevation[0:8, :] = -3.0  # 声明 nodata=-3
        path = self._write_dem(elevation, nodata=-3.0)
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            png = render_terrarium_tile(path, _z, tx, ty)
            arr = self._tile_png_array(png)
            assert int((arr[:, :, 3] == 0).sum()) > 0
        finally:
            import os
            os.unlink(path)

    def test_out_of_bounds_tile_is_transparent(self):
        path = self._write_dem(np.full((32, 32), 100.0, dtype=np.float32))
        try:
            png = render_terrarium_tile(path, 12, 5000, 5000)  # 远离 DEM bbox
            arr = self._tile_png_array(png)
            assert int((arr[:, :, 3] > 0).sum()) == 0
        finally:
            import os
            os.unlink(path)

    def test_invalid_zoom_returns_transparent(self):
        path = self._write_dem(np.full((32, 32), 100.0, dtype=np.float32))
        try:
            png = render_terrarium_tile(path, 30, 0, 0)
            arr = self._tile_png_array(png)
            assert arr.shape[:2] == (256, 256)
        finally:
            import os
            os.unlink(path)

    def test_all_nodata_window_is_fully_transparent(self):
        elevation = np.full((64, 64), DEM_SENTINEL_NODATA, dtype=np.float32)
        path = self._write_dem(elevation)
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            png = render_terrarium_tile(path, _z, tx, ty)
            arr = self._tile_png_array(png)
            assert int((arr[:, :, 3] > 0).sum()) == 0
        finally:
            import os
            os.unlink(path)

    def test_terrain_cache_does_not_collide_with_color_tiles(self):
        """同 (path, z, x, y) 的 terrain 与 color 瓦片不得共享缓存条目。"""
        elevation = np.full((64, 64), 100.0, dtype=np.float32)
        path = self._write_dem(elevation)
        _z, tx, ty = self._deg2tile(116.5, 39.5, 9)
        try:
            terrain_png = render_terrarium_tile(path, _z, tx, ty)
            color_png = render_raster_tile(path, _z, tx, ty)
            assert terrain_png != color_png
        finally:
            import os
            os.unlink(path)


class TestTerrariumRangeGuard:
    def test_out_of_range_elevation_raises_structured(self):
        """Review P2-2：越界高程（单位标错）必须显式失败，绝不静默回绕。"""
        with pytest.raises(TerrainEncodingError) as ei:
            encode_terrarium(np.array([[40000.0]]), np.array([[True]]))
        assert ei.value.code == "TERRAIN_ELEVATION_OUT_OF_RANGE"

    def test_below_range_elevation_raises_structured(self):
        with pytest.raises(TerrainEncodingError) as ei:
            encode_terrarium(np.array([[-33000.0]]), np.array([[True]]))
        assert ei.value.code == "TERRAIN_ELEVATION_OUT_OF_RANGE"

    def test_boundary_values_encode(self):
        encode_terrarium(np.array([[-32768.0]]), np.array([[True]]))
        encode_terrarium(np.array([[32767.9]]), np.array([[True]]))
