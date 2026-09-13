"""数据不达标样本回归（ADR-0151 / AC-02 P8 · 30 样本）。

验收门（任务书 §5）：30 个「数据不达标」样本 100% 产出 eligible 方案 +
reason_code；零「全禁 + 点图兜底」静默路径；P0 勘察的 case05/06 矛盾
计划在此固定为回归锚。

V4 新维度行（基数/缺失率/分布形态/CRS 尺度/时间覆盖）：生产 pack 尚未
声明 V4 维度（唯一事实源缺位），直接用生产 recipe 断言会空转（拒绝永不
触发）—— 这些行按 test_eligibility_v4.py 的模式换用**声明了维度的
recipe 克隆**（同 id 注册、finally 还原），确保换案/reason_code 断言
真实参与裁决（review P3 修复：消解空转行）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.services.gis_harness.intent import MapRequestIntent
from app.services.gis_harness.planner import MapProductPlanner
from app.services.gis_harness.recipes import (
    CartographyRecipe,
    EligibilityRule,
    FieldExpectation,
    RecipeFallback,
    get_recipe_registry,
)


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

# ── V4 新维度行的裁决声明（review P3：消解空转行）────────────────────────
# 生产 pack 未声明 V4 维度 → 对生产 recipe 这些行必然 eligible（断言空转）。
# 每行给出：在 recipe 克隆的哪条元素规则上声明什么维度 + 期望的原因码。
# 克隆按同 id 注册（模式同 test_eligibility_v4.py / 说明卡锚测试），测试
# 结束还原原 recipe，不污染同进程其他用例。
V4_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "基数不匹配(分类当连续)": dict(
        element="value_semantics",
        dims=dict(field_expectations=[FieldExpectation(
            field="value", kind="continuous", min_unique_ratio=0.05)]),
        expected_code="FIELD_NOT_CONTINUOUS"),
    "缺失率超标": dict(
        element="value_semantics",
        dims=dict(field_expectations=[FieldExpectation(
            field="value", max_missing_ratio=0.5)]),
        expected_code="FIELD_MISSING_RATIO_HIGH"),
    "零膨胀分布": dict(
        element="aggregate_grid",
        dims=dict(allowed_distribution_shapes=["uniform"]),
        expected_code="DISTRIBUTION_UNFIT"),
    "地理CRS聚合": dict(
        element="aggregate_grid",
        dims=dict(require_projected_crs=True),
        expected_code="PROJECTED_CRS_REQUIRED"),
    "稀疏聚合": dict(
        element="aggregate_grid",
        dims=dict(min_point_density=5.0),
        expected_code="SPARSE_FOR_AGGREGATION"),
    "趋势无时间字段": dict(
        element="temporal_series",
        dims=dict(requires_temporal=True),
        expected_code="TEMPORAL_FIELD_ABSENT"),
}


def _v4_clone(recipe_id: str, element: str, dims: Dict[str, Any]) -> CartographyRecipe:
    """生产 recipe 的带 V4 维度声明克隆（同 id；声明到指定元素规则上）。"""
    base = get_recipe_registry().get(recipe_id)
    assert base is not None, recipe_id
    clone = base.model_copy(deep=True)
    for rule in clone.eligibility:
        if rule.element == element:
            for k, v in dims.items():
                setattr(rule, k, v)
            break
    else:
        clone.eligibility.append(EligibilityRule(element=element, **dims))
    return clone


@pytest.mark.parametrize("label,recipe_id,intent_kwargs,profile", CASES)
def test_ineligible_data_yields_eligible_plan(
    label: str,
    recipe_id: str,
    intent_kwargs: Dict[str, Any],
    profile: Dict[str, Any],
) -> None:
    """验收门：不达标数据 100% 产出 eligible 终稿方案 + 可见 reason_code。"""
    planner = MapProductPlanner()
    reg = get_recipe_registry()
    override = V4_OVERRIDES.get(label)
    original: Optional[CartographyRecipe] = None
    if override is not None:
        original = reg.get(recipe_id)
        clone = _v4_clone(recipe_id, override["element"], override["dims"])
        reg.unregister(recipe_id)
        reg.register(clone)
    try:
        intent = MapRequestIntent(query=f"回归样本：{label}", **intent_kwargs)
        plan = planner.plan_from_intent(
            intent, recipe_id=recipe_id, use_memo=False)
        fin = planner.finalize_with_profile(plan, profile)
    finally:
        if override is not None and original is not None:
            reg.unregister(recipe_id)
            reg.register(original)

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
    # 4) V4 新维度行必须真实触发（非空转）：声明的维度的原因码必须出现在
    #    决策证据里 —— 否则克隆声明没参与裁决（review P3 回归锚）。
    if override is not None:
        assert override["expected_code"] in codes, (
            f"{label}: V4 维度未触发（{override['expected_code']} 缺席）"
            f"—— 断言空转 codes={codes}")


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


def test_two_degraded_elements_promote_single_primary() -> None:
    """P3 回归锚：多个被禁元素同批降级时只允许存在一个 enabled primary。

    此前元素级降级循环对每个降级元素各自提升 primary —— 两个禁用元素
    （visual_heatmap + aggregate_grid）配两个不同 use 目标（point_overlay +
    simple_point_map）时产出两个 enabled primary，破坏单一 primary 不变式
    （finalize 下游按 `role == "primary" and enabled` 取 first 才没炸）。
    """
    planner = MapProductPlanner()
    reg = get_recipe_registry()
    dual = CartographyRecipe(
        id="dual_degrade_recipe",
        name="双元素降级锚",
        required_geometry=["Point", "MultiPoint"],
        eligibility=[
            EligibilityRule(element="visual_heatmap", check_points=True,
                            reason_code="INSUFFICIENT_POINTS"),
            EligibilityRule(element="aggregate_grid", min_points=20,
                            reason_code="GRID_TOO_SPARSE"),
        ],
        primary_cartography="visual_heatmap",
        secondary_cartography=["aggregate_grid", "point_overlay", "simple_point_map"],
        fallbacks=[
            RecipeFallback(when="热力降级", reason_code="INSUFFICIENT_POINTS",
                           use="point_overlay"),
            RecipeFallback(when="格网降级", reason_code="GRID_TOO_SPARSE",
                           use="simple_point_map"),
        ],
    )
    reg.register(dual)
    try:
        intent = MapRequestIntent(query="双元素降级回归")
        plan = planner.plan_from_intent(
            intent, recipe_id="dual_degrade_recipe", use_memo=False)
        fin = planner.finalize_with_profile(plan, _profile(["Point"], 5))
        assert fin.status == "finalized"
        # 前提：两个元素确实被禁用（bug 触发条件真实存在，防空转）
        disabled = {d["element"] for d in fin.eligibility.get("disabled", [])}
        assert {"visual_heatmap", "aggregate_grid"} <= disabled
        disabled_layers = {ly.cartography for ly in fin.map_layers if not ly.enabled}
        assert {"visual_heatmap", "aggregate_grid"} <= disabled_layers
        # 不变式：至多一个 enabled primary，且是首个声明的回退目标
        primaries = [ly for ly in fin.map_layers
                     if ly.role == "primary" and ly.enabled]
        assert len(primaries) == 1, (
            "单一 primary 不变式被破坏: "
            f"{[(ly.cartography, ly.role, ly.enabled) for ly in fin.map_layers]}")
        assert primaries[0].cartography == "point_overlay"
    finally:
        reg.unregister("dual_degrade_recipe")
