"""Golden Corpus builder — 结构化 golden 语料（ADR-0101 D8）.

确定性用例矩阵：native 模型 × 兼容组合模板 × output target × locale ×
page profile + 边界用例（planned 泄漏门、缺 context、fallback 触发、
多层绑定、极端标题）。

每个用例固化 resolve → compose → validate → solve 的**结构化 digest**
（非像素）：选型、组件实例、组合校验、布局求解。黄金文件入库受
``GOLDEN_CORPUS_UPDATE=1`` 显式刷新 —— 禁止无审查的批量快照更新。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

GOLDEN_DIR = Path(__file__).parent / "goldens"

LONG_TITLE_ZH = "成都市国土空间总体规划「三区三线」实施评估与城市更新单元划定专题图（2021—2035 年）"
LONG_TITLE_EN = "Chengdu Territorial Spatial Planning Implementation Assessment & Urban Renewal Unit Delineation Thematic Map (2021-2035)"
EXTREME_LEGEND_LABEL = "0.000012345 – 9876543210.12345（超大跨度数值分级区间标签极端用例）"


def _native_templates_by_type(component_type: str) -> List[Any]:
    """确定性序的 native 组件模板（variants 维度钉选用）。"""
    from app.lib.cartography.component_templates import SEED_COMPONENT_TEMPLATES
    return sorted(
        (t for t in SEED_COMPONENT_TEMPLATES
         if t.component_type == component_type and t.runtime_status == "native"),
        key=lambda t: t.id,
    )


def _model_output_targets(model) -> List[str]:
    targets = [t for t in model.export_compatibility if t in ("interactive", "png", "pdf", "svg")]
    return targets or ["interactive"]


def _pick_output(template_targets: List[str], model_targets: List[str]) -> str:
    for t in ("interactive", "png", "pdf"):
        if t in template_targets and t in model_targets:
            return t
    common = [t for t in template_targets if t in model_targets]
    return common[0] if common else (template_targets[0] if template_targets else "interactive")


def build_cases() -> List[Dict[str, Any]]:
    """确定性构建用例清单（排序稳定；同库状态永远同清单）。"""
    from app.lib.cartography.composition_templates import get_composition_template_registry
    from app.lib.cartography.model_library import get_map_model_registry

    model_reg = get_map_model_registry()
    compo_reg = get_composition_template_registry()

    cases: List[Dict[str, Any]] = []

    # ── 基础矩阵：native 模型 × 兼容模板（前 5 个，确定性序；V4 扩容）──
    for model_id in model_reg.native_ids():
        model = model_reg.resolve(model_id)
        candidates = [
            t for t in compo_reg.find_for_map_model(model_id)
            if not t.compatible_map_models or model_id in t.compatible_map_models
        ][:5]
        model_targets = _model_output_targets(model)
        for tpl in candidates:
            output = _pick_output(tpl.output_targets, model_targets)
            cases.append({
                "id": f"base::{model_id}::{tpl.id}::{output}",
                "kind": "base",
                "model": model_id,
                "template": tpl.id,
                "output": output,
                "profile": "viewport",
                "title": f"{model_id} 专题图",
                "subtitle": "",
                "context": [],
                "bindings": {"primary": "layer-primary"},
                "layer_models": {"layer-primary": model_id},
            })

    # ── locale / profile 变体（长标题中英 + A4 版式）───────────────────
    locale_models = [
        "administrative_choropleth", "normalized_choropleth", "diverging_choropleth",
        "risk_exposure_classes", "equity_assessment", "zoning_planning",
        "site_selection_result", "mcda_score_map", "change_comparison_map",
        "spectral_index_surface", "terrain_analytical_surface", "flow_od_arc",
        # V4 模型加入压力清单
        "bivariate_choropleth", "hillshade", "point_cluster", "dot_density_map",
        "temporal_comparison_map", "classification_result_map",
        "confusion_matrix_map", "kernel_density_surface",
        # V4 压力清单第二批
        "interpolation_result_map", "weighted_overlay_surface",
        "gwr_coefficient_map", "network_flow_map", "dbscan_cluster_map",
        "multi_ring_buffer_map", "voronoi_partition_map", "bivariate_raster",
        "uncertainty_choropleth", "sar_change_detection",
    ]
    for model_id in locale_models:
        if model_reg.resolve(model_id) is None:
            continue
        for tpl_id, profile, title in [
            ("composition.government_report_map", "a4_portrait", LONG_TITLE_ZH),
            ("composition.statistical_report", "a4_landscape", LONG_TITLE_EN),
        ]:
            if compo_reg.get(tpl_id) is None:
                continue
            cases.append({
                "id": f"stress::{model_id}::{tpl_id}::{profile}",
                "kind": "stress",
                "model": model_id,
                "template": tpl_id,
                "output": "pdf",
                "profile": profile,
                "title": title,
                "subtitle": "副标题：数据口径与评价方法说明（极端长度换行压力用例 / extreme-length wrapping stress）",
                "context": ["statistics"],
                "bindings": {"primary": "layer-primary"},
                "layer_models": {"layer-primary": model_id},
            })

    # ── 多层绑定用例 ───────────────────────────────────────────────────
    multi_cases = [
        ("visual_heatmap", "composition.standard_analysis",
         {"primary": "layer-heat", "secondary": "layer-choro"},
         {"layer-heat": "visual_heatmap", "layer-choro": "administrative_choropleth"}),
        ("administrative_choropleth", "composition.statistical_map",
         {"primary": "layer-2020", "secondary": "layer-2025"},
         {"layer-2020": "administrative_choropleth", "layer-2025": "administrative_choropleth"}),
        ("flow_od_arc", "composition.flow_map_report",
         {"primary": "layer-flow", "secondary": "layer-admin"},
         {"layer-flow": "flow_od_arc", "layer-admin": "categorical_thematic"}),
        # V4 多层绑定扩充
        ("bivariate_choropleth", "composition.statistical_report",
         {"primary": "layer-biv", "secondary": "layer-boundary"},
         {"layer-biv": "bivariate_choropleth", "layer-boundary": "administrative_aggregation"}),
        ("point_cluster", "composition.density_map",
         {"primary": "layer-cluster", "secondary": "layer-choro"},
         {"layer-cluster": "point_cluster", "layer-choro": "administrative_choropleth"}),
        ("hillshade", "composition.terrain_analysis",
         {"primary": "layer-shade", "secondary": "layer-tint"},
         {"layer-shade": "hillshade", "layer-tint": "classified_raster"}),
    ]
    for model_id, tpl_id, bindings, layer_models in multi_cases:
        if model_reg.resolve(model_id) is None or compo_reg.get(tpl_id) is None:
            continue
        cases.append({
            "id": f"multi::{model_id}::{tpl_id}",
            "kind": "multi",
            "model": model_id,
            "template": tpl_id,
            "output": "pdf",
            "profile": "viewport",
            "title": "多层绑定用例",
            "subtitle": "",
            "context": [],
            "bindings": bindings,
            "layer_models": layer_models,
        })

    # ── V4 variants 维度：每个 native 组件变体经 resolver 钉选走全链 ────
    from app.lib.cartography.component_registry import get_component_registry
    comp_reg = get_component_registry()
    for desc in sorted(comp_reg.native_descriptors(), key=lambda d: d.id):
        templates = [
            t for t in _native_templates_by_type(desc.type)
            if t.variant in desc.variants
        ]
        for tpl in templates:
            cases.append({
                "id": f"variants::{desc.type}::{tpl.id}",
                "kind": "variants",
                "model": "administrative_choropleth",
                "template": "composition.standard_analysis",
                "output": "interactive",
                "profile": "viewport",
                "title": f"变体钉选 {desc.type}/{tpl.variant}",
                "subtitle": "",
                "context": [],
                "bindings": {"primary": "layer-primary"},
                "layer_models": {"layer-primary": "administrative_choropleth"},
                "preferred_variants": {desc.type: tpl.id},
            })

    # ── V4 profiles 维度：page profile 矩阵（版式对布局的影响固化）─────
    for model_id in ("administrative_choropleth", "visual_heatmap",
                     "bivariate_choropleth", "hillshade",
                     "temporal_comparison_map", "confusion_matrix_map"):
        for profile in ("viewport", "a4_portrait", "a4_landscape",
                        "presentation_16x9", "academic_figure"):
            cases.append({
                "id": f"profiles::{model_id}::{profile}",
                "kind": "profiles",
                "model": model_id,
                "template": "composition.statistical_map",
                "output": "pdf",
                "profile": profile,
                "title": f"版式矩阵 {model_id}@{profile}",
                "subtitle": "",
                "context": ["statistics"],
                "bindings": {"primary": "layer-primary"},
                "layer_models": {"layer-primary": model_id},
            })

    # ── V4 layout 维度：约束式求解场景（V3 solver 语义固化）────────────
    for scenario in (
        "compact", "occupancy-avoid", "collision-group", "width-units",
        "wide-profile", "occupancy-full", "group-conflict", "avoid-chain",
        "compact-print", "mixed",
        "exclusive-top-center", "avoid-everywhere",
    ):
        cases.append({
            "id": f"layout::{scenario}",
            "kind": "layout",
            "model": "administrative_choropleth",
            "template": "",
            "output": "interactive",
            "profile": "viewport",
            "title": f"布局约束场景 {scenario}",
            "subtitle": "",
            "context": [],
            "bindings": {"primary": "layer-primary"},
            "layer_models": {"layer-primary": "administrative_choropleth"},
            "layout_scenario": scenario,
        })

    # ── V4 labels 维度：标注引擎确定性场景 ─────────────────────────────
    for scenario in (
        "point-preferred", "point-collision", "line-upright",
        "line-repeat", "polygon-inside", "min-zoom-gate",
        "callout-fallback", "viewport-edge",
        "shield-line", "wide-text", "dense-declutter", "mixed-kinds",
    ):
        cases.append({
            "id": f"labels::{scenario}",
            "kind": "labels",
            "model": "administrative_choropleth",
            "template": "",
            "output": "interactive",
            "profile": "viewport",
            "title": f"标注场景 {scenario}",
            "subtitle": "",
            "context": [],
            "bindings": {"primary": "layer-primary"},
            "layer_models": {"layer-primary": "administrative_choropleth"},
            "label_scenario": scenario,
        })

    # ── planned 门（实际不变量：planned 模型层不得获得任何绑定组件实例；
    # 组合选型不得落在按 planned 模型特化的模板上 —— resolver 记因
    # model_planned 后仅 generic 兜底。通用 chrome（title/scale_bar 等
    # 与模型无关）合法在场，不是门禁对象。）────────────────────────
    for model_id in model_reg.planned_ids():
        cases.append({
            "id": f"planned-gate::{model_id}",
            "kind": "planned-gate",
            "model": model_id,
            "template": "",
            "output": "interactive",
            "profile": "viewport",
            "title": "planned 门禁用例",
            "subtitle": "",
            "context": [],
            "bindings": {"primary": "layer-primary"},
            "layer_models": {"layer-primary": model_id},
        })

    # ── 缺失可选 context / fallback 触发 ───────────────────────────────
    cases.append({
        "id": "edge::no-context::statistical_report",
        "kind": "edge",
        "model": "administrative_choropleth",
        "template": "composition.statistical_report",
        "output": "pdf",
        "profile": "a4_portrait",
        "title": "缺失统计 context 用例",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "administrative_choropleth"},
    })
    cases.append({
        "id": "edge::unknown-model::minimal_interactive",
        "kind": "edge",
        "model": "visual_heatmap",
        "template": "composition.minimal_interactive",
        "output": "interactive",
        "profile": "viewport",
        "title": "极简兜底用例",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "visual_heatmap"},
    })

    # ── V4 edge 补充：钉选拒绝 / planned 模板拒绝 / 空 title / 极端图例 ──
    cases.append({
        "id": "edge::variant-pin::unknown-template",
        "kind": "edge",
        "model": "administrative_choropleth",
        "template": "composition.standard_analysis",
        "output": "interactive",
        "profile": "viewport",
        "title": "钉选不存在模板的拒绝路径",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "administrative_choropleth"},
        "preferred_variants": {"legend": "legend/does-not-exist"},
    })
    cases.append({
        "id": "edge::variant-pin::native-chart-kind",
        "kind": "edge",
        "model": "administrative_choropleth",
        "template": "composition.standard_analysis",
        "output": "interactive",
        "profile": "viewport",
        "title": "钉选 native 图表 kind 模板的接受路径",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "administrative_choropleth"},
        "preferred_variants": {"chart_panel": "chart-panel/kind-bar"},
    })
    cases.append({
        "id": "edge::empty-title::report_map",
        "kind": "edge",
        "model": "administrative_choropleth",
        "template": "composition.report_map",
        "output": "pdf",
        "profile": "a4_portrait",
        "title": "",
        "subtitle": "",
        "context": [],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "administrative_choropleth"},
    })
    cases.append({
        "id": "edge::extreme-legend-label::bivariate",
        "kind": "edge",
        "model": "bivariate_choropleth",
        "template": "composition.statistical_map",
        "output": "pdf",
        "profile": "a4_landscape",
        "title": "双变量极端图例标签用例",
        "subtitle": EXTREME_LEGEND_LABEL,
        "context": ["statistics"],
        "bindings": {"primary": "layer-primary"},
        "layer_models": {"layer-primary": "bivariate_choropleth"},
    })

    cases.sort(key=lambda c: c["id"])
    return cases


def _layout_scenario_participants(scenario: str) -> tuple:
    """layout:: 场景 → (participants, constraints, profile)。确定性构造。"""
    from app.lib.cartography.layout_solver import LayoutConstraints, LayoutParticipantV3

    def p(pid, ptype, zone, **kw):
        return LayoutParticipantV3(id=pid, type=ptype, requested_zone=zone, **kw)

    if scenario == "compact":
        parts = [p(f"c{i}", "chart_panel", "top-left", fallbacks=["top-right", "bottom-left"])
                 for i in range(3)]
        return parts, LayoutConstraints(compact=True), "viewport"
    if scenario == "occupancy-avoid":
        parts = [p("legend", "legend", "bottom-left", fallbacks=["bottom-right"],
                   avoid_zones=["bottom-left"])]
        return parts, LayoutConstraints(zone_occupancy={"bottom-left": 2}), "viewport"
    if scenario == "collision-group":
        parts = [p("legend-a", "legend", "bottom-left", collision_group="legend-family"),
                 p("legend-b", "legend", "bottom-left", collision_group="legend-family",
                   fallbacks=["bottom-right", "top-left", "top-right"])]
        return parts, LayoutConstraints(), "viewport"
    if scenario == "width-units":
        parts = [p("wide", "chart_panel", "top-left", width_units=2),
                 p("narrow", "statistics_panel", "top-left")]
        return parts, LayoutConstraints(), "a4_landscape"
    if scenario == "wide-profile":
        parts = [p("stats", "statistics_panel", "top-left"),
                 p("chart-1", "chart_panel", "top-left", fallbacks=["top-right"]),
                 p("chart-2", "chart_panel", "top-left", fallbacks=["top-right"])]
        return parts, LayoutConstraints(), "presentation_16x9"
    if scenario == "occupancy-full":
        parts = [p("chart-x", "chart_panel", "top-left", fallbacks=["top-right"])]
        return parts, LayoutConstraints(zone_occupancy={"top-left": 4, "top-right": 2}), "viewport"
    if scenario == "group-conflict":
        parts = [p(f"l{ch}", "legend", "bottom-left", collision_group="fam",
                   fallbacks=["bottom-right", "top-left", "top-right"])
                 for ch in "abcde"]
        return parts, LayoutConstraints(), "viewport"
    if scenario == "avoid-chain":
        parts = [p("inset", "inset_map", "top-right",
                   fallbacks=["top-left", "bottom-right"],
                   avoid_zones=["top-right", "top-left"])]
        return parts, LayoutConstraints(zone_occupancy={"top-right": 1, "top-left": 2}), "viewport"
    if scenario == "compact-print":
        parts = [p("legend", "legend", "bottom-left"),
                 p("chart", "chart_panel", "top-left"),
                 p("stats", "statistics_panel", "top-left")]
        return parts, LayoutConstraints(compact=True, print_mode=True), "a4_portrait"
    if scenario == "exclusive-top-center":
        parts = [
            p("title", "title", "top-center"),
            p("subtitle", "subtitle", "top-center", fallbacks=["top-left"]),
        ]
        return parts, LayoutConstraints(), "viewport"
    if scenario == "avoid-everywhere":
        parts = [p("legend", "legend", "bottom-left",
                   fallbacks=["bottom-right", "top-left"],
                   avoid_zones=["bottom-left", "bottom-right", "top-left"])]
        return parts, LayoutConstraints(
            zone_occupancy={"bottom-left": 2, "bottom-right": 1}), "viewport"
    # mixed
    parts = [
        p("title", "title", "top-center"),
        p("legend", "legend", "bottom-left", collision_group="legend-family"),
        p("colorbar", "continuous_colorbar", "bottom-left",
          collision_group="legend-family", fallbacks=["bottom-right"]),
        p("chart", "chart_panel", "top-left", width_units=2),
        p("stats", "statistics_panel", "top-left", fallbacks=["bottom-right"]),
    ]
    return parts, LayoutConstraints(zone_occupancy={"bottom-right": 1}), "a4_landscape"


def _label_scenario_solution(scenario: str):
    """labels:: 场景 → 确定性 LabelSolution。"""
    from app.lib.cartography.label_engine import (
        LabelEngineInput,
        LabelFeature,
        solve_labels,
    )

    def feat(fid, text, kind, geom, **kw):
        return LabelFeature(id=fid, text=text, kind=kind, geometry=geom, **kw)

    if scenario == "point-preferred":
        feats = [feat("p1", "天府广场", "point", [[104.06, 30.65]])]
        vp = [104.0, 30.5, 104.2, 30.8]
        return solve_labels(LabelEngineInput(features=feats, viewport=vp, zoom=12))
    if scenario == "point-collision":
        feats = [
            feat("hi", "高优先", "point", [[104.06, 30.65]], priority=1),
            feat("lo", "低优先标签文本", "point", [[104.061, 30.651]], priority=9),
        ]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=12))
    if scenario == "line-upright":
        feats = [feat("r1", "锦江", "line",
                      [[104.05, 30.60], [104.06, 30.70], [104.04, 30.75]])]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=11))
    if scenario == "line-repeat":
        feats = [
            feat("a1", "同文本河流", "line", [[104.05, 30.60], [104.10, 30.60]]),
            feat("a2", "同文本河流", "line", [[104.05, 30.70], [104.10, 30.70]]),
        ]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=11))
    if scenario == "polygon-inside":
        feats = [feat("d1", "锦江区", "polygon",
                      [[104.05, 30.60], [104.10, 30.60], [104.10, 30.66], [104.05, 30.66], [104.05, 30.60]])]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=11))
    if scenario == "min-zoom-gate":
        feats = [feat("small", "只在放大后出现", "point", [[104.06, 30.65]], min_zoom=13.0)]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=11))
    if scenario == "callout-fallback":
        feats = [
            feat("a", "AAA", "point", [[104.06, 30.65]], priority=1),
            feat("b", "BBB长标签", "point", [[104.0601, 30.6501]], priority=2,
                 max_displacement=4.0),
        ]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=12))
    if scenario == "viewport-edge":
        feats = [feat("edge", "边界注记", "point", [[104.005, 30.505]])]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=12))
    if scenario == "shield-line":
        feats = [feat("hwy", "G5 京昆高速", "line",
                      [[104.02, 30.55], [104.08, 30.62], [104.15, 30.66]],
                      shield=True)]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=10))
    if scenario == "wide-text":
        feats = [feat("w", "成都市国土空间总体规划", "point", [[104.06, 30.65]])]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=12))
    if scenario == "dense-declutter":
        # 局部测试数据构造：确定性 LCG 伪随机（固定种子，引擎本身无随机）
        state = 42

        def _lcg():
            nonlocal state
            state = (state * 1103515245 + 12345) % (2 ** 31)
            return state / (2 ** 31)

        feats = [
            feat(f"pt{i:02d}", f"点位{i:02d}", "point",
                 [[104.0 + _lcg() * 0.2, 30.5 + _lcg() * 0.3]],
                 priority=i % 10)
            for i in range(20)
        ]
        return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=12))
    # mixed-kinds
    feats = [
        feat("m-point", "点注记", "point", [[104.06, 30.65]]),
        feat("m-line", "线注记", "line", [[104.02, 30.55], [104.12, 30.62]]),
        feat("m-poly", "面注记", "polygon",
             [[104.05, 30.68], [104.09, 30.68], [104.09, 30.72], [104.05, 30.72],
              [104.05, 30.68]]),
    ]
    return solve_labels(LabelEngineInput(features=feats, viewport=[104.0, 30.5, 104.2, 30.8], zoom=11))


def _scenario_digest(case: Dict[str, Any]) -> Dict[str, Any]:
    """layout::/labels:: 场景 digest（不走组合管线；组件/校验占位为空 ——
    既有不变量测试对空组件集自然通过）。"""
    base = {
        "case": case["id"],
        "kind": case["kind"],
        "model": case["model"],
        "template": case["template"],
        "output": case["output"],
        "profile": case["profile"],
        "selection": {
            "composition_template_id": "",
            "selected": [],
            "required_slots": [],
            "component_templates": {},
            "reason_codes": ["scenario_case"],
            "rejected_count": 0,
        },
        "components": [],
        "validation": {"ok": True, "error_codes": [], "warning_codes": []},
        "layout": {
            "placements": {}, "moved": [], "suppressed": [],
            "warning_count": 0, "reasons": {}, "conflicts": [],
        },
    }
    if case["kind"] == "layout":
        from app.lib.cartography.layout_solver import solve_layout_v3
        parts, cons, profile = _layout_scenario_participants(case["layout_scenario"])
        sol = solve_layout_v3(parts, page_profile=profile, constraints=cons)
        base["profile"] = profile
        base["layout"] = {
            "placements": {p.id: p.zone for p in sol.placements},
            "moved": sorted(p.id for p in sol.placements if p.moved),
            "suppressed": sorted(p.id for p in sol.suppressed),
            "warning_count": len(sol.warnings),
            "reasons": {p.id: p.reason for p in sol.placements if p.reason != "requested"},
            "conflicts": sorted(c.component_id for c in sol.conflicts),
            "compact": sol.compact,
            "has_fallback_plan": bool(sol.fallback_plan_zh),
        }
    elif case["kind"] == "labels":
        sol = _label_scenario_solution(case["label_scenario"])
        base["labels"] = {
            "placed": sorted(
                (p.feature_id, p.status, round(p.x, 6), round(p.y, 6),
                 round(p.angle, 4))
                for p in sol.placements if p.status != "suppressed"
            ),
            "suppressed": sorted(
                (p.feature_id, p.reason)
                for p in sol.placements if p.status == "suppressed"
            ),
            "stats": dict(sorted(sol.stats.items())) if isinstance(sol.stats, dict) else {},
        }
    return base


def digest_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """单用例结构化 digest：resolve → compose → validate → solve。"""
    # V4 场景维度（layout/labels）不走组合管线
    if case.get("kind") in ("layout", "labels"):
        return _scenario_digest(case)

    from app.lib.cartography.composition_validation import validate_component_composition
    from app.lib.cartography.layout_solver import (
        LayoutParticipantV3,
        solve_layout_v3,
    )
    from app.services.gis_harness.component_composer import ComponentComposer
    from app.services.gis_harness.component_resolver import ComponentResolver

    preferred = case.get("preferred_variants") or {}
    sel = ComponentResolver().resolve(
        composition_template_id=case["template"],
        map_model_id=case["model"],
        output_target=case["output"],
        available_context=case["context"],
        **({"preferred_variants": preferred} if preferred else {}),
    )
    components = ComponentComposer().compose(
        sel,
        title_text=case["title"],
        subtitle_text=case["subtitle"],
        layer_bindings=case["bindings"],
        layer_model_ids=case["layer_models"],
    )
    layer_ids = list(case["bindings"].values())
    validation = validate_component_composition(
        components,
        composition_template_id=sel.composition_template_id,
        map_model_id=case["model"],
        layer_ids=layer_ids,
        output_target=case["output"],
        layer_model_ids=case["layer_models"],
    )

    required_types = set()
    if hasattr(sel, "required"):
        required_types = set()
    # optional 判定：选型里 required 槽类型 → optional=False。
    # resolver.required 记录的是槽 id 而非类型；用 composition 槽反查。
    from app.lib.cartography.composition_templates import get_composition_template_registry
    compo = get_composition_template_registry().get(sel.composition_template_id)
    required_slots = {s.id for s in (compo.component_slots if compo else []) if s.cardinality == "required"}
    for slot in (compo.component_slots if compo else []):
        if slot.cardinality == "required":
            required_types.update(slot.allowed_component_types)

    participants = [
        LayoutParticipantV3(
            id=c.id, type=c.type,
            requested_zone=c.position,
            priority=c.priority,
            optional=c.type not in required_types,
        )
        for c in components
    ]
    layout = solve_layout_v3(participants, page_profile=case["profile"])

    return {
        "case": case["id"],
        "kind": case["kind"],
        "model": case["model"],
        "template": case["template"],
        "output": case["output"],
        "profile": case["profile"],
        "selection": {
            "composition_template_id": sel.composition_template_id,
            "selected": sorted(sel.selected),
            "required_slots": sorted(required_slots),
            "component_templates": dict(sorted(sel.component_templates.items())),
            "reason_codes": sorted(sel.reason_codes),
            "rejected_count": len(sel.rejected),
        },
        "components": [
            {
                "id": c.id,
                "type": c.type,
                "position": c.position,
                "priority": c.priority,
                "variant": c.variant,
                "templateId": c.templateId,
                "layerId": str((c.options or {}).get("layerId") or ""),
                "title": str((c.options or {}).get("text") or "") if c.type in ("title", "subtitle") else "",
            }
            for c in components
        ],
        "validation": {
            "ok": validation.ok,
            "error_codes": sorted(v.code for v in validation.errors),
            "warning_codes": sorted(v.code for v in validation.warnings),
        },
        "layout": {
            "placements": {p.id: p.zone for p in layout.placements},
            "moved": sorted(p.id for p in layout.placements if p.moved),
            "suppressed": sorted(p.id for p in layout.suppressed),
            "warning_count": len(layout.warnings),
            "reasons": {p.id: p.reason for p in layout.placements if p.reason != "requested"},
            "conflicts": sorted(c.component_id for c in layout.conflicts),
        },
    }


def digest_sha(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def golden_path(case_id: str) -> Path:
    safe = case_id.replace("::", "__").replace("/", "_")
    return GOLDEN_DIR / f"{safe}.json"


def load_golden(case_id: str) -> Dict[str, Any] | None:
    path = golden_path(case_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_golden(case_id: str, payload: Dict[str, Any]) -> None:
    path = golden_path(case_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
