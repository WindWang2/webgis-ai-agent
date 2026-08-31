"""Product Facet Contract（产品类型级 facet 必需性）单元测试。

不变式（product_facets.py）：
- 派生只读、确定性（同章节必同契约）、零持久化；
- 输入全部是既有事实（recipe_id / template_selection / in-process registry）；
- 保守诚实：不可解析 → 空契约/部分契约，绝不虚构 required；
- chart:required / legend:required 合成在真实 planner 路径上生效
  （export_profile 断线修复）。
"""
from app.services.gis_harness.product_facets import (
    EMPTY_FACET_CONTRACT,
    derive_facet_contract,
)
from app.services.gis_harness.product_graph import (
    KIND_CHART,
    KIND_LEGEND,
    KIND_MAP_LAYER,
    build_facet_completion,
    build_product_graph,
)
from app.services.gis_harness.planner import MapProductPlanner
from app.services.gis_harness.intent import resolve_map_request_intent


def _chapter(recipe_id="categorical_distribution", **extra):
    chapter = {
        "plan_id": "p1",
        "query": "成都各类学校占比",
        "recipe_id": recipe_id,
        "intent": {"task": "categorical_distribution"},
        "data_requirements": [
            {"capability": "poi_query", "status": "available", "bound_ref": "ref:geojson-poi"},
        ],
        "analysis_steps": [
            {"capability": "category_breakdown", "status": "done",
             "bound_ref": "ref:stats-1"},
        ],
        "map_layers": [
            {"role": "primary", "layer_id": "poi-points", "enabled": True,
             "source_capability": "poi_query"},
        ],
        "template_selection": {},
    }
    chapter.update(extra)
    return chapter


# ── 契约推导 ────────────────────────────────────────────────────────────


def test_contract_deterministic_same_chapter_same_result():
    chapter = _chapter()
    assert derive_facet_contract(chapter) == derive_facet_contract(chapter)


def test_contract_chart_required_from_recipe_export_profile():
    """categorical_distribution recipe 声明 export_profile.chart=True ——
    契约直接从 recipe 推导（不再依赖手工注入 template_selection）。"""
    contract = derive_facet_contract(_chapter())
    assert contract.recipe_id == "categorical_distribution"
    assert contract.chart_required is True
    assert any("export_profile.chart" in s for s in contract.sources)


def test_contract_empty_for_unresolvable_recipe():
    contract = derive_facet_contract(_chapter(recipe_id="no-such-recipe"))
    assert contract.chart_required is False
    assert contract.legend_required is False
    assert contract.recipe_id == "no-such-recipe"


def test_contract_empty_for_none_chapter():
    assert derive_facet_contract(None) == EMPTY_FACET_CONTRACT
    assert derive_facet_contract("not-a-dict") == EMPTY_FACET_CONTRACT


def test_contract_legend_required_from_composition_required_slot():
    """composition.density_map 把 colorbar 声明为 required 槽位 ——
    产品图视角下图例族缺席即欠账。"""
    chapter = _chapter(
        recipe_id="poi_distribution_overview",
        template_selection={"composition_template_id": "composition.density_map"},
    )
    contract = derive_facet_contract(chapter)
    assert contract.composition_template_id == "composition.density_map"
    assert contract.legend_required is True
    assert "continuous_colorbar" in contract.required_component_types
    assert any(s.startswith("composition.density_map") for s in contract.sources)


def test_contract_legend_informational_without_composition():
    contract = derive_facet_contract(_chapter())
    assert contract.legend_required is False


def test_contract_legacy_template_selection_profile_still_honored():
    """旧会话/手工注入的 template_selection.export_profile.chart 兼容读面。"""
    chapter = _chapter(
        recipe_id="poi_distribution_overview",
        template_selection={"export_profile": {"formats": ["png"], "chart": True}},
    )
    assert derive_facet_contract(chapter).chart_required is True


def test_contract_product_type_from_intent_task():
    contract = derive_facet_contract(_chapter())
    assert contract.product_type == "categorical_distribution"


# ── 产品图合成（contract 驱动）──────────────────────────────────────────


def test_chart_required_node_synthesized_via_recipe_contract():
    """recipe export_profile.chart=True 而无 enabled chart 组件 → 合成
    chart:required pending 节点（真实路径，非手工注入 export_profile）。"""
    graph = build_product_graph(_chapter(), {"layers": [], "sources": {}, "layout": {}})
    chart_required = [
        n for n in graph.nodes
        if n.kind == KIND_CHART and n.key == "chart-required"
    ]
    assert chart_required and chart_required[0].status == "pending"


def test_chart_required_not_synthesized_when_chart_present():
    mapspec = {
        "layers": [],
        "sources": {},
        "layout": {"components": [
            {"id": "c1", "type": "chart_panel", "enabled": True,
             "options": {"chartRef": "ref:chart-1"}},
        ]},
    }
    graph = build_product_graph(_chapter(), mapspec)
    assert not [n for n in graph.nodes if n.key == "chart-required"]


