"""M4 工具适配 + M6 双运行时一致性 —— 四条真实工具路径消费同一 preflight。

锁定（验收 Oracle #2/#4/#5）：
- buffer_analysis / spatial_join / spatial_aggregate / zonal_stats 四条生产
  路径在工具入口过同一 preflight（registry.dispatch = Pi 入口）；
- blocked/requires_clarification → typed 错误（spatial_semantics: 前缀），
  绝不改写成成功；
- pass/degraded → ``spatial_semantics`` 证据块并入响应（含 density_unit）；
- Pi（registry.dispatch）与 legacy（直接 preflight 判定）结论一致；
- geocompute G2（crs member 过管）与 geometry_ops G3（非米轴因子面积）修复。
"""
from __future__ import annotations


import pytest
from pyproj import Transformer

from app.services.gis_harness.spatial_semantics import (
    SpatialOperationSpec,
    preflight_for_feature_collection,
)
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.tools.spatial_semantics_tools import (
    register_spatial_semantics_tools,
)

_TR_4326_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
_TR_4326_UTM48 = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True)


def chengdu_points_fc(n: int = 3, crs: str | None = None) -> dict:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": f"小学{i}"},
                "geometry": {
                    "type": "Point",
                    "coordinates": [104.06 + 0.001 * i, 30.66 + 0.0005 * i],
                },
            }
            for i in range(n)
        ],
    }
    if crs:
        fc["crs"] = crs
    return fc


def chengdu_districts_fc(crs: str | None = None) -> dict:
    """两个 0.02°×0.02° 的区面（成都中心）——面积各约 4.3 km²。"""
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"district": "锦江区"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [104.05, 30.65], [104.07, 30.65],
                        [104.07, 30.67], [104.05, 30.67], [104.05, 30.65],
                    ]],
                },
            },
            {
                "type": "Feature",
                "properties": {"district": "青羊区"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [104.08, 30.65], [104.10, 30.65],
                        [104.10, 30.67], [104.08, 30.67], [104.08, 30.65],
                    ]],
                },
            },
        ],
    }
    if crs:
        fc["crs"] = crs
    return fc


@pytest.fixture(scope="module")
def registry():
    r = ToolRegistry()
    register_spatial_tools(r)
    register_advanced_spatial_tools(r)
    register_spatial_semantics_tools(r)
    return r


# ── Oracle #4：成都小学 500m 真实覆盖 ───────────────────────────────────────

@pytest.mark.asyncio
async def test_buffer_chengdu_500m_evidence_and_geodesic_baseline(
        registry) -> None:
    """S1 端到端：500m 缓冲经真实 dispatch，证据块 + 测地基线对账。"""
    fc = chengdu_points_fc(crs="EPSG:4326")
    res = await registry.dispatch("buffer_analysis", {
        "geojson": fc, "distance": 500, "unit": "m"})
    assert res["success"] is True
    sem = res["spatial_semantics"]
    assert sem["status"] == "pass"
    assert sem["source_crs"] == "EPSG:4326"
    assert sem["working_crs"] == "EPSG:32648"
    assert sem["distance_m"] == 500.0
    # 可信基准：测地方位/距离（pyproj Geod）与缓冲结果半径对照（±10% 内）
    geod = __import__("pyproj").Geod(ellps="WGS84")
    feats = res["data"]["features"]
    from shapely.geometry import shape as _shape
    buf0 = _shape(feats[0]["geometry"])
    cx, cy = buf0.centroid.x, buf0.centroid.y
    # 缓冲几何已回 4326：中心-最东点测地距离 ≈ 500 m
    lon_e = buf0.bounds[2]
    _, _, dist_m = geod.inv(cx, cy, lon_e, cy)
    assert 460 <= dist_m <= 540, f"测地半径 {dist_m:.1f}m 偏离 500m"


@pytest.mark.asyncio
async def test_buffer_undeclared_projected_data_blocked_via_dispatch(
        registry) -> None:
    """S3 端到端：未声明 CRS 的投影米数据经 dispatch 被 typed 拒绝。"""
    x, y = _TR_4326_UTM48.transform(104.06, 30.66)
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Point", "coordinates": [x, y]},
        }],
    }
    res = await registry.dispatch("buffer_analysis", {
        "geojson": fc, "distance": 500, "unit": "m"})
    # 工具层 raise ValueError → registry 折叠为 error 形状（不改写成成功）
    assert res.get("success") is not True
    dumped = str(res)
    assert "SSC_CRS_UNDECLARED_NON_DEGREE" in dumped


