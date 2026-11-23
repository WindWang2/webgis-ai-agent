"""Regression for #682 — zonal_stats must honor projected CRS via tool route.

#599 fixed clip/overlay/dissolve/join/h3 but zonal_stats dropped the
FeatureCollection `crs` at two points:
  1. app/tools/advanced_spatial.py tool boundary (features list only)
  2. app/services/spatial_analyzer.py rebuild without `crs`

A declared EPSG:3857 zone FC on a 3857 raster must not be misread as
EPSG:4326 — pyproj returns (inf,inf) silently and GIS-23 must fire, or
stats come back as None/zero and look plausible.

The test walks the TOOL ROUTE (ToolRegistry.dispatch) so the passthrough
is exercised, not just raster_ops directly.
"""
import copy
import os
import uuid

import numpy as np
import pyproj
import pytest
import rasterio
from rasterio.transform import from_origin

from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry
from app.utils.coord_transform import transform_geojson

# WGS84 zone ~ 0.05° box near Beijing (used as source for transform)
WGS84_ZONE_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"zone_id": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [116.0, 39.90],
                        [116.05, 39.90],
                        [116.05, 40.00],
                        [116.0, 40.00],
                        [116.0, 39.90],
                    ]
                ],
            },
        }
    ],
}


def _data_path(name: str) -> str:
    # validate_data_path allows <cwd>/data (and <cwd>/tmp). Under worktree
    # the pytest cwd (worktree root) differs from the bare relative path, so
    # resolve against the runtime cwd's data/tmp. Prefer data, fall back to tmp.
    data_dir = os.path.realpath("./data")
    tmp_dir = os.path.realpath("./tmp")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    # Return an absolute path under the cwd's data dir so validate passes.
    return os.path.join(data_dir, name)


