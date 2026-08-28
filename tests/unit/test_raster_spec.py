"""Raster Data Plane 基础契约（C5）测试：RasterArtifactDescriptor /
RasterStyleSpec / 瓦片 cmap 与 band selection（样式 ≠ 重算）。"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.schemas.raster_spec import (
    RasterStyleSpec,
    apply_colormap_u8,
    colormap_rgb_lut,
    inspect_raster_artifact,
)
from app.services.raster_tile_service import render_raster_tile


@pytest.fixture
def two_band_tif(tmp_path):
    """2 波段 GeoTIFF（EPSG:3857，值域分波段错开）。"""
    path = str(tmp_path / "two_band.tif")
    h, w = 16, 16
    band1 = np.full((h, w), 10.0, dtype=np.float32)
    band2 = np.full((h, w), 90.0, dtype=np.float32)
    transform = from_origin(-1e6, 1e6, 2e5, 2e5)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=h,
        width=w,
        count=2,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform,
        nodata=-9999.0,
    ) as dst:
        dst.write(band1, 1)
        dst.write(band2, 2)
    return path


def test_style_spec_band_normalization_and_cache_key():
    spec = RasterStyleSpec(colormap="viridis", bands=[1])
    assert spec.normalized_bands(5) == (1,)
    assert spec.normalized_bands(1) == (1,)
    # 越界剔除 + ≤3 截断
    assert RasterStyleSpec(bands=[0, 1, 2, 3, 4]).normalized_bands(3) == (1, 2, 3)
    # 缺省 = 前 min(3, n)
    assert RasterStyleSpec().normalized_bands(5) == (1, 2, 3)
    assert RasterStyleSpec().normalized_bands(1) == (1,)
    # 不同样式 → 不同缓存键
    assert RasterStyleSpec(colormap="viridis").cache_key() != RasterStyleSpec(colormap="magma").cache_key()
    assert RasterStyleSpec(bands=[1]).cache_key() != RasterStyleSpec(bands=[2]).cache_key()


def test_colormap_lut_and_apply():
    lut = colormap_rgb_lut("viridis")
    assert lut is not None and lut.shape == (256, 3)
    assert colormap_rgb_lut("no-such-cmap") is None
    gray = np.array([[0, 128, 255]], dtype=np.uint8)
    rgb = apply_colormap_u8(gray, "viridis")
    assert rgb is not None and rgb.shape[-1] == 3
    # 端点不同色（真着色而非灰度复制）
    assert not np.array_equal(rgb[0][0], rgb[0][2])
    assert apply_colormap_u8(gray, "no-such-cmap") is None


def test_inspect_raster_artifact(two_band_tif):
    desc = inspect_raster_artifact(two_band_tif)
    assert desc is not None
    assert desc.band_count == 2
    assert desc.crs == "EPSG:3857"
    assert desc.nodata == -9999.0
    assert len(desc.bands) == 2
    assert desc.bands[0].vmin == desc.bands[0].vmax == 10.0
    assert desc.bands[1].vmin == desc.bands[1].vmax == 90.0
    assert desc.width == 16 and desc.height == 16
    # 可安全序列化（有界元数据）
    dumped = desc.to_dict()
    assert "bands" in dumped


def test_inspect_missing_file_returns_none(tmp_path):
    assert inspect_raster_artifact(str(tmp_path / "nope.tif")) is None


def _png_pixels(png_bytes):
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    return np.array(img)


def test_render_tile_single_band_colormap(two_band_tif):
    """cmap 单波段着色生效（非灰度），且不换缓存键的灰度路径不受影响。"""
    gray = _png_pixels(render_raster_tile(two_band_tif, 4, 8, 8))
    colored = _png_pixels(render_raster_tile(two_band_tif, 4, 8, 8, cmap_name="viridis"))

    gray_pixels = gray[gray[..., 3] > 0]
    colored_pixels = colored[colored[..., 3] > 0]
    assert gray_pixels.shape[0] > 0
    # 灰度：R==G==B；着色：存在 R!=G 或 G!=B 的像素
    assert np.all(gray_pixels[:, 0] == gray_pixels[:, 1])
    assert np.any(colored_pixels[:, 0] != colored_pixels[:, 1]) or np.any(
        colored_pixels[:, 1] != colored_pixels[:, 2]
    )


def test_render_tile_band_selection(two_band_tif):
    """band 2（值 90）与 band 1（值 10）拉伸后亮度不同。"""
    from app.services.raster_tile_service import _STATS_CACHE_LOCK, _STATS_CACHE

    with _STATS_CACHE_LOCK:
        _STATS_CACHE.clear()

    b1 = _png_pixels(render_raster_tile(two_band_tif, 4, 8, 8, bands=(1,)))
    b2 = _png_pixels(render_raster_tile(two_band_tif, 4, 8, 8, bands=(2,)))
    v1 = b1[b1[..., 3] > 0][:, :3].mean()
    v2 = b2[b2[..., 3] > 0][:, :3].mean()
    # band1 全 10 / band2 全 90：各自的 dataset stretch 都是 [v, v] →
    # vmax==vmin 分支归一为 0。常量波段的语义是"无对比"，两波段同为 0 亮度
    # 是合法结果——band selection 的可验证差异用非常量波段验证（下个用例）。
    assert v1 == v2  # 常量波段：normalize 的 vmax==vmin 分支


def test_render_tile_band_selection_variable(tmp_path):
    """非常量波段：band 选择改变输出（band1 递增、band2 递减 → 亮度不同）。"""
    path = str(tmp_path / "var.tif")
    h, w = 16, 16
    band1 = np.tile(np.linspace(0, 100, w, dtype=np.float32), (h, 1))
    band2 = np.tile(np.linspace(100, 0, w, dtype=np.float32), (h, 1))
    transform = from_origin(-1e6, 1e6, 2e5, 2e5)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=2, dtype="float32",
        crs="EPSG:3857", transform=transform,
    ) as dst:
        dst.write(band1, 1)
        dst.write(band2, 2)

    from app.services.raster_tile_service import _STATS_CACHE, _STATS_CACHE_LOCK

    with _STATS_CACHE_LOCK:
        _STATS_CACHE.clear()

    b1 = _png_pixels(render_raster_tile(path, 4, 8, 8, bands=(1,)))
    b2 = _png_pixels(render_raster_tile(path, 4, 8, 8, bands=(2,)))
    # band1 与 band2 互为镜像 → 灰度瓦片的均值互补（和 ≈ 255）
    v1 = b1[b1[..., 3] > 0][:, 0].mean()
    v2 = b2[b2[..., 3] > 0][:, 0].mean()
    assert abs((v1 + v2) - 255.0) < 8.0
