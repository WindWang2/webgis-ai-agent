"""V3 §D：矢量化/拓扑修复 + PostGIS 通道 + ROI 裁剪 的单元契约测试。"""
from __future__ import annotations

import numpy as np
import pytest
from rasterio.transform import Affine, from_origin

from app.lib.modelops.vectorize import VectorizeParams, vectorize_class_raster


def _block_classes():
    """64x64：类 1 = 左上 20x20 方块；类 2 = 右下 16x16 方块；255 = 背景。"""
    classes = np.full((64, 64), 255, dtype=np.uint8)
    classes[4:24, 4:24] = 1
    classes[40:56, 40:56] = 2
    return classes


def test_vectorize_produces_georeferenced_polygons():
    transform = from_origin(500000.0, 4000000.0, 10.0, 10.0)
    fc = vectorize_class_raster(
        _block_classes(),
        transform=transform,
        class_names=("background", "water", "urban"),
        params=VectorizeParams(simplify_tolerance_px=0.0, min_area_px=0.0),
    )
    assert fc["type"] == "FeatureCollection"
    by_class = {f["properties"]["class"]: f for f in fc["features"]}
    assert set(by_class) == {1, 2}
    # 地理坐标（rasterio xy 中心偏移约定，与检测框 GeoJSON 同一口径）：
    # 20x20 @10m 像素 = 200m 宽度；角点含 ±半像元中心偏移。
    water = by_class[1]
    ring = water["geometry"]["coordinates"][0]
    xs = [pt[0] for pt in ring]
    ys = [pt[1] for pt in ring]
    assert min(xs) == pytest.approx(500045.0, abs=1e-6)
    assert max(ys) == pytest.approx(3999955.0, abs=1e-6)
    assert water["properties"]["class_name"] == "water"
    assert (max(xs) - min(xs)) * (max(ys) - min(ys)) == pytest.approx(200 * 200)


def test_vectorize_deterministic():
    transform = from_origin(0.0, 0.0, 1.0, 1.0)
    fc_a = vectorize_class_raster(_block_classes(), transform=transform)
    fc_b = vectorize_class_raster(_block_classes(), transform=transform)
    assert fc_a == fc_b


def test_vectorize_simplify_and_area_filter():
    transform = from_origin(0.0, 0.0, 1.0, 1.0)
    classes = _block_classes()
    classes[60, 60] = 2  # 1 像素小面（默认 min_area_px=4 过滤）
    fc = vectorize_class_raster(classes, transform=transform)
    assert all(f["properties"]["class"] != 2 or f["properties"]["area_px"] > 4
               for f in fc["features"])
    # 大容差简化不应破坏拓扑（仍为有效多边形）。
    fc_simplified = vectorize_class_raster(
        classes, transform=transform,
        params=VectorizeParams(simplify_tolerance_px=2.0),
    )
    for f in fc_simplified["features"]:
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert len(f["geometry"]["coordinates"][0]) >= 4


def test_vectorize_confidence_attribute():
    transform = from_origin(0.0, 0.0, 1.0, 1.0)
    confidence = np.where(_block_classes() == 1, 0.83, 0.1).astype(np.float32)
    fc = vectorize_class_raster(
        _block_classes(), transform=transform, confidence=confidence
    )
    water = next(f for f in fc["features"] if f["properties"]["class"] == 1)
    assert water["properties"]["mean_confidence"] == pytest.approx(0.83, abs=1e-3)


def test_vectorize_pixel_to_geographic_semantics():
    """像素 (col,row) → 地理 (x,y) 的轴序验收（转置错误会在这里暴露）。

    rasterio ``xy`` 的中心偏移约定：像素 (c,r) 中心 =
    (c + 0.5) * xsize + x0 —— 与 artifacts.py 检测框同一口径。"""
    transform = from_origin(100.0, 200.0, 2.0, 3.0)  # 像元 2m x 3m
    classes = np.full((8, 8), 255, dtype=np.uint8)
    classes[0, 0] = 1  # 第 0 行、第 0 列
    fc = vectorize_class_raster(
        classes, transform=transform,
        params=VectorizeParams(min_area_px=0.0),  # 单像素面保留
    )
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    assert min(xs) == pytest.approx(101.0)   # 100 + 2/2
    assert max(xs) == pytest.approx(103.0)   # 100 + 2*1.5
    assert max(ys) == pytest.approx(198.5)   # 200 - 3/2
    assert min(ys) == pytest.approx(195.5)   # 200 - 3*1.5


def test_vectorize_rotation_transform_supported():
    transform = Affine(2.0, 0.0, 0.0, 0.0, -3.0, 100.0)
    classes = np.full((4, 4), 1, dtype=np.uint8)
    fc = vectorize_class_raster(classes, transform=transform)
    assert fc["features"], "rotated/affine transforms must still vectorize"