def test_legend_required_node_synthesized_with_done_layer():
    """density_map 组合 + 主题层已落 MapSpec + 无图例族组件 → legend 欠账。"""
    chapter = _chapter(
        recipe_id="poi_distribution_overview",
        template_selection={"composition_template_id": "composition.density_map"},
    )
    mapspec = {
        "layers": [{"id": "poi-points", "source": "s1", "type": "heatmap"}],
        "sources": {},
        "layout": {"components": []},
    }
    graph = build_product_graph(chapter, mapspec)
    legend_required = [
        n for n in graph.nodes
        if n.kind == KIND_LEGEND and n.key == "legend-required"
    ]
    assert legend_required and legend_required[0].status == "pending"


def test_legend_required_not_synthesized_without_layers():
    """无层不欠图例 —— 诚实护栏：不给空地图虚构欠账。"""
    chapter = _chapter(
        recipe_id="poi_distribution_overview",
        template_selection={"composition_template_id": "composition.density_map"},
    )
    graph = build_product_graph(chapter, {"layers": [], "sources": {}, "layout": {}})
    assert not [n for n in graph.nodes if n.key == "legend-required"]


def test_legend_required_satisfied_by_existing_colorbar():
    chapter = _chapter(
        recipe_id="poi_distribution_overview",
        template_selection={"composition_template_id": "composition.density_map"},
    )
    mapspec = {
        "layers": [{"id": "poi-points", "source": "s1", "type": "heatmap"}],
        "sources": {},
        "layout": {"components": [
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "poi-points"}},
        ]},
    }
    graph = build_product_graph(chapter, mapspec)
    assert not [n for n in graph.nodes if n.key == "legend-required"]


# ── facet required / dependencies ───────────────────────────────────────


