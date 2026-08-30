"""Component Library 2.0 — 对抗场景测试（goal §20 A–H）。

场景直接落在 planner/composer/validation/facet-completion 的公开入口上
（不 mock 内部状态），锁定 v2 的语义契约：

- A  heatmap + choropleth → colorbar + 分级图例并存（binding 级冲突）
- B  两个 chart 面板独立（chartRef/placement/collapsed）
- C  学术图 + 区位插图（live + export 双面在场）
- D  user 拖放的面板不被 auto layout 挪回
- E  同层多图例按 conflict/cardinality 规则处理
- F  chart artifact 缺失 → 仅该 chart facet needs_repair
- G  SVG/PDF parity —— annotation/inset/多图例导出真值不撒谎
- H  小视口 overflow 的确定性钳制（前端 resolve-layout 同语义）
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.components import (
    CartographyComponent,
    ComponentPlacement,
    annotation_component,
    chart_panel_component,
    colorbar_component,
    inset_map_component,
    legend_component,
    validate_annotation_payload,
    validate_inset_payload,
)


# ── Scenario A：heatmap + choropleth 双图例并存 ────────────────────────────


def _multi_layer_components() -> list:
    return [
        CartographyComponent(id="title", type="title", position="top-center", priority=10),
        CartographyComponent(
            id="colorbar-main", type="continuous_colorbar", position="bottom-right", priority=15,
            options={"layerId": "school-heatmap"},
        ),
        CartographyComponent(
            id="legend-secondary", type="legend", position="bottom-left", priority=16,
            options={"layerId": "district-choropleth"},
        ),
        CartographyComponent(id="north-arrow", type="north_arrow", position="top-right", priority=30),
        CartographyComponent(id="scale-bar", type="scale_bar", position="bottom-right", priority=20),
        CartographyComponent(id="attribution", type="attribution", position="bottom-left", priority=50),
    ]


def test_scenario_a_heatmap_and_choropleth_both_legends():
    """A：heatmap 主层 colorbar + choropleth 参考层分级图例 —— 双图例
    合法并存，不再被 type 级冲突杀掉一半。"""
    from app.lib.cartography.composition_validation import validate_component_composition

    comps = _multi_layer_components()
    layer_models = {
        "school-heatmap": "visual_heatmap",
        "district-choropleth": "administrative_choropleth",
    }
    res = validate_component_composition(
        comps,
        composition_template_id="composition.standard_analysis",
        map_model_id="visual_heatmap",
        layer_ids=["school-heatmap", "district-choropleth"],
        output_target="interactive",
        layer_model_ids=layer_models,
    )
    assert res.ok, [v.to_dict() for v in res.errors]
    types = {c.type for c in comps if c.enabled}
    assert "continuous_colorbar" in types and "legend" in types


def test_scenario_a_composer_expands_per_layer_legends():
    """A（组合层）：all_thematic 图例槽位按层展开 —— heatmap 层→colorbar、
    choropleth 层→legend，各绑各层。"""
    from app.services.gis_harness.component_composer import get_component_composer
    from app.services.gis_harness.component_resolver import get_component_resolver

    sel = get_component_resolver().resolve(
        composition_template_id="composition.standard_analysis",
        map_model_id="visual_heatmap",
        output_target="interactive",
    )
    composed = get_component_composer().compose(
        sel,
        title_text="成都学校分布",
        layer_bindings={
            "primary": "school-heatmap",
            "reference": "district-choropleth",
        },
        composition_template_id=sel.composition_template_id,
        layer_model_ids={
            "school-heatmap": "visual_heatmap",
            "district-choropleth": "administrative_choropleth",
        },
    )
    legend_by_layer = {
        c.options.get("layerId"): c.type
        for c in composed
        if c.type in ("legend", "categorical_legend", "continuous_colorbar")
    }
    assert legend_by_layer.get("school-heatmap") == "continuous_colorbar"
    assert legend_by_layer.get("district-choropleth") == "legend"
    # primary 层保留旧固定 id（向后兼容），参考层 id 内嵌角色
    ids = {c.id for c in composed}
    assert "colorbar-main" in ids and "legend-reference" in ids


# ── Scenario B：两个 chart 面板独立 ────────────────────────────────────────


def test_scenario_b_two_chart_panels_independent():
    """B：两个 chart_panel 实例 —— 独立 id / chartRef / placement /
    collapsed；组合校验不再按 zero_or_one 拒绝。"""
    from app.lib.cartography.composition_validation import validate_component_composition

    chart_a = chart_panel_component(
        chart={"type": "bar", "title": "各区学校数量", "data": [{"name": "锦江区", "value": 12}]},
        component_id="chart-district",
        placement=ComponentPlacement(mode="floating", x=20, y=20, width=320, height=240),
    )
    chart_b = chart_panel_component(
        chart_ref="ref:chart-type-mix",
        component_id="chart-type",
        variant="compact",
        placement=ComponentPlacement(mode="floating", x=360, y=20, collapsed=True),
    )
    assert chart_a.id != chart_b.id
    assert chart_a.options["chart"] and chart_b.options["chartRef"] == "ref:chart-type-mix"
    assert chart_b.placement.collapsed is True and chart_a.placement.collapsed is False

    comps = [
        CartographyComponent(id="title", type="title", position="top-center", priority=10),
        chart_a, chart_b,
    ]
    res = validate_component_composition(
        comps,
        composition_template_id="composition.statistical_map",
        map_model_id="administrative_choropleth",
        output_target="interactive",
    )
    # chart 槽 max_count=3 → 两个实例不超限
    assert not any(
        v.code == "cardinality_exceeded" and v.slot == "chart_panel" for v in res.violations
    )


# ── Scenario C：学术图 + 区位插图 ──────────────────────────────────────────


def test_scenario_c_academic_map_with_inset_selected():
    """C：学术组合 + inset_context → inset_map 入选且 native（渲染/导出
    真值两侧登记）。"""
    from app.services.gis_harness.component_resolver import get_component_resolver

    sel = get_component_resolver().resolve(
        composition_template_id="composition.academic_map",
        map_model_id="administrative_choropleth",
        output_target="pdf",
        available_context=["inset_context"],
    )
    assert "inset_map" in sel.selected

    inset = inset_map_component(
        bbox=[97.0, 26.0, 108.0, 34.0],
        main_bbox=[103.9, 30.6, 104.2, 30.8],
        boundary=[[97.0, 30.0], [100.0, 33.0], [104.0, 34.0], [108.0, 30.0], [104.0, 26.5], [100.0, 26.0]],
        label="中国范围",
    )
    assert inset.type == "inset_map"
    assert inset.options["mainBbox"] == [103.9, 30.6, 104.2, 30.8]
    assert len(inset.options["boundary"]) == 6


def test_scenario_c_inset_payload_validation():
    """C（payload 门）：bbox 缺失/无序、boundary 超限均拒绝。"""
    assert validate_inset_payload({}) is not None  # 缺 bbox
    assert validate_inset_payload({"bbox": [108, 26, 97, 34]}) is not None  # 无序
    ok = validate_inset_payload({"bbox": [97, 26, 108, 34], "label": "区位"})
    assert ok is None
    big = {"bbox": [97, 26, 108, 34], "boundary": [[0.0, 0.0]] * 513}
    assert validate_inset_payload(big) is not None


# ── Scenario D：user 拖放不被 auto layout 挪回 ─────────────────────────────


def test_scenario_d_user_floating_survives_mutations():
    """D：floating placement（用户拖放）在后续 position/其它突变后保持；
    position 突变才重新锚定（anchor 双写契约）。"""
    from app.services.gis_harness.components import mutate_component

    panel = chart_panel_component(
        chart={"type": "bar", "title": "t", "data": [{"name": "a", "value": 1}]},
        component_id="chart-1",
    )
    comps = [panel]
    # 用户拖拽 → floating
    dragged, _ = mutate_component(
        comps, component_id="chart-1",
        placement={"mode": "floating", "x": 500, "y": 300, "width": 320, "height": 240},
    )
    assert dragged[0].placement.mode == "floating"
    assert dragged[0].placement.x == 500
    # 无关突变（variant）不动 user placement
    bumped, _ = mutate_component(
        dragged, component_id="chart-1", variant="compact",
    )
    assert bumped[0].placement.mode == "floating"
    assert bumped[0].placement.x == 500


# ── Scenario E：同层多图例 conflict/cardinality 规则 ───────────────────────


def test_scenario_e_same_layer_legend_rules():
    """E：同层同型重复 → binding_conflict；同层跨族（图例+色条）→ 冲突；
    不同层各自图例 → 合法。"""
    from app.lib.cartography.composition_validation import validate_binding_conflicts

    def legend(cid: str, layer: str, ctype: str = "legend") -> CartographyComponent:
        return CartographyComponent(
            id=cid, type=ctype, position="bottom-left", priority=16,
            options={"layerId": layer},
        )

    dup = [legend("a", "L1"), legend("b", "L1")]
    issues = validate_binding_conflicts(dup)
    assert issues and issues[0][1] == "legend"

    cross = [legend("a", "L1"), legend("b", "L1", "continuous_colorbar")]
    assert validate_binding_conflicts(cross)

    distinct = [legend("a", "L1"), legend("b", "L2"), legend("c", "L3", "continuous_colorbar")]
    assert not validate_binding_conflicts(distinct)


# ── Scenario F：chart artifact 缺失 → 局部 needs_repair ────────────────────


def _chapter_with_chart(chart_ref: str) -> dict:
    return {
        "query": "成都学校",
        "map_layers": [
            {"layer_id": "school-heatmap", "role": "primary", "enabled": True},
        ],
        "template_selection": {"export_profile": {"formats": ["png"]}},
    }


def test_scenario_f_missing_chart_artifact_is_local_repair():
    """F：chartRef 指向的 artifact 不在 descriptors → 仅该 chart facet
    needs_repair；其余 facet 状态不受影响（地图不整体假 complete）。"""
    from app.services.gis_harness.product_graph import (
        FS_COMPLETE,
        FS_NEEDS_REPAIR,
        build_facet_completion,
    )

    mapspec = {
        "layers": [{"id": "school-heatmap", "source": "src-1"}],
        "sources": [{"id": "src-1", "ref": "ref:data-1"}],
        "layout": {"components": [
            {"id": "title", "type": "title", "enabled": True},
            {"id": "chart-1", "type": "chart_panel", "enabled": True,
             "options": {"chartRef": "ref:chart-evicted"}},
        ]},
    }
    # descriptors 有数据层 ref、无 chart ref（被逐出）
    descriptors = {"ref:data-1": {"bbox": [103.9, 30.6, 104.2, 30.8]}}
    facets = build_facet_completion(
        _chapter_with_chart("ref:chart-evicted"),
        mapspec,
        descriptors=descriptors,
        current_revision=1,
    )
    by_kind = {}
    for f in facets:
        by_kind.setdefault(f.kind, []).append(f)
    charts = by_kind.get("chart", [])
    assert charts and charts[0].status == FS_NEEDS_REPAIR
    layers = by_kind.get("map_layer", [])
    assert layers and layers[0].status == FS_COMPLETE


def test_scenario_f_chart_ref_present_keeps_complete():
    """F（反例）：descriptor 在场 → chart facet 保持 complete（不虚构失败）。"""
    from app.services.gis_harness.product_graph import FS_COMPLETE, build_facet_completion

    mapspec = {
        "layers": [{"id": "school-heatmap", "source": "src-1"}],
        "sources": [{"id": "src-1", "ref": "ref:data-1"}],
        "layout": {"components": [
            {"id": "chart-1", "type": "chart_panel", "enabled": True,
             "options": {"chartRef": "ref:chart-ok"}},
        ]},
    }
    facets = build_facet_completion(
        _chapter_with_chart("ref:chart-ok"),
        mapspec,
        descriptors={"ref:data-1": {"bbox": [103.9, 30.6, 104.2, 30.8]},
                     "ref:chart-ok": {"bbox": None}},
        current_revision=1,
    )
    charts = [f for f in facets if f.kind == "chart"]
    assert charts and charts[0].status == FS_COMPLETE


# ── Scenario G：SVG/PDF parity —— 导出真值不撒谎 ───────────────────────────


def test_scenario_g_export_truth_for_new_components():
    """G：annotation / inset_map / 图例族的 exporter 真值与消费方一致 ——
    descriptor、矩阵、导出目录三方零漂移（SVG=PNG 包装链，同一画布
    chrome 路径）。"""
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.cartography.component_renderers import get_component_renderer_registry
    from app.lib.cartography.export_component_catalog import build_catalog

    reg = get_component_registry()
    rr = get_component_renderer_registry()
    # 无漂移（descriptor ↔ 矩阵对账在 registry.validate 内）
    assert not reg.validate()
    # annotation 导出真值（此前矩阵谎报为空）
    assert rr.has_exporter("annotation", "png")
    assert rr.has_exporter("annotation", "svg")
    # inset 双面在场
    assert rr.has_renderer("inset_map")
    assert rr.has_exporter("inset_map", "pdf")
    # 图例族逐类型导出真值
    for t in ("legend", "categorical_legend", "continuous_colorbar"):
        assert rr.has_exporter(t, "svg")
    # 导出目录与矩阵一致
    catalog = {c["type"]: c for c in build_catalog()["componentTypes"]}
    for t in ("annotation", "inset_map", "legend", "continuous_colorbar"):
        support = rr.support_for(t)
        assert sorted(catalog[t]["exporterSupport"]) == sorted(support.exporters)
        assert catalog[t]["rendererRequired"] == (t != "export_layout")


# ── Scenario H：小视口 overflow 的确定性钳制（后端契约位） ──────────────────


def test_scenario_h_floating_payload_bounds():
    """H（后端侧）：placement 像素域有界（ge 约束）—— 视口内钳制语义由
    前端 resolve-layout.clampFloatingRect 承担（同名场景前端测试锁定）。"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ComponentPlacement(mode="floating", x=99999, y=0)
    with pytest.raises(ValidationError):
        ComponentPlacement(mode="floating", x=0, y=0, width=5000)


# ── 注记框架 payload（v2 有界契约）────────────────────────────────────────


def test_annotation_payload_bounds():
    assert validate_annotation_payload({"variant": "callout", "text": "汶川震中"}) is not None
    ok = validate_annotation_payload({
        "variant": "callout", "text": "汶川震中 M8.0", "anchor": [103.4, 31.0],
    })
    assert ok is None
    group = {"variant": "group", "items": [
        {"text": f"点 {i}", "anchor": [103.0 + i * 0.01, 31.0]} for i in range(12)
    ]}
    assert validate_annotation_payload(group) is None
    over = {"variant": "group", "items": [{"text": "x"}] * 13}
    assert validate_annotation_payload(over) is not None
    assert validate_annotation_payload({"text": "x" * 201}) is not None
