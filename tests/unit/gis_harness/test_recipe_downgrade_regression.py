"""数据不达标样本回归（ADR-0151 / AC-02 P8 · 30 样本）。

验收门（任务书 §5）：30 个「数据不达标」样本 100% 产出 eligible 方案 +
reason_code；零「全禁 + 点图兜底」静默路径；P0 勘察的 case05/06 矛盾
计划在此固定为回归锚。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.planner import MapProductPlanner


def _profile(
    geom: Optional[List[str]] = None,
    n: Optional[int] = None,
    fields: Optional[Dict[str, Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    p: Dict[str, Any] = {
        "geometryTypes": geom or [],
        "featureCount": n,
        "fields": fields or {},
    }
    p.update(extra)
    return p


# 30 个数据不达标样本：(标签, recipe_id, intent_kwargs, profile)
CASES = [
    # ── 样本量分档 ──────────────────────────────────────────────────
    ("heatmap 5点", "poi_distribution_overview", {}, _profile(["Point"], 5)),
    ("grid 3点", "grid_density_aggregate", {}, _profile(["Point"], 3)),
    ("grid 15点", "grid_density_aggregate", {}, _profile(["Point"], 15)),
    ("kriging 5点", "kriging_interpolation_workflow",
     {}, _profile(["Point"], 5, {"value": {}})),
    ("大样本 10000", "poi_distribution_overview", {}, _profile(["Point"], 10000)),
    # ── 几何类别失配 ────────────────────────────────────────────────
    ("extrusion 点数据(P0-05)", "extrusion_3d_thematic", {},
     _profile(["Point"], 200, {"height": {}})),
    ("moran 点数据(P0-06)", "global_moran_autocorrelation", {},
     _profile(["Point"], 300, {"value": {}})),
    ("grid 面数据", "grid_density_aggregate", {}, _profile(["Polygon"], 500)),
    ("heatmap 面数据", "poi_distribution_overview", {}, _profile(["Polygon"], 80)),
    ("od_flow 面数据", "od_flow_overview", {}, _profile(["Polygon"], 80)),
    ("raster 点数据", "raster_distribution", {}, _profile(["Point"], 60)),
    ("moran 线数据", "global_moran_autocorrelation", {},
     _profile(["LineString"], 120, {"value": {}})),
    ("choropleth 点数据可容", "administrative_choropleth", {},
     _profile(["Point"], 90)),
    ("sar 面数据", "sar_backscatter_overview", {}, _profile(["Polygon"], 40)),
    ("terrain 线数据", "terrain_morphometry_suite", {},
     _profile(["LineString"], 70)),
    # ── 字段缺失 / 基数 / 缺失率（新维度）──────────────────────────
    ("moran 无数值字段", "global_moran_autocorrelation", {},
     _profile(["Polygon"], 200, {"name": {}})),
    ("基数不匹配(分类当连续)", "global_moran_autocorrelation", {},
     _profile(["Polygon"], 200,
              {"value": {"uniqueRatio": 0.02, "kind": "categorical"}})),
    ("缺失率超标", "global_moran_autocorrelation", {},
     _profile(["Polygon"], 200,
              {"value": {"missingRatio": 0.95, "uniqueRatio": 0.9}})),
    ("零膨胀分布", "grid_density_aggregate", {},
     _profile(["Point"], 400,
              {"value": {"uniqueRatio": 0.8}},
              distribution={"zeroRatio": 0.7})),
    # ── CRS / 尺度（新维度）────────────────────────────────────────
    ("地理CRS聚合", "grid_density_aggregate", {},
     _profile(["Point"], 300, {"v": {}}, crs="EPSG:4326", crsClass="geographic")),
    ("稀疏聚合", "grid_density_aggregate", {},
     _profile(["Point"], 300, {"v": {}},
              crs="EPSG:32648", crsClass="projected",
              pointDensityPerKm2=0.01)),
    # ── 时间覆盖（新维度）──────────────────────────────────────────
    ("趋势无时间字段", "temporal_profile_station", {},
     _profile(["Point"], 100, {"v": {}})),
    # ── 决策族 / 网络族 ────────────────────────────────────────────
    ("选址 线数据", "site_selection", {}, _profile(["LineString"], 50)),
    ("适宜性 点数据", "suitability_assessment", {},
     _profile(["Point"], 50)),
    ("路网 面数据", "road_network_inventory", {}, _profile(["Polygon"], 30)),
    ("OD走廊 点数据", "od_corridor_mapping", {}, _profile(["Point"], 30)),
    ("公共服务覆盖 面数据", "transit_service_coverage", {},
     _profile(["Polygon"], 25)),
    ("均衡性 缺分母", "spatial_equity", {}, _profile(["Point"], 100)),
    ("风险暴露 面数据", "multi_hazard_composite", {}, _profile(["Polygon"], 60)),
    ("夜光活力 点数据", "nightlight_vitality_profile", {},
     _profile(["Point"], 45)),
]


@pytest.mark.parametrize("label,recipe_id,intent_kwargs,profile", CASES)
def test_ineligible_data_yields_eligible_plan(
    label: str,
    recipe_id: str,
    intent_kwargs: Dict[str, Any],
    profile: Dict[str, Any],
) -> None:
    """验收门：不达标数据 100% 产出 eligible 终稿方案 + 可见 reason_code。"""
    planner = MapProductPlanner()
    intent = MapRequestIntent(query=f"回归样本：{label}", **intent_kwargs)
    plan = planner.plan_from_intent(intent, recipe_id=recipe_id, use_memo=False)
    fin = planner.finalize_with_profile(plan, profile)

    assert fin.status == "finalized", label
    # 1) 最终方案 eligible（origin 元素级降级 或 换案为 eligible recipe）
    assert fin.eligibility.get("eligible") is True, (
        f"{label}: 终稿方案不合格 disabled={fin.eligibility.get('disabled')}")
    # 2) 产品非空：有存活图层或说明卡组件（禁止空白图/空 MapSpec）
    has_live_layer = any(ly.enabled for ly in fin.map_layers)
    has_card = any(c.type == "methodology_note" for c in fin.components)
    assert has_live_layer or has_card, f"{label}: 空产品"
    # 3) reason_code 可见：origin 失格/降级时 fallbacks 必须携带机器可读码
    codes = {fb.reason_code for fb in fin.fallbacks}
    swapped = any(fb.from_element == recipe_id for fb in fin.fallbacks)
    if fin.recipe_id != recipe_id:
        # 换案：必须有链式决策 + 原因码 + 披露 + 尝试证据
        assert swapped, f"{label}: 换案缺决策"
        swap = next(fb for fb in fin.fallbacks if fb.from_element == recipe_id)
        assert swap.reason_code and swap.reason_code != "INELIGIBLE", label
        assert swap.disclosure, label
        assert swap.attempts, label
    elif codes:
        assert codes != {"INELIGIBLE"}, label


def test_no_silent_point_map_path() -> None:
    """零「全禁 + 点图兜底」静默路径：RECIPE_INELIGIBLE 决策必须带披露。"""
    planner = MapProductPlanner()
    intent = MapRequestIntent(query="3D 立体对比", task="administrative_statistic")
    plan = planner.plan_from_intent(
        intent, recipe_id="extrusion_3d_thematic", use_memo=False)
    fin = planner.finalize_with_profile(
        plan, _profile(["Point"], 200, {"height": {}}))
    for fb in fin.fallbacks:
        if fb.reason_code == "RECIPE_INELIGIBLE":
            assert fb.disclosure, "RECIPE_INELIGIBLE 必须带用户可见披露"
            assert fb.attempts or fb.to_element, "必须记录链式尝试或换案目标"


def test_exhausted_chain_produces_data_card() -> None:
    """链穷尽 → 数据不足说明卡（非空白图）：构造 choropleth 也不可用的场景。

    poi 与 choropleth 都要求非 unknown 事实 —— 用「点数低于硬下限 + 地理
    CRS 聚合冲突」把通用链打穿（说明卡路径为防御性纵深，本测试锁定其
    存在性）。
    """
    planner = MapProductPlanner()
    from app.services.gis_harness.recipes import (
        CartographyRecipe,
        get_recipe_registry,
    )

    reg = get_recipe_registry()
    original_choropleth = reg.get("administrative_choropleth")
    assert original_choropleth is not None
    hard = CartographyRecipe(
        id="card_anchor_recipe",
        name="说明卡锚",
        required_geometry=["Polygon"],
        primary_cartography="administrative_choropleth",
    )
    # 临时封堵通用链的万能接收器（choropleth 无几何要求恒 eligible）：
    # 克隆要求 Raster 几何 → 任何矢量数据都不可兜底 → 链真正穷尽。
    blocked = CartographyRecipe(
        id="administrative_choropleth",
        name="choropleth(测试封堵)",
        required_geometry=["Raster"],
        primary_cartography="administrative_choropleth",
    )
    reg.unregister("administrative_choropleth")
    reg.register(blocked)
    reg.register(hard)
    try:
        intent = MapRequestIntent(query="说明卡锚测试")
        plan = planner.plan_from_intent(
            intent, recipe_id="card_anchor_recipe", use_memo=False)
        fin = planner.finalize_with_profile(plan, _profile(["LineString"], 50))
        assert fin.status == "finalized"
        assert fin.eligibility.get("eligible") is not True
        assert any(
            c.type == "methodology_note" and any(
                w.get("code") == "INSUFFICIENT_DATA"
                for w in (c.options or {}).get("warnings", [])
            )
            for c in fin.components
        ), "链穷尽必须产出数据不足说明卡"
        assert fin.fallbacks and fin.fallbacks[-1].disclosure
    finally:
        reg.unregister("card_anchor_recipe")
        reg.unregister("administrative_choropleth")
        reg.register(original_choropleth)
