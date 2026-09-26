# -*- coding: utf-8 -*-
"""Golden product corpus 生成器（ADR-0183 M9）。

确定性合成 54+ 产品语义案例（zh/en、缺数据、降级、输出变体、编辑序列、
组件族扫描）。只在本仓库开发流程中运行一次（或语料需要演进时重跑），
产物提交为 tests/fixtures/product_corpus/product_cases.json。
"""
import json
import pathlib

cases = []


def add(cid, *, query, lang, archetype, task, charts=None, statistics=None,
        output_intents=None, primary_layer=None, bound=True,
        expected_views=None, expected_relation_kinds=None,
        expected_fallback_codes=None, expected_complete=True,
        expected_completeness_codes=None, delivery=None, edits=None, **extra):
    case = {
        "id": cid, "query": query, "lang": lang,
        "archetype": archetype, "task": task,
        "charts": charts or [], "statistics": statistics or [],
        "output_intents": output_intents or ["map"],
        "primary_layer": primary_layer, "bound": bound,
        "expected_views": expected_views or ["map"],
        "expected_relation_kinds": expected_relation_kinds or [],
        "expected_fallback_codes": expected_fallback_codes or [],
        "expected_complete": expected_complete,
        "expected_completeness_codes": expected_completeness_codes or [],
        "delivery": delivery, "edits": edits or [],
    }
    case.update(extra)
    cases.append(case)


def layer(lt, carto):
    return {"layer_type": lt, "cartography": carto}


HEAT = layer("heatmap", "visual_heatmap")
FILL = layer("fill", "administrative_choropleth")
POINT = layer("circle", "simple_point_map")
RASTER = layer("raster", "raster_surface")

# ── 1. 六原型 × 中英（12）───────────────────────────────────────────────
arch_q = [
    ("distribution_overview", "poi_distribution", "成都小学分布情况",
     "Primary school distribution in Chengdu", HEAT),
    ("regional_comparison", "administrative_statistic", "成都各区小学数量对比",
     "Compare primary school counts across Chengdu districts", FILL),
    ("density_analysis", "density_quantitative", "成都人口密度分析",
     "Population density analysis in Chengdu", HEAT),
    ("remote_sensing", "vegetation_index", "成都NDVI植被指数分布",
     "NDVI vegetation index over Chengdu", RASTER),
    ("simple_view", "simple_view", "看看成都的大学",
     "Show me universities in Chengdu", POINT),
    ("proportional_symbol", "proportional_symbol", "成都各区GDP规模分布",
     "GDP proportional symbols across Chengdu districts", POINT),
]
for arch, task, qzh, qen, ly in arch_q:
    base = dict(archetype=arch, task=task, primary_layer=ly,
                bound=arch != "remote_sensing")
    if arch == "regional_comparison":
        base["statistics"] = ["admin_aggregation", "ranking"]
    add(f"arch-{arch}-zh", query=qzh, lang="zh", **base)
    add(f"arch-{arch}-en", query=qen, lang="en", **base)

# ── 2. 输出信号组合（10）───────────────────────────────────────────────
signal_combo = [
    ("chart", {"charts": ["bar"], "output_intents": ["map", "chart"]},
     {"expected_views": ["map", "chart"],
      "expected_relation_kinds": ["chart_linked_to_map"]}),
    ("stats", {"statistics": ["total"], "output_intents": ["map", "statistics"]},
     {"expected_views": ["map", "stats_panel"],
      "expected_relation_kinds": ["derived_statistic"]}),
    ("chart-alias", {"charts": ["admin_bar"], "output_intents": ["map", "chart"]},
     {"expected_views": ["map", "chart"],
      "expected_fallback_codes": ["chart_kind_unmapped"]}),
    ("chart-stats", {"charts": ["grouped_bar"], "statistics": ["ranking"],
                     "output_intents": ["map", "chart", "statistics"]},
     {"expected_views": ["map", "chart", "stats_panel"]}),
    ("en-chart", {"charts": ["timeseries"], "output_intents": ["map", "chart"]},
     {"expected_views": ["map", "chart"]}),
    ("export-png", {"output_intents": ["map"],
                    "delivery": {"targets": ["interactive", "png"]}}, {}),
    # F14：publication 矢量链已渲染 statistics_panel 族 —— 此前的
    # export_partial_coverage 披露不再触发（预期空 = 无覆盖缺口）。
    ("export-pdf-csv", {"statistics": ["total"],
                        "output_intents": ["map", "statistics"],
                        "delivery": {"targets": ["interactive", "pdf", "csv"]}},
     {"expected_completeness_codes": []}),
    ("export-svg", {"output_intents": ["map"], "delivery": {"targets": ["svg"]}}, {}),
    ("report-audience", {"statistics": ["total"],
                         "output_intents": ["map", "statistics"],
                         "delivery": {"targets": ["interactive", "pdf"],
                                      "audience": "report"}}, {}),
    ("aspect-169", {"output_intents": ["map"],
                    "delivery": {"targets": ["interactive", "png"],
                                 "aspect": "16:9"}}, {}),
]
for name, plan_fields, extra in signal_combo:
    add(f"signal-{name}", query=f"signal case {name}", lang="en",
        archetype="distribution_overview", task="poi_distribution",
        primary_layer=HEAT, **plan_fields, **extra)