def _make_raster(path: str, crs: str, value: float = 10.0) -> None:
    """Create a small constant-value raster covering the WGS84 zone bbox.

    For EPSG:4326 the grid is in degrees; for EPSG:3857 it is in metres
    reprojected from the same WGS84 bbox so the zone+ raster overlap when
    CRS is respected.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if crs == "EPSG:4326":
        west, south, east, north = 115.95, 39.85, 116.10, 40.05
        res = 0.02
        width = int(round((east - west) / res))
        height = int(round((north - south) / res))
        transform = from_origin(west, north, res, res)
    else:
        tr = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x0, y0 = tr.transform(115.95, 39.85)
        x1, y1 = tr.transform(116.10, 40.05)
        width, height = 10, 10
        res_x = (x1 - x0) / width
        res_y = (y1 - y0) / height
        transform = from_origin(x0, y1, res_x, res_y)
    data = np.full((height, width), value, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=np.float32,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(data, 1)


@pytest.fixture()
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


@pytest.mark.asyncio
async def test_zonal_stats_tool_3857_zone_on_3857_raster(registry):
    """Projected (EPSG:3857) zone FC on a 3857 raster via tool route — must be ~10."""
    raster_path = _data_path(f"test_682_3857_{uuid.uuid4().hex[:8]}.tif")
    _make_raster(raster_path, "EPSG:3857", value=10.0)
    # Build a 3857-declared FC from the WGS84 zone
    fc_3857 = transform_geojson(WGS84_ZONE_FC, from_crs="EPSG:4326", to_crs="EPSG:3857")
    # transform_geojson already writes crs when input has none? Input has no crs
    # so we must declare it explicitly (and transform_geojson preserves it on output
    # when from is 4326->3857 and input had no crs it will NOT add crs — so inject).
    fc_3857 = dict(fc_3857)
    fc_3857["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}
    try:
        result = await registry.dispatch("zonal_stats", {"geojson": fc_3857, "raster_path": raster_path})
        assert result.get("success") is True, result
        data = result.get("data")
        assert isinstance(data, dict) and data.get("type") == "FeatureCollection"
        feats = data.get("features", [])
        assert len(feats) == 1
        props = feats[0].get("properties", {})
        # On the bug (crs stripped) this zone is misread as 4326 and reprojected
        # 4326->3857 on 12M-metre coords -> (inf,inf) -> _has_inf_coords fires or
        # rasterstats returns None stats. Either way mean is not 10.
        assert props.get("mean") is not None, f"mean is None — CRS was likely dropped: {props}"
        assert abs(float(props["mean"]) - 10.0) < 1e-6, f"mean should be 10, got {props.get('mean')}"
        # default stats are mean/sum/max/min (no count)
        assert props.get("sum") is not None
    finally:
        try:
            os.unlink(raster_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_zonal_stats_tool_3857_zone_on_4326_raster(registry):
    """Projected (EPSG:3857) zone FC on a WGS84 raster via tool route — must be ~42."""
    raster_path = _data_path(f"test_682_4326_{uuid.uuid4().hex[:8]}.tif")
    _make_raster(raster_path, "EPSG:4326", value=42.0)
    fc_3857 = transform_geojson(WGS84_ZONE_FC, from_crs="EPSG:4326", to_crs="EPSG:3857")
    fc_3857 = dict(fc_3857)
    fc_3857["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}
    try:
        result = await registry.dispatch("zonal_stats", {"geojson": fc_3857, "raster_path": raster_path})
        assert result.get("success") is True, result
        data = result.get("data")
        feats = data.get("features", [])
        assert len(feats) == 1
        props = feats[0].get("properties", {})
        # Bug: 3857 coords treated as 4326 -> identity reprojection (4326->4326)
        # but coords are ~12M so they miss the raster entirely -> mean None.
        assert props.get("mean") is not None, f"mean is None — CRS was dropped: {props}"
        assert abs(float(props["mean"]) - 42.0) < 1e-6
        assert props.get("sum") is not None
    finally:
        try:
            os.unlink(raster_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_zonal_stats_string_form_crs_member(tmp_path):
    """#708: the string-form ``"crs": "EPSG:4326"`` member must be honored —
    the old dict-only read raised AttributeError on the very form the
    contract authority (extract_declared_crs) accepts."""
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    raster = os.path.join(str(tmp_path), "const.tif")
    _make_raster(raster, "EPSG:4326", value=1.0)

    fc = {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",  # string shorthand, not the dict form
        "features": WGS84_ZONE_FC["features"],
    }
    stats = zonal_statistics(fc, raster, stats=["mean"])
    assert stats and stats[0].get("mean") is not None
    assert stats[0]["mean"] == pytest.approx(1.0, nan_ok=True)


def test_zonal_stats_does_not_mutate_input_fc():
    """#1634：zonal_stats 绝不原地修改调用方传入的 FeatureCollection。

    统计结果只进返回值的要素拷贝；输入 FC（工具层 ref 载荷的同一个 dict）
    的 properties 不得被分析管线静默改写（data_quality 全链 deepcopy，
    此处补漏）。
    """
    import copy as _copy

    from app.services.spatial_analyzer import SpatialAnalyzer

    raster_path = _data_path(f"test_1634_zonal_{uuid.uuid4().hex[:8]}.tif")
    _make_raster(raster_path, "EPSG:4326", value=10.0)
    fc = _copy.deepcopy(WGS84_ZONE_FC)
    snapshot = _copy.deepcopy(fc)
    try:
        res = SpatialAnalyzer.zonal_stats(fc, raster_path)
        assert res.success is True, res.summary
        # 返回值带统计值
        props = res.data["features"][0]["properties"]
        assert props.get("mean") is not None
        assert abs(float(props["mean"]) - 10.0) < 1e-6
        # 输入逐字节未改
        assert fc == snapshot, "zonal_stats 不得原地修改调用方输入的 properties"
    finally:
        try:
            os.unlink(raster_path)
        except OSError:
            pass


def test_zonal_stats_pipeline_receives_copied_features(monkeypatch):
    """P2：非破坏拷贝必须真正进入分析管线。

    旧代码 fc_payload = features（调用方原始 dict），522-526 的浅拷贝只用于
    返回值；分析管线一旦原地改写要素，调用方 FC 即被污染。这里用会改写输入的
    fake pipeline 钉住隔离契约。
    """
    from app.services.spatial_analyzer import SpatialAnalyzer

    fc = {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [0, 0]},
            "properties": {"zone_id": 1},
        }],
    }
    snapshot = copy.deepcopy(fc)
    fake_path = _data_path(f"fake_{uuid.uuid4().hex[:8]}.tif")

    def _mutating_zonal_statistics(payload, raster_path):
        for feat in payload["features"]:
            feat["properties"]["mean"] = 999.0
        return [{"mean": 999.0} for _ in payload["features"]]

    monkeypatch.setattr(
        "app.lib.geo_analysis.raster_ops.zonal_statistics",
        _mutating_zonal_statistics,
    )
    res = SpatialAnalyzer.zonal_stats(fc, fake_path)
    assert res.success is True, res.summary
    assert fc == snapshot, "zonal_stats 必须把要素拷贝交给分析管线，不得暴露调用方 dict"
    # 顶层 crs 仍随载荷透传（GIS-682）；统计照常合入返回的拷贝
    assert res.data["features"][0]["properties"]["mean"] == 999.0


@pytest.mark.parametrize("pixel_bounds", [
    (0.2, 0.2, 1.6, 1.6),
    (0.4, 0.4, 0.8, 0.8),
    (1.2, 1.2, 2.6, 3.6),
    (-0.3, -0.3, 1.6, 1.6),
    (3.4, 3.4, 5.2, 5.2),
    (1.0, 1.0, 3.0, 3.0),
])
def test_fractional_zonal_window_matches_full_raster_oracle(tmp_path, pixel_bounds):
    """#2234: bounding windows must not truncate covered cell centers."""
    from rasterio.features import geometry_mask
    from shapely.geometry import Polygon, mapping

    from app.lib.geo_analysis.raster_ops import _windowed_zonal_stats

    transform = from_origin(100, 200, 2, 3)
    left, top, right, bottom = pixel_bounds
    polygon = Polygon([transform * point for point in [
        (left, top), (right, top), (right, bottom), (left, bottom),
    ]])
    image = np.arange(1, 26, dtype="float64").reshape(5, 5)
    image[1, 1] = -9999
    raster_path = tmp_path / "fractional.tif"
    with rasterio.open(
        raster_path, "w", driver="GTiff", height=5, width=5, count=1,
        dtype="float64", crs="EPSG:3857", transform=transform, nodata=-9999,
    ) as dst:
        dst.write(image, 1)
    covered = geometry_mask(
        [mapping(polygon)], out_shape=image.shape, transform=transform,
        invert=True, all_touched=False,
    )
    values = image[covered & (image != -9999)]
    assert values.size > 0
    result = _windowed_zonal_stats(
        mapping(polygon), str(raster_path),
        stats=["count", "sum", "mean", "min", "max", "std"],
    )[0]
    assert result["count"] == values.size
    for key in ("sum", "mean", "min", "max", "std"):
        assert result[key] == pytest.approx(getattr(values, key)())
    assert result["valid_pixel_coverage"] == pytest.approx(
        values.size / np.count_nonzero(covered))


