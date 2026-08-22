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
    assert any(ly["type"] == "heatmap" for ly in spec["layers"])
    # 产品证据完整
    ev = res["map_product_evidence"]
    assert ev["intent_resolution"]["task"] == "distribution_overview"
    assert ev["recipe_selection"]["selected"] == "poi_distribution_overview"
    # #784: 教育模板规划的 reference 行政区层未绑定（本流程只授权热力+点）
    # —— completeness 必须如实列出缺失的规划层，不再假报 complete。
    comp = ev["map_product_completeness"]
    assert comp["complete"] is False
    assert "planned_layers" in comp["missing"]
    assert comp["unbound_planned_layers"] == ["administrative_choropleth"]
    assert comp["present"]["interactive_map"] is True


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
    assert not any(ly["type"] == "heatmap" for ly in spec["layers"])


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


def _polygon_fc(n: int = 8) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[104.0 + i * 0.01, 30.6],
                                                          [104.01 + i * 0.01, 30.6],
                                                          [104.01 + i * 0.01, 30.61],
                                                          [104.0 + i * 0.01, 30.61],
                                                          [104.0 + i * 0.01, 30.6]]]},
         "properties": {"name": f"zone{i}"}}
        for i in range(n)
    ]}


@pytest.mark.asyncio
async def test_map_product_polygon_case_c(registry, clean_session):
    """Case C 工具级：Polygon 主数据 → 热力禁用 + GEOMETRY_NOT_SUPPORTED 证据。"""
    ref = await session_data_manager.store(clean_session, _polygon_fc(), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    cartos = {b["cartography"] for b in res["layers"]}
    assert "visual_heatmap" not in cartos
    reasons = {f["reason_code"] for f in res["fallbacks"]}
    assert "GEOMETRY_NOT_SUPPORTED" in reasons
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(clean_session)
    assert not any(ly["type"] == "heatmap" for ly in spec["layers"])


@pytest.mark.asyncio
async def test_map_product_recipe_id_plan_continuity(registry, clean_session):
    """计划连续性：意图阶段接受的 recipe_id 纠偏在产品阶段保留。"""
    ref = await session_data_manager.store(clean_session, _point_fc(60), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "primary_ref": ref, "recipe_id": "point_density"},
        session_id=clean_session,
    )
    assert res["recipe_id"] == "point_density"
    assert res["plan_id"].startswith("plan-")


@pytest.mark.asyncio
async def test_primary_ref_not_transparently_dereferenced(registry, clean_session):
    """skip-list 直接断言：工具收到的是 ref 游标字符串，而非内联 FC。"""
    from unittest.mock import AsyncMock, patch

    ref = await session_data_manager.store(clean_session, _point_fc(15), prefix="geojson")
    calls = []
    original = session_data_manager.get_ref_descriptor

    async def spy(sid, rid):
        calls.append(rid)
        return await original(sid, rid)

    with patch.object(session_data_manager, "get_ref_descriptor", AsyncMock(side_effect=spy)):
        res = await registry.dispatch(
            "webgis_map_product",
            {"query": "成都小学的分布情况", "session_id": clean_session,
             "primary_ref": ref},
            session_id=clean_session,
        )
    # 工具按原始 ref 字符串取 descriptor —— 透明解引用没有发生（否则收到
    # 的是 dict，get_ref_descriptor 收不到 ref 串，流程也不会有热力层）。
    assert ref in calls
    assert res["success"] is True


@pytest.mark.asyncio
async def test_map_product_through_dispatch_service(registry, clean_session):
    """经 ToolDispatchService 全链路：raw_result 携带产品证据 + slim payload。"""
    from app.services.tool_dispatch_service import ToolDispatchService

    ref = await session_data_manager.store(clean_session, _point_fc(40), prefix="geojson")
    service = ToolDispatchService(registry=registry)
    executed: set = set()
    result = await service.dispatch(
        {"id": "call_prod_1",
         "function": {"name": "webgis_map_product",
                      "arguments": {"query": "成都小学的分布情况",
                                    "session_id": clean_session,
                                    "primary_ref": ref}}},
        clean_session, executed,
    )
    assert result.status == "ok"
    raw = result.raw_result
    assert isinstance(raw.get("map_product_evidence"), dict)
    assert raw["map_product_evidence"]["recipe_selection"]["selected"] == \
        "poi_distribution_overview"
    # slim 事件与 LLM payload 不携带全量 FC（fetch-on-demand 不变量）
    assert "FeatureCollection" not in result.llm_payload or \
        result.llm_payload.count("coordinates") < 5
    # dedup：同 turn 内完全相同的调用被拦截为 repeated
    result2 = await service.dispatch(
        {"id": "call_prod_1",
         "function": {"name": "webgis_map_product",
                      "arguments": {"query": "成都小学的分布情况",
                                    "session_id": clean_session,
                                    "primary_ref": ref}}},
        clean_session, executed,
    )
    assert result2.status == "repeated"


