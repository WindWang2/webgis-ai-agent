"""map-product-runtime-v2 — Harness 工具面（Slice 4）。

覆盖：
- webgis_component_catalog：组件状态 + variant 目录 + revision 读取（Agent
  『发现组件』的最小工具面）；
- webgis_component_update 增强：create upsert chart_panel/statistics_panel、
  placement、variant、chart/stats payload 校验、乐观并发 superseded；
- generate_chart attach_to_map：图表 → 地图浮动 chart_panel（组件突变，
  不动数据层）；
- chart artifact ref 通道（大载荷走 ref，GET 端点数据形态）。
"""
import shutil
import uuid

import pytest

from app.services.gis_harness.components import build_default_components
from app.services.mapspec_store import mapspec_store
from app.services.session_data import session_data_manager


@pytest.fixture
async def clean_session():
    sid = f"tool-session-{uuid.uuid4().hex[:8]}"
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


async def _seed_components(sid: str):
    comps = build_default_components(primary_cartography="visual_heatmap")
    await mapspec_store.layout_set(sid, components=[c.to_mapspec() for c in comps])


# ── webgis_component_catalog ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catalog_reports_components_variants_revision(registry, clean_session):
    await _seed_components(clean_session)
    res = await registry.dispatch(
        "webgis_component_catalog", {"session_id": clean_session},
        session_id=clean_session,
    )
    assert res["success"] is True
    types = {c["type"] for c in res["components"]}
    assert {"north_arrow", "continuous_colorbar", "scale_bar", "title"} <= types
    assert isinstance(res["mutation_revision"], int)
    by_type = {v["type"]: v for v in res["available_variants"]}
    assert "compass_rose" in by_type["north_arrow"]["variants"]
    assert "chart_panel" in by_type  # native descriptor 在目录中


@pytest.mark.asyncio
async def test_catalog_sees_user_drag_placement(registry, clean_session):
    """Golden G3（UI→Agent 感知）：用户拖拽提交后 catalog 读到新位置。"""
    await _seed_components(clean_session)
    await mapspec_store.patch_component(
        clean_session, component_id="north-arrow",
        placement={"mode": "floating", "x": 210, "y": 130},
    )
    res = await registry.dispatch(
        "webgis_component_catalog", {"session_id": clean_session},
        session_id=clean_session,
    )
    north = next(c for c in res["components"] if c["type"] == "north_arrow")
    assert north["placement"]["x"] == 210 and north["placement"]["y"] == 130


@pytest.mark.asyncio
async def test_catalog_summary_includes_chart_binding(registry, clean_session):
    await _seed_components(clean_session)
    chart = {"type": "bar", "title": "各区学校数", "data": [{"name": "a", "value": 3}]}
    await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "chart-districts",
         "component_type": "chart_panel", "chart": chart, "create": True},
        session_id=clean_session,
    )
    res = await registry.dispatch(
        "webgis_component_catalog", {"session_id": clean_session},
        session_id=clean_session,
    )
    panel = next(c for c in res["components"] if c["type"] == "chart_panel")
    assert panel["chart"]["binding"] == "inline"
    assert panel["chart"]["type"] == "bar"
    assert panel["chart"]["points"] == 1


# ── webgis_component_update 增强 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_component_update_creates_chart_panel_inline(registry, clean_session):
    await _seed_components(clean_session)
    chart = {"type": "bar", "title": "各区学校数",
             "data": [{"name": "武侯区", "value": 88}, {"name": "锦江区", "value": 64}]}
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "chart-districts",
         "component_type": "chart_panel", "chart": chart, "create": True,
         "placement": {"mode": "floating", "x": 20, "y": 20, "width": 320}},
        session_id=clean_session,
    )
    assert res["success"] is True, res.get("message")
    assert res["change"]["created"] is True
    assert res["component_mutation_evidence"]["layer_count_unchanged"] is True
    spec = await mapspec_store.get_mapspec(clean_session)
    panel = next(c for c in spec["layout"]["components"] if c["id"] == "chart-districts")
    assert panel["options"]["chart"] == chart
    assert panel["placement"]["width"] == 320


@pytest.mark.asyncio
async def test_component_update_rejects_invalid_chart(registry, clean_session):
    await _seed_components(clean_session)
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "chart-bad",
         "component_type": "chart_panel", "create": True,
         "chart": {"type": "rose", "title": "t", "data": [{"name": "a", "value": 1}]}},
        session_id=clean_session,
    )
    assert res["success"] is False
    assert "chart" in res["message"]


@pytest.mark.asyncio
async def test_component_update_stats_panel(registry, clean_session):
    await _seed_components(clean_session)
    stats = {"title": "成都小学", "items": [{"label": "学校总数", "value": 432, "unit": "所"}]}
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "stats-main",
         "component_type": "statistics_panel", "stats": stats, "create": True},
        session_id=clean_session,
    )
    assert res["success"] is True, res.get("message")
    spec = await mapspec_store.get_mapspec(clean_session)
    panel = next(c for c in spec["layout"]["components"] if c["id"] == "stats-main")
    assert panel["options"]["stats"]["items"][0]["value"] == 432


@pytest.mark.asyncio
async def test_component_update_variant_and_placement(registry, clean_session):
    await _seed_components(clean_session)
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_type": "north_arrow",
         "variant": "arrow_simple",
         "placement": {"mode": "anchor", "anchor": "top-left"}},
        session_id=clean_session,
    )
    assert res["success"] is True, res.get("message")
    spec = await mapspec_store.get_mapspec(clean_session)
    north = next(c for c in spec["layout"]["components"] if c["type"] == "north_arrow")
    assert north["variant"] == "arrow_simple"
    assert north["position"] == "top-left"  # anchor 双写一致
    assert north["placement"]["anchor"] == "top-left"