# ── #2134 回归（zonal_statistics 输入识别与有界读）────────────────────

def test_zonal_unrecognized_string_raises(tmp_path):
    """#2134: 既非路径也非 GeoJSON 的字符串 typed 拒绝（不静默返回 []）。"""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    from app.lib.geo_analysis.raster_ops import zonal_statistics

    raster_path = str(tmp_path / "z.tif")
    with rasterio.open(raster_path, "w", driver="GTiff", height=4, width=4,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(0, 4, 1, 1)) as dst:
        dst.write(np.ones((4, 4), dtype="float32"), 1)
    with pytest.raises(ValueError, match="无法识别"):
        zonal_statistics("this is not geojson nor a path", raster_path)
    with pytest.raises(ValueError, match="解析失败"):
        zonal_statistics('{"type": "FeatureCollection", broken', raster_path)


def test_zonal_chunked_large_zone_matches_full_array_oracle(
        tmp_path, monkeypatch):
    """#2134: 大 zone 分块读的统计与整幅单次读一致（Welford 合并口径）。"""
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import mapping, Polygon

    import app.lib.geo_analysis.raster_ops as ops

    rng = np.random.default_rng(2134)
    h, w = 64, 24   # 高 64 > chunk 16，强制多块
    data = rng.normal(size=(h, w))
    raster_path = str(tmp_path / "big.tif")
    transform = from_origin(100, 200, 2, 3)
    with rasterio.open(raster_path, "w", driver="GTiff", height=h, width=w,
                       count=1, dtype="float64", crs="EPSG:3857",
                       transform=transform, nodata=-9999.0) as dst:
        dst.write(data, 1)
    poly = Polygon([(100, 0), (148, 0), (148, 200), (100, 200)])
    fc = {"type": "FeatureCollection", "crs": "EPSG:3857",
          "features": [{"type": "Feature", "geometry": mapping(poly),
                        "properties": {}}]}
    monkeypatch.setattr("app.lib.geo_analysis.raster_grid"
                        ".window_side_from_budget", lambda *a, **k: 16)
    (row,) = ops.zonal_statistics(
        fc, raster_path,
        stats=["count", "sum", "mean", "min", "max", "std"])
    # 整幅 oracle（分块前的单次读口径）：cell-center 成员掩膜
    from rasterio.features import geometry_mask
    covered = geometry_mask([mapping(poly)], out_shape=data.shape,
                            transform=transform, invert=True)
    valid = covered & (data != -9999.0)
    assert row["count"] == int(valid.sum())
    for key in ("sum", "mean", "min", "max", "std"):
        assert row[key] == pytest.approx(getattr(data[valid], key)(),
                                         rel=1e-10), key
    assert row["valid_pixel_coverage"] == pytest.approx(1.0)


