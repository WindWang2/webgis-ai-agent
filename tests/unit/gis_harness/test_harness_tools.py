"""GIS Harness agent 工具契约测试：webgis_map_intent / webgis_map_product /
webgis_component_update。"""
import pytest
import shutil

from app.services.session_data import session_data_manager
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.tools.registry import ToolRegistry
from app.services.gis_harness.tools import register_gis_harness_tools


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_gis_harness_tools(reg)
    return reg


@pytest.fixture
async def clean_session():
    sid = "test-gis-harness-tools-session"
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)
    yield sid
    await session_data_manager.clear_session(sid)
    shutil.rmtree(BASE_STORAGE_DIR / sid, ignore_errors=True)


def _point_fc(n: int) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
         "properties": {"name": f"p{i}"}}
        for i in range(n)
    ]}


@pytest.mark.asyncio
async def test_map_intent_tool_deterministic(registry):
    res = await registry.dispatch(
        "webgis_map_intent", {"query": "成都小学的分布情况"}, session_id=None,
    )
    assert res["success"] is True
    assert res["intent"]["task"] == "distribution_overview"
    assert res["recommended_recipe"] == "poi_distribution_overview"
    assert res["plan"]["status"] == "draft"
    caps = {c["capability"] for c in res["capabilities"]}
    assert "poi_query" in caps
    # 无副作用：不写 MapSpec、不查数据


@pytest.mark.asyncio
async def test_map_intent_tool_merges_hints_explicitly(registry):
    res = await registry.dispatch(
        "webgis_map_intent",
        {"query": "成都每平方公里小学密度",
         "task_hint": "distribution_overview"},  # 试图降级 → 被护栏拒绝
        session_id=None,
    )
    assert res["intent"]["task"] == "analytical_density"
    assert any("rejected" in h for h in res["intent"]["hint_applied"])


@pytest.mark.asyncio
async def test_map_product_golden_flow(registry, clean_session):
    """Golden：充足点数 POI ref → 热力+点授权 + 组件 + evidence。"""
    ref = await session_data_manager.store(clean_session, _point_fc(60), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "primary_ref": ref, "title": "成都市小学分布"},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["recipe_id"] == "poi_distribution_overview"
    # 热力 + 点两层都授权
    cartos = {b["cartography"] for b in res["layers"]}
    assert "visual_heatmap" in cartos and "point_overlay" in cartos
    # 组件落 MapSpec
    comp_types = [c["type"] for c in res["components"]]
    assert "title" in comp_types and "continuous_colorbar" in comp_types
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(clean_session)
    assert any(c["type"] == "title" and c["options"]["text"] == "成都市小学分布"
               for c in spec["layout"]["components"])
    assert any(l["type"] == "heatmap" for l in spec["layers"])
    # 产品证据完整
    ev = res["map_product_evidence"]
    assert ev["intent_resolution"]["task"] == "distribution_overview"
    assert ev["recipe_selection"]["selected"] == "poi_distribution_overview"
    assert ev["map_product_completeness"]["complete"] is True


@pytest.mark.asyncio
async def test_map_product_7_points_fallback_evidence(registry, clean_session):
    """Case B 工具级：7 点 → 热力禁用 + fallback 证据 + 点图补齐。"""
    ref = await session_data_manager.store(clean_session, _point_fc(7), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    cartos = {b["cartography"] for b in res["layers"]}
    assert "visual_heatmap" not in cartos  # 热力被确定性禁用
    assert "point_overlay" in cartos
    fb = res["fallbacks"][0]
    assert fb["reason_code"] == "INSUFFICIENT_POINTS"
    assert fb["evidence"] == {"point_count": 7, "min_points": 10}
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(clean_session)
    assert not any(l["type"] == "heatmap" for l in spec["layers"])


@pytest.mark.asyncio
async def test_map_product_binds_existing_layers(registry, clean_session):
    """已有 dispatch 授权的图层（layer_ids）被角色绑定，不重复授权。"""
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    from app.services.mapspec_store import mapspec_store
    fc = _point_fc(30)
    layer, _, _ = convert_analysis_to_mapspec_layer({
        "geojson": fc, "algorithm": "heatmap_data", "type_hint": "heatmap",
        "metadata": {"radius_px": 18, "point_count": 30},
    })
    layer["id"] = "result-existing"
    await mapspec_store.layer_upsert(clean_session, layer, fc)

    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "layer_ids": ["result-existing"]},
        session_id=clean_session,
    )
    assert res["success"] is True
    binding = next(b for b in res["layers"] if b["layer_id"] == "result-existing")
    assert binding["role"] == "primary" and binding["cartography"] == "visual_heatmap"
    # 只补点叠加（不重复建热力层）
    authored = [b for b in res["layers"] if b.get("authored")]
    assert all(b["cartography"] == "point_overlay" for b in authored)


@pytest.mark.asyncio
async def test_component_update_mutation_only(registry, clean_session):
    """『换一个指南针』：只改组件，图层不动。"""
    from app.services.gis_harness.components import build_default_components
    from app.services.mapspec_store import mapspec_store
    fc = _point_fc(20)
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    layer, _, _ = convert_analysis_to_mapspec_layer({"geojson": fc, "algorithm": "heatmap_data",
                                                     "type_hint": "heatmap",
                                                     "metadata": {"point_count": 20}})
    layer["id"] = "result-heat"
    await mapspec_store.layer_upsert(clean_session, layer, fc)
    comps = build_default_components(primary_cartography="visual_heatmap")
    await mapspec_store.layout_set(clean_session, components=[c.to_mapspec() for c in comps])

    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_type": "north_arrow",
         "options": {"variant": "compass_rose"}},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["change"]["options"]["to"]["variant"] == "compass_rose"
    ev = res["component_mutation_evidence"]
    assert ev["layer_count_unchanged"] is True
    assert ev["layers_before"] == ev["layers_after"]
    spec = await mapspec_store.get_mapspec(clean_session)
    north = next(c for c in spec["layout"]["components"] if c["type"] == "north_arrow")
    assert north["options"]["variant"] == "compass_rose"


@pytest.mark.asyncio
async def test_component_update_requires_target(registry, clean_session):
    res = await registry.dispatch(
        "webgis_component_update",
        {"session_id": clean_session, "component_type": "north_arrow"},
        session_id=clean_session,
    )
    assert res["success"] is False
    assert "至少提供一个突变字段" in res["message"]


@pytest.mark.asyncio
async def test_map_intent_registered_in_full_registry():
    """经 init_tools 全量注册后三个工具在册（Pi webgis_execute 代理可达）。"""
    from app.tools import init_tools
    reg = ToolRegistry()
    init_tools(reg)
    for name in ("webgis_map_intent", "webgis_map_product", "webgis_component_update"):
        assert name in reg._tools