@pytest.mark.asyncio
async def test_map_product_heatmap_carries_legend_and_colorbar_binding(registry, clean_session):
    """#718: product-authored heatmap layers carry legend_spec from the same
    NATIVE_HEATMAP_COLORS source as heatmap_data, and the colorbar component
    references the authored layer + palette."""
    ref = await session_data_manager.store(clean_session, _point_fc(60), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "primary_ref": ref, "palette": "viridis"},
        session_id=clean_session,
    )
    assert res["success"] is True
    from app.services.mapspec_store import mapspec_store
    spec = await mapspec_store.get_mapspec(clean_session)
    heat = next(ly for ly in spec["layers"] if ly["type"] == "heatmap")
    ls = heat.get("legend_spec")
    assert isinstance(ls, dict) and ls["type"] == "continuous", (
        f"product-authored heatmap must carry legend_spec, got {ls}"
    )
    from app.lib.cartography.palettes import heatmap_legend_colors
    assert ls["palette_colors"] == heatmap_legend_colors("viridis")
    bar = next(
        c for c in spec["layout"]["components"] if c["type"] == "continuous_colorbar"
    )
    assert bar["options"].get("layerId") == heat["id"], (
        f"colorbar must bind the authored heatmap layer, got {bar['options']}"
    )
    assert bar["options"].get("palette") == "viridis"


@pytest.mark.asyncio
async def test_map_product_evidence_candidates_not_degenerate(registry, clean_session):
    """#723: recipe_selection.candidates must record the selector's actual
    candidate set, not a post-hoc [selected_id] singleton."""
    ref = await session_data_manager.store(clean_session, _point_fc(60), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session, "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    candidates = res["map_product_evidence"]["recipe_selection"]["candidates"]
    assert res["recipe_id"] in candidates
    assert len(candidates) >= 1
    # the list must come from select_candidates (recipe ids incl. unselected),
    # not from filtering all_ids by the selection
    if len(candidates) == 1:
        from app.services.gis_harness.intent import resolve_map_request_intent
        from app.services.gis_harness.planner import MapProductPlanner
        planner = MapProductPlanner()
        expected = [
            c.id for c in planner.recipes.select_candidates(
                resolve_map_request_intent("成都小学的分布情况"))
        ]
        assert candidates == expected


@pytest.mark.asyncio
async def test_map_product_authoring_failure_flagged_not_swallowed(registry, clean_session, monkeypatch):
    """#716: when authoring commits fail, the tool must say so — flag +
    warnings + honest summary — instead of reporting a clean product."""
    ref = await session_data_manager.store(clean_session, _point_fc(60), prefix="geojson")

    from app.services.mapspec_store import mapspec_store as _store

    async def _fail_upsert(session_id, layer, source_data=None):
        return {"success": False, "message": "injected failure 716"}

    monkeypatch.setattr(_store, "layer_upsert", _fail_upsert)

    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session,
         "primary_ref": ref},
        session_id=clean_session,
    )
    # nothing mounted → the caller must not treat this as a complete product
    assert res.get("cartographic_authoring_failed") is True
    assert res["layers"] == []
    assert any("authoring failed" in w for w in res.get("warnings", []))
    assert "⚠" in res["summary"]


