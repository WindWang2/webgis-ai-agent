"""webgis_map_product 设计候选 + 决策 trace 生产接线测试（M2）。

真实 dispatch 缝（ToolRegistry.dispatch → webgis_map_product 主链）断言：
1. 结果载荷携带 design_candidates（表达模型层 2–4 候选）与
   design_decision_trace（决策链工件）；
2. 稀疏点 + 密度语义 → typed blocked 候选可见（INSUFFICIENT_POINTS）；
3. 候选引擎异常 → best_effort 降级不阻断组装主链（success 仍 True）。

数据：合成成都小学点位（同 test_harness_kernel_e2e_scenarios 惯例）。
"""
import uuid
from unittest.mock import patch

import pytest

from app.services.session_data import session_data_manager
from app.services.gis_harness.tools import register_gis_harness_tools
from app.tools.registry import ToolRegistry

pytestmark = pytest.mark.cartography


def _point_fc(n: int, with_population: bool = False):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [104.06 + i * 0.008, 30.66 + i * 0.004]},
                "properties": (
                    {"name": f"模拟小学-{i}", "population": 1000 + i}
                    if with_population else {"name": f"模拟小学-{i}"}
                ),
            }
            for i in range(n)
        ],
    }


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture
async def sid():
    session_id = f"sess-design-trace-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(session_id)
    yield session_id
    await session_data_manager.clear_session(session_id)


async def _product(registry, sid: str, fc: dict, query: str = "成都市小学分布情况"):
    ref = await session_data_manager.store(sid, fc, prefix="geojson")
    return await registry.dispatch(
        "webgis_map_product",
        {"query": query, "session_id": sid, "primary_ref": ref,
         "title": "成都市小学分布（模拟数据）"},
        session_id=sid,
    )


@pytest.mark.asyncio
async def test_product_result_carries_design_candidates_and_trace(registry, sid):
    res = await _product(registry, sid, _point_fc(40))
    assert res["success"] is True
    dc = res.get("design_candidates") or {}
    assert dc.get("count", 0) >= 2, "表达模型层候选必须随产品结果下发"
    assert dc["version"] == 1
    cands = dc["candidates"]
    assert all(c.get("mapModel") and c.get("suitability") for c in cands)
    families = {c["family"] for c in cands}
    assert families & {"density_surface", "aggregation"}, \
        "分布任务应给出聚合/密度族候选"
    trace = res.get("design_decision_trace") or {}
    assert trace["version"] == 1
    assert trace["dataFacts"], "数据资格事实行必须在场（Agent 可解释面）"
    assert trace["selected"]["candidateId"], "选定候选必须可寻址"
    assert trace["selected"]["mapModel"], trace["selected"]


@pytest.mark.asyncio
async def test_sparse_density_query_yields_typed_blocked_candidate(registry, sid):
    """3 点 + 密度语义 → 热力候选 typed blocked 且在 trace 收据中可见。"""
    res = await _product(
        registry, sid, _point_fc(3), query="成都小学密度热力分析")
    assert res["success"] is True
    dc = res["design_candidates"]
    heat = [c for c in dc["candidates"] if c["mapModel"] == "visual_heatmap"]
    receipts = res["design_decision_trace"]["scientificReceipts"]
    if heat:
        assert heat[0]["suitability"] == "blocked"
        assert "INSUFFICIENT_POINTS_FOR_DENSITY" in heat[0]["conditionCodes"]
        assert any("INSUFFICIENT_POINTS_FOR_DENSITY" in r for r in receipts)
    else:
        # planner 侧热力被禁时（f02b 同款资格链），blocked 证据仍须在收据面
        assert any("INSUFFICIENT_POINTS_FOR_DENSITY" in r for r in receipts), \
            "typed blocked 证据必须可见，不得静默"


@pytest.mark.asyncio
async def test_engine_failure_degrades_without_blocking_product(registry, sid):
    """候选引擎异常 → 降级披露 + 主链不受影响（best_effort 纪律）。"""
    ref = await session_data_manager.store(sid, _point_fc(12), prefix="geojson")
    # tools.py 为函数内延迟 import —— patch 源模块符号即在调用期生效
    with patch(
        "app.lib.cartography.design_candidates.generate_design_candidates",
        side_effect=RuntimeError("boom"),
    ):
        res = await registry.dispatch(
            "webgis_map_product",
            {"query": "成都市小学分布情况", "session_id": sid,
             "primary_ref": ref, "title": "分布"},
            session_id=sid,
        )
    assert res["success"] is True, "证据面降级绝不阻断组装主链"
    assert "design_candidates_degraded" in res
    assert res["design_candidates_degraded"]["code"] == "design_candidates_error"
