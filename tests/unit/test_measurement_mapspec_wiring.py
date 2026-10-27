"""G3D03 M5 接线：三维量测工具产物 → MapSpec 图层（ADR-0274 §后果）。

验证（复用既有 dispatch → converter 通道，不建第二渲染状态）：

- 四个量测工具 payload 按 ``_author_display_result`` 的真实形状
  （{geojson, algorithm, result_ref, ...}）经
  ``convert_analysis_to_mapspec_layer`` 得到合法 ``MapSpecLayer``；
- 几何类别：profile/LOS/surface_distance → line，surface_area → polygon；
- LOS 遮挡 Point 与剖面 LineString 同 FC 消费（line 为主类别）；
- 组装进 ``MapSpecDocument`` 后 schema 级有效（2D/3D 同感：GeoJSON
  3D 坐标对 MapLibre 透明）。
"""

from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from app.lib.cartography.mapspec_schema import MapSpecDocument, MapSpecLayer
from app.services.analysis_cartography_converter import (
    convert_analysis_to_mapspec_layer,
    is_analysis_result,
)

pytestmark = pytest.mark.unit


def _write_tif(path, arr, transform, crs=None):
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
    ) as dst:
        dst.write(arr.astype("float64"), 1)


def _dispatch_analysis_payload(tool_name: str, result: dict) -> dict:
    """复刻 tool_dispatch_service._author_display_result 的 analysis_payload
    （含 #688 profile 键 —— review P3-4：覆盖 descriptor/profile 零遍历路径）。"""
    fc = result["geojson"]
    geometry_types = sorted({f["geometry"]["type"] for f in fc["features"]})
    return {
        "geojson": fc,
        "algorithm": tool_name,
        "result_ref": "ref:geojson-test",
        "legend_spec": result.get("legend_spec"),
        "type_hint": result.get("type_hint"),
        "metadata": None,
        "profile": {
            "featureCount": len(fc["features"]),
            "geometryTypes": geometry_types,
        },
    }


@pytest.fixture()
def measurement_payloads(tmp_path, monkeypatch):
    from app.tools.registry import ToolRegistry
    from app.tools import terrain_analysis as ta

    monkeypatch.setattr(ta, "validate_data_path", lambda p: str(p))
    registry = ToolRegistry()
    ta.register_terrain_tools(registry)

    n = 12
    dem = 0.5 * np.add.outer(np.arange(n), np.arange(n)).astype(float)
    tf = (1.0, 0.0, 0.0, 0.0, -1.0, float(n))
    path = tmp_path / "plane.tif"
    _write_tif(path, dem, tf, crs="EPSG:32650")

    wall = np.zeros((20, 20))
    wall[:, 10:14] = 30.0
    wall_path = tmp_path / "wall.tif"
    _write_tif(wall_path, wall, (1.0, 0.0, 0.0, 0.0, -1.0, 20.0), crs="EPSG:32650")

    results = {
        "terrain_profile": registry._tools["terrain_profile"](
            str(path), [[1.5, 0.5], [9.5, 0.5]]
        ),
        "line_of_sight_analysis": registry._tools["line_of_sight_analysis"](
            str(wall_path), 1.5, 0.5, 18.5, 0.5, observer_height=2, target_height=2
        ),
        "surface_distance_analysis": registry._tools["surface_distance_analysis"](
            str(path), [[1.5, 0.5], [9.5, 0.5]]
        ),
        "surface_area_analysis": registry._tools["surface_area_analysis"](
            str(path), [[0.1, 0.1], [11.9, 0.1], [11.9, 11.9], [0.1, 11.9]]
        ),
    }
    return {
        name: _dispatch_analysis_payload(name, result)
        for name, result in results.items()
    }


@pytest.mark.parametrize(
    "tool_name,expected_category",
    [
        ("terrain_profile", "line"),
        ("line_of_sight_analysis", "line"),
        ("surface_distance_analysis", "line"),
        ("surface_area_analysis", "fill"),
    ],
)
def test_measurement_payload_to_mapspec_layer(
    measurement_payloads, tool_name, expected_category
):
    payload = measurement_payloads[tool_name]
    assert is_analysis_result(payload) is True
    layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(dict(payload))
    assert isinstance(layer, dict) and layer.get("id"), (tool_name, warnings)
    assert inline_geojson is not None
    # schema 级校验：产物是合法 MapSpecLayer
    validated = MapSpecLayer.model_validate(layer)
    assert validated.id == layer["id"]
    assert expected_category in (validated.type or ""), (
        tool_name,
        validated.type,
        warnings,
    )
    # 内联 GeoJSON 与工具产物同源（dispatch ref 化前的同一 FC）
    assert inline_geojson["type"] == "FeatureCollection"
    assert len(inline_geojson["features"]) >= 1


def test_los_layer_keeps_obstruction_point(measurement_payloads):
    payload = measurement_payloads["line_of_sight_analysis"]
    layer, inline_geojson, _ = convert_analysis_to_mapspec_layer(dict(payload))
    kinds = {f["properties"].get("kind") for f in inline_geojson["features"]}
    assert "sight_line" in kinds and "terrain_profile" in kinds
    assert "obstruction" in kinds
    ob = [
        f
        for f in inline_geojson["features"]
        if f["properties"].get("kind") == "obstruction"
    ][0]
    assert ob["geometry"]["type"] == "Point"
    assert len(ob["geometry"]["coordinates"]) == 3  # 3D position


def test_measurement_layers_assemble_into_mapspec_document(measurement_payloads):
    layers = []
    for tool_name, payload in measurement_payloads.items():
        layer, _, _ = convert_analysis_to_mapspec_layer(
            dict(payload), layer={"id": f"lyr-{tool_name}"[:40]}
        )
        layers.append(layer)
    doc = MapSpecDocument.model_validate(
        {
            "version": "1.6",
            "metadata": {"name": "g3d03-measurements"},
            "layers": layers,
            "sources": {},
        }
    )
    assert len(doc.layers) == 4
    layer_types = {lyr.type for lyr in doc.layers}
    assert any("line" in (t or "") for t in layer_types)
    assert "fill" in layer_types