# ── 3. 缺数据 / 降级（8）───────────────────────────────────────────────
add("degraded-no-bound-ref", query="成都小学分布情况", lang="zh",
    archetype="distribution_overview", task="poi_distribution",
    primary_layer=HEAT, bound=False)
add("degraded-no-layer", query="成都小学分布情况", lang="zh",
    archetype="distribution_overview", task="poi_distribution",
    primary_layer=None, bound=False)
add("degraded-unknown-archetype", query="hologram request xyz", lang="en",
    archetype="", task="simple_view", primary_layer=POINT)
add("degraded-unknown-chart-kind", query="unknown chart kind case", lang="en",
    archetype="distribution_overview", task="poi_distribution",
    charts=["hologram_chart"], output_intents=["map", "chart"],
    primary_layer=HEAT, expected_views=["map", "chart"],
    expected_fallback_codes=["chart_kind_unmapped"])
add("degraded-unknown-composition", query="composition missing case", lang="en",
    archetype="density_analysis", task="density_quantitative",
    primary_layer=HEAT)
add("degraded-unknown-recipe", query="unknown recipe case", lang="en",
    archetype="regional_comparison", task="administrative_statistic",
    statistics=["admin_aggregation"], output_intents=["map", "statistics"],
    primary_layer=FILL, expected_views=["map", "stats_panel"])
add("degraded-empty-targets", query="empty delivery case", lang="en",
    archetype="simple_view", task="simple_view", primary_layer=POINT,
    delivery={"targets": ["interactive"]})
add("degraded-no-delivery", query="no delivery case", lang="en",
    archetype="simple_view", task="simple_view", primary_layer=POINT)

# ── 4. 编辑序列（14）───────────────────────────────────────────────────
edit_seq = [
    ("remove-stats", [{"op": "remove_view", "target": "v-stats"}],
     {"expected_views_after": ["map", "chart"]}),
    ("chart-to-bar", [{"op": "replace_component", "target": "v-chart",
                       "payload": {"chart_kind": "bar"}}],
     {"expected_chart_kind": "bar", "expected_unapplied": ["chart_regen"]}),
    ("add-inset", [{"op": "add_view", "target": "",
                    "payload": {"view": {"view_id": "v-inset", "kind": "inset",
                                         "title": "主城区"}}}],
     {"expected_views_after": ["map", "chart", "stats_panel", "inset"]}),
    ("filter-farmland", [{"op": "set_view_filter", "target": "v-map",
                          "payload": {"filter": {"category": "耕地"}}}],
     {"expected_filter": {"category": "耕地"},
      "expected_unapplied": ["data_requery"]}),
    ("delivery-169", [{"op": "set_delivery", "target": "",
                       "payload": {"delivery": {"targets": ["interactive", "png"],
                                                "aspect": "16:9"}}}],
     {"expected_aspect": "16:9"}),
    ("toggle-off-chart", [{"op": "toggle_component", "target": "chart_panel",
                           "payload": {"component_type": "chart_panel",
                                       "enabled": False}}],
     {}),
    ("caption", [{"op": "set_caption", "target": "v-map",
                  "payload": {"text": "2024年成都小学分布"}}],
     {"expected_title": "2024年成都小学分布"}),
    ("two-edits", [{"op": "set_view_filter", "target": "v-map",
                    "payload": {"filter": {"year": "2024"}}},
                   {"op": "replace_component", "target": "v-chart",
                    "payload": {"chart_kind": "grouped_bar"}}],
     {"expected_filter": {"year": "2024"}, "expected_chart_kind": "grouped_bar"}),
    ("retract-comparison", [{"op": "add_view", "target": "",
                             "payload": {"view": {"view_id": "v-compare",
                                                  "kind": "comparison",
                                                  "required": True}}},
                            {"op": "remove_view", "target": "v-compare",
                             "reason": "不要对比了"}],
     {"expected_views_after": ["map", "chart", "stats_panel"]}),
    ("invalid-remove-unknown", [{"op": "remove_view", "target": "ghost"}],
     {"expect_edit_error": True}),
    ("invalid-chart-kind", [{"op": "replace_component", "target": "v-chart",
                             "payload": {"chart_kind": "hologram"}}],
     {"expect_edit_error": True}),
    ("invalid-delivery", [{"op": "set_delivery", "target": "",
                           "payload": {"delivery": {"targets": ["hologram"]}}}],
     {"expect_edit_error": True}),
    ("invalid-add-dup", [{"op": "add_view", "target": "",
                          "payload": {"view": {"view_id": "v-map", "kind": "map"}}}],
     {"expect_edit_error": True}),
    ("unknown-op", [{"op": "teleport", "target": ""}],
     {"expect_edit_error": True}),
]
base_edit = dict(archetype="regional_comparison", task="administrative_statistic",
                 statistics=["admin_aggregation"], charts=["grouped_bar"],
                 output_intents=["map", "statistics", "chart"],
                 primary_layer=FILL,
                 expected_views=["map", "chart", "stats_panel"])
