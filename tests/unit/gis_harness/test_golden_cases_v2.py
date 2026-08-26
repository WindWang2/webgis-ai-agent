"""Golden Case G1/G2 —— 成都小学分布（端到端设计验证）。

G1 成都小学的分布情况 + 各区学校数量统计：
  POI 查询产物 → MapProduct（heatmap + points + 组件）→ 统计聚合 →
  generate_chart(attach_to_map) → 地图浮动 chart_panel + 统计证据。
  不伪造数据：统计图数据来自显式传入的聚合结果。

G2 组件二次交互（把统计柱状图移到右下角缩小一些 / 换指南针）：
  只发生 component mutation —— 图层数组逐字不变（cartographic
  fingerprint 的图层投影不变），无数据工具重跑。
"""
import shutil
import uuid

import pytest

from app.lib.cartography.quality_loop import cartographic_fingerprint
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager


@pytest.fixture
async def golden_session():
    sid = f"golden-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    from app.services.mapspec.store import BASE_STORAGE_DIR
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
async def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    reg = ToolRegistry()
    init_tools(reg)
    return reg


def _school_fc(n: int = 60) -> dict:
    """成都范围的模拟学校 POI（大都市圈坐标带）。"""
    import random
    rng = random.Random(42)
    features = []
    districts = ["锦江区", "青羊区", "金牛区", "武侯区", "成华区", "龙泉驿区"]
    for i in range(n):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [
                104.0 + rng.random() * 0.25, 30.55 + rng.random() * 0.2,
            ]},
            "properties": {"name": f"小学{i}", "district": districts[i % len(districts)]},
        })
    return {"type": "FeatureCollection", "features": features}


async def _produce_map(registry, sid: str, fc: dict) -> dict:
    """地图产品：POI ref → webgis_map_product（heatmap 主表达）。"""
    ref_id = await session_data_manager.store(sid, fc, prefix="geojson")
    await session_data_manager.set_alias(sid, ref_id, "schools")
    res = await registry.dispatch(
        "webgis_map_product",
        {"session_id": sid, "primary_ref": "schools",
         "query": "成都小学的分布情况", "title": "成都小学分布"},
        session_id=sid,
    )
    assert res.get("success") is True, res.get("message") or res
    return res


@pytest.mark.asyncio
async def test_g1_chengdu_schools_product_with_chart_panel(registry, golden_session):
    fc = _school_fc()
    product = await _produce_map(registry, golden_session, fc)

    # 产品图层：POI 数据绑定为 primary（仅点数据时 point_overlay 是诚实产品
    # ——不伪造区县面/热力；有密度意图或聚合面时才会是 heatmap/choropleth）
    bound = [b for b in product["layers"] if b.get("role") == "primary"]
    assert len(bound) == 1
    # 组件集：chrome 家族恒在（title/north_arrow/scale_bar/attribution）
    spec = await mapspec_store.get_mapspec(golden_session)
    types = {c["type"] for c in spec["layout"]["components"]}
    assert {"title", "north_arrow", "scale_bar", "attribution"} <= types

    # 各区学校数量统计（显式聚合——不伪造）
    from collections import Counter
    counter = Counter(f["properties"]["district"] for f in fc["features"])
    stats_data = [{"name": d, "value": c} for d, c in sorted(counter.items())]

    chart_res = await registry.dispatch(
        "generate_chart",
        {"chart_type": "bar", "title": "成都各区学校数量",
         "data": stats_data, "attach_to_map": True,
         "position": "bottom-right"},
        session_id=golden_session,
    )
    assert chart_res["chart"]["type"] == "bar"
    assert len(chart_res["chart"]["data"]) == 6
    assert chart_res["map_chart_panel"]["attached"] is True

    # 图表面板入 MapSpec，绑定真实统计值
    spec = await mapspec_store.get_mapspec(golden_session)
    panel = next(c for c in spec["layout"]["components"] if c["type"] == "chart_panel")
    assert panel["options"]["chart"]["title"] == "成都各区学校数量"
    assert sum(p["value"] for p in panel["options"]["chart"]["data"]) == len(fc["features"])

    # Agent 能发现组件并读取图表绑定（catalog 单调用）
    catalog = await registry.dispatch(
        "webgis_component_catalog", {"session_id": golden_session},
        session_id=golden_session,
    )
    panel_summary = next(c for c in catalog["components"] if c["type"] == "chart_panel")
    assert panel_summary["chart"]["binding"] == "inline"
    assert panel_summary["chart"]["points"] == 6


@pytest.mark.asyncio
async def test_g2_component_only_interaction_layer_projection_unchanged(registry, golden_session):
    """组件二次交互：移动/缩放统计图、换指南针 —— 图层投影逐字不变。"""
    fc = _school_fc()
    await _produce_map(registry, golden_session, fc)
    stats = [{"name": "a区", "value": 10}, {"name": "b区", "value": 5}]
    await registry.dispatch(
        "generate_chart",
        {"chart_type": "bar", "title": "各区学校数", "data": stats, "attach_to_map": True},
        session_id=golden_session,
    )

    spec_before = await mapspec_store.get_mapspec(golden_session)
    layers_before = spec_before["layers"]
    sources_before = spec_before["sources"]

    # 『把统计柱状图移到右下角，缩小一些』
    move = await registry.dispatch(
        "webgis_component_update",
        {"component_type": "chart_panel",
         "placement": {"mode": "floating", "x": 760, "y": 640, "width": 260, "height": 200}},
        session_id=golden_session,
    )
    assert move["success"] is True, move.get("message")
    # 『图例放到左下角』——按产品实际存在的 legend 族组件（point_overlay 主
    # 表达可能无 colorbar；无 legend 族时退化为移动 title，突变语义不变）
    spec_mid = await mapspec_store.get_mapspec(golden_session)
    present_types = {c["type"] for c in spec_mid["layout"]["components"]}
    legend_type = next(
        (t for t in ("continuous_colorbar", "legend", "categorical_legend") if t in present_types),
        "title",
    )
    legend_move = await registry.dispatch(
        "webgis_component_update",
        {"component_type": legend_type, "position": "bottom-left"},
        session_id=golden_session,
    )
    assert legend_move["success"] is True, legend_move.get("message")
    # 『换成玫瑰样式的指南针』
    compass = await registry.dispatch(
        "webgis_component_update",
        {"component_type": "north_arrow", "variant": "compass_rose"},
        session_id=golden_session,
    )
    assert compass["success"] is True

    spec_after = await mapspec_store.get_mapspec(golden_session)
    # 数据层与数据源逐字不变（无 POI 重查 / 无重分析 / 无图层重排）
    assert spec_after["layers"] == layers_before
    assert spec_after["sources"] == sources_before
    assert move["component_mutation_evidence"]["layer_count_unchanged"] is True

    # 图层投影的 cartographic fingerprint 不变（组件突变不触发图层侧门禁）
    layers_projection_before = cartographic_fingerprint({"layers": layers_before})
    layers_projection_after = cartographic_fingerprint({"layers": spec_after["layers"]})
    assert layers_projection_after == layers_projection_before

    # 组件状态确实改变（placement/variant 落 MapSpec）
    panel = next(c for c in spec_after["layout"]["components"] if c["type"] == "chart_panel")
    assert panel["placement"]["x"] == 760 and panel["placement"]["width"] == 260
    north = next(c for c in spec_after["layout"]["components"] if c["type"] == "north_arrow")
    assert north["variant"] == "compass_rose"
