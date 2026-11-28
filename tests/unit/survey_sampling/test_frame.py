"""抽样框构建测试（WP2 frame）：真实 GeoTIFF（EPSG:6933 等面积）。

不 mock：rasterio 写已知答案分类栅格 → RasterReader → build_frame。
覆盖：计数/索引/坐标、digest 幂等、NoData 排除、面积核查拒绝（4326
大跨纬）、空类拒绝、类值超 dtype 拒绝。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.lib.gis.scientific_errors import DegenerateData
from app.lib.geo_raster.reader import RasterReader
from app.lib.survey_sampling.frame import (
    build_frame,
    frame_strata_specs,
    pixel_center_xy,
)

CLASS_DICT = {1: "forest", 2: "crop", 3: "water"}


def _write_class_tif(
    path: Path, *, width: int = 60, height: int = 40,
    crs: str = "EPSG:6933", pixel: float = 100.0,
    nodata: int | None = 0,
) -> Path:
    """已知答案分类图：左侧 1/2 = forest，右上 crop，底部 water，
    右下角 nodata 补丁。"""
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(7)
    data = rng.choice([1, 2, 3], size=(height, width),
                      p=[0.5, 0.3, 0.2]).astype(np.uint8)
    data[:, width // 2:] = 1                       # 强制不对称层面积
    data[height - 10:, : width // 3] = 3
    if nodata is not None:
        data[0:5, 0:5] = nodata
    transform = from_origin(500000.0, 5200000.0, pixel, pixel)
    kwargs = dict(driver="GTiff", width=width, height=height, count=1,
                  dtype="uint8", crs=crs, transform=transform)
    if nodata is not None:
        kwargs["nodata"] = nodata
    with rasterio.open(path, "w", **kwargs) as dst:
        dst.write(data, 1)
    return path


def _expected_counts(path: Path, nodata: int | None = 0) -> dict:
    import rasterio

    with rasterio.open(path) as src:
        data = src.read(1)
    out = {}
    for v in CLASS_DICT:
        out[CLASS_DICT[v]] = int((data == v).sum())
    return out


def test_frame_counts_and_digest(tmp_path: Path) -> None:
    path = _write_class_tif(tmp_path / "cls.tif")
    with RasterReader.open(str(path)) as reader:
        meta = reader.metadata()
        frame = build_frame(reader, band=1, class_dictionary=CLASS_DICT,
                            nodata=0, crs=str(meta.crs))
    expected = _expected_counts(path)
    for spec in frame_strata_specs(frame):
        assert spec.N_units == expected[spec.stratum_id]
    total = sum(expected.values())
    assert frame.payload["total_units"] == total
    assert frame.payload["crs"] == "EPSG:6933"
    assert frame.payload["excluded"]["nodata"] == 25       # 5×5 nodata 补丁
    # 面积核查通过（等面积投影）：spread ≤ 1%
    assert frame.payload["pixel_area_spread"] <= 0.01
    assert frame.payload["total_area"] == pytest.approx(
        total * frame.payload["pixel_area_mean"], rel=1e-6)
    # digest 幂等：同源二次构建同 digest
    with RasterReader.open(str(path)) as reader:
        frame2 = build_frame(reader, band=1, class_dictionary=CLASS_DICT,
                             nodata=0, crs=str(meta.crs))
    assert frame.digest == frame2.digest == frame.payload["frame_digest"]
    # 单元坐标：像元中心（transform 正算一致）+ WGS84 合法
    specs = frame_strata_specs(frame)
    sid = specs[0].stratum_id
    x, y, lon, lat = frame.unit_coordinates(sid, 0)
    arr = frame.index_arrays[sid]
    row, col = divmod(int(arr[0]), 60)
    ex, ey = pixel_center_xy(frame.transform, col, row)
    assert (x, y) == (pytest.approx(ex), pytest.approx(ey))
    assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_frame_unit_coordinates_roundtrip(tmp_path: Path) -> None:
    path = _write_class_tif(tmp_path / "cls.tif")
    with RasterReader.open(str(path)) as reader:
        frame = build_frame(reader, band=1, class_dictionary=CLASS_DICT,
                            nodata=0, crs="EPSG:6933")
    for spec in frame_strata_specs(frame):
        n = spec.N_units
        for k in (0, n // 2, n - 1):
            x, y, lon, lat = frame.unit_coordinates(spec.stratum_id, k)
            assert np.isfinite([x, y, lon, lat]).all()
    with pytest.raises(IndexError):
        frame.unit_coordinates(frame_strata_specs(frame)[0].stratum_id, 10**9)


def test_frame_rejects_unequal_area(tmp_path: Path) -> None:
    """EPSG:4326 大跨纬（0.25°×60 行）：像元测地面积变化 >1% ⇒ typed 拒绝。"""
    import rasterio
    from rasterio.transform import from_origin

    path = tmp_path / "geo.tif"
    data = np.ones((60, 60), dtype=np.uint8)
    with rasterio.open(path, "w", driver="GTiff", width=60, height=60,
                       count=1, dtype="uint8", crs="EPSG:4326",
                       nodata=None,
                       transform=from_origin(100.0, 45.0, 0.25, 0.25)) as dst:
        dst.write(data, 1)
    with RasterReader.open(str(path)) as reader:
        with pytest.raises(DegenerateData, match="pixel area varies"):
            build_frame(reader, band=1,
                        class_dictionary={1: "forest"},
                        nodata=None, crs="EPSG:4326")


def test_frame_rejects_empty_class(tmp_path: Path) -> None:
    path = _write_class_tif(tmp_path / "cls.tif")
    with RasterReader.open(str(path)) as reader:
        with pytest.raises(DegenerateData, match="zero valid pixels"):
            build_frame(reader, band=1,
                        class_dictionary={1: "forest", 9: "ghost"},
                        nodata=0, crs="EPSG:6933")


def test_frame_rejects_class_value_out_of_dtype(tmp_path: Path) -> None:
    path = _write_class_tif(tmp_path / "cls.tif")
    with RasterReader.open(str(path)) as reader:
        with pytest.raises(DegenerateData, match="not representable"):
            build_frame(reader, band=1,
                        class_dictionary={1: "forest", 300: "overflow"},
                        nodata=0, crs="EPSG:6933")


def test_frame_rejects_unknown_crs(tmp_path: Path) -> None:
    path = _write_class_tif(tmp_path / "cls.tif")
    with RasterReader.open(str(path)) as reader:
        with pytest.raises(DegenerateData, match="CRS"):
            build_frame(reader, band=1, class_dictionary={1: "forest"},
                        nodata=0, crs="")
