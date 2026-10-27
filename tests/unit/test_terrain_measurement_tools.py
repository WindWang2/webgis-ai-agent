"""G3D03 三维量测工具接线 conformance（app/tools/terrain_analysis.py 增量）。

覆盖（ADR-0274）：

- 四个工具的科学证据块（algorithm/tool/inputs/assumptions/transformations）；
- GeoJSON 产物形状（3D LineString / Point 遮挡 / Polygon 面 —— dispatch
  ``result["geojson"]`` 通道的 ref 化前提）；
- 垂直基准治理：datum 不一致类型化拒绝 / observer_z 同 datum 放行 + 旗标；
- ft 折算披露、地理 CRS cos-lat + 椭球测地参考量披露；
- 负例：契约词表违规、nodata 政策、零长/越界几何、polygon 全在栅格外。
"""

from __future__ import annotations

import math

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


def _plane_tif(tmp_path, n=12, s=0.5, crs="EPSG:32650"):
    dem = s * np.add.outer(np.arange(n), np.arange(n)).astype(float)
    tf = (1.0, 0.0, 0.0, 0.0, -1.0, float(n))
    path = tmp_path / "plane.tif"
    _write_tif(path, dem, tf, crs=crs)
    return path


def test_terrain_profile_tool_evidence_and_geojson(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    res = terrain_tools._tools["terrain_profile"](str(path), [[1.5, 0.5], [9.5, 0.5]])
    assert res["success"] is True
    assert res["horizontal_m"] == pytest.approx(8.0, abs=1e-4)
    assert res["surface_distance_m"] == pytest.approx(8.0 * math.sqrt(1.25), abs=1e-4)
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.profile"
    assert ev["tool"] == "terrain_profile"
    assert ev["inputs"]["artifact_type"] == "terrain_surface"
    assert ev["reproducibility"]["deterministic"] is True
    assert ev["parameters_applied"]["vertical_datum"] == "unknown"
    assert any("metric cell sizes" in t for t in ev["transformations_applied"])
    fc = res["geojson"]
    assert fc["type"] == "FeatureCollection"
    geom = fc["features"][0]["geometry"]
    assert geom["type"] == "LineString"
    assert len(geom["coordinates"][0]) == 3  # 3D position
    assert fc["features"][0]["properties"]["kind"] == "terrain_profile"
    # 未知 datum 的诚实披露进入 quality_flags
    assert "vertical_datum_unknown" in res["quality_flags"]


def test_terrain_profile_tool_json_vertices_and_contract(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    res = terrain_tools._tools["terrain_profile"](str(path), "[[1.5, 0.5], [9.5, 0.5]]")
    assert res["horizontal_m"] == pytest.approx(8.0, abs=1e-4)
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        terrain_tools._tools["terrain_profile"](
            str(path), [[1.5, 0.5], [9.5, 0.5]], vertical_datum="navd88"
        )
    with pytest.raises(ValueError, match="at least 2"):
        terrain_tools._tools["terrain_profile"](str(path), [[1.5, 0.5]])


def test_line_of_sight_tool_evidence_and_geojson(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    res = terrain_tools._tools["line_of_sight_analysis"](
        str(path), 1.5, 0.5, 9.5, 0.5, observer_height=5, target_height=2
    )
    assert res["visible"] is True
    assert res["min_clearance_m"] == pytest.approx(2.0, abs=1e-4)
    assert res["curvature_model"] == "none"
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.line_of_sight"
    assert any("对称" in a or "symmetric" in a for a in ev["assumptions"])
    kinds = {f["properties"]["kind"] for f in res["geojson"]["features"]}
    assert kinds == {"sight_line", "terrain_profile"}

    # 遮挡：高墙 DEM → obstruction Point 进 GeoJSON
    dem = np.zeros((20, 20))
    dem[:, 10:14] = 30.0
    wall = tmp_path / "wall.tif"
    _write_tif(wall, dem, (1.0, 0.0, 0.0, 0.0, -1.0, 20.0), crs="EPSG:32650")
    res2 = terrain_tools._tools["line_of_sight_analysis"](
        str(wall), 1.5, 0.5, 18.5, 0.5, observer_height=2, target_height=2
    )
    assert res2["visible"] is False
    assert res2["obstruction"]["x"] == pytest.approx(10.5, abs=1e-6)
    kinds2 = {
        f["properties"]["kind"]: f["geometry"]["type"]
        for f in res2["geojson"]["features"]
    }
    assert kinds2["obstruction"] == "Point"
    assert kinds2["sight_line"] == "LineString"
    # 曲率模型披露
    res3 = terrain_tools._tools["line_of_sight_analysis"](
        str(wall), 1.5, 0.5, 18.5, 0.5, curvature="earth_refracted"
    )
    assert "0.1307" in res3["curvature_model"]
    assert res3["meta"]["curvature"]["refraction_coefficient"] == pytest.approx(0.1307)


def test_line_of_sight_vertical_datum_governance(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    from app.lib.gis.scientific_errors import VerticalReferenceMismatch

    with pytest.raises(VerticalReferenceMismatch) as ei:
        terrain_tools._tools["line_of_sight_analysis"](
            str(path),
            1.5,
            0.5,
            9.5,
            0.5,
            observer_z=10.0,
            vertical_datum="geoid_height_egm96",
            override_vertical_datum="ellipsoid_height",
        )
    assert ei.value.scientific_code == "VERTICAL_REFERENCE_MISMATCH"
    # 同 datum 放行 + 披露旗标
    res = terrain_tools._tools["line_of_sight_analysis"](
        str(path),
        1.5,
        0.5,
        9.5,
        0.5,
        observer_z=10.0,
        vertical_datum="geoid_height_egm96",
        override_vertical_datum="geoid_height_egm96",
    )
    assert "absolute_z_override_applied" in res["quality_flags"]
    assert res["eye_z_observer_m"] == pytest.approx(12.0, abs=1e-4)  # 10 + 2


def test_surface_distance_tool_convergence_and_geodesic(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    res = terrain_tools._tools["surface_distance_analysis"](
        str(path), [[1.5, 0.5], [9.5, 0.5]]
    )
    assert res["surface_distance_m"] == pytest.approx(8.0 * math.sqrt(1.25), abs=1e-4)
    assert res["surface_distance_m"] >= res["horizontal_m"]
    assert res["convergence"]["delta_rel"] == 0.0
    assert res["geodesic_horizontal_m"] is None  # 投影 CRS 无测地参考量
    ev = res["scientific_evidence"]
    assert ev["algorithm"] == "terrain.surface_distance"
    assert res["geojson"]["features"][0]["properties"]["kind"] == "surface_distance"

    # 地理 CRS：cos-lat 披露 + 椭球测地参考量
    n = 12
    dem = 0.5 * np.add.outer(np.arange(n), np.arange(n)).astype(float)
    geo = tmp_path / "geo.tif"
    _write_tif(geo, dem, (0.001, 0.0, 10.0, 0.0, -0.001, 10.01), crs="EPSG:4326")
    res2 = terrain_tools._tools["surface_distance_analysis"](
        str(geo), [[10.0045, 10.0095], [10.0085, 10.0095]]
    )
    assert res2["geodesic_horizontal_m"] is not None
    # 网格米制 vs 椭球测地同量级（cos(lat≈10°) 近似良好，差 < 0.5%）
    rel_gap = (
        abs(res2["horizontal_m"] - res2["geodesic_horizontal_m"])
        / res2["geodesic_horizontal_m"]
    )
    assert rel_gap < 5e-3
    ev2 = res2["scientific_evidence"]
    assert any("cos(" in t for t in ev2["transformations_applied"])
    assert ev2["inputs"]["crs_class"] == "geographic"


def test_surface_distance_tool_ft_units(tmp_path, terrain_tools):
    n = 12
    dem_ft = (0.5 * np.add.outer(np.arange(n), np.arange(n)).astype(float)) / 0.3048
    path = tmp_path / "plane_ft.tif"
    _write_tif(path, dem_ft, (1.0, 0.0, 0.0, 0.0, -1.0, float(n)), crs="EPSG:32650")
    res = terrain_tools._tools["surface_distance_analysis"](
        str(path), [[1.5, 0.5], [9.5, 0.5]], z_units="ft"
    )
    assert res["surface_distance_m"] == pytest.approx(8.0 * math.sqrt(1.25), abs=1e-4)
    assert any(
        "0.3048" in t for t in res["scientific_evidence"]["transformations_applied"]
    )
    with pytest.raises(ValueError, match="parameter_contract_violation"):
        terrain_tools._tools["surface_distance_analysis"](
            str(path), [[1.5, 0.5], [9.5, 0.5]], z_units="yards"
        )


def test_surface_area_tool_polygon_and_nodata(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    res = terrain_tools._tools["surface_area_analysis"](
        str(path), [[0.1, 0.1], [11.9, 0.1], [11.9, 11.9], [0.1, 11.9]]
    )
    assert res["planar_area_m2"] == pytest.approx(121.0, abs=1e-4)
    assert res["surface_area_m2"] == pytest.approx(121.0 * math.sqrt(1.5), abs=1e-4)
    assert res["roughness_ratio"] >= 1.0
    assert res["geojson"]["features"][0]["geometry"]["type"] == "Polygon"

    # nodata 大洞（quad 失效比例 ~0.36）：显式收紧阈值 → 类型化失败
    dem = 0.5 * np.add.outer(np.arange(12), np.arange(12)).astype(float)
    dem[:, 8:12] = np.nan
    holed = tmp_path / "holed.tif"
    _write_tif(
        holed,
        dem,
        (1.0, 0.0, 0.0, 0.0, -1.0, 12.0),
        crs="EPSG:32650",
        nodata=float("nan"),
    )
    with pytest.raises(Exception) as ei:
        terrain_tools._tools["surface_area_analysis"](
            str(holed),
            [[0.1, 0.1], [11.9, 0.1], [11.9, 11.9], [0.1, 11.9]],
            max_invalid_fraction=0.3,
        )
    assert (
        "NO_VALID_OBSERVATIONS" in str(ei.value)
        or ei.value.__class__.__name__ == "NoValidObservations"
    )
    res2 = terrain_tools._tools["surface_area_analysis"](
        str(holed),
        [[0.1, 0.1], [11.9, 0.1], [11.9, 11.9], [0.1, 11.9]],
        max_invalid_fraction=0.9,
    )
    assert any(q.startswith("invalid_quad_fraction:") for q in res2["quality_flags"])


def test_measurement_vocab_parity_pinned():
    """review P2-1：契约 enum 与 lib 词表必须钉死（双写漂移即红）。"""
    from app.lib.geo_analysis.measurements import (
        VERTICAL_DATUM_VOCABULARY,
        Z_UNIT_VOCABULARY,
    )
    from app.lib.gis.parameter_contracts import get_parameter_contract_registry

    reg = get_parameter_contract_registry()
    datum_sorted = sorted(VERTICAL_DATUM_VOCABULARY)
    units_sorted = sorted(Z_UNIT_VOCABULARY)
    for cid in (
        "terrain_profile",
        "line_of_sight_analysis",
        "surface_distance_analysis",
        "surface_area_analysis",
    ):
        contract = reg.get(cid)
        assert contract is not None, cid
        enums = {p.name: p.enum_values for p in contract.parameters if p.type == "enum"}
        assert sorted(enums["vertical_datum"]) == datum_sorted, cid
        assert sorted(enums["z_units"]) == units_sorted, cid
    los = reg.get("line_of_sight_analysis")
    los_enums = {p.name: p.enum_values for p in los.parameters if p.type == "enum"}
    assert sorted(los_enums["override_vertical_datum"]) == datum_sorted
    assert los_enums["curvature"] == ["none", "earth", "earth_refracted"]
    for cid in ("terrain_profile", "surface_distance_analysis"):
        c = reg.get(cid)
        assert {p.name: p.enum_values for p in c.parameters if p.type == "enum"}[
            "nodata_policy"
        ] == ["error", "skip"], cid


def test_measurement_tool_negative_paths(tmp_path, terrain_tools):
    path = _plane_tif(tmp_path)
    # 无 DEM 文件 → 类型化读取错误
    from app.lib.geo_raster.reader import RasterReaderError

    with pytest.raises(RasterReaderError):
        terrain_tools._tools["terrain_profile"](
            str(tmp_path / "missing.tif"), [[1.5, 0.5], [9.5, 0.5]]
        )
    # 几何越界（error 政策类型化失败）
    from app.lib.gis.scientific_errors import NoValidObservations

    with pytest.raises(NoValidObservations):
        terrain_tools._tools["surface_distance_analysis"](
            str(path), [[1.5, 0.5], [45.5, 0.5]]
        )
    # polygon 完全在栅格外 → 无候选 quad
    with pytest.raises(NoValidObservations, match="candidate quads"):
        terrain_tools._tools["surface_area_analysis"](
            str(path), [[100.1, 100.1], [101.9, 100.1], [101.0, 101.9]]
        )
    # 顶点数上限（工具层护栏）
    many = [[1.5 + i * 0.01, 0.5] for i in range(300)]
    with pytest.raises(ValueError, match="too many points"):
        terrain_tools._tools["terrain_profile"](str(path), many)