# ── PostGIS 通道 ─────────────────────────────────────────────────────


def test_postgis_table_name_validation():
    from app.services.modelops.geo_output import validate_table_name

    assert validate_table_name("landcover_v2") == "landcover_v2"
    for bad in ("", "1abc", "has space", "UPPER", "a;b", "x" * 64, 'a"b'):
        with pytest.raises(ValueError):
            validate_table_name(bad)


def test_postgis_honest_skip_without_dsn(monkeypatch):
    from app.services.modelops.geo_output import publish_geojson_to_postgis

    monkeypatch.delenv("MODELOPS_POSTGIS_DSN", raising=False)
    fc = {"type": "FeatureCollection", "features": []}
    result = publish_geojson_to_postgis(fc, table="t")
    assert result["published"] is False
    assert result["reason"] == "empty feature collection"
    result = publish_geojson_to_postgis(
        {"type": "FeatureCollection", "features": [{"type": "Feature",
         "properties": {}, "geometry": {"type": "Point", "coordinates": [1, 2]}}]},
        table="t",
    )
    assert result["published"] is False
    assert "DSN" in result["reason"]


def test_postgis_if_exists_closed_vocab():
    from app.services.modelops.geo_output import publish_geojson_to_postgis

    with pytest.raises(ValueError):
        publish_geojson_to_postgis(
            {"type": "FeatureCollection", "features": []},
            table="t", if_exists="replace",  # 静默毁表不在授权面
        )


def test_postgis_publish_captures_frame(monkeypatch):
    """驱动可用路径：捕获 to_postgis 调用参数（无真实 PostGIS）。"""
    import geopandas as gpd

    from app.services.modelops import geo_output

    captured = {}

    class _FakeEngine:
        pass

    def fake_to_postgis(self, name, con, if_exists="fail", index=False):
        captured["table"] = name
        captured["rows"] = len(self)
        captured["if_exists"] = if_exists

    # sqlite DSN：create_engine 可构造且不需要 psycopg 驱动；写入被 fake 捕获。
    monkeypatch.setattr(geo_output.os, "environ", {**geo_output.os.environ,
                                                   "MODELOPS_POSTGIS_DSN": "sqlite://"})
    monkeypatch.setattr(gpd.GeoDataFrame, "to_postgis", fake_to_postgis, raising=False)
    result = geo_output.publish_geojson_to_postgis(
        {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"class": 1},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}}
        ]},
        table="polygons", if_exists="append",
    )
    assert result["published"] is True
    assert captured["table"] == "polygons"
    assert captured["rows"] == 1
    assert captured["if_exists"] == "append"


# ── ROI 计划裁剪（纯几何部分）────────────────────────────────────────


def test_clip_plan_to_roi_geometry():
    from app.lib.modelops.descriptor import (
        ClassSchema,
        GeoModelDescriptor,
        NormalizationSpec,
        SpatialRequirements,
    )
    from app.lib.modelops.planning import plan_tiles
    from app.services.modelops.engine import _clip_plan_to_roi

    desc = GeoModelDescriptor.model_validate(
        {
            "model_id": "roi-model", "model_version": "1.0.0", "checksum": "a" * 64,
            "provider_type": "local_reference", "provider_ref": "tiny-reference",
            "provider_semantic_version": "tiny/1.0.0",
            "task_types": ("semantic_segmentation",),
            "input_modalities": ("optical_rgb",), "input_bands": 3,
            "normalization": NormalizationSpec(kind="none"),
            "output_types": ("class_raster",),
            "class_schema": ClassSchema(classes=("background", "bright", "mid")),
            "spatial": SpatialRequirements(chip_size=(16, 16), context_size=(16, 16)),
            "license": "test",
        }
    )
    full = plan_tiles(desc, raster_height=128, raster_width=128)
    clipped, origin = _clip_plan_to_roi(desc, (32, 40, 96, 104),
                                        raster_width=128, raster_height=128)
    assert origin == (32, 40)
    assert clipped.raster_width == 64 and clipped.raster_height == 64
    # 窗口保持 ROI 本地坐标（读取时才平移到绝对坐标）。
    first = clipped.tiles[0]
    assert first.core_window[0] >= 0 and first.core_window[1] >= 0
    # ROI 内 tile 覆盖完整 ROI（core 网格重排后仍无缝）。
    coverage = np.zeros((64, 64), dtype=np.uint16)
    for t in clipped.tiles:
        row, col, h, w = t.core_window
        coverage[row: row + h, col: col + w] += 1
    assert bool((coverage == 1).all())
    assert len(clipped.tiles) < len(full.tiles)