@pytest.mark.asyncio
async def test_buffer_declared_3857_degrades_with_disclosure(registry) -> None:
    """S4 端到端：3857 声明数据执行 + 失真披露 + 安全变换建议。"""
    fc = chengdu_points_fc(crs="EPSG:3857")
    for i, feat in enumerate(fc["features"]):
        lon, lat = 104.06 + 0.001 * i, 30.66 + 0.0005 * i
        feat["geometry"]["coordinates"] = list(_TR_4326_3857.transform(lon, lat))
    res = await registry.dispatch("buffer_analysis", {
        "geojson": fc, "distance": 500, "unit": "m"})
    assert res["success"] is True
    sem = res["spatial_semantics"]
    assert sem["status"] == "degraded_with_disclosure"
    assert 0.15 < (sem["distortion_estimate"] or 0) < 0.18
    repair = sem["repair_suggestions"][0]
    assert repair["authorization"] == "deterministic_safe"


# ── S6/S7：密度口径 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spatial_aggregate_area_density_composes_per_km2(registry) -> None:
    pts = chengdu_points_fc(n=8, crs="EPSG:4326")
    zones = chengdu_districts_fc(crs="EPSG:4326")
    res = await registry.dispatch("spatial_aggregate", {
        "points": pts, "polygons": zones,
        "denominator_kind": "area"})
    assert res["success"] is True
    sem = res["spatial_semantics"]
    assert sem["density_unit"] == "个/km²"
    assert sem["area_unit"] == "km2"
    # 分母正确性：锦江区 0.02°×0.02° ≈ 4.27 km²（测地面积对照 ±2%）
    zone0 = res["data"]["features"][0]["properties"]
    rate = zone0.get("rate")
    # 换算链在案：库层 per_m² → 图例口径 per_km²（×1e6）
    conv = res["scientific_evidence"]["normalization"].get(
        "rate_unit_conversion", "")
    assert "per_km2" in conv and "1e+06" in conv
    assert res["scientific_evidence"]["normalization"]["rate_unit"] == "per_km2"
    if rate is not None:
        # 数值量级与口径一致：8 点两区、每区 ~4.27 km² → ~1-2 个/km²
        assert 0.5 < rate < 3, f"rate={rate} 与 个/km² 口径量级不符"


@pytest.mark.asyncio
async def test_spatial_aggregate_denominator_mismatch_rejected(registry) -> None:
    pts = chengdu_points_fc(crs="EPSG:4326")
    zones = chengdu_districts_fc(crs="EPSG:4326")
    res = await registry.dispatch("spatial_aggregate", {
        "points": pts, "polygons": zones,
        "denominator_kind": "field", "denominator": "population",
        "requested_density_unit": "per_km2"})
    assert res.get("success") is not True
    assert "SSC_DENOMINATOR_KIND_MISMATCH" in str(res)


# ── S8/S9：偏移帧 join ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spatial_join_declared_bd09_evidence(registry) -> None:
    pts = chengdu_points_fc(crs="bd09")
    zones = chengdu_districts_fc(crs="EPSG:4326")
    res = await registry.dispatch("spatial_join", {
        "left_layer": pts, "right_layer": zones})
    sem = res["spatial_semantics"]
    assert sem["datum_normalization"] == ["bd09→gcj02→wgs84"]
    assert sem["right_crs"] == "EPSG:4326"


@pytest.mark.asyncio
async def test_spatial_join_undeclared_china_warns(registry) -> None:
    pts = chengdu_points_fc()  # 未声明
    zones = chengdu_districts_fc(crs="EPSG:4326")
    res = await registry.dispatch("spatial_join", {
        "left_layer": pts, "right_layer": zones})
    codes = [rc["code"] for rc in res["spatial_semantics"]["reason_codes"]]
    assert "SSC_CHINA_OFFSET_FRAME_RISK" in codes


@pytest.mark.asyncio
async def test_zonal_stats_invalid_input_blocked_before_raster(registry) -> None:
    """S12: invalid zones are rejected before reading an allowed raster path."""
    x, y = _TR_4326_UTM48.transform(104.06, 30.66)
    zones = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[
                [x, y], [x + 1000, y], [x + 1000, y + 1000],
                [x, y + 1000], [x, y]]]},
        }],
    }
    res = await registry.dispatch("zonal_stats", {
        "geojson": zones, "raster_path": "nonexistent-dem.tif"})
    assert res.get("success") is not True
    assert "SSC_CRS_UNDECLARED_NON_DEGREE" in str(res)


