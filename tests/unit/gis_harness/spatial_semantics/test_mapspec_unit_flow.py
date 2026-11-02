"""M5 MapSpec/制图反馈 —— 密度口径 → legend_spec.unit 链路测试。

锁定（验收 Oracle #5）：分析工具输出的科学口径（density_unit=个/km²）经
create_thematic_map（user-wins unit 通道）写入 legend_spec.unit —— 前端
graduated 图例显示同一单位（前端侧由 vitest 锁定）；create_thematic_map
的量纲判定 crs 不再硬编码 4326（真实声明 CRS 进 DEGREE_LIKE_METRIC 检查）。
"""
from __future__ import annotations

import pytest

from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.cartography import register_cartography_tools
from app.tools.registry import ToolRegistry


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    register_cartography_tools(r)
    return r


def _density_zones_fc() -> dict:
    """两个成都区面（各 0.02°×0.02°）+ 点位 —— aggregate area 分母形态。"""
    zones = {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {"type": "Feature",
             "properties": {"district": "锦江区"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [104.05, 30.65], [104.07, 30.65],
                 [104.07, 30.67], [104.05, 30.67], [104.05, 30.65]]]}},
            {"type": "Feature",
             "properties": {"district": "青羊区"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [104.08, 30.65], [104.10, 30.65],
                 [104.10, 30.67], [104.08, 30.67], [104.08, 30.65]]]}},
        ],
    }
    points = {
        "type": "FeatureCollection",
        "crs": "EPSG:4326",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point",
                          "coordinates": [104.06 + 0.001 * i, 30.66]}}
            for i in range(6)
        ],
    }
    return points, zones


@pytest.mark.asyncio
async def test_density_unit_flows_to_legend_spec(registry) -> None:
    """S6 端到端：aggregate 密度口径 → thematic map unit → legend_spec。"""
    points, zones = _density_zones_fc()
    agg = await registry.dispatch("spatial_aggregate", {
        "points": points, "polygons": zones, "denominator_kind": "area"})
    assert agg["success"] is True
    density_unit = agg["spatial_semantics"]["density_unit"]
    assert density_unit == "个/km²"

    # Agent 把口径作为 unit 传入（user-wins 通道）→ MapSpec legend_spec
    out = await registry.dispatch("create_thematic_map", {
        "geojson": agg["data"], "field": "rate",
        "method": "equal_interval", "k": 2, "unit": density_unit,
    })
    assert "legend_spec" in out
    assert out["legend_spec"].get("unit") == "个/km²"


@pytest.mark.asyncio
async def test_count_density_without_denominator_no_fake_unit(registry) -> None:
    """纯计数（denominator_kind=count）不合成密度口径 —— 不虚构单位。"""
    points, zones = _density_zones_fc()
    agg = await registry.dispatch("spatial_aggregate", {
        "points": points, "polygons": zones})
    assert agg["success"] is True
    sem = agg["spatial_semantics"]
    assert sem.get("density_unit", "") == ""


@pytest.mark.asyncio
async def test_thematic_map_uses_declared_crs_for_measurement_check(
        registry) -> None:
    """G12 修复：投影声明的 CRS 进量纲检查（不再硬编码 4326）。

    声明 EPSG:32648（米）+ 字段值呈度级（≤360 带小数）+ 米制字段名 ——
    在真实投影 CRS 下不再误报 DEGREE_LIKE_METRIC（该检查只对地理 CRS
    有意义，measurement.py:528 白名单）。
    """
    gj = {
        "type": "FeatureCollection",
        "crs": "EPSG:32648",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [500000.5, 3600000.5]},
             "properties": {"dist_m": 120.75}},
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [500010.5, 3600010.5]},
             "properties": {"dist_m": 240.25}},
        ],
    }
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "dist_m", "method": "equal_interval", "k": 2})
    assert "legend_spec" in out
    checks = out.get("measurement_checks") or []
    assert not any(c.get("code") == "DEGREE_LIKE_METRIC" for c in checks), (
        "投影 CRS 下不应触发度级值冒充米制检查（crs 硬编码 4326 的回潮）"
    )