def test_facet_required_from_contract():
    chapter = _chapter(recipe_id="poi_distribution_overview")
    mapspec = {
        "layers": [{"id": "poi-points", "source": "s1", "type": "heatmap"}],
        "sources": {},
        "layout": {"components": [
            {"id": "legend-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "poi-points"}},
        ]},
    }
    facets = build_facet_completion(chapter, mapspec)
    legend = next(f for f in facets if f.kind == KIND_LEGEND)
    assert legend.required is False  # 无组合契约 → informational


def test_facet_dependencies_layer_and_chart():
    """facet 供给边：map_layer ← analysis 行；chart/statistics ← 表/聚合类
    analysis 行（chart 欠账 ≠ 重查数据 的产品图表达）。"""
    chapter = _chapter(recipe_id="administrative_choropleth")
    chapter["intent"] = {"task": "administrative_statistic"}
    chapter["analysis_steps"].append(
        {"capability": "admin_aggregation", "status": "done",
         "bound_ref": "ref:stats-admin"},
    )
    mapspec = {
        "layers": [{"id": "poi-points", "source": "s1", "type": "circle"}],
        "sources": {},
        "layout": {"components": [
            {"id": "chart-panel", "type": "chart_panel", "enabled": True,
             "options": {"chartRef": "ref:chart-1"}},
        ]},
    }
    facets = build_facet_completion(chapter, mapspec)
    layer = next(f for f in facets if f.kind == KIND_MAP_LAYER)
    assert "analysis:poi_query" in layer.dependencies
    chart = next(f for f in facets if f.kind == KIND_CHART)
    # admin_aggregation 产出 admin_aggregate_table（表类）→ chart 供给上游
    assert "analysis:admin_aggregation" in chart.dependencies


def test_facet_completion_serializable_bounded():
    chapter = _chapter()
    facets = build_facet_completion(chapter, {"layers": [], "sources": {}, "layout": {}})
    for f in facets:
        d = f.to_dict()
        assert isinstance(d["required"], bool)
        assert len(d["dependencies"]) <= 4
        assert len(d["capability_ids"]) <= 4


# ── planner 接线（export_profile 断线修复 + 任务条件能力）────────────────


def _planner():
    return MapProductPlanner()


def test_planner_finalized_template_selection_carries_export_profile():
    intent = resolve_map_request_intent("成都各类公园占比分布")
    planner = _planner()
    plan = planner.plan_from_intent(intent)
    finalized = planner.finalize_with_profile(plan, None)
    recipe = planner.recipes.get(finalized.recipe_id)
    assert recipe is not None
    profile = finalized.template_selection.get("export_profile")
    assert isinstance(profile, dict) and profile == dict(recipe.export_profile)


def test_change_detection_task_plans_raster_change_capability():
    intent = resolve_map_request_intent("对比这一区域两个时期的卫星影像变化")
    assert intent.task == "change_detection"
    planner = _planner()
    plan = planner.plan_from_intent(intent)
    caps = [r.capability for r in plan.data_requirements] + [
        s.capability for s in plan.analysis_steps
    ]
    assert "raster_change_detection" in caps


def test_plain_raster_task_does_not_plan_change_capability():
    intent = resolve_map_request_intent("展示这片区域的高程栅格分布")
    assert intent.task == "raster_distribution"
    planner = _planner()
    plan = planner.plan_from_intent(intent)
    caps = [r.capability for r in plan.data_requirements] + [
        s.capability for s in plan.analysis_steps
    ]
    assert "raster_change_detection" not in caps


# ── Scenario G：遥感产品闭环（NDVI 链）───────────────────────────────────


def test_scenario_g_ndvi_product_closure():
    """Scenario G（/goal §15）：NDVI 产品链 ——
    遥感意图 → raster recipe/remote_sensing 模板 → raster_source 能力行 →
    composition.remote_sensing_map（colorbar required 槽）→ facet contract
    legend_required → 有 colorbar 绑定时语义 QA 零发现。

    样式变化不重算 NDVI 由 raster runtime v3 契约锁定
    （test_raster_runtime_v3：reuse 命中 + 样式仅进瓦片缓存键）—— 此处
    锁定产品图/契约层的应然构成。
    """
    from app.services.gis_harness.map_completion import validate_semantics
    from app.services.gis_harness.product_graph import KIND_MAP_LAYER

    intent = resolve_map_request_intent("计算这片区域卫星影像的NDVI植被指数分布")
    planner = _planner()
    plan = planner.plan_from_intent(intent)
    finalized = planner.finalize_with_profile(plan, None)

    # 数据/能力面：raster_source 在计划行中（光谱指数由 raster runtime v3 执行）
    caps = [r.capability for r in finalized.data_requirements] + [
        s.capability for s in finalized.analysis_steps
    ]
    assert "raster_source" in caps

    # 版面面：remote_sensing 组合 → colorbar 为 required 槽
    contract = derive_facet_contract(finalized.model_dump())
    assert contract.composition_template_id == "composition.remote_sensing_map"
    assert contract.legend_required is True
    assert "continuous_colorbar" in contract.required_component_types

    # 产品图：raster 图层 facet 在场（绑定后的章节 —— layer_id 由执行期
    # 回填，同真实会话流）。
    chapter = finalized.model_dump()
    chapter["map_layers"] = [
        {"role": "primary", "layer_id": "ndvi-surface", "enabled": True,
         "source_capability": "raster_source"},
    ]
    graph = build_product_graph(chapter, {
        "layers": [{"id": "ndvi-surface", "source": "s1", "type": "raster",
                    "legend_spec": {"type": "continuous", "field": "ndvi",
                                    "min": -1, "max": 1, "palette_colors": ["#fff", "#000"]}}],
        "sources": {},
        "layout": {"components": [
            {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
             "options": {"layerId": "ndvi-surface"}},
        ]},
    })
    assert any(n.kind == KIND_MAP_LAYER for n in graph.nodes)
    assert not [n for n in graph.nodes if n.key == "legend-required"]  # colorbar 已满足

    # 语义 QA：连续 legend_spec + 正确 colorbar 绑定 → 零发现
    findings = validate_semantics(
        chapter,
        {
            "layers": [{"id": "ndvi-surface", "source": "s1", "type": "raster",
                        "legend_spec": {"type": "continuous", "field": "ndvi",
                                        "min": -1, "max": 1, "palette_colors": ["#fff", "#000"]}}],
            "sources": {},
            "layout": {"components": [
                {"id": "colorbar-main", "type": "continuous_colorbar", "enabled": True,
                 "options": {"layerId": "ndvi-surface"}},
            ]},
        },
        [],
        contract=contract,
    )
    assert findings == []


def test_scenario_g_ndvi_missing_colorbar_owed_in_product_graph():
    """colorbar 缺席 → legend:required 欠账节点（QA/产品图双通道可见）。"""
    intent = resolve_map_request_intent("计算这片区域卫星影像的NDVI植被指数分布")
    planner = _planner()
    finalized = planner.finalize_with_profile(planner.plan_from_intent(intent), None)
    chapter = finalized.model_dump()
    chapter["map_layers"] = [
        {"role": "primary", "layer_id": "ndvi-surface", "enabled": True,
         "source_capability": "raster_source"},
    ]
    graph = build_product_graph(chapter, {
        "layers": [{"id": "ndvi-surface", "source": "s1", "type": "raster"}],
        "sources": {},
        "layout": {"components": []},
    })
    owed = [n for n in graph.nodes if n.key == "legend-required"]
    assert owed and owed[0].status == "pending"


def test_annotation_facet_has_no_table_dependencies():
    """annotation 是文本注记，不从统计表派生 —— 供给边只属于
    chart/statistics（GIS review F7）。"""
    chapter = _chapter()
    chapter["analysis_steps"].append(
        {"capability": "admin_aggregation", "status": "done", "bound_ref": "ref:stats-1"},
    )
    mapspec = {
        "layers": [],
        "sources": {},
        "layout": {"components": [
            {"id": "note-1", "type": "annotation", "enabled": True,
             "options": {"text": "主城区"}},
        ]},
    }
    facets = build_facet_completion(chapter, mapspec)
    annotation = next(f for f in facets if f.kind == "annotation")
    assert annotation.dependencies == []
    chart_missing = next(
        (f for f in facets if f.kind == KIND_CHART and f.key == "chart-required"), None)
    if chart_missing:
        assert "analysis:admin_aggregation" in chart_missing.dependencies