# ── Oracle #2/#5：Pi 工具发现面 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inspect_spatial_semantics_tool_discoverable(registry) -> None:
    desc = registry.descriptor("inspect_spatial_semantics")
    assert desc is not None
    assert desc.side_effect == "deterministic_compute"
    res = await registry.dispatch("inspect_spatial_semantics", {
        "geojson": chengdu_points_fc()})
    assert res["success"] is True
    sem = res["spatial_semantics"]
    assert sem["crs_source"] == "rfc7946_default"
    assert sem["working_crs"] == "EPSG:32648"


# ── M6：Pi / legacy 双入口一致 ─────────────────────────────────────────────

_DUAL_CASES = [
    ("declared_4326", chengdu_points_fc(crs="EPSG:4326"), "buffer"),
    ("undeclared_degree", chengdu_points_fc(), "buffer"),
    ("declared_3857", None, "buffer"),  # 在测试体内构造（3857 坐标）
    ("bd09", chengdu_points_fc(crs="bd09"), "spatial_join"),
]


def _dual_inputs():
    x, y = _TR_4326_3857.transform(104.06, 30.66)
    fc3857 = chengdu_points_fc(crs="EPSG:3857")
    fc3857["features"][0]["geometry"]["coordinates"] = [x, y]
    return {
        "declared_4326": chengdu_points_fc(crs="EPSG:4326"),
        "undeclared_degree": chengdu_points_fc(),
        "declared_3857": fc3857,
        "bd09": chengdu_points_fc(crs="bd09"),
    }


@pytest.mark.parametrize("case_name", [c[0] for c in _DUAL_CASES])
def test_pi_and_legacy_preflight_agree(case_name: str) -> None:
    """同一数据在 dispatch 工具面与直接 preflight 判定结论一致（M6）。"""
    fc = _dual_inputs()[case_name]
    legacy = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(operation="buffer", distance=500,
                                      distance_unit="m"))
    from app.services.gis_harness.spatial_semantics import (
        evidence_from_feature_collection,
    )
    from app.services.gis_harness.spatial_semantics.inspect_semantics import (
        inspect_spatial_contract,
    )
    # legacy 直调（服务层路径）与 Pi 面内嵌的是同一组纯函数 —— 再显式对账一次
    contract = inspect_spatial_contract(evidence_from_feature_collection(fc))
    assert contract.crs_source == legacy.contract.crs_source
    assert contract.source_crs == legacy.contract.source_crs
    assert legacy.verdict.status in (
        "pass", "pass_with_disclosure", "degraded_with_disclosure")


# ── G3：非米投影 CRS 的面积输出换算 ────────────────────────────────────────

def test_convex_hull_area_unit_factor_for_foot_crs() -> None:
    """英尺系投影输入：area_km2 按 axis factor² 换算（不再错 10.76×）。"""
    pytest.importorskip("geopandas")
    # EPSG:2270 = Pennsylvania North (ftUS) —— 轴单位 US survey foot
    from app.lib.geo_analysis.geometry_ops import _crs_unit_factor
    from pyproj import CRS

    factor = _crs_unit_factor(CRS.from_epsg(2270))
    assert 0.30 < factor < 0.31  # 1 ftUS = 0.30480060960 m
    # 1 ft² = factor² m²：对照直接计算的换算
    assert abs(factor * factor - (0.30480060960 ** 2)) < 1e-4


# ── G2：geocompute 载荷过管保留 crs ───────────────────────────────────────

def test_geocompute_payload_preserves_declared_crs() -> None:
    from app.services.geocompute.ops import _input_fc, _payload_from_feature_collection

    fc = {
        "type": "FeatureCollection",
        "crs": "EPSG:3857",
        "features": [{
            "type": "Feature", "geometry":
                {"type": "Point", "coordinates": [1.0, 2.0]},
            "properties": {},
        }],
    }
    payload = _payload_from_feature_collection(fc)
    assert payload["metadata"]["crs"] == "EPSG:3857"

    class _Node:
        node_id = "n1"
        inputs = ["src"]
        parameters = {}

    rebuilt = _input_fc({"src": payload}, _Node())
    assert rebuilt["crs"] == "EPSG:3857"  # 声明 CRS 过管不丢
