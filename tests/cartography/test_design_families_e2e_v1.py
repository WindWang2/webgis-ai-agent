"""M5 五类专题方案经真实 webgis_map_product 链路验证（dispatch 缝）。

F1 POI 点分布 vs 聚合/密度候选；F2 密度（足额 honored / 稀疏 typed blocked
在 M2 已覆盖）；F3 区级原始计数 vs 真实分母密度（typed 门控对）；F4 双时期
变化（change 族候选）；F5 图表联动口径（M4 检查在成图 spec 上不 fail）。

数据：合成成都学校点位/区面（fixtures_data 同源惯例，显式 _simulated）。
"""
import uuid

import pytest

from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.session_data import session_data_manager
from app.services.gis_harness.tools import register_gis_harness_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.cartography


def _points(n: int):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point",
                      "coordinates": [104.04 + (i % 7) * 0.02,
                                      30.62 + (i // 7) * 0.02]},
         "properties": {"name": f"模拟小学-{i}", "_simulated": True}}
        for i in range(n)]}


_DISTRICTS = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区"]


def _districts_fc(*, with_population: bool = False, with_year: bool = False):
    feats = []
    for i, name in enumerate(_DISTRICTS):
        props = {"name": name, "school_count": 3 + i * 2, "_simulated": True}
        if with_population:
            props["population"] = 400_000 + i * 50_000
        if with_year:
            props["year"] = 2015 + (i % 2) * 10
        x0 = 103.98 + i * 0.06
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, 30.55], [x0 + 0.05, 30.55],
                [x0 + 0.05, 30.70], [x0, 30.70], [x0, 30.55]]]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": feats}


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture
async def sid():
    session_id = f"sess-design-f{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


async def _product(registry, sid, fc, query, title="专题图（模拟数据）"):
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": query, "session_id": sid, "primary_ref": ref,
         "title": title},
        session_id=sid,
    )
    assert res["success"] is True, res.get("error")
    return res


# ── F1：POI 点分布 → 点族 + 聚合/密度族并列候选 ─────────────────────────


async def test_f1_point_distribution_families(registry, sid):
    res = await _product(registry, sid, _points(40), "成都市小学分布情况")
    dc = res["design_candidates"]
    families = {c["family"] for c in dc["candidates"]}
    assert "point" in families
    assert families & {"density_surface", "aggregation"}
    trace = res["design_decision_trace"]
    assert trace["selected"]["mapModel"]
    assert trace["dataFacts"]


# ── F2：足额点 + 显式密度语义 → 热力候选 honored ────────────────────────


async def test_f2_dense_density_request_honored(registry, sid):
    res = await _product(registry, sid, _points(40), "成都小学密度分布")
    dc = res["design_candidates"]
    assert "visual_heatmap" in dc["userRequestHandling"]["honored"]
    heat = [c for c in dc["candidates"] if c["mapModel"] == "visual_heatmap"]
    assert heat and heat[0]["suitability"] == "suitable"
    assert "KDE_NOT_POPULATION_DENSITY" in heat[0]["conditionCodes"]


# ── F3：区级原始计数 vs 真实分母（typed 门控对）────────────────────────


async def test_f3_denominator_gate_via_product(registry, sid):
    no_denom = await _product(
        registry, sid, _districts_fc(), "成都市各区小学数量统计",
        title="各区小学数量（原始计数，模拟数据）")
    dc = no_denom["design_candidates"]
    normalized = [c for c in dc["candidates"]
                  if c["mapModel"] == "normalized_choropleth"]
    # 无分母：normalized 缺席或 blocked；原始计数表达在场
    assert all(c["suitability"] == "blocked" for c in normalized)
    raw = [c for c in dc["candidates"]
           if c["mapModel"] == "administrative_choropleth"]
    assert raw, "无分母时原始计数表达必须可用"
    assert any("分母缺席" in f for f in
               no_denom["design_decision_trace"]["dataFacts"])

    sid2 = sid + "-b"
    await session_data_manager.clear_session(sid2)
    with_denom = await _product(
        registry, sid2, _districts_fc(with_population=True),
        "成都市各区小学密度统计", title="各区每万人小学（模拟数据）")
    dc2 = with_denom["design_candidates"]
    normalized2 = [c for c in dc2["candidates"]
                   if c["mapModel"] == "normalized_choropleth"]
    assert normalized2, "分母在场（population 字段）→ 率语义候选进池"
    assert normalized2[0]["denominator"] == "required_satisfied"
    facts2 = with_denom["design_decision_trace"]["dataFacts"]
    assert any("分母在场" in f for f in facts2)
    await session_data_manager.clear_session(sid2)


# ── F4：双时期事实 → change 族候选（结构化类目派生）────────────────────


async def test_f4_temporal_change_candidate(registry, sid):
    res = await _product(
        registry, sid, _districts_fc(with_year=True),
        "对比2015年和2025年成都各区土地利用变化",
        title="双时期对比（模拟数据）")
    dc = res["design_candidates"]
    assert any(c["family"] == "change" for c in dc["candidates"]), \
        "双期 + change 意图 → change 族候选"
    # 负向：同数据非 change 意图 → change 族不入场
    sid2 = sid + "-c"
    await session_data_manager.clear_session(sid2)
    res2 = await _product(
        registry, sid2, _districts_fc(with_year=True),
        "成都市各区小学数量统计", title="单期（模拟数据）")
    dc2 = res2["design_candidates"]
    assert all(c["family"] != "change" for c in dc2["candidates"])
    await session_data_manager.clear_session(sid2)


# ── F5：成图 spec 上图表-地图口径检查不 fail ───────────────────────────


async def test_f5_chart_map_coherence_on_committed_spec(registry, sid):
    res = await _product(registry, sid, _points(40), "成都市小学分布情况")
    spec = res.get("mapspec") or {}
    assert spec.get("layers"), "成图 spec 必须含图层"
    report = evaluate_cartography_semantics(spec)
    coherence = [c for c in report.checks
                 if c.rule == "CHART_MAP_FIELD_COHERENCE"]
    for check in coherence:
        assert check.status != "fail", check.message
    # 组件契约：图例/色条按需在场（热力 → continuous_colorbar）
    comp_types = {c.get("type") for c in
                  (spec.get("layout") or {}).get("components") or []}
    if any(ly.get("type") == "heatmap" for ly in spec.get("layers") or []):
        assert "continuous_colorbar" in comp_types