@pytest.mark.asyncio
async def test_completeness_interactive_map_requires_bound_layer(registry, clean_session):
    """#716: interactive_map completeness counts AUTHORED+BOUND layers, not
    planned-defaulted ones."""
    from app.services.gis_harness.planner import MapProductPlanner
    from app.services.gis_harness.intent import resolve_map_request_intent

    planner = MapProductPlanner()
    plan = planner.plan_from_intent(
        resolve_map_request_intent("成都小学的分布情况"),
    )
    plan = planner.finalize_with_profile(plan, {"featureCount": 60, "geometryTypes": ["Point"]})
    # planned layers exist and default enabled, but nothing is bound yet
    assert any(ly.enabled for ly in plan.map_layers)
    comp = planner.assess_completeness(plan)
    assert comp["present"]["interactive_map"] is False, (
        "planned-but-unbound layers must not count as interactive_map present"
    )


# ─── #784: 角色绑定 / 缺层可见性 / done 能力面 契约 ────────────────────────


@pytest.mark.asyncio
async def test_map_product_simple_view_complete_and_primary(registry, clean_session):
    """#784: simple_view 流（primary_ref 点数据）→ 单层绑定 primary 角色，
    completeness 真实 complete。旧实现把 circle 一律记成
    secondary/point_overlay，计划标签 simple_point_map 永远匹配不上，
    已挂载图层的产品被误报 incomplete。"""
    ref = await session_data_manager.store(clean_session, _point_fc(12), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "给我看看成都小学", "session_id": clean_session, "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["layers"], "点主数据应授权一个点图层"
    first = res["layers"][0]
    assert first["role"] == "primary"
    assert first["cartography"] == "simple_point_map"
    assert first["layer_type"] == "circle"  # 实际图层类型如实记录
    comp = res["completeness"]
    assert comp["missing"] == []
    assert comp["complete"] is True


@pytest.mark.asyncio
async def test_map_product_grid_flow_binds_aggregate_grid(registry, clean_session):
    """#784: 格网流绑定 H3 fill → 计划标签 aggregate_grid + primary 角色
    （旧实现记成 reference/administrative_choropleth）；h3_binning
    provenance 使 grid_binning 步骤标记完成。"""
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    from app.services.mapspec_store import mapspec_store
    h3_layer, _, _ = convert_analysis_to_mapspec_layer({
        "geojson": _polygon_fc(25), "algorithm": "h3_binning",
    })
    h3_layer["id"] = "result-h3"
    await mapspec_store.layer_upsert(clean_session, h3_layer, _polygon_fc(25))
    # 主数据是原始点（格网聚合的输入）—— eligibility 以输入点数判定
    ref = await session_data_manager.store(clean_session, _point_fc(60), prefix="geojson")

    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学按格网分布", "session_id": clean_session,
         "layer_ids": ["result-h3"], "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    binding = next(b for b in res["layers"] if b["layer_id"] == "result-h3")
    assert binding["role"] == "primary"
    assert binding["cartography"] == "aggregate_grid"
    assert binding["layer_type"] == "fill"
    comp = res["completeness"]
    assert comp["present"]["statistics"] is True
    assert comp["complete"] is True


@pytest.mark.asyncio
async def test_map_product_admin_flow_choropleth_binds_primary(registry, clean_session):
    """#784: 行政统计流绑定 spatial_aggregate 的 fill → primary 角色（旧实现
    一律 reference）；admin_aggregation 步骤经 provenance 映射标记完成，
    statistics 维不再误报 missing。"""
    from app.services.analysis_cartography_converter import (
        convert_analysis_to_mapspec_layer,
    )
    from app.services.mapspec_store import mapspec_store
    agg_layer, _, _ = convert_analysis_to_mapspec_layer({
        "geojson": _polygon_fc(5), "algorithm": "spatial_aggregate",
    })
    agg_layer["id"] = "result-aggregate"
    await mapspec_store.layer_upsert(clean_session, agg_layer, _polygon_fc(5))

    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "统计各区小学数量并绘图", "session_id": clean_session,
         "layer_ids": ["result-aggregate"]},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["recipe_id"] == "administrative_choropleth"
    binding = next(b for b in res["layers"] if b["layer_id"] == "result-aggregate")
    assert binding["role"] == "primary", "choropleth 主层不得降级为 reference"
    assert binding["cartography"] == "administrative_choropleth"
    assert binding["layer_type"] == "fill"
    comp = res["completeness"]
    assert comp["present"]["statistics"] is True
    assert comp["complete"] is True


