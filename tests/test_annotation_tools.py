"""Round 8: 测量 + 标注工具"""
import math

import pytest

from app.tools.registry import ToolRegistry
from app.tools.annotation import (
    register_annotation_tools,
    polyline_length_km,
    spherical_polygon_area_km2,
    _validate_coords,
)


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_annotation_tools(r)
    return r


# ─── geometry primitives ────────────────────────────────────────────

def test_polyline_length_zero_for_single_point():
    assert polyline_length_km([[0, 0]]) == 0.0


def test_polyline_length_beijing_shanghai_within_1pct_of_truth():
    # Truth ~1067-1080 km (great circle 北京 -> 上海)
    d = polyline_length_km([[116.407, 39.904], [121.473, 31.231]])
    assert 1050 < d < 1090


def test_polyline_length_chains_segments():
    a = polyline_length_km([[0, 0], [1, 0]])
    b = polyline_length_km([[1, 0], [2, 0]])
    chained = polyline_length_km([[0, 0], [1, 0], [2, 0]])
    assert abs(chained - (a + b)) < 1e-6


def test_polygon_area_1deg_square_at_equator():
    area = spherical_polygon_area_km2([[0, 0], [1, 0], [1, 1], [0, 1]])
    # 1°×1° 在赤道 ~12363 km²
    assert 12000 < area < 12700


def test_polygon_area_handles_closed_ring():
    # 首尾重复点不影响结果
    closed = spherical_polygon_area_km2([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]])
    open_ = spherical_polygon_area_km2([[0, 0], [1, 0], [1, 1], [0, 1]])
    assert abs(closed - open_) < 1.0


def test_polygon_area_orientation_invariant():
    cw = spherical_polygon_area_km2([[0, 0], [1, 0], [1, 1], [0, 1]])
    ccw = spherical_polygon_area_km2([[0, 0], [0, 1], [1, 1], [1, 0]])
    assert abs(cw - ccw) < 1e-3


def test_validate_coords_rejects_short():
    assert _validate_coords([[0, 0]], 2) is not None


def test_validate_coords_rejects_out_of_range():
    assert _validate_coords([[200, 0], [0, 0]], 2) is not None
    assert _validate_coords([[0, 100], [0, 0]], 2) is not None


def test_validate_coords_accepts_valid():
    assert _validate_coords([[116.4, 39.9], [116.5, 40.0]], 2) is None


# ─── tool dispatch ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_measure_distance_emits_polyline_command(registry):
    out = await registry.dispatch("measure_distance", {"coordinates": [[0, 0], [1, 0]]})
    assert out["success"]
    assert out["command"] == "draw_measurement"
    assert out["params"]["shape"] == "polyline"
    assert out["distance_km"] > 0
    assert out["params"]["coordinates"] == [[0, 0], [1, 0]]
    assert "label" in out["params"]


@pytest.mark.asyncio
async def test_measure_distance_uses_meters_below_1km(registry):
    out = await registry.dispatch("measure_distance", {"coordinates": [[0, 0], [0.001, 0]]})
    # ~111m
    assert "m" in out["summary"] and "km" not in out["summary"]


@pytest.mark.asyncio
async def test_measure_distance_rejects_single_point(registry):
    out = await registry.dispatch("measure_distance", {"coordinates": [[0, 0]]})
    assert "error" in out or out.get("code") == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_measure_area_emits_polygon_command(registry):
    out = await registry.dispatch("measure_area",
                                  {"coordinates": [[0, 0], [1, 0], [1, 1], [0, 1]]})
    assert out["success"]
    assert out["params"]["shape"] == "polygon"
    assert out["area_km2"] > 12000


@pytest.mark.asyncio
async def test_measure_area_rejects_too_few_points(registry):
    out = await registry.dispatch("measure_area", {"coordinates": [[0, 0], [1, 0]]})
    assert "error" in out or out.get("code") == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_add_marker_emits_command(registry):
    out = await registry.dispatch("add_marker",
                                  {"longitude": 116.4, "latitude": 39.9, "label": "天安门"})
    assert out["success"]
    assert out["command"] == "add_marker"
    assert out["params"]["longitude"] == 116.4
    assert out["params"]["latitude"] == 39.9
    assert out["params"]["label"] == "天安门"
    # 默认颜色
    assert out["params"]["color"] == "#ef4444"


@pytest.mark.asyncio
async def test_add_marker_rejects_out_of_range(registry):
    out = await registry.dispatch("add_marker", {"longitude": 200, "latitude": 100})
    assert "error" in out or out.get("code") == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_clear_annotations_emits_command(registry):
    out = await registry.dispatch("clear_annotations", {})
    assert out["success"]
    assert out["command"] == "clear_annotations"


def test_all_four_tools_registered(registry):
    names = [s["function"]["name"] for s in registry._schemas]
    assert set(names) == {"measure_distance", "measure_area", "add_marker", "clear_annotations"}