def test_zonal_wide_zone_blocks_respect_budget_window(tmp_path, monkeypatch):
    """P2 修复：宽 zone 的分块读取不得按 chunk_rows×zone_width 分配。

    旧实现只按行分块：2048 行 × 10 万列的宽 zone 单块 ≈1.6GB float64
    （再加掩膜/抽取拷贝），远超 side² 预算窗口。修复后块同时受列约束
    （块面积 ≤ side²）。本测试以 side=4 + 40 列宽栅格钉死该不变量：
    未修复代码的单块为 4×40=160 > 16。
    """
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from shapely.geometry import mapping, Polygon

    import app.lib.geo_analysis.raster_ops as ops
    import app.lib.geo_raster as gr

    h, w = 8, 40                      # 宽 zone：列数远超预算边长
    data = np.arange(h * w, dtype="float64").reshape(h, w)
    raster_path = str(tmp_path / "wide.tif")
    transform = from_origin(100, 200, 2, 3)   # x∈[100,180], y∈[176,200]
    with rasterio.open(raster_path, "w", driver="GTiff", height=h, width=w,
                       count=1, dtype="float64", crs="EPSG:3857",
                       transform=transform, nodata=-9999.0) as dst:
        dst.write(data, 1)
    poly = Polygon([(100, 176), (180, 176), (180, 200), (100, 200)])
    fc = {"type": "FeatureCollection", "crs": "EPSG:3857",
          "features": [{"type": "Feature", "geometry": mapping(poly),
                        "properties": {}}]}

    side = 4
    monkeypatch.setattr(
        "app.lib.geo_analysis.raster_grid.window_side_from_budget",
        lambda *a, **k: side)

    reads: list = []
    orig_reader = gr.RasterSource.reader

    def _spy_reader(self):
        r = orig_reader(self)
        orig_rw = r.read_window

        def _wrapped(window, *a, **k):
            reads.append(tuple(int(v) for v in window))
            return orig_rw(window, *a, **k)

        r.read_window = _wrapped
        return r

    monkeypatch.setattr(gr.RasterSource, "reader", _spy_reader)

    (row,) = ops.zonal_statistics(
        fc, raster_path, stats=["count", "sum", "mean", "min", "max", "std"])
    assert reads, "no windowed reads recorded"
    for col_off, row_off, bw, bh in reads:
        assert bw * bh <= side * side, (
            f"read block {bw}×{bh}={bw * bh} cells exceeds budget window "
            f"side²={side * side}（宽 zone 未按列分块？）")
    # 统计口径不受分块形状影响
    assert row["count"] == h * w
    assert row["sum"] == pytest.approx(float(data.sum()), rel=1e-12)
    assert row["mean"] == pytest.approx(float(data.mean()), rel=1e-12)
    assert row["min"] == pytest.approx(float(data.min()))
    assert row["max"] == pytest.approx(float(data.max()))