@pytest.mark.asyncio
async def test_map_product_polygon_primary_ref_no_duplicate_fill(registry, clean_session):
    """#784: 面 primary_ref 不再授权 product-*-points 常量 fill 重复层
    （need_points 必须以真实点几何为前提）。"""
    from app.services.mapspec_store import mapspec_store
    ref = await session_data_manager.store(clean_session, _polygon_fc(), prefix="geojson")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session, "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    spec = await mapspec_store.get_mapspec(clean_session)
    assert not any(
        ly["id"].endswith("-points") or ly.get("type") == "circle"
        for ly in spec["layers"]
    ), "面主数据上不得授权点叠加/重复 fill 层"


# ─── #781: 栅格 primary_ref 不落 POI 产品族 ───────────────────────────────


def _raster_payload() -> dict:
    return {"file_path": "/tmp/ndvi_test.tif", "bounds": [104.0, 30.6, 104.1, 30.7],
            "width": 8, "height": 8, "band": 1}


@pytest.mark.asyncio
async def test_map_product_raster_query_routes_raster_recipe(registry, clean_session):
    """#781: 栅格查询（intent 阶段）→ raster_distribution recipe，产品阶段
    不再对栅格 ref 授权挂在 geojson 名义源上的空 circle 层。"""
    from app.services.mapspec_store import mapspec_store
    ref = await session_data_manager.store(clean_session, _raster_payload(), prefix="raster")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "分析某栅格NDVI并生成专题图", "session_id": clean_session,
         "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert res["recipe_id"] == "raster_distribution"
    assert res["layers"] == [], "栅格主数据不得经 POI 授权路径产出 circle 层"
    spec = await mapspec_store.get_mapspec(clean_session)
    for ly in spec["layers"]:
        source = spec.get("sources", {}).get(ly.get("source", ""), {})
        if ly.get("type") == "circle":
            assert source.get("type") != "geojson" or source.get("ref_id") != ref, (
                "不得提交 geojson 名义源指向栅格 artifact 的 circle 层"
            )


@pytest.mark.asyncio
async def test_map_product_raster_descriptor_gates_point_authoring(registry, clean_session):
    """#781: 即便查询是 POI 措辞，descriptor 判定 raster 的 primary_ref
    也不触发点层授权（gate 不依赖 intent 词汇命中）。"""
    ref = await session_data_manager.store(clean_session, _raster_payload(), prefix="raster")
    res = await registry.dispatch(
        "webgis_map_product",
        {"query": "成都小学的分布情况", "session_id": clean_session, "primary_ref": ref},
        session_id=clean_session,
    )
    assert res["success"] is True
    assert all(not b["layer_id"].endswith("-points") for b in res["layers"])
    assert all(b.get("layer_type") != "circle" for b in res["layers"])


# ─── #780 / #785: intent 工具面的 hint 语义 ──────────────────────────────


@pytest.mark.asyncio
async def test_map_intent_invalid_task_hint_degrades_gracefully(registry):
    """#780: 单个非法 task hint 值不再让整个 intent 工具调用失败。"""
    res = await registry.dispatch(
        "webgis_map_intent",
        {"query": "成都小学的分布情况", "task_hint": "accessibility"},
        session_id=None,
    )
    assert res["success"] is True
    assert res["intent"]["task"] == "distribution_overview"
    assert any("rejected (invalid value)" in h for h in res["intent"]["hint_applied"])


@pytest.mark.asyncio
async def test_map_intent_subject_hint_typed_by_token_tables(registry):
    """#785: 主体提示经栅格/面/线 token 表定类型 —— NDVI 是 raster 主体，
    不再无条件 poi（geometry_expectation 同步重算）。"""
    res = await registry.dispatch(
        "webgis_map_intent",
        {"query": "分析这个数据并制图", "subject_hint": "NDVI"},
        session_id=None,
    )
    intent = res["intent"]
    assert intent["subject"]["type"] == "raster"
    assert intent["subject"]["category"] == "ndvi"
    assert intent["entity_type"] == "raster"
    assert intent["geometry_expectation"] == "raster"