@pytest.mark.asyncio
async def test_component_update_stale_revision_superseded(registry, clean_session):
    """Golden 并发：Agent 持旧 revision，用户拖拽（revision+1）后 → superseded。"""
    await _seed_components(clean_session)
    catalog = await registry.dispatch(
        "webgis_component_catalog", {"session_id": clean_session},
        session_id=clean_session,
    )
    stale_revision = catalog["mutation_revision"]
    # 用户交互推进 revision
    await mapspec_store.patch_component(
        clean_session, component_id="north-arrow",
        placement={"mode": "floating", "x": 5, "y": 5},
        expected_revision=stale_revision,
    )
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "north-arrow",
         "placement": {"mode": "anchor", "anchor": "top-right"},
         "expected_revision": stale_revision},
        session_id=clean_session,
    )
    assert res["success"] is False
    assert res.get("superseded") is True
    # 用户位置未被旧 Agent 决策覆盖
    spec = await mapspec_store.get_mapspec(clean_session)
    north = next(c for c in spec["layout"]["components"] if c["type"] == "north_arrow")
    assert north["placement"]["x"] == 5


@pytest.mark.asyncio
async def test_component_update_missing_without_create_lists_current(registry, clean_session):
    await _seed_components(clean_session)
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "ghost", "enabled": False},
        session_id=clean_session,
    )
    assert res["success"] is False
    assert "north-arrow" in res["correction_hint"]


# ── generate_chart attach_to_map ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_chart_attaches_map_panel(registry, clean_session):
    await _seed_components(clean_session)
    res = await registry.dispatch(
        "generate_chart",
        {"chart_type": "bar", "title": "各区学校数量",
         "data": '[{"name":"武侯区","value":88},{"name":"锦江区","value":64}]',
         "session_id": clean_session, "attach_to_map": True},
        session_id=clean_session,
    )
    assert res["chart"]["type"] == "bar"
    assert res["map_chart_panel"]["attached"] is True
    assert res["map_chart_panel"]["inline"] is True
    assert res["map_chart_panel"]["layer_count_unchanged"] is True
    spec = await mapspec_store.get_mapspec(clean_session)
    panel = next(c for c in spec["layout"]["components"] if c["type"] == "chart_panel")
    assert panel["options"]["chart"]["title"] == "各区学校数量"


@pytest.mark.asyncio
async def test_generate_chart_attach_without_session_fails_gracefully(registry, clean_session):
    # 直接调工具面（registry.dispatch 会注入 session_id，绕过注入验证缺省路径）
    from app.tools.chart import generate_chart_tool
    res = await generate_chart_tool(
        chart_type="bar", title="t", data='[{"name":"a","value":1}]',
        attach_to_map=True, session_id="",
    )
    # 图表本体仍生成；attach 缺 session 显式失败可见
    assert res["chart"]["type"] == "bar"
    assert res["map_chart_panel"]["attached"] is False
    assert "hint" in res["map_chart_panel"]


@pytest.mark.asyncio
async def test_generate_chart_attach_does_not_rerun_data(registry, clean_session):
    """Golden G2 前置：attach 是组件突变——图层集合与 ref 计数不变。"""
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104, 30]},
         "properties": {"name": "p1"}},
    ]}
    layer, _, _ = convert_analysis_to_mapspec_layer(
        {"geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap",
         "metadata": {"point_count": 1}},
    )
    layer["id"] = "result-heat"
    await mapspec_store.layer_upsert(clean_session, layer, fc)
    await _seed_components(clean_session)
    spec_before = await mapspec_store.get_mapspec(clean_session)
    layers_before = [l["id"] for l in spec_before["layers"]]

    res = await registry.dispatch(
        "generate_chart",
        {"chart_type": "bar", "title": "统计", "data": '[{"name":"a","value":1}]',
         "session_id": clean_session, "attach_to_map": True},
        session_id=clean_session,
    )
    assert res["map_chart_panel"]["attached"] is True
    spec_after = await mapspec_store.get_mapspec(clean_session)
    assert [l["id"] for l in spec_after["layers"]] == layers_before
    # 数据层 fingerprint 语义：layers 数组逐字不变
    assert spec_after["layers"] == spec_before["layers"]


# ── chart artifact ref 通道 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_large_chart_goes_through_ref_channel(registry, clean_session):
    from app.lib.json_size import estimate_json_bytes
    await _seed_components(clean_session)
    # 大载荷（inline 阈值 32KB）：400 点 × 长类目名
    data = [{"name": f"district-unit-{i:04d}-" + "x" * 80, "value": i} for i in range(400)]
    chart = {"type": "bar", "title": "大数据", "data": data}
    assert estimate_json_bytes(chart) > 32 * 1024

    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_id": "chart-big",
         "component_type": "chart_panel", "chart": chart, "create": True},
        session_id=clean_session,
    )
    assert res["success"] is True, res.get("message")
    assert res["chart_ref"].startswith("ref:chart-")
    spec = await mapspec_store.get_mapspec(clean_session)
    panel = next(c for c in spec["layout"]["components"] if c["id"] == "chart-big")
    assert "chart" not in panel["options"]  # 大载荷不 inline 进 MapSpec
    assert panel["options"]["chartRef"] == res["chart_ref"]

    # ref 本体可读（chart-artifacts 端点数据形态）
    stored = await session_data_manager.get(clean_session, res["chart_ref"])
    assert stored["chart"]["title"] == "大数据"
    assert len(stored["chart"]["data"]) == 400
