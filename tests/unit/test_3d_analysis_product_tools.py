"""G3D-V2-C 三维分析产品工具接线 conformance（viewshed 几何 / cut-fill 土方）。

覆盖（ADR-0276）：

- viewshed_analysis include_geometry=True：GeoJSON 可视面（Polygon、
  cell-edge 对齐、observer 属性随行）+ visibility_geometry 披露
  （attribution/coverage/effective_resolution）；
- **stats 零漂移**：include_geometry True/False 两模式 visible_fraction/
  visible_area_m2/visible_cells/meta 逐位一致（#1829 stats 口径不变量）；
- cut_fill_volume_analysis：constant/mean 基准 stats（解析闭式对账）+
  site/挖方/填方分区 GeoJSON + 证据块（algorithm/tool/assumptions）；
- 垂直基准治理：override datum 混算类型化拒绝；ft 折算；
- 负例：polygon 全在栅格外、mean 带 base_z、constant 缺 base_z、
  nodata 超阈。
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

pytestmark = pytest.mark.unit


def _write_tif(path, arr, transform, crs=None, nodata=None):
    with rasterio.open(
        str(path),
        "w",
        driver="GTiff",
        height=arr.shape[0],
        width=arr.shape[1],
        count=1,
        dtype="float64",
        crs=crs,
        transform=Affine(*transform),
        nodata=nodata,
    ) as dst:
        dst.write(arr.astype("float64"), 1)


@pytest.fixture()
def terrain_tools(monkeypatch):
    from app.tools.registry import ToolRegistry
    from app.tools import terrain_analysis as ta

    monkeypatch.setattr(ta, "validate_data_path", lambda p: str(p))
    registry = ToolRegistry()
    ta.register_terrain_tools(registry)
    return registry


def _flat_tif(tmp_path, n=20, z=10.0, cell=10.0, crs="EPSG:32650"):
    """平 DEM：全域可见（无遮挡），几何 = 圆盘 ∩ 栅格。"""
    dem = np.full((n, n), z)
    tf = (cell, 0.0, 0.0, 0.0, -cell, float(n * cell))
    path = tmp_path / "flat.tif"
    _write_tif(path, dem, tf, crs=crs)
    return path


# ── viewshed_analysis 几何输出 ─────────────────────────────────────


def test_viewshed_tool_geometry_output_and_stats_zero_drift(
    tmp_path, terrain_tools
):
    path = _flat_tif(tmp_path)
    fn = terrain_tools._tools["viewshed_analysis"]
    center = 100.0
    with_geom = fn(
        str(path), observer_x=center, observer_y=center,
        max_distance=60.0, include_geometry=True,
    )
    without = fn(
        str(path), observer_x=center, observer_y=center,
        max_distance=60.0, include_geometry=False,
    )
    # stats 零漂移（include_geometry 只增几何，不动统计）
    for key in ("visible_fraction", "visible_area_m2", "visible_cells"):
        assert with_geom[key] == without[key], key
    assert with_geom["meta"] == without["meta"]

    fc = with_geom["geojson"]
    assert fc["type"] == "FeatureCollection"
    assert fc["features"], "flat DEM within max_distance must yield polygons"
    feat = fc["features"][0]
    assert feat["geometry"]["type"] == "Polygon"
    assert feat["properties"]["kind"] == "visibility"
    assert feat["properties"]["observer_x"] == center
    # 平 DEM：max_distance=60 → 可见像元都在盘内；几何归因必须完整
    geo = with_geom["visibility_geometry"]
    assert geo["geometry_attribution_fraction"] == 1.0
    cell_area = 100.0
    attributed = geo["attributed_visible_area_m2"]
    assert abs(attributed - with_geom["visible_cells"] * cell_area) < 1e-6
    assert geo["effective_resolution_m"] == [10.0, 10.0]


def test_viewshed_tool_geometry_excludes_nodata(tmp_path, terrain_tools):
    dem = np.full((16, 16), 10.0)
    dem[6:9, 6:9] = -9999.0  # 观察点旁 nodata 块
    tf = (10.0, 0.0, 0.0, 0.0, -10.0, 160.0)
    path = tmp_path / "hole.tif"
    _write_tif(path, dem, tf, crs="EPSG:32650", nodata=-9999.0)
    fn = terrain_tools._tools["viewshed_analysis"]
    res = fn(str(path), observer_x=30.0, observer_y=30.0, max_distance=5000.0)
    fc = res["geojson"]
    # nodata 像元不可声称 → 可视面以洞或边界让开 3×3 块
    assert res["visibility_geometry"]["valid_cells"] == 16 * 16 - 9
    coords = fc["features"][0]["geometry"]["coordinates"]
    xs_all, ys_all = [], []
    for ring in coords:
        for x, y in ring:
            xs_all.append(x)
            ys_all.append(y)
    # 洞矩形 [60,90]×[70,100] 应有顶点出现（cell-edge：60..90）
    assert 60.0 in xs_all and 90.0 in xs_all


# ── cut_fill_volume_analysis ───────────────────────────────────────


def _vol_tif(tmp_path, dem, cell=10.0, crs="EPSG:32650"):
    n = dem.shape[0]
    tf = (cell, 0.0, 0.0, 0.0, -cell, float(n * cell))
    path = tmp_path / "vol.tif"
    _write_tif(path, dem, tf, crs=crs)
    return path


def test_cut_fill_tool_constant_base_closed_form(tmp_path, terrain_tools):
    """平 DEM z=10，base=8，全域 20×20：cut = 2 × 40000 m² = 80000 m³。"""
    dem = np.full((20, 20), 10.0)
    path = _vol_tif(tmp_path, dem)
    fn = terrain_tools._tools["cut_fill_volume_analysis"]
    ring = [[0.0, 200.0], [200.0, 200.0], [200.0, 0.0], [0.0, 0.0], [0.0, 200.0]]
    res = fn(str(path), polygon=ring, reference="constant", base_z=8.0)
    assert res["success"] is True
    assert res["cut_volume_m3"] == pytest.approx(80000.0, abs=1e-4)
    assert res["fill_volume_m3"] == pytest.approx(0.0, abs=1e-9)
    assert res["net_volume_m3"] == pytest.approx(80000.0, abs=1e-4)
    # GeoJSON：site 面 + cut 分区（fill 分区为 0 → 无 feature）
    kinds = [f["properties"]["kind"] for f in res["geojson"]["features"]]
    assert "cut_fill_site" in kinds
    assert "cut_region" in kinds
    assert "fill_region" not in kinds
    # 证据块
    ev = res.get("scientific_evidence") or res.get("evidence")
    assert ev is not None
    assert ev["algorithm"] == "terrain.cut_fill_volume"


def test_cut_fill_tool_mean_base_balanced(tmp_path, terrain_tools):
    cols = np.arange(20)
    dem = 0.1 * np.tile(cols, (20, 1)).astype(float)  # 0..1.9 斜面
    path = _vol_tif(tmp_path, dem)
    fn = terrain_tools._tools["cut_fill_volume_analysis"]
    ring = [[0.0, 200.0], [200.0, 200.0], [200.0, 0.0], [0.0, 0.0], [0.0, 200.0]]
    res = fn(str(path), polygon=ring, reference="mean")
    assert res["net_volume_m3"] == pytest.approx(0.0, abs=1e-6)
    assert res["cut_cells"] + res["fill_cells"] == 400
    kinds = {f["properties"]["kind"] for f in res["geojson"]["features"]}
    assert "cut_region" in kinds and "fill_region" in kinds


def test_cut_fill_tool_polygon_subset_region(tmp_path, terrain_tools):
    """掩膜只取右半（x>100）：线性面体积可加——与全域对账。"""
    cols = np.arange(20)
    dem = 0.2 * np.tile(cols, (20, 1)).astype(float)
    path = _vol_tif(tmp_path, dem)
    fn = terrain_tools._tools["cut_fill_volume_analysis"]
    full = fn(
        str(path),
        polygon=[[0.0, 200.0], [200.0, 200.0], [200.0, 0.0], [0.0, 0.0], [0.0, 200.0]],
        reference="constant",
        base_z=0.0,
    )
    right = fn(
        str(path),
        polygon=[[100.0, 200.0], [200.0, 200.0], [200.0, 0.0], [100.0, 0.0], [100.0, 200.0]],
        reference="constant",
        base_z=0.0,
    )
    left = fn(
        str(path),
        polygon=[[0.0, 200.0], [100.0, 200.0], [100.0, 0.0], [0.0, 0.0], [0.0, 200.0]],
        reference="constant",
        base_z=0.0,
    )
    # 分区可加（不重不漏）：左 + 右 = 全域
    assert full["cut_volume_m3"] == pytest.approx(
        left["cut_volume_m3"] + right["cut_volume_m3"], abs=1e-6
    )
    assert full["usable_cells"] == left["usable_cells"] + right["usable_cells"]
    # 半域边界像元披露：左边缘 20 + 上下右三边贴栅格界（30 − 角重复 4）= 56
    assert right["meta"]["boundary_cells"] == 56


def test_cut_fill_tool_vertical_datum_governance(tmp_path, terrain_tools):
    dem = np.full((10, 10), 10.0)
    path = _vol_tif(tmp_path, dem)
    fn = terrain_tools._tools["cut_fill_volume_analysis"]
    ring = [[0.0, 100.0], [100.0, 100.0], [100.0, 0.0], [0.0, 0.0], [0.0, 100.0]]
    with pytest.raises(Exception) as exc_info:
        fn(
            str(path), polygon=ring, reference="constant", base_z=8.0,
            vertical_datum="ellipsoid_height",
            override_vertical_datum="geoid_height_egm96",
        )
    assert "VERTICAL_REFERENCE_MISMATCH" in str(exc_info.value) or (
        "mixing" in str(exc_info.value).lower()
    )


def test_cut_fill_tool_ft_units(tmp_path, terrain_tools):
    dem = np.full((10, 10), 10.0)  # ft
    path = _vol_tif(tmp_path, dem)
    fn = terrain_tools._tools["cut_fill_volume_analysis"]
    ring = [[0.0, 100.0], [100.0, 100.0], [100.0, 0.0], [0.0, 0.0], [0.0, 100.0]]
    res = fn(
        str(path), polygon=ring, reference="constant", base_z=8.0, z_units="ft"
    )
    assert res["cut_volume_m3"] == pytest.approx(
        2.0 * 0.3048 * 10000, abs=1e-4
    )
    assert res["meta"]["z_unit_transformations"]


def test_cut_fill_tool_negative_paths(tmp_path, terrain_tools):
    dem = np.full((10, 10), 10.0)
    path = _vol_tif(tmp_path, dem)
    fn = terrain_tools._tools["cut_fill_volume_analysis"]
    outside = [[5000.0, 5100.0], [5200.0, 5100.0], [5200.0, 5000.0], [5000.0, 5000.0], [5000.0, 5100.0]]
    with pytest.raises(Exception):
        fn(str(path), polygon=outside, reference="constant", base_z=1.0)
    with pytest.raises(Exception):
        fn(str(path), polygon=[[0, 0], [1, 0], [0, 0]], reference="mean", base_z=3.0)
    with pytest.raises(Exception):
        fn(
            str(path),
            polygon=[[0.0, 100.0], [100.0, 100.0], [100.0, 0.0], [0.0, 0.0], [0.0, 100.0]],
            reference="constant",
        )
    dem2 = np.full((10, 10), np.nan)
    path2 = _vol_tif(tmp_path, dem2)
    with pytest.raises(Exception):
        fn(
            str(path2),
            polygon=[[0.0, 100.0], [100.0, 100.0], [100.0, 0.0], [0.0, 0.0], [0.0, 100.0]],
            reference="constant",
            base_z=1.0,
        )


def test_cut_fill_tool_registered_conformance(terrain_tools):
    """G03 对账面：工具注册元数据与输出语义声明一致（geojson_fc/bounded）。"""
    meta = terrain_tools._metadata["cut_fill_volume_analysis"]
    assert meta["output_semantic_type"] == "geojson_fc"
    assert meta["deterministic"] is True