for name, edits, extra in edit_seq:
    add(f"edit-{name}", query=f"edit case {name}", lang="zh",
        edits=edits, **base_edit, **extra)

# ── 5. MapSpec 兼容 / chart kind 扫描（6）──────────────────────────────
compat_combo = [
    ("sweep1", ["bar", "line", "pie"]),
    ("sweep2", ["scatter", "histogram", "ranking_list"]),
    ("sweep3", ["rose", "donut", "heat_matrix"]),
    ("sweep4", ["area", "box_plot", "cumulative"]),
    ("sweep5", ["grouped_bar", "stacked_bar", "horizontal_bar"]),
    ("sweep6", ["kpi_card", "radar", "timeseries", "violin"]),
]
for name, kinds in compat_combo:
    add(f"compat-chart-{name}", query=f"compat {name}", lang="en",
        archetype="density_analysis", task="density_quantitative",
        charts=[kinds[0]], statistics=["grid_aggregate"],
        output_intents=["map", "chart", "statistics"],
        primary_layer=HEAT, expected_views=["map", "chart", "stats_panel"],
        edits=[{"op": "replace_component", "target": "v-chart",
                "payload": {"chart_kind": k}} for k in kinds],
        expected_chart_kind=kinds[-1])

# ── 6. 对比声明 / 插图（4）─────────────────────────────────────────────
add("comparison-claim-zh", query="成都各区小学数量对比", lang="zh",
    archetype="regional_comparison", task="administrative_statistic",
    statistics=["admin_aggregation"], output_intents=["map", "statistics"],
    primary_layer=FILL, expected_views=["map", "stats_panel"],
    expected_completeness_codes=["comparison_claim_unsupported"])
add("comparison-with-view", query="成都各区小学数量对比", lang="zh",
    archetype="regional_comparison", task="administrative_statistic",
    statistics=["admin_aggregation"], output_intents=["map", "statistics"],
    primary_layer=FILL, expected_views=["map", "stats_panel"],
    edits=[{"op": "add_view", "target": "",
            "payload": {"view": {"view_id": "v-compare", "kind": "comparison",
                                 "required": True}}},
           {"op": "add_view", "target": "",
            "payload": {"view": {"view_id": "v-stats2", "kind": "stats_panel"}}}],
    expected_complete=True)
add("comparison-en", query="Compare school counts across Chengdu districts",
    lang="en", archetype="regional_comparison", task="administrative_statistic",
    statistics=["admin_aggregation"], output_intents=["map", "statistics"],
    primary_layer=FILL, expected_views=["map", "stats_panel"])
add("inset-edit-zh", query="成都各区小学数量对比，加主城区插图", lang="zh",
    archetype="regional_comparison", task="administrative_statistic",
    statistics=["admin_aggregation"], output_intents=["map", "statistics"],
    primary_layer=FILL, expected_views=["map", "stats_panel"],
    edits=[{"op": "add_view", "target": "",
            "payload": {"view": {"view_id": "v-inset", "kind": "inset",
                                 "required": True, "title": "主城区"}}}],
    expected_views_after=["map", "stats_panel", "inset"])

out = {"version": "1.0",
       "description": "Semantic Map Product Graph golden corpus (ADR-0183 M9)",
       "cases": cases}
p = pathlib.Path(__file__).resolve().parent  # scripts/
target = p / "product_cases.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
ids = [c["id"] for c in cases]
assert len(ids) == len(set(ids)), "duplicate case ids"
print("cases:", len(cases), "->", target)
